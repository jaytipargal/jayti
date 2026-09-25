#!/usr/bin/env python3
"""Colab-side E2E for the 7-8B QLoRA path.

Chain: pull Hub queue -> segment+redact -> mark cursor -> QLoRA train (A100) ->
merge+GGUF export -> pack devices -> push artifacts to the TAN Drive folder ->
(optional) publish adapter+GGUF to a private HF repo.

This is the QLoRA counterpart of `colab_segment_e2e.py` (GPT-2 path). It reuses
segment_jsonl for data prep and drive_sync for publishing, so the ingest,
redaction, and Drive-folder-only rules are unchanged.

Env:
  JTAGENT_ROOT          default /content/TAN/jtagent
  JTAGENT_BASE_MODEL    default Qwen/Qwen2.5-7B-Instruct
  JTAGENT_QUANT         default Q4_K_M
  LLAMA_CPP_DIR         path to a cloned llama.cpp (for GGUF export)
  JTAGENT_PUBLISH_HF    "1" to also publish to a private HF repo
  EKA_DEVICE_ID/KEY     hub /pull + /pull/mark credentials
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("JTAGENT_ROOT", "/content/TAN/jtagent"))
BASE_MODEL = os.environ.get("JTAGENT_BASE_MODEL", "Qwen/Qwen2.5-7B-Instruct")
QUANT = os.environ.get("JTAGENT_QUANT", "Q4_K_M")


def _sandbox_on_path() -> None:
    here = Path(__file__).resolve().parent
    for cand in (here, Path("/content/jayti/sandbox/jtagent")):
        if (cand / "qlora_train.py").exists() and str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
            break


def run(root: Path = ROOT) -> dict:
    _sandbox_on_path()
    import drive_sync  # type: ignore
    import export_gguf  # type: ignore
    import qlora_train  # type: ignore
    import segment_jsonl  # type: ignore
    from pack_devices import pack_devices  # type: ignore

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    # 1) Data prep (hub pull -> redact -> per-category jsonl; seed fallback).
    manifest = segment_jsonl.run(root)

    # 2) QLoRA train one unified adapter.
    train = qlora_train.train_qlora(root, config=qlora_train.build_config({"base_model": BASE_MODEL}))
    adapter = train.get("adapter")

    # 3) Export merged GGUF (best-effort — needs llama.cpp present).
    gguf_report = None
    gguf_path = None
    if adapter:
        try:
            gguf_report = export_gguf.export(
                Path(adapter),
                BASE_MODEL,
                root / "gguf",
                quant=QUANT,
                llama_cpp_dir=os.environ.get("LLAMA_CPP_DIR"),
            )
            gguf_path = gguf_report.get("quant_gguf")
        except Exception as exc:  # noqa: BLE001
            gguf_report = {"error": f"{type(exc).__name__}: {exc}"}
            print("GGUF export skipped:", gguf_report["error"])

    # 4) Device packs point at the new adapter + GGUF.
    pack_devices(root, extra={"adapter": adapter, "gguf": gguf_path, "base_model": BASE_MODEL})

    # 5) Publish artifacts to the TAN Drive folder.
    push_rc = drive_sync.cmd_push(drive_sync.DEFAULT_FOLDER_ID, root.parent)

    # 6) Optional: private HF publish.
    hf_report = None
    if os.environ.get("JTAGENT_PUBLISH_HF", "").lower() in {"1", "true", "yes"} and adapter:
        import hf_publish  # type: ignore

        hf_report = hf_publish.publish(Path(adapter), gguf_path=Path(gguf_path) if gguf_path else None)

    report = {
        "ok": True,
        "root": str(root),
        "base_model": BASE_MODEL,
        "manifest": manifest,
        "train": train,
        "gguf": gguf_report,
        "drive_push_rc": push_rc,
        "hf": hf_report,
    }
    print(json.dumps(report, indent=2, default=str))
    print("COLAB_QLORA_E2E_DONE")
    return report


def main() -> int:
    run(ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
