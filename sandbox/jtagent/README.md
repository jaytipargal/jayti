# jtagent sandbox (Colab + shared TAN Drive)

Personal agent **jtagent** (`go4garage01` on Hugging Face) for Jayti’s sandbox.

## What this slice does

1. Reads Hub model **metadata** for `go4garage01/jt-agent-model` and dataset card for `go4garage01/jt-agent-data` (no Chrome sqlite, no encryption key files).
2. Writes artifacts under the **shared folder only**: `/content/TAN/jtagent/` (folder id `1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB`). Does **not** `colab drivemount` all of My Drive.
3. Trains a **CPU-safe GPT-2 LoRA** (same base as `scripts/eka_train.py`). The Hub dump is ~29.5 GB of shards and is **not** full-finetuned on this Colab CPU VM.
4. Arranges device packs:
   - `devices/samsung_s24_ultra/` — phone + Termux
   - `devices/asus_vivobook/` — VivoBook
   - `devices/windows_pc_abcom/` — G4G-Laptop (Lenovo 82KA; **not** the ASUS)

Physical S24 / VivoBook pick this up when Drive syncs `jaytipargal.jp@gmail.com`. This Cloud Agent cannot ADB or Windows-MCP those machines from here.

## Run on the live Colab session

```bash
colab --auth=oauth2 exec -s jt-agent -f sandbox/jtagent/colab_run.py --timeout 1200
```
