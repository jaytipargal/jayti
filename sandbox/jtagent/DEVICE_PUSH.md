# jtagent — physical device push (eka_agent_push.py)

Hub: `https://agent.jaytipargal.tech`  
Ingest: `POST /ingest` with headers `X-Device-Id` + `X-Api-Key`  
Register new keys (one-time, on VPS): `scripts/eka_register_device.sh <device_id>`

Each physical device needs its **own** `device.env` (mode `600`). The Cloud Agent sandbox
uses `jtagent_sandbox` only for simulated ingest — do not copy that key to phones or laptops.

## device.env format (all devices)

```bash
EKA_DEVICE_ID=<device_id>
EKA_DEVICE_KEY=jt_<issued_once_at_registration>
EKA_VPS_URL=https://agent.jaytipargal.tech
```

Lookup order in `eka_agent_push.py`: `$EKA_DEVICE_ENV` → `/etc/eka-agent/device.env` →
`~/.eka_agent/device.env`.

---

## Samsung S24 Ultra (Termux)

| Field | Value |
|-------|-------|
| **device_id** | `samsung_s24_ultra` |
| **Hub URL** | `https://agent.jaytipargal.tech` |
| **env path** | `~/.eka_agent/device.env` (Termux; mode 600) |
| **push command** | `python3 eka_agent_push.py --device samsung_s24_ultra` |

### One-time setup (Termux on phone)

```bash
pkg update -y && pkg install -y python curl sqlite termux-api
mkdir -p ~/.eka_agent && chmod 700 ~/.eka_agent

# Paste the three lines from VPS register_device output:
cat > ~/.eka_agent/device.env <<'EOF'
EKA_DEVICE_ID=samsung_s24_ultra
EKA_DEVICE_KEY=jt_PASTE_FROM_VPS_REGISTER_ONCE
EKA_VPS_URL=https://agent.jaytipargal.tech
EOF
chmod 600 ~/.eka_agent/device.env

# Copy push script (from jayti repo or scp)
mkdir -p ~/eka_agent
cp /path/to/jayti/scripts/eka_agent_push.py ~/eka_agent/
cd ~/eka_agent

# Dry-run first (collect only, no POST)
python3 eka_agent_push.py --device samsung_s24_ultra --dry-run

# Real push
python3 eka_agent_push.py --device samsung_s24_ultra
```

### Battery / Doze (Samsung kills background Termux)

Grant Termux storage + run `termux-setup-storage` before WhatsApp DB paths work.

```bash
# Whitelist Termux from Doze (needs adb or root; run from PC with phone attached)
adb shell dumpsys deviceidle whitelist +com.termux

# Inspect Doze / idle state
adb shell dumpsys deviceidle
adb shell dumpsys battery

# Keep CPU awake during push (Termux package)
pkg install -y termux-api
termux-wake-lock
python3 ~/eka_agent/eka_agent_push.py --device samsung_s24_ultra
# release when done:
termux-wake-unlock
```

Optional daily cron via **Termux:Boot** + `crond` (same `0 0 * * *` line as Linux).

**Collectors:** WhatsApp `msgstore.db`, call logs, SMS (root), call recordings dir, new apps.

---

## Asus VivoBook (dual-boot: Linux or Windows)

The VivoBook boots both Linux and Windows. `collect_asus_vivobook` auto-detects
the OS: on Windows it scans `Desktop/Documents/Downloads` + Chrome history; on
Linux it scans the home dir + `/var/log`. Use the setup below that matches the
booted OS (env path differs).

| Field | Value |
|-------|-------|
| **device_id** | `asus_vivobook` |
| **Hub URL** | `https://agent.jaytipargal.tech` |
| **env path** | Linux `/etc/eka-agent/device.env` or `~/.eka_agent/device.env`; Windows `%USERPROFILE%\.eka_agent\device.env` |
| **push command** | `python3 eka_agent_push.py --device asus_vivobook` |

### One-time setup

```bash
sudo install -d -m 700 /etc/eka-agent
sudo tee /etc/eka-agent/device.env <<'EOF'
EKA_DEVICE_ID=asus_vivobook
EKA_DEVICE_KEY=jt_PASTE_FROM_VPS_REGISTER_ONCE
EKA_VPS_URL=https://agent.jaytipargal.tech
EOF
sudo chmod 600 /etc/eka-agent/device.env

sudo apt-get update && sudo apt-get install -y python3 curl
sudo install -m 755 /path/to/jayti/scripts/eka_agent_push.py /usr/local/bin/eka_agent_push.py

cd /path/to/jayti/scripts
python3 eka_agent_push.py --device asus_vivobook --dry-run
python3 eka_agent_push.py --device asus_vivobook
```

