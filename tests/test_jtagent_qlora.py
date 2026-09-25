"""Pure-logic tests for the 7-8B QLoRA path: qlora_train, export_gguf,
hf_publish, plus the drive_sync/pack_devices wiring that carries the GGUF.

Everything here is offline: heavy deps (torch/transformers/peft/datasets) are
stubbed by conftest, huggingface_hub is only imported inside publish() (never
reached in dry-run), and no network/GPU is touched.
"""
import json
from pathlib import Path

import pytest

import qlora_train
import export_gguf
import hf_publish
import drive_sync
from pack_devices import pack_devices
from device_bind import DRIVE_OWNER_EMAIL


# ── qlora_train pure helpers ────────────────────────────────────────────
def test_target_modules_qwen_llama_vs_gpt2():
    assert "q_proj" in qlora_train.default_target_modules("Qwen/Qwen2.5-7B-Instruct")
    assert "gate_proj" in qlora_train.default_target_modules("meta-llama/Llama-3.1-8B")
    assert qlora_train.default_target_modules("gpt2") == ["c_attn", "c_proj"]


def test_build_config_env_overrides(monkeypatch):
    monkeypatch.setenv("JTAGENT_BASE_MODEL", "meta-llama/Llama-3.1-8B-Instruct")
    monkeypatch.setenv("JTAGENT_LORA_R", "8")
    monkeypatch.setenv("JTAGENT_EPOCHS", "5")
    cfg = qlora_train.build_config()
    assert cfg["base_model"] == "meta-llama/Llama-3.1-8B-Instruct"
    assert cfg["lora_r"] == 8
    assert cfg["epochs"] == 5
    assert "down_proj" in cfg["target_modules"]


def test_build_config_bad_ints_fall_back(monkeypatch):
    monkeypatch.setenv("JTAGENT_LORA_R", "not-a-number")
    assert qlora_train.build_config()["lora_r"] == 16


def _write_cat(training: Path, cat: str, rows: list[dict]):
    dest = training / cat / "2026-09-25.jsonl"
    dest.parent.mkdir(parents=True, exist_ok=True)
    pad = [{"input": f"pad-{i}", "output": "ok", "category": "pad"} for i in range(10)]
    with dest.open("w", encoding="utf-8") as fh:
        for r in pad + rows:
            fh.write(json.dumps(r) + "\n")


def test_iter_training_rows_skips_pad_and_dedupes(tmp_path):
    training = tmp_path / "training"
    _write_cat(
        training,
        "whatsapp_chat",
        [
            {"input": "Q1", "output": "A1", "category": "whatsapp_chat", "title": "t"},
            {"input": "Q1", "output": "A1", "category": "whatsapp_chat"},  # dup
            {"input": "", "output": "x", "category": "whatsapp_chat"},  # empty instruction
        ],
    )
    rows = list(qlora_train.iter_training_rows(training))
    assert len(rows) == 1
    assert rows[0]["instruction"] == "Q1"
    assert rows[0]["category"] == "whatsapp_chat"


def test_iter_training_rows_pretty_prints_json_output(tmp_path):
    training = tmp_path / "training"
    _write_cat(training, "hub", [{"input": "Q", "output": json.dumps({"a": 1}), "category": "hub"}])
    rows = list(qlora_train.iter_training_rows(training))
    assert '"a": 1' in rows[0]["output"] and "\n" in rows[0]["output"]


def test_chunk_to_messages_shape():
    msgs = qlora_train.chunk_to_messages({"instruction": "hi", "output": "yo"}, "SYS")
    assert [m["role"] for m in msgs] == ["system", "user", "assistant"]
    assert msgs[0]["content"] == "SYS"
    assert msgs[2]["content"] == "yo"


def test_train_qlora_no_rows_is_offline_noop(tmp_path):
    (tmp_path / "training").mkdir()
    out = qlora_train.train_qlora(tmp_path, ensure_deps=False)
    assert out["status"] == "no_rows"
    assert out["adapter"] is None


def test_train_qlora_with_rows_reports_missing_deps_offline(tmp_path):
    # conftest stubs transformers/peft but not BitsAndBytesConfig /
    # prepare_model_for_kbit_training, so the guarded import fails cleanly
    # instead of trying to train. ensure_deps=False keeps it network-free.
    _write_cat(tmp_path / "training", "hub", [{"input": "Q", "output": "A", "category": "hub"}])
    out = qlora_train.train_qlora(tmp_path, ensure_deps=False)
    assert out["rows"] == 1
    assert out["status"].startswith("missing_deps")
    assert out["adapter"] is None


# ── export_gguf pure helpers ────────────────────────────────────────────
def test_resolve_quant_default_and_validation():
    assert export_gguf.resolve_quant(None) == "Q4_K_M"
    assert export_gguf.resolve_quant("q5_k_m") == "Q5_K_M"
    with pytest.raises(ValueError):
        export_gguf.resolve_quant("Q9_NOPE")


def test_gguf_paths_naming():
    paths = export_gguf.gguf_paths(
        Path("/r/adapters/adapter_2026-09-25_qlora/final"),
        "Qwen/Qwen2.5-7B-Instruct",
        Path("/r/gguf"),
        "Q4_K_M",
    )
    assert paths["stem"] == "adapter_2026-09-25_qlora"
    assert paths["quant_gguf"].name == "adapter_2026-09-25_qlora.q4_k_m.gguf"
    assert paths["f16_gguf"].name == "adapter_2026-09-25_qlora.f16.gguf"


