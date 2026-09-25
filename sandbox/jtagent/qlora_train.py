#!/usr/bin/env python3
"""QLoRA fine-tune a real ~7-8B instruct base on Colab (A100) for jtagent.

This is the replacement for the GPT-2 LoRA path (`segment_train.py` +
`scripts/eka_train.py`) when the goal is a capable personal assistant that can
be exported to GGUF and run on the VivoBook (Ollama) / S24 (llama.cpp).

Why not `go4garage01/jt-agent-model`: that 29.5GB repo is an MLX-quantized
Qwen3-VL multimodal bundle (Apple-Silicon only, no valid HF config) and cannot
be loaded by transformers/peft on Colab NVIDIA GPUs. We fine-tune a clean,
trainable instruct base instead (default Qwen2.5-7B-Instruct).

Input:  segment_jsonl output under <ROOT>/training/<category>/*.jsonl
        (already redacted via eka_redact/eka_scan_secrets).
Output: one unified LoRA adapter under <ROOT>/adapters/adapter_<date>_qlora/final

Heavy deps (torch, transformers, peft, datasets, bitsandbytes, accelerate) are
imported lazily inside `train_qlora` so the pure helpers below stay importable
and unit-testable offline.

Usage:
  python qlora_train.py                          # ROOT from JTAGENT_ROOT
  python qlora_train.py /content/TAN/jtagent     # explicit root
  JTAGENT_BASE_MODEL=meta-llama/Llama-3.1-8B-Instruct python qlora_train.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("JTAGENT_ROOT", "/content/TAN/jtagent"))

DEFAULT_BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"
DEFAULT_SYSTEM_PROMPT = (
    "You are jtagent, Jayti's personal assistant. Answer from the provided "
    "personal context accurately and concisely. Keep secrets redacted and never "
    "claim a device is online without evidence."
)

# LoRA target modules per architecture family. Qwen2.5 / Llama-3 share the same
# attention + MLP projection names.
LLAMA_QWEN_TARGETS = [
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
]
GPT2_TARGETS = ["c_attn", "c_proj"]

# eka_train / segment_jsonl pad the first 10 lines of every category file with
# {"category": "pad"} rows; skip them here too.
PAD_CATEGORY = "pad"


def resolve_base_model() -> str:
    return os.environ.get("JTAGENT_BASE_MODEL", "").strip() or DEFAULT_BASE_MODEL


def default_target_modules(model_name: str) -> list[str]:
    """Best-effort LoRA target modules from the base model id."""
    name = (model_name or "").lower()
    if "gpt2" in name or "gpt-2" in name:
        return list(GPT2_TARGETS)
    # Qwen2/2.5, Llama-3, Mistral, Gemma-2 all expose these proj names.
    return list(LLAMA_QWEN_TARGETS)


def build_config(overrides: dict | None = None) -> dict:
    """Hyperparameters, env-overridable. Pure — no heavy imports."""

    def _int(key: str, default: int) -> int:
        try:
            return int(os.environ.get(key, default))
        except (TypeError, ValueError):
            return default

    def _float(key: str, default: float) -> float:
        try:
            return float(os.environ.get(key, default))
        except (TypeError, ValueError):
            return default

    cfg = {
        "base_model": resolve_base_model(),
        "lora_r": _int("JTAGENT_LORA_R", 16),
        "lora_alpha": _int("JTAGENT_LORA_ALPHA", 32),
        "lora_dropout": _float("JTAGENT_LORA_DROPOUT", 0.05),
        "learning_rate": _float("JTAGENT_LR", 2e-4),
        "epochs": _int("JTAGENT_EPOCHS", 3),
        "batch_size": _int("JTAGENT_BATCH", 1),
        "grad_accum": _int("JTAGENT_GRAD_ACCUM", 16),
        "max_seq_len": _int("JTAGENT_MAX_SEQ", 2048),
        "load_in_4bit": os.environ.get("JTAGENT_4BIT", "1").lower() not in {"0", "false", "no"},
        "system_prompt": os.environ.get("JTAGENT_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
    }
    cfg["target_modules"] = default_target_modules(cfg["base_model"])
    if overrides:
        cfg.update(overrides)
    return cfg


def _coerce_output(output) -> str:
    """Pretty-print JSON outputs, leave plain text alone (matches eka_train)."""
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            return output
        return json.dumps(parsed, indent=2, ensure_ascii=False, default=str)
    return json.dumps(output, ensure_ascii=False, default=str)


def iter_training_rows(training_dir: Path):
    """Yield {instruction, output, category, title} from every category file.

    Skips the 10 pad rows per file and any row whose category is 'pad'. Dedups
    on (instruction, output) so re-runs that re-pad don't inflate the set.
    """
    training_dir = Path(training_dir)
    seen: set[tuple[str, str]] = set()
    if not training_dir.exists():
        return
    for cat_dir in sorted(training_dir.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name.startswith("."):
            continue
        for jsonl in sorted(cat_dir.glob("*.jsonl")):
            with jsonl.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("category") == PAD_CATEGORY:
                        continue
                    instruction = str(row.get("input") or row.get("instruction") or "").strip()
                    if not instruction:
                        continue
                    output = _coerce_output(row.get("output", ""))
                    key = (instruction, output)
                    if key in seen:
                        continue
                    seen.add(key)
                    yield {
                        "instruction": instruction,
                        "output": output,
                        "category": row.get("category") or cat_dir.name,
                        "title": row.get("title", ""),
                    }


def chunk_to_messages(row: dict, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> list[dict]:
    """One training row → chat messages for the base's chat template."""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": row.get("instruction", "")},
        {"role": "assistant", "content": row.get("output", "")},
    ]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pip(pkgs: list[str]) -> None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *pkgs])