### Daily cron (00:00 UTC)

```bash
(crontab -l 2>/dev/null | grep -v eka_agent_push; echo "0 0 * * * /usr/bin/python3 /usr/local/bin/eka_agent_push.py --device asus_vivobook >> ~/.eka_agent/push.log 2>&1") | crontab -
```

On **Windows** boot, use the same one-time setup as `windows_pc_abcom` below but
with `EKA_DEVICE_ID=asus_vivobook` (env at `%USERPROFILE%\.eka_agent\device.env`),
then schedule with Task Scheduler instead of cron.

**Collectors (auto-detected):** Linux — home-directory file deltas (<10 MB) +
`/var/log` → `file_change`, `system_logs`; Windows — `Desktop/Documents/Downloads`
+ Chrome history → `file_change`, `browser_data`.

**Integrity:** Liveness for **ASUS VivoBook** may only be claimed from this hardware.

---

## windows_pc_abcom (Lenovo G4G-LAPTOP — NOT ASUS)

| Field | Value |
|-------|-------|
| **device_id** | `windows_pc_abcom` |
| **Hub URL** | `https://agent.jaytipargal.tech` |
| **env path** | `%USERPROFILE%\.eka_agent\device.env` |
| **push command** | `python eka_agent_push.py --device windows_pc_abcom` |

> **Integrity:** This machine is **Lenovo G4G-LAPTOP (82KA)**, not an ASUS VivoBook.
> Never write ASUS liveness, ASUS hardware claims, or `asus_vivobook` ingest from this PC.

### One-time setup (PowerShell)

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.eka_agent"
@'
EKA_DEVICE_ID=windows_pc_abcom
EKA_DEVICE_KEY=jt_PASTE_FROM_VPS_REGISTER_ONCE
EKA_VPS_URL=https://agent.jaytipargal.tech
'@ | Set-Content -Path "$env:USERPROFILE\.eka_agent\device.env" -NoNewline
icacls "$env:USERPROFILE\.eka_agent\device.env" /inheritance:r /grant:r "$env:USERNAME`:F"

# Copy eka_agent_push.py to e.g. C:\Users\abcom\eka_agent\
cd C:\Users\abcom\eka_agent
python eka_agent_push.py --device windows_pc_abcom --dry-run
python eka_agent_push.py --device windows_pc_abcom
```

Scheduled task (daily 00:00): see `scripts/setup_windows_tasks.bat`.

**Collectors:** Desktop/Documents/Downloads file changes, Chrome history → `file_change`, `browser_data`.

---

## Register a device key (VPS — run once per device)

On the Jayti VPS (bootstrap key in `/etc/jayti/hub.env`):

```bash
sudo bash /path/to/jayti/scripts/eka_register_device.sh samsung_s24_ultra "Samsung S24 Ultra" phone android
sudo bash /path/to/jayti/scripts/eka_register_device.sh asus_vivobook "Asus VivoBook" laptop linux
sudo bash /path/to/jayti/scripts/eka_register_device.sh windows_pc_abcom "G4G-LAPTOP Lenovo 82KA" desktop windows
```

Copy the printed `device.env` block to the target device immediately (key shown once).

---

## Verify Hub queue (any machine)

```bash
curl -s https://agent.jaytipargal.tech/status | python3 -m json.tool
# expect items_unprocessed > 0 after a fresh push with status=new rows
python3 sandbox/jtagent/hub_status.py
```

---

## Cloud sandbox simulate (no physical device)

From a Cloud Agent VM with `/workspace/.config/jtagent-devices.env` (`jtagent_sandbox`, mode 600):

```bash
cd /workspace/jayti
python3 sandbox/jtagent/simulate_sandbox_ingest.py
```

Pushes 9 redacted rows (3× `whatsapp_chat`, 3× `browser_data`, 3× `system_logs`) for
Colab `segment_jsonl.py` / training queue smoke — **not** a substitute for real device ingest.

---

## CATEGORY_MAP (training segments)

| data_type | training category |
|-----------|-------------------|
| `whatsapp_chat` | `whatsapp_chat` |
| `browser_data` | `browser_data` |
| `system_logs` | `infrastructure` |
| `file_change` | `extracted_text` |

See `scripts/eka_agent_pull.py` and `sandbox/jtagent/segment_jsonl.py` for the full map.
