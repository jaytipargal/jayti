#!/usr/bin/env python3
"""Merge a LoRA adapter into its base, convert to GGUF, quantize.

Pipeline: base + adapter -> merged fp16 HF dir -> f16 GGUF -> quantized GGUF
(Q4_K_M by default). The quantized file is what the VivoBook (Ollama) and S24
(llama.cpp) load. Publish it to Drive `jtagent/gguf/` (drive_sync push) and/or
Hugging Face (hf_publish).

Heavy steps (transformers/peft merge, llama.cpp convert + quantize) are guarded
so the pure path/command helpers stay importable and unit-testable offline.

Usage:
  python export_gguf.py --adapter <ROOT>/adapters/adapter_<date>_qlora/final \
                        --base Qwen/Qwen2.5-7B-Instruct \
                        --out <ROOT>/gguf --quant Q4_K_M
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Quant types llama-quantize accepts that we expose; Q4_K_M is the default sweet
# spot for a 7-8B model on a laptop (~4.5GB) and small enough to sync via Drive.
KNOWN_QUANTS = {
    "Q2_K",
    "Q3_K_M",
    "Q4_0",
    "Q4_K_M",
    "Q5_K_M",
    "Q6_K",
    "Q8_0",
    "F16",
    "BF16",
}
DEFAULT_QUANT = "Q4_K_M"

# Candidate names for the llama.cpp tools across versions.
CONVERT_SCRIPTS = ("convert_hf_to_gguf.py", "convert-hf-to-gguf.py")
QUANTIZE_BINS = ("llama-quantize", "quantize")


def resolve_quant(name: str | None) -> str:
    if not name:
        return DEFAULT_QUANT
    upper = name.upper()
    if upper not in KNOWN_QUANTS:
        raise ValueError(f"unknown quant {name!r}; choose one of {sorted(KNOWN_QUANTS)}")
    return upper


def gguf_paths(adapter_dir: Path, base_model: str, out_root: Path, quant: str) -> dict:
    """Deterministic output paths for one export. Pure."""
    adapter_dir = Path(adapter_dir)
    out_root = Path(out_root)
    # adapter_<date>_qlora/final -> stem 'adapter_<date>_qlora'
    stem = adapter_dir.parent.name if adapter_dir.name == "final" else adapter_dir.name
    base_slug = base_model.strip("/").replace("/", "_")
    return {
        "stem": stem,
        "merged_dir": out_root / f"{stem}_merged",
        "f16_gguf": out_root / f"{stem}.f16.gguf",
        "quant_gguf": out_root / f"{stem}.{quant.lower()}.gguf",
        "base_slug": base_slug,
    }


def find_llama_cpp(root: Path | None = None) -> dict:
    """Locate convert script + quantize binary. Returns {convert, quantize}."""
    search = []
    if root:
        search.append(Path(root))
    env_root = os.environ.get("LLAMA_CPP_DIR")
    if env_root:
        search.append(Path(env_root))
    search += [Path("/content/llama.cpp"), Path.home() / "llama.cpp", Path("llama.cpp")]

    convert = None
    quantize = shutil.which("llama-quantize") or shutil.which("quantize")
    for base in search:
        if not base or not base.exists():
            continue
        for name in CONVERT_SCRIPTS:
            cand = base / name
            if cand.is_file():
                convert = str(cand)
                break
        if quantize is None:
            for name in QUANTIZE_BINS:
                for sub in (base, base / "build" / "bin"):
                    cand = sub / name
                    if cand.is_file():
                        quantize = str(cand)
                        break
                if quantize:
                    break
        if convert:
            break
    return {"convert": convert, "quantize": quantize}


def convert_cmd(convert_script: str, hf_dir: Path, out_gguf: Path, outtype: str = "f16") -> list[str]:
    return [
        sys.executable,
        str(convert_script),
        str(hf_dir),
        "--outfile",
        str(out_gguf),
        "--outtype",
        outtype,
    ]


def quantize_cmd(quantize_bin: str, in_gguf: Path, out_gguf: Path, quant: str) -> list[str]:
    return [str(quantize_bin), str(in_gguf), str(out_gguf), quant]


def _pip(pkgs: list[str]) -> None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *pkgs])


def merge_adapter(base_model: str, adapter_dir: Path, merged_dir: Path) -> Path:
    """Load base + adapter, merge_and_unload, save a plain fp16 HF dir."""
    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError:
        _pip(["torch", "transformers>=4.44", "peft>=0.12", "accelerate>=0.33"])
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

    merged_dir = Path(merged_dir)
    merged_dir.mkdir(parents=True, exist_ok=True)
    base = AutoModelForCausalLM.from_pretrained(
        base_model, torch_dtype=torch.float16, trust_remote_code=True
    )
    model = PeftModel.from_pretrained(base, str(adapter_dir))
    model = model.merge_and_unload()
    model.save_pretrained(str(merged_dir), safe_serialization=True)
    AutoTokenizer.from_pretrained(base_model, trust_remote_code=True).save_pretrained(
        str(merged_dir)
    )
    return merged_dir


def export(
    adapter_dir: Path,
    base_model: str,
    out_root: Path,
    quant: str = DEFAULT_QUANT,
    llama_cpp_dir: Path | None = None,
    keep_merged: bool = False,
) -> dict:
    """Full export. Returns a report dict; raises on missing llama.cpp tools."""
    quant = resolve_quant(quant)
    paths = gguf_paths(adapter_dir, base_model, out_root, quant)
    Path(out_root).mkdir(parents=True, exist_ok=True)

    tools = find_llama_cpp(llama_cpp_dir)
    if not tools["convert"] or not tools["quantize"]:
        raise RuntimeError(
            "llama.cpp not found: need convert_hf_to_gguf.py + llama-quantize. "
            "Set LLAMA_CPP_DIR or clone https://github.com/ggerganov/llama.cpp."
        )

    merge_adapter(base_model, adapter_dir, paths["merged_dir"])
    subprocess.check_call(convert_cmd(tools["convert"], paths["merged_dir"], paths["f16_gguf"]))
    subprocess.check_call(
        quantize_cmd(tools["quantize"], paths["f16_gguf"], paths["quant_gguf"], quant)
    )

    if not keep_merged:
        shutil.rmtree(paths["merged_dir"], ignore_errors=True)

    report = {
        "adapter": str(adapter_dir),
        "base_model": base_model,
        "quant": quant,
        "f16_gguf": str(paths["f16_gguf"]),
        "quant_gguf": str(paths["quant_gguf"]),
        "size_mb": round(Path(paths["quant_gguf"]).stat().st_size / (1024 * 1024), 1)
        if Path(paths["quant_gguf"]).exists()
        else None,
    }
    (Path(out_root) / "EXPORT_REPORT.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
    print("EXPORT_GGUF_DONE")
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Merge LoRA adapter and export a quantized GGUF")
    p.add_argument("--adapter", required=True, type=Path)
    p.add_argument("--base", default=os.environ.get("JTAGENT_BASE_MODEL", "Qwen/Qwen2.5-7B-Instruct"))
    p.add_argument("--out", type=Path, default=Path(os.environ.get("JTAGENT_ROOT", ".")) / "gguf")
    p.add_argument("--quant", default=DEFAULT_QUANT)
    p.add_argument("--llama-cpp-dir", type=Path, default=None)
    p.add_argument("--keep-merged", action="store_true")
    args = p.parse_args(argv)
    export(
        args.adapter,
        args.base,
        args.out,
        quant=args.quant,
        llama_cpp_dir=args.llama_cpp_dir,
        keep_merged=args.keep_merged,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
