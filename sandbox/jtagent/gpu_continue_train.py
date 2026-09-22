"""Continue jtagent GPU segmented training without waiting for a human."""

from __future__ import annotations

import json
import sys
from pathlib import Path

print("GPU_CONTINUE_START")
sys.path.insert(0, "/content/jayti/scripts")
sys.path.insert(0, "/content/jayti/sandbox/jtagent")

import drive_sync  # noqa: E402
import segment_jsonl  # noqa: E402
import segment_train  # noqa: E402

FOLDER_ID = "1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB"
TAN = Path("/content/TAN")
ROOT = Path("/content/TAN/jtagent")

drive_sync.cmd_mount(FOLDER_ID, TAN)
print("pull_rc", drive_sync.cmd_pull(FOLDER_ID, TAN))
manifest = segment_jsonl.run(ROOT)
print(json.dumps(manifest, indent=2))
report = segment_train.train_categories(ROOT)
print("push_rc", drive_sync.cmd_push(FOLDER_ID, TAN))
print(json.dumps(report, indent=2))
print("GPU_CONTINUE_DONE")
