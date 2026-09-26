# jtagent pipeline unblock runbook

This runbook executes the four requested phases with the scripts in this repo.

## Phase 1 — edge node ingestion (VivoBook + Samsung S24)

Use `DEVICE_PUSH.md` for per-device bootstrap and daily scheduling.

### Minimum acceptance gate

- `items_unprocessed > 0` on Hub after push
- at least 3 rows each for training categories:
  - `whatsapp_chat`
  - `browser_data`
  - `infrastructure` (from `system_logs`)

### Commands

```bash
# On each physical device after writing device.env:
python3 /path/to/jayti/scripts/eka_agent_push.py --device asus_vivobook
python3 /path/to/jayti/scripts/eka_agent_push.py --device samsung_s24_ultra

# Verify from any node:
curl -s https://agent.jaytipargal.tech/status | python3 -m json.tool
```

Samsung Termux background hardening (required for long pushes):

```bash
termux-wake-lock
# From adb host:
adb shell dumpsys deviceidle whitelist +com.termux
```

## Phase 2 — Colab T4 PEFT generation

`colab_operator.py` is the host-side orchestrator for queue->train->Drive publish.

```bash
# No-op if queue is empty
python3 sandbox/jtagent/colab_operator.py

# Sandbox smoke path: pushes 9 redacted test rows before training
python3 sandbox/jtagent/colab_operator.py --simulate-ingest
```

What it does:

1. Verifies/creates `jt-agent-gpu`
2. Restores adapters after prune (best effort)
3. Uploads env + `colab_segment_e2e.py`
4. Pulls `/pull?status=new`, marks `/pull/mark`, trains per category (N>=3)
5. Tars artifacts and publishes back to Drive via `rclone`

## Phase 3 — edge inference deployment prep

Current training output is GPT-2 LoRA adapters under:

- `/content/TAN/jtagent/adapters/adapter_YYYY-MM-DD_<category>/final`

Use these adapters in edge packs immediately; GGUF / `.pte` compilation is a separate export pass.

### Vivobook local deploy (Ollama path)

1. Pull adapter artifacts from Drive to local disk.
2. Build merged/quantized model (GGUF export pass).
3. Recreate Modelfile with enforced system prompt and point to GGUF path.
4. Start from `edge_deploy/vivobook/Modelfile.template`.

### Samsung S24 local deploy

1. Pull artifacts from Drive to Termux storage.
2. Run `llama.cpp` or ExecuTorch runner against compiled mobile artifact.
3. Start from `edge_deploy/s24/termux-run.sh`.

> Note: physical deployment requires direct access to those nodes; this cloud VM cannot mount their local filesystems.

## Phase 4 — infrastructure state resolution

### Vultr 8444 retrieval API

Run from VPS web console:

```bash
curl -fsSL https://raw.githubusercontent.com/jaytipargal/jayti/cursor/hub-json-content-coerce-7af4/sandbox/jtagent/vps_retrieval_recovery.sh | sudo bash
```

Expected success: `localhost:8444/healthz HTTP 200`.

### windowsmcp TLS

This requires network path/cert remediation from the Lenovo/Windows side and MCP endpoint owner; cloud VM probes alone cannot install missing roots on that node.

### rclone replication

Drive publish is active through `rclone copy ... jtagent_tan:jtagent` from this VM. To switch auth principal to `info@kailash-ai.com`, either complete `rclone config reconnect jtagent_tan` with that account in browser, or — headless — use a service account from that project: share the TAN folder with the SA's email and set `RCLONE_DRIVE_SERVICE_ACCOUNT_FILE=<key.json>` (see `edge_deploy/configure_rclone_sa.sh`).
