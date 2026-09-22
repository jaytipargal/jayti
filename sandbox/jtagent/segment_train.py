#!/usr/bin/env python3
"""Train GPT-2 LoRA per category on Colab T4 (eka_train).

Expects segment_jsonl output under /content/TAN/jtagent/training/{category}/.
Writes adapters to adapters/adapter_{date}_{category}/final and updates
RUN_REPORT.md + optional training_status via DSN.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("JTAGENT_ROOT", "/content/TAN/jtagent"))
MIN_CHUNKS = int(os.environ.get("JTAGENT_MIN_CHUNKS", "3"))


def _pip(pkgs: list[str]) -> None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *pkgs])


def _ensure_deps() -> None:
    # Keep CUDA torch already on the image; only fill missing train stack.
    pkgs = ["transformers", "peft", "datasets", "accelerate", "torchao>=0.16"]
    try:
        import torch  # noqa: F401
    except ImportError:
        pkgs.insert(0, "torch")
    _pip(pkgs)


def _load_eka_train():
    for p in (
        Path("/content/jayti/scripts"),
        Path(__file__).resolve().parents[2] / "scripts",
    ):
        if (p / "eka_train.py").exists():
            sys.path.insert(0, str(p))
            break
    import eka_train  # type: ignore

    return eka_train


def _category_files(training: Path) -> dict[str, Path]:
    found = {}
    if not training.exists():
        return found
    for cat_dir in sorted(training.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name.startswith("."):
            continue
        jsonls = sorted(cat_dir.glob("*.jsonl"))
        if jsonls:
            found[cat_dir.name] = jsonls[-1]
    return found


def _count_train_rows(path: Path) -> int:
    # eka_train skips first 10 pad lines
    n = 0
    with path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i < 10 or not line.strip():
                continue
            n += 1
    return n


def _write_training_status(dsn: str | None, batch_id: str, stats: dict) -> None:
    if not dsn:
        return
    try:
        import psycopg
    except ImportError:
        return
    try:
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO training_status
                    (batch_id, batch_date, chunks_created, duplicates, p0_found, p1_found,
                     lora_adapter, train_status, trained_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (batch_id) DO UPDATE SET
                      chunks_created = EXCLUDED.chunks_created,
                      lora_adapter = EXCLUDED.lora_adapter,
                      train_status = EXCLUDED.train_status,
                      trained_at = EXCLUDED.trained_at
                    """,
                    (
                        batch_id,
                        date.today(),
                        stats.get("chunks_created", 0),
                        stats.get("duplicates", 0),
                        stats.get("p0_found", 0),
                        stats.get("p1_found", 0),
                        stats.get("lora_adapter"),
                        stats.get("train_status", "done"),
                    ),
                )
            conn.commit()
        print("training_status upserted", batch_id)
    except Exception as exc:  # noqa: BLE001
        print("training_status write skipped:", type(exc).__name__, exc)


def train_categories(root: Path = ROOT) -> dict:
    _ensure_deps()
    import torch

    print(
        "cuda",
        torch.cuda.is_available(),
        torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
    )
    eka_train = _load_eka_train()
    sandbox = Path(__file__).resolve().parent
    if str(sandbox) not in sys.path:
        sys.path.insert(0, str(sandbox))
    from pack_devices import pack_devices  # type: ignore

    training = root / "training"
    cats = _category_files(training)
    adapters_root = root / "adapters"
    adapters_root.mkdir(parents=True, exist_ok=True)
    day = date.today().isoformat()
    results = {}
    total_chunks = 0

    eka_train.NUM_EPOCHS = int(os.environ.get("JTAGENT_EPOCHS", "2"))
    eka_train.BATCH_SIZE = 1
    eka_train.MAX_SEQ_LEN = int(os.environ.get("JTAGENT_MAX_SEQ", "256"))
    eka_train.ADAPTER_DIR = str(adapters_root)

    for cat, path in cats.items():
        n = _count_train_rows(path)
        print(f"category={cat} chunks={n} file={path}")
        if n < MIN_CHUNKS:
            results[cat] = {"skipped": True, "reason": f"N<{MIN_CHUNKS}", "chunks": n}
            continue
        eka_train.TRAINING_FILE = str(path)
        label = f"{day}_{cat}"
        chunks = eka_train.load_training_data(date_str=None, batch_size=max(n, MIN_CHUNKS))
        try:
            adapter = eka_train.train_lora(chunks, label)
        except Exception as exc:  # noqa: BLE001
            print("train_error", cat, type(exc).__name__, exc)
            results[cat] = {"error": f"{type(exc).__name__}: {exc}", "chunks": n}
            continue
        total_chunks += n
        results[cat] = {"adapter": adapter, "chunks": n, "label": label}

    primary = next(
        (v.get("adapter") for v in results.values() if v.get("adapter")),
        None,
    )
    pack_devices(
        root,
        extra={
            "adapter": primary,
            "adapters_by_category": {
                k: v.get("adapter") for k, v in results.items() if v.get("adapter")
            },
            "colab_session": os.environ.get("COLAB_SESSION", "jt-agent-gpu"),
            "gpu": "T4" if torch.cuda.is_available() else "cpu",
        },
    )

    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "min_chunks": MIN_CHUNKS,
        "results": results,
        "primary_adapter": primary,
        "cuda": torch.cuda.is_available(),
    }
    (root / "RUN_REPORT.md").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    dsn = os.environ.get("JAYTI_PG_DSN") or os.environ.get("EKA_PG_DSN")
    local_dsn = Path(__file__).resolve().parent / ".local" / "jayti_pg.dsn"
    if not dsn and local_dsn.is_file():
        dsn = local_dsn.read_text(encoding="utf-8").strip()
    _write_training_status(
        dsn,
        f"seg_{day}",
        {
            "chunks_created": total_chunks,
            "duplicates": 0,
            "p0_found": 0,
            "p1_found": 0,
            "lora_adapter": primary,
            "train_status": "done" if primary else "pending",
        },
    )
    print(json.dumps(report, indent=2))
    print("SEGMENT_LORA_DONE")
    return report


def main() -> int:
    root = ROOT
    if len(sys.argv) > 1:
        root = Path(sys.argv[1])
    train_categories(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
