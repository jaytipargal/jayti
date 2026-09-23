#!/usr/bin/env python3
"""Colab-side E2E step: pull Hub queue, segment, train, and ack pull cursor."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HUB_URL = os.environ.get("JAYTI_HUB_URL", "https://agent.jaytipargal.tech").rstrip("/")
ROOT = Path(os.environ.get("JTAGENT_ROOT", "/content/TAN/jtagent"))
ENV_FILE = Path(os.environ.get("JTAGENT_COLAB_ENV", "/content/jtagent-dev.env"))
BRANCH = os.environ.get("JTAGENT_BRANCH", "cursor/hub-json-content-coerce-7af4")
REPO = Path("/content/jayti")


def load_env() -> None:
    if os.environ.get("EKA_DEVICE_ID") and os.environ.get("EKA_DEVICE_KEY"):
        return
    if not ENV_FILE.is_file():
        raise FileNotFoundError(f"missing env file: {ENV_FILE}")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def ensure_repo() -> None:
    target = REPO / "scripts" / "eka_train.py"
    if not target.is_file():
        if REPO.exists():
            shutil.rmtree(REPO)
        subprocess.check_call(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "-b",
                BRANCH,
                "https://github.com/jaytipargal/jayti.git",
                str(REPO),
            ]
        )
        return
    subprocess.check_call(["git", "-C", str(REPO), "fetch", "origin", BRANCH])
    subprocess.check_call(["git", "-C", str(REPO), "reset", "--hard", f"origin/{BRANCH}"])


def mark_processed(cursor: int) -> dict:
    body = json.dumps({"cursor": int(cursor)}).encode("utf-8")
    req = urllib.request.Request(
        f"{HUB_URL}/pull/mark",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Device-Id": os.environ["EKA_DEVICE_ID"],
            "X-Api-Key": os.environ["EKA_DEVICE_KEY"],
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    load_env()
    ensure_repo()
    ROOT.mkdir(parents=True, exist_ok=True)

    sandbox = REPO / "sandbox" / "jtagent"
    if str(sandbox) not in sys.path:
        sys.path.insert(0, str(sandbox))

    import segment_jsonl  # type: ignore
    import segment_train  # type: ignore

    items = segment_jsonl.pull_from_hub()
    if not items:
        report = {"ok": True, "noop": "no_hub_items", "pulled": 0}
        print(json.dumps(report, indent=2))
        print("COLAB_SEGMENT_E2E_DONE")
        return 0

    cursor = max(int(i.get("id") or 0) for i in items)
    chunks = []
    skipped = 0
    for item in items:
        chunk = segment_jsonl._item_to_chunk(item)
        if chunk is None:
            skipped += 1
            continue
        chunks.append(chunk)

    chunks = segment_jsonl.redact_chunks(chunks)
    manifest = segment_jsonl.write_category_jsonl(ROOT, chunks)
    pull_mark = mark_processed(cursor)
    train_report = segment_train.train_categories(ROOT)

    report = {
        "ok": True,
        "pulled": len(items),
        "chunks": len(chunks),
        "skipped": skipped,
        "cursor": cursor,
        "manifest": manifest,
        "pull_mark": pull_mark,
        "train": train_report,
    }
    print(json.dumps(report, indent=2))
    print("COLAB_SEGMENT_E2E_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
