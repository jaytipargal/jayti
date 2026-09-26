# jtagent QLoRA runbook — Colab → VPS → devices

Exact command order to train the 7-8B QLoRA agent, serve the light agent on the
VPS, and run the GGUF model on the devices. Everything here needs your own
accounts/hardware; none of it runs from CI.

> Branch: while PR #13 is open, clone `claude/gifted-ride-qn610c`. After it
> merges, use `main`.
> Drive owner + Colab Google account = `jaytipargl.jp@gmail.com` (no "a"). The
> GitHub repo (`jaytipargal/jayti`) and the `agent.jaytipargal.tech` domain carry
> the "a"; HF is user `jtagent`. Don't confuse the spellings.

---

## Phase 0 — one-time prep

1. **Colab Pro A100** — open the notebook, Runtime → Change runtime type → **A100 GPU**.
2. **HF token** (write) at <https://huggingface.co/settings/tokens>. In Colab, save it as a secret named `HF_TOKEN` (the notebook reads it), or `os.environ["HF_TOKEN"]=...`.
3. **Make the personal HF data repos private** (HF → each repo → Settings → Change visibility): `jtagent/jt-agent-data`, `jtagent/chrome-browser-data`, `jtagent/jt-agent-dataset`.
4. **rclone remote for the TAN Drive** — once, on **each** machine that syncs (Colab, VPS, VivoBook, S24). Two options:
   - **Headless service account (devices, unattended):** in the `info@kailash-ai.com` GCloud project (Drive API enabled; `jaytipargal.jp` is admin) create a service account and download its JSON key; share the TAN folder with the service account's email (reader suffices to pull); then on the device run `edge_deploy/configure_rclone_sa.sh <key.json>` (or the `.ps1` on Windows). Equivalent for `drive_sync.py`: `RCLONE_DRIVE_SERVICE_ACCOUNT_FILE=<key.json>`.
   - **Interactive OAuth** (Colab/VPS), as a TAN writer or the owner (`jaytipargl.jp`):
     ```bash
     rclone config create jtagent_tan drive \
       scope=drive root_folder_id=1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB
     # follow the browser OAuth prompt; then verify:
     rclone lsd jtagent_tan:
     ```
   (Base model is downloaded from HF, not Drive — no need to stage 15 GB.)

---

## Phase A — Colab: train → GGUF → Drive (→ HF)

Either run the notebook `sandbox/jtagent/jtagent_colab.ipynb` top-to-bottom (the
last cell runs the full E2E), **or** these exact cells on a fresh A100 runtime:

```python
# A1 — repo + llama.cpp (build the quantizer; convert script is pure-python)
!git clone --depth 1 -b claude/gifted-ride-qn610c https://github.com/jaytipargal/jayti /content/jayti
!git clone --depth 1 https://github.com/ggerganov/llama.cpp /content/llama.cpp
!pip -q install -r /content/llama.cpp/requirements.txt
!cd /content/llama.cpp && cmake -B build && cmake --build build --config Release -j --target llama-quantize
```

```python
# A2 — env
import os
os.environ.update({
    "JTAGENT_ROOT": "/content/TAN/jtagent",
    "JTAGENT_BASE_MODEL": "Qwen/Qwen2.5-7B-Instruct",   # not gated; Llama-3.1 needs license acceptance
    "JTAGENT_QUANT": "Q4_K_M",
    "LLAMA_CPP_DIR": "/content/llama.cpp",
    "JTAGENT_PUBLISH_HF": "1",                          # also push adapter+GGUF to a PRIVATE HF repo
})
# HF token (or a Colab secret named HF_TOKEN):
# os.environ["HF_TOKEN"] = "hf_xxx"
# Hub /pull creds — optional; without them it uses the seed/Postgres fallback:
# os.environ["EKA_DEVICE_ID"] = "jtagent_sandbox"; os.environ["EKA_DEVICE_KEY"] = "jt_xxx"
```

