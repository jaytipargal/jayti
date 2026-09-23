#!/usr/bin/env python3
"""Create a tarball of jtagent train artifacts on Colab."""

from __future__ import annotations

import tarfile
from pathlib import Path

ROOT = Path("/content/TAN/jtagent")
OUT = Path("/content/jtagent-artifacts.tgz")
PATHS = ("adapters", "training", "devices", "RUN_REPORT.md")


def main() -> int:
    with tarfile.open(OUT, "w:gz") as tar:
        for rel in PATHS:
            src = ROOT / rel
            if src.exists():
                tar.add(src, arcname=f"jtagent/{rel}")
    print("bytes", OUT.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
