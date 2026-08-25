# Jayti Agent — Central Ingestion & Continuous Learning System

A multi-device data ingestion, training, and correlation system for building a private AI agent. Devices push delta data to a central PostgreSQL database on a Vultr VPS; the main PC pulls, processes, creates training chunks, runs LoRA fine-tuning, and updates a vector database.

## Architecture Overview

```
Devices (6)          →  Vultr VPS (PostgreSQL + FastAPI)  →  Main PC (Pull + Train)
  S24 Ultra              ingestion_queue                      Pull from VPS
  Windows PC             device_registry                      Create training chunks
  Asus VivoBook          mobility_map                         LoRA fine-tuning
  JP DriveBackup         audit_trail                          ChromaDB vector update
  JP Birthday Server     correlations                         Push adapter back
  Termux S24             training_status
```

**Daily flow:**
1. **00:00** — Each device scans for delta data and pushes to VPS via `POST /ingest`
2. **01:00** — Main PC pulls all new items via `GET /pull`, saves locally
3. **01:15** — Processes items: categorize, dedup (SHA-256), create input/output training chunks
4. **01:45** — Marks items as processed on VPS
5. **02:00** — Runs LoRA fine-tuning on today's chunks (GPT-2 base, CPU or GPU)
6. **02:30** — Updates ChromaDB vector store with new embeddings
7. **03:00** — Reports training status to VPS, logs audit trail

## Project Structure

```
eka-agent-deploy/
├── scripts/
│   ├── eka_agent_server.py        # FastAPI central API server (runs on VPS)
│   ├── eka_agent_push.py          # Device push agent (runs on each device)
│   ├── eka_agent_pull.py          # Main PC pull & process pipeline
│   ├── eka_vector_db.py           # ChromaDB vector database management
│   ├── eka_train.py               # LoRA fine-tuning pipeline
│   ├── setup_vps_db.sh            # PostgreSQL database setup script
│   ├── deploy_push_agent.sh       # Device deployment script
│   └── setup_windows_tasks.bat    # Windows scheduled tasks setup
├── config/
│   ├── eka-agent-nginx-ssl.conf   # Nginx reverse proxy config (HTTPS)
│   └── eka-agent-api.service      # systemd service file for API
├── docs/
│   └── AGENT_DATABASE_ARCHITECTURE.md  # Full architecture documentation
├── .gitignore
└── README.md
```

## Setup Instructions

### 1. VPS Setup (Vultr)

```bash
# On your VPS (Ubuntu 22.04 LTS):
# 1. Install PostgreSQL
sudo apt update && sudo apt install -y postgresql postgresql-contrib

# 2. Create database and schema
#    Edit setup_vps_db.sh to set your DB_PASSWORD and API_KEY, then:
sudo bash setup_vps_db.sh

# 3. Install Python dependencies
sudo apt install -y python3-pip python3-venv
python3 -m venv /root/eka_venv
source /root/eka_venv/bin/activate
pip install fastapi uvicorn psycopg2-binary pydantic

# 4. Copy eka_agent_server.py to VPS and set up systemd service
sudo cp config/eka-agent-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable eka-agent-api
sudo systemctl start eka-agent-api

# 5. Set up Nginx reverse proxy with SSL
sudo apt install -y nginx certbot python3-certbot-nginx
sudo cp config/eka-agent-nginx-ssl.conf /etc/nginx/sites-available/eka-agent
sudo ln -s /etc/nginx/sites-available/eka-agent /etc/nginx/sites-enabled/
sudo certbot --nginx -d agent.urgaa.in
sudo systemctl reload nginx
```

### 2. Device Setup

```bash
# On each device:
# 1. Copy eka_agent_push.py and deploy_push_agent.sh to the device
# 2. Edit the API_KEY in eka_agent_push.py to match your VPS API key
# 3. Run the deployment script:
bash deploy_push_agent.sh <device_name>
#    device_name: samsung_s24_ultra | windows_pc_abcom | asus_vivobook |
#                 jp_drivebackup | jp_birthday_site_server | termux_s24
```