```python
# A3 — run the whole pipeline: segment → QLoRA → merge+GGUF → pack → Drive push (→ HF)
!python /content/jayti/sandbox/jtagent/colab_qlora_e2e.py
```

Success looks like `COLAB_QLORA_E2E_DONE`, with on Drive:
`jtagent/adapters/adapter_<date>_qlora/final`, `jtagent/gguf/*.q4_k_m.gguf`,
`jtagent/devices/*/pack.jsonl`, `jtagent/RUN_REPORT.md`.

---

## Phase B — VPS (Vultr web console, as root): light agent + retrieval

The heavy model runs on the VivoBook; the VPS keeps the retrieval-grounded GPT-2
responder.

```bash
# B1 — get the branch
sudo mkdir -p /opt/jayti/src && cd /opt/jayti/src
sudo git clone -b claude/gifted-ride-qn610c https://github.com/jaytipargal/jayti jayti \
  || (cd jayti && sudo git fetch origin claude/gifted-ride-qn610c \
      && sudo git checkout claude/gifted-ride-qn610c && sudo git pull)

# B2 — build the agent venv + copy server/sync helper into /opt/jayti/agent
sudo JTAGENT_REPO_DIR=/opt/jayti/src/jayti bash /opt/jayti/src/jayti/sandbox/jtagent/vps_install_agent.sh

# B3 — rclone remote for adapter sync (Phase 0 step 4, on the VPS)
rclone config create jtagent_tan drive scope=drive root_folder_id=1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB

# B4 — install + start the unit (ExecStartPre runs vps_agent_sync.sh to pull the newest adapter)
sudo install -m0644 /opt/jayti/src/jayti/server/jayti-agent/jtagent.service /etc/systemd/system/jtagent.service
sudo systemctl daemon-reload && sudo systemctl enable --now jtagent

# B5 — restore retrieval on :8444 (currently 502)
sudo bash /opt/jayti/src/jayti/sandbox/jtagent/vps_retrieval_recovery.sh

# B6 — verify
curl -s localhost:8000/health
curl -s localhost:8444/healthz
curl -s https://agent.jaytipargal.tech/agent/health
curl -s https://agent.jaytipargal.tech/retrieve/health   # was 502 → expect ok
```

---

## Phase C — devices: pull the GGUF and run

**VivoBook — Linux boot / WSL:**
```bash
cd /path/to/jayti/sandbox/jtagent/edge_deploy/vivobook
rclone config create jtagent_tan drive scope=drive root_folder_id=1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB
bash pull_and_build.sh          # pulls newest GGUF from Drive, ollama create jtagent
ollama run jtagent
```

**VivoBook — Windows boot (PowerShell):**
```powershell
cd C:\path\to\jayti\sandbox\jtagent\edge_deploy\vivobook
rclone config create jtagent_tan drive scope=drive root_folder_id=1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB
powershell -ExecutionPolicy Bypass -File .\pull_and_build.ps1
ollama run jtagent
```

**Samsung S24 (Termux):** a 4.5 GB Q4 is heavy for a phone — either re-export a
smaller quant in Phase A (`JTAGENT_QUANT=Q3_K_M`, or a ≤3B base) or have the S24
query the VivoBook/VPS. To run locally:
```bash
# in Termux, after pulling the GGUF into ~/storage and installing llama.cpp:
bash /path/to/jayti/sandbox/jtagent/edge_deploy/s24/termux-run.sh ~/storage/jt-agent.gguf "hi"
```

---

## Feeding new data (optional, before Phase A)

To train on fresh device data instead of the seed set, push from each device
first (see `DEVICE_PUSH.md` for per-device setup + key registration):
```bash
python scripts/eka_agent_push.py --device asus_vivobook      # OS auto-detected
python scripts/eka_agent_push.py --device samsung_s24_ultra
```
Then set `EKA_DEVICE_ID`/`EKA_DEVICE_KEY` in Phase A2 so the Colab run pulls the
queue via the Hub.