def _ensure_deps() -> None:
    pkgs = [
        "transformers>=4.44",
        "peft>=0.12",
        "datasets>=2.20",
        "accelerate>=0.33",
        "bitsandbytes>=0.43",
    ]
    try:
        import torch  # noqa: F401
    except ImportError:
        pkgs.insert(0, "torch")
    _pip(pkgs)


def train_qlora(
    root: Path = ROOT,
    out_dir: Path | None = None,
    config: dict | None = None,
    ensure_deps: bool = True,
) -> dict:
    """4-bit QLoRA SFT over the segmented personal data → one LoRA adapter."""
    root = Path(root)
    cfg = config or build_config()
    rows = list(iter_training_rows(root / "training"))
    day = date.today().isoformat()
    out_dir = Path(out_dir) if out_dir else root / "adapters" / f"adapter_{day}_qlora" / "final"

    result = {
        "ts": _now_iso(),
        "root": str(root),
        "base_model": cfg["base_model"],
        "rows": len(rows),
        "adapter": None,
        "status": "pending",
    }
    if not rows:
        result["status"] = "no_rows"
        print("QLORA_TRAIN_SKIPPED: no training rows under", root / "training")
        return result

    if ensure_deps:
        _ensure_deps()

    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
            Trainer,
            TrainingArguments,
        )
    except ImportError as exc:
        result["status"] = f"missing_deps:{exc}"
        print("QLORA_TRAIN_FAILED:", result["status"])
        return result

    has_gpu = bool(getattr(torch, "cuda", None) and torch.cuda.is_available())
    print(f"cuda={has_gpu} base={cfg['base_model']} rows={len(rows)}")

    tokenizer = AutoTokenizer.from_pretrained(cfg["base_model"], trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    quant_config = None
    if cfg["load_in_4bit"] and has_gpu:
        quant_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )

    model = AutoModelForCausalLM.from_pretrained(
        cfg["base_model"],
        quantization_config=quant_config,
        device_map="auto" if has_gpu else None,
        torch_dtype=torch.bfloat16 if has_gpu else torch.float32,
        trust_remote_code=True,
    )
    model.config.use_cache = False
    if quant_config is not None:
        model = prepare_model_for_kbit_training(model)
    model.gradient_checkpointing_enable()

    lora = LoraConfig(
        r=cfg["lora_r"],
        lora_alpha=cfg["lora_alpha"],
        lora_dropout=cfg["lora_dropout"],
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=cfg["target_modules"],
    )
    model = get_peft_model(model, lora)

    def _render(row):
        messages = chunk_to_messages(row, cfg["system_prompt"])
        text = tokenizer.apply_chat_template(messages, tokenize=False)
        return {"text": text}

    dataset = Dataset.from_list(rows).map(_render)

    def _tokenize(ex):
        toks = tokenizer(
            ex["text"],
            truncation=True,
            max_length=cfg["max_seq_len"],
            padding="max_length",
        )
        toks["labels"] = toks["input_ids"].copy()
        return toks

    tokenized = dataset.map(_tokenize, remove_columns=dataset.column_names)

    out_dir.parent.mkdir(parents=True, exist_ok=True)
    args = TrainingArguments(
        output_dir=str(out_dir.parent),
        num_train_epochs=cfg["epochs"],
        per_device_train_batch_size=cfg["batch_size"],
        gradient_accumulation_steps=cfg["grad_accum"],
        learning_rate=cfg["learning_rate"],
        logging_steps=5,
        save_total_limit=1,
        report_to="none",
        bf16=has_gpu,
        gradient_checkpointing=True,
        remove_unused_columns=False,
        dataloader_pin_memory=False,
    )
    Trainer(model=model, args=args, train_dataset=tokenized).train()

    model.save_pretrained(str(out_dir))
    tokenizer.save_pretrained(str(out_dir))

    size = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file())
    result.update(
        {
            "adapter": str(out_dir),
            "adapter_size_mb": round(size / (1024 * 1024), 1),
            "status": "completed",
        }
    )
    report_path = root / "RUN_REPORT.md"
    report_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    print("QLORA_TRAIN_DONE")
    return result


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(argv[0]) if argv else ROOT
    train_qlora(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