### 3. Main PC Setup (Windows)

```batch
# 1. Install Python 3.12+ and dependencies:
pip install fastapi uvicorn psycopg2-binary pydantic chromadb torch transformers peft datasets

# 2. Edit API_KEY in eka_agent_pull.py to match your VPS API key

# 3. Set up Windows scheduled tasks:
scripts\setup_windows_tasks.bat
#    This creates:
#      EkaAgentPush — daily at 00:00 (collect delta, push to VPS)
#      EkaAgentPull — daily at 01:00 (pull from VPS, create chunks, train)
```

### 4. Vector Database Setup

```bash
# Initialize ChromaDB and index first batch:
python scripts/eka_vector_db.py --setup

# Index all chunks (takes hours for large datasets):
python scripts/eka_vector_db.py --index-all

# Search the vector DB:
python scripts/eka_vector_db.py --search "WhatsApp messages from S24"
```

### 5. Training Pipeline

```bash
# Daily training on today's chunks:
python scripts/eka_train.py --daily

# Train on a specific date:
python scripts/eka_train.py --date 2026-08-20

# List all trained adapters:
python scripts/eka_train.py --list-adapters

# Rollback to a previous adapter:
python scripts/eka_train.py --rollback 2026-08-19
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/health` | Health check |
| `POST` | `/ingest` | Devices push data (requires `X-API-Key` header) |
| `GET`  | `/pull` | Main PC pulls new/processed data |
| `POST` | `/pull/mark` | Mark items as processed |
| `GET`  | `/status` | Check all device health |
| `POST` | `/register` | Register/update a device |
| `POST` | `/audit` | Log an agent action |
| `GET`  | `/audit/query` | Query audit trail with filters |
| `POST` | `/correlation` | Store a cross-device correlation |
| `GET`  | `/training/status` | Get training batch status |
| `POST` | `/training/status` | Update training batch status |

## Database Schema (6 tables)

| Table | Purpose |
|-------|---------|
| `ingestion_queue` | Raw data from devices with SHA-256 dedup |
| `device_registry` | Device info, apps, credentials |
| `mobility_map` | Email → device mapping |
| `audit_trail` | Every agent action logged |
| `correlations` | Cross-device data links |
| `training_status` | Daily training batch records |

## ⚠️ Important Note About Secrets

**All secrets have been removed from this repository.** You must replace the following placeholders with your own values before deploying:

| Placeholder | Location | Replace With |
|-------------|----------|--------------|
| `YOUR_DB_PASSWORD_HERE` | `scripts/eka_agent_server.py`, `scripts/setup_vps_db.sh` | Your PostgreSQL password |
| `YOUR_API_KEY_HERE` | `scripts/eka_agent_server.py`, `scripts/eka_agent_push.py`, `scripts/eka_agent_pull.py`, `scripts/setup_vps_db.sh`, `scripts/deploy_push_agent.sh` | Your API key (generate a random string) |

**Never commit real passwords, API keys, SSH keys, service account keys, or Firebase credentials to this repository.** The `.gitignore` is configured to exclude these files, but always verify with `git diff --check` before committing.

## Tech Stack

- **VPS**: Vultr (Ubuntu 22.04 LTS, 2 vCPU, 4GB RAM, 100GB SSD)
- **Database**: PostgreSQL 16 with JSONB
- **API Server**: Python FastAPI + Uvicorn (port 8443)
- **Reverse Proxy**: Nginx with Let's Encrypt SSL
- **Vector DB**: ChromaDB (persistent, local on PC)
- **Training**: LoRA fine-tuning on GPT-2 (CPU-compatible, GPU optional)
- **Device Agents**: Python with curl for HTTP push
- **Scheduling**: Cron (Linux/Termux) + Windows Task Scheduler

## License

Private project. All rights reserved.
