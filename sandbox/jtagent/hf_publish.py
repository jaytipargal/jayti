#!/usr/bin/env python3
"""Publish a trained LoRA adapter (+ optional GGUF) to a PRIVATE HF repo.

Keeps trained artifacts versioned on the Hub for the `jtagent` / `go4garage01`
account. Repos are created private by default — this pipeline handles personal
data, so public is never the default.

`huggingface_hub` is imported lazily inside the functions that need it, because
the offline test suite does not stub it; the pure helpers below import cleanly.

Usage:
  python hf_publish.py --adapter <ROOT>/adapters/adapter_<date>_qlora/final
  python hf_publish.py --adapter .../final --gguf <ROOT>/gguf/xxx.q4_k_m.gguf \
                       --repo go4garage01/jt-agent-lora
  python hf_publish.py --adapter .../final --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DEFAULT_ORG = "go4garage01"
DEFAULT_NAME = "jt-agent-lora"


def resolve_repo_id(explicit: str | None = None, org: str | None = None, name: str | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("JTAGENT_HF_REPO", "").strip()
    if env:
        return env
    org = (org or os.environ.get("JTAGENT_HF_ORG") or DEFAULT_ORG).strip("/")
    name = (name or DEFAULT_NAME).strip("/")
    return f"{org}/{name}"


def resolve_token() -> str | None:
    for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACEHUB_API_TOKEN"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    for cand in (Path("/content/hf.token"), Path("/content/jtagent-hf.token")):
        if cand.is_file():
            text = cand.read_text(encoding="utf-8").strip()
            if text:
                return text
    return None


def select_upload_files(adapter_dir: Path | None, gguf_path: Path | None = None) -> dict:
    """What we would upload. Pure — just existence checks, no network."""
    plan: dict[str, list[str]] = {"adapter": [], "gguf": []}
    if adapter_dir:
        adapter_dir = Path(adapter_dir)
        if adapter_dir.is_dir():
            plan["adapter"] = sorted(
                str(p.relative_to(adapter_dir)) for p in adapter_dir.rglob("*") if p.is_file()
            )
    if gguf_path:
        gguf_path = Path(gguf_path)
        if gguf_path.is_file():
            plan["gguf"] = [gguf_path.name]
    return plan


def publish(
    adapter_dir: Path | None,
    gguf_path: Path | None = None,
    repo_id: str | None = None,
    private: bool = True,
    token: str | None = None,
    dry_run: bool = False,
) -> dict:
    repo_id = resolve_repo_id(repo_id)
    plan = select_upload_files(adapter_dir, gguf_path)
    report = {
        "repo_id": repo_id,
        "private": private,
        "adapter_files": len(plan["adapter"]),
        "gguf_files": len(plan["gguf"]),
        "dry_run": dry_run,
        "uploaded": False,
    }
    if dry_run:
        print("HF_PUBLISH_DRY_RUN:", report)
        return report

    if not plan["adapter"] and not plan["gguf"]:
        report["status"] = "nothing_to_upload"
        print("HF_PUBLISH_SKIPPED: nothing to upload")
        return report

    token = token or resolve_token()
    try:
        from huggingface_hub import HfApi, create_repo, upload_file, upload_folder
    except ImportError:
        import subprocess

        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "huggingface_hub"])
        from huggingface_hub import HfApi, create_repo, upload_file, upload_folder

    create_repo(repo_id, private=private, exist_ok=True, repo_type="model", token=token)

    if plan["adapter"]:
        upload_folder(
            repo_id=repo_id,
            folder_path=str(adapter_dir),
            path_in_repo="adapter",
            token=token,
            commit_message="Publish jtagent LoRA adapter",
        )
    if plan["gguf"]:
        upload_file(
            repo_id=repo_id,
            path_or_fileobj=str(gguf_path),
            path_in_repo=f"gguf/{Path(gguf_path).name}",
            token=token,
            commit_message="Publish jtagent GGUF",
        )
    # Confirm privacy after the fact.
    try:
        info = HfApi(token=token).repo_info(repo_id, repo_type="model")
        report["private"] = bool(getattr(info, "private", private))
    except Exception:  # noqa: BLE001
        pass
    report["uploaded"] = True
    print("HF_PUBLISH_DONE:", report)
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Publish jtagent adapter/GGUF to a private HF repo")
    p.add_argument("--adapter", type=Path, default=None)
    p.add_argument("--gguf", type=Path, default=None)
    p.add_argument("--repo", default=None, help="owner/name (default go4garage01/jt-agent-lora)")
    p.add_argument("--public", action="store_true", help="create/keep the repo public (NOT default)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    if not args.adapter and not args.gguf:
        p.error("need --adapter and/or --gguf")
    publish(
        args.adapter,
        gguf_path=args.gguf,
        repo_id=args.repo,
        private=not args.public,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