def test_convert_and_quantize_cmds():
    conv = export_gguf.convert_cmd("/l/convert_hf_to_gguf.py", Path("/m"), Path("/o.gguf"))
    assert "/l/convert_hf_to_gguf.py" in conv and "--outfile" in conv
    q = export_gguf.quantize_cmd("/l/llama-quantize", Path("/a.gguf"), Path("/b.gguf"), "Q4_K_M")
    assert q == ["/l/llama-quantize", "/a.gguf", "/b.gguf", "Q4_K_M"]


def test_find_llama_cpp_locates_tools(tmp_path):
    (tmp_path / "convert_hf_to_gguf.py").write_text("#", encoding="utf-8")
    bind = tmp_path / "build" / "bin"
    bind.mkdir(parents=True)
    (bind / "llama-quantize").write_text("#", encoding="utf-8")
    tools = export_gguf.find_llama_cpp(tmp_path)
    assert tools["convert"].endswith("convert_hf_to_gguf.py")
    assert tools["quantize"].endswith("llama-quantize")


def test_export_raises_without_llama_cpp(tmp_path, monkeypatch):
    monkeypatch.delenv("LLAMA_CPP_DIR", raising=False)
    monkeypatch.setattr(export_gguf, "find_llama_cpp", lambda *a, **k: {"convert": None, "quantize": None})
    with pytest.raises(RuntimeError):
        export_gguf.export(tmp_path / "final", "Qwen/Qwen2.5-7B-Instruct", tmp_path / "gguf")


# ── hf_publish pure helpers ─────────────────────────────────────────────
def test_resolve_repo_id_precedence(monkeypatch):
    assert hf_publish.resolve_repo_id("me/explicit") == "me/explicit"
    monkeypatch.setenv("JTAGENT_HF_REPO", "env/repo")
    assert hf_publish.resolve_repo_id() == "env/repo"
    monkeypatch.delenv("JTAGENT_HF_REPO")
    assert hf_publish.resolve_repo_id() == "go4garage01/jt-agent-lora"


def test_select_upload_files_lists_adapter_and_gguf(tmp_path):
    adapter = tmp_path / "final"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
    (adapter / "adapter_model.safetensors").write_text("x", encoding="utf-8")
    gguf = tmp_path / "m.q4_k_m.gguf"
    gguf.write_text("x", encoding="utf-8")
    plan = hf_publish.select_upload_files(adapter, gguf)
    assert "adapter_config.json" in plan["adapter"]
    assert plan["gguf"] == ["m.q4_k_m.gguf"]


def test_publish_dry_run_is_private_and_uploads_nothing(tmp_path):
    adapter = tmp_path / "final"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
    report = hf_publish.publish(adapter, dry_run=True)
    assert report["private"] is True
    assert report["uploaded"] is False
    assert report["adapter_files"] == 1


def test_adapter_path_in_repo_is_versioned_by_run_dir(tmp_path):
    run = tmp_path / "adapters" / "adapter_2026-09-25_qlora"
    final = run / "final"
    final.mkdir(parents=True)
    # A `final/` leaf versions by its run dir; a bare run dir versions by itself.
    assert hf_publish.adapter_version(final) == "adapter_2026-09-25_qlora"
    assert hf_publish.adapter_path_in_repo(final) == "adapter/adapter_2026-09-25_qlora"
    assert hf_publish.adapter_path_in_repo(run) == "adapter/adapter_2026-09-25_qlora"
    # Two runs never share a Hub path, so a later publish can't overwrite an earlier one.
    other = tmp_path / "adapters" / "adapter_2026-09-26_qlora" / "final"
    other.mkdir(parents=True)
    assert hf_publish.adapter_path_in_repo(other) != hf_publish.adapter_path_in_repo(final)
    # The dry-run report surfaces the versioned path before anything uploads.
    (final / "adapter_config.json").write_text("{}", encoding="utf-8")
    report = hf_publish.publish(final, dry_run=True)
    assert report["adapter_path_in_repo"] == "adapter/adapter_2026-09-25_qlora"


def test_resolve_token_prefers_env(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_abc")
    assert hf_publish.resolve_token() == "hf_abc"


# ── wiring: drive_sync + pack_devices carry the GGUF ────────────────────
def test_gguf_in_push_paths():
    assert "jtagent/gguf" in drive_sync.PUSH_REL_PATHS


def test_pack_devices_carries_gguf_and_owner_email(tmp_path):
    pack_devices(
        tmp_path,
        extra={
            "adapter": "/content/TAN/jtagent/adapters/adapter_demo/final",
            "gguf": "/content/TAN/jtagent/gguf/adapter_demo.q4_k_m.gguf",
        },
    )
    pack = json.loads(
        (tmp_path / "devices" / "asus_vivobook" / "pack.jsonl").read_text(encoding="utf-8")
    )
    assert pack["gguf_drive_path"] == "jtagent/gguf/adapter_demo.q4_k_m.gguf"
    assert pack["train_base_model"] == "Qwen/Qwen2.5-7B-Instruct"
    assert pack["serving"].startswith("ollama")
    assert pack["email"] == DRIVE_OWNER_EMAIL
