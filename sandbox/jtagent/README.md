# jtagent sandbox (Colab T4 + shared TAN Drive + Hub Postgres)

Personal agent **jtagent** (`go4garage01` on Hugging Face) for Jayti’s sandbox.

## What this slice does

1. **Hub / Postgres** — live Hub at `https://agent.jaytipargal.tech` (`/healthz`, `/status`). Sandbox can also use a dedicated `eka_agent` Postgres (same six tables as `scripts/setup_vps_db.sh`) via `JAYTI_PG_DSN` or gitignored `.local/jayti_pg.dsn`. Never the Global Devices Neon `gd-catalog`.
2. **Drive (folder only)** — `drive_sync.py` `mount|pull|push` for folder id `1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB` → `/content/TAN`. Uses rclone `--drive-root-folder-id`. Does **not** `colab drivemount` all of My Drive.
3. **Segmented JSONL** — `segment_jsonl.py` pulls Hub `/pull` or `ingestion_queue`, maps via `CATEGORY_MAP`/`PRIORITY_MAP`, redacts with `eka_redact` / `eka_scan_secrets`, writes `training/{category}/`. `identity`, `hub`, `drive`, `pipeline`, `devices`, and `train` map to **`sandbox_ops`**. Falls back to `seed_chunks` **only** if the queue is empty. Skips chrome sqlite / encryption key-derivation / live WhatsApp DBs.
4. **GPT-2 LoRA** — `segment_train.py` trains per category with N≥min_chunks on Colab T4 via `scripts/eka_train.py`. Does **not** full-SFT the ~29.5GB Hub shards. Adapters under `jtagent/adapters/`.
5. **Device packs** — `devices/{samsung_s24_ultra,asus_vivobook,windows_pc_abcom}/` with adapter paths. `windows_pc_abcom` is Lenovo **NOT_ASUS**.

## Status (read-only)

```bash
python sandbox/jtagent/hub_status.py
python sandbox/jtagent/deploy_smoke.py
```

## Drive sync

```bash
# Needs rclone Drive OAuth once (RCLONE_DRIVE_TOKEN or rclone config reconnect)
python sandbox/jtagent/drive_sync.py mount --folder-id 1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB
python sandbox/jtagent/drive_sync.py pull
python sandbox/jtagent/drive_sync.py push
```

## Segment + train on Colab T4

```bash
colab --auth=oauth2 new -s jt-agent-gpu --gpu T4   # if pruned
# upload jayti tree to /content/jayti on the VM, then:
colab --auth=oauth2 exec -s jt-agent-gpu -f sandbox/jtagent/colab_run.py --timeout 2400
# or continue:
colab --auth=oauth2 exec -s jt-agent-gpu -f sandbox/jtagent/gpu_continue_train.py --timeout 2400
```

## Queue-aware operator (host side)

```bash
# Runs queue gate + Colab pull/train + /pull/mark + Drive publish
python sandbox/jtagent/colab_operator.py

# Optional smoke: first push 9 redacted sandbox rows, then run the same flow
python sandbox/jtagent/colab_operator.py --simulate-ingest
```

Operator helpers committed in this repo:

- `colab_segment_e2e.py` (Colab side pull/mark/train)
- `colab_tar_artifacts.py` (Colab side tarball of adapters/training/devices)
- `colab_restore_runtime.py` (restore adapters/repo after prune)
- `PIPELINE_UNBLOCK_RUNBOOK.md` (edge + infra execution plan)
- `DEVICE_PUSH.md` (physical node push commands)
- `edge_deploy/` (Vivobook Modelfile + S24 Termux runner templates)

## QLoRA path (7-8B → GGUF) — capable model for the devices

The GPT-2 LoRA path above stays as the light VPS responder. For a capable
assistant that runs on the VivoBook, fine-tune a real trainable base and export
GGUF (needs Colab Pro **A100**; the 29.5 GB `go4garage01/jt-agent-model` is an
MLX/Qwen3-VL bundle and is **not** a train target):

```bash
# One-shot on the Colab GPU VM (after Drive mount + repo clone):
JTAGENT_BASE_MODEL=Qwen/Qwen2.5-7B-Instruct JTAGENT_QUANT=Q4_K_M \
LLAMA_CPP_DIR=/content/llama.cpp \
python sandbox/jtagent/colab_qlora_e2e.py
```

Pieces: `qlora_train.py` (4-bit QLoRA, one unified adapter), `export_gguf.py`
(merge → GGUF → quantize), `hf_publish.py` (private HF publish), then
`drive_sync.py push` sends `jtagent/{adapters,gguf,devices}` to the TAN folder.
Devices pick up the GGUF with `edge_deploy/vivobook/pull_and_build.{sh,ps1}`.
The VPS keeps GPT-2 adapters via `vps_install_agent.sh` + `vps_agent_sync.sh`.

## Follow-on (not this pass)

Llama 3.2 1B/3B Unsloth QLoRA → GGUF/ExecuTorch → sqlite-vec CRDT → Ollama Modelfile / Termux. Prefer documenting over implementing when T4 memory conflicts with GPT-2 LoRA E2E.

Physical S24 / VivoBook pick adapters up when Drive syncs `jaytipargl.jp@gmail.com`. This Cloud Agent does not claim those devices online without evidence.
