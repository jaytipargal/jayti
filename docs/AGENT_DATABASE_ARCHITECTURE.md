# EKA Agent — Central Database & Continuous Learning Architecture

## Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        DAILY DATA FLOW                               │
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                          │
│  │ S24 Ultra│  │ Windows  │  │ VivoBook │                          │
│  │ (phone)  │  │ (PC)     │  │ (laptop) │                          │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘                          │
│       │              │              │                                │
│       │    ┌─────────┼──────────────┤                                │
│       │    │         │              │                                │
│       ▼    ▼         ▼              ▼                                │
│  ┌──────────────────────────────────────┐                           │
│  │       CENTRAL CLOUD DATABASE          │                           │
│  │       (Vultr VPS + PostgreSQL)       │                           │
│  │                                       │                           │
│  │  ┌─────────────────────────────┐     │                           │
│  │  │ ingestion_queue             │     │                           │
│  │  │ device_registry             │     │                           │
│  │  │ mobility_map                │     │                           │
│  │  │ auth_registry               │     │                           │
│  │  │ audit_trail                 │     │                           │
│  │  │ correlations                │     │                           │
│  │  │ training_chunks             │     │                           │
│  │  └─────────────────────────────┘     │                           │
│  └──────────────────┬───────────────────┘                           │
│                     │                                                │
│                     │ (pull + process + train)                      │
│                     ▼                                                │
│  ┌──────────────────────────────────────┐                           │
│  │          MAIN PC (Windows)            │                           │
│  │                                       │                           │
│  │  ┌─────────────────────────────┐     │                           │
│  │  │ Pull from central DB         │     │                           │
│  │  │ → Delta detection            │     │                           │
│  │  │ → Chunk creation (10 steps)  │     │                           │
│  │  │ → Dedup (SHA-256)            │     │                           │
│  │  │ → Append to training file    │     │                           │
│  │  │ → LoRA fine-tune             │     │                           │
│  │  │ → Vector DB embedding update │     │                           │
│  │  │ → Push trained adapter back  │     │                           │
│  │  └─────────────────────────────┘     │                           │
│  │                                       │                           │
│  │  D:\training-data\ (5.1 GB+)         │                           │
│  │  D:\training-data\daily_ingestion\   │                           │
│  └──────────────────────────────────────┘                           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Component Architecture

### 1. DEVICES (Data Producers — Push Only)

Each device runs a lightweight collection agent that:
- Scans for new/changed data (delta detection)
- Packages data as JSON
- Pushes to central DB via HTTP POST (curl — terminal-based, Property 3)
- NO database running on the device
- NO processing on the device
- NO training on the device

```
Device Flow:
  1. Cron job triggers at 00:00 (each device local time)
  2. Agent scans for delta (new files, new messages, new logs since last_sync)
  3. Packages each item as JSON: {device, source, type, content, timestamp, hash}
  4. Pushes via: curl -X POST https://<vps-ip>:5432/ingest -d '{"items": [...]}'
  5. Updates last_sync_timestamp locally (simple file: /etc/eka_agent/last_sync)
```

#### Per-Device Collection Details:

```
SAMSUNG S24 ULTRA (via Termux):
├── WhatsApp messages   → msgstore.db query (delta by timestamp)
├── Call logs           → contacts2.db query (delta by date)
├── Call recordings     → /sdcard/Recordings/ (delta by mtime)
├── Contacts            → contacts2.db query (delta by CONTACT_STATUS)
├── SMS messages        → telephony.db query (delta by date)
├── Location history    → Google location API or local GPS log
├── Instagram activity  → /data/data/com.instagram/ (requires root/ADB)
├── Browser history     → Chrome sync or local history DB
├── New app installs    → pm list packages -3 (diff against last scan)
└── Push via:           → curl -X POST https://VPS:8443/ingest -d '{...}'

WINDOWS PC (local):
├── Watched directories → Desktop, Documents, Downloads (delta by mtime)
├── Chrome data         → %LOCALAPPDATA%\Google\Chrome\ (history, passwords)
├── WhatsApp Web        → %APPDATA%\WhatsApp\ (local storage)
├── Gmail               → IMAP fetch or Gmail API (delta by date)
├── Financial docs      → Invoice PDFs, statements (delta by mtime)
├── Git repos           → git log --since="last_sync" (per repo)
├── New installations   → Get-ItemProperty registry scan
├── Log files           → C:\actions-runner\, event logs (delta by mtime)
└── Push via:           → curl -X POST https://VPS:8443/ingest -d '{...}'
                        (or python eka_agent.ingest --push for local)

ASUS VIVOBOOK (via SSH/local):
├── Firebase telemetry  → Firestore read (delta by lastSeen timestamp)
├── Chrome sync         → Chrome profile data
├── System logs         → /var/log/ (delta by mtime)
├── New files           → find /home -newer last_sync_marker
└── Push via:           → curl -X POST https://VPS:8443/ingest -d '{...}'

JP DRIVEBACKUP (via Google Drive API):
├── Synced folder changes → Google Drive API (delta by modifiedTime)
├── Backup artifacts      → New files in backup folder
├── Django fixtures       → .json files (delta by mtime)
└── Push via:             → curl -X POST https://VPS:8443/ingest -d '{...}'
                          (or python script with google-api-python-client)

JP BIRTHDAY SITE SERVER (via SSH):
├── Analytics data    → /var/log/nginx/ or Django analytics app
├── Django admin      → Django DB query (delta by last_modified)
├── Site content      → /var/www/ (delta by mtime)
├── Access logs       → /var/log/nginx/access.log (delta by size)
└── Push via:         → ssh user@server "curl -X POST https://VPS:8443/ingest -d '{...}'"

TERMUX S24 (via Firestore/local):
├── Firestore changes    → Firestore onSnapshot or polling (delta by lastSeen)
├── Daemon logs          → ~/.eka-agent/logs/ (delta by mtime)
├── Command queue        → Firestore commandQueue map changes
└── Push via:            → curl -X POST https://VPS:8443/ingest -d '{...}'
```

---

### 2. CENTRAL CLOUD DATABASE (Vultr VPS + PostgreSQL)

```
┌──────────────────────────────────────────────────────────┐
│                  VULTR VPS                               │
│                                                          │
│  ┌────────────────────────────────────────────────┐     │
│  │  PostgreSQL 16                                  │     │
│  │                                                 │     │
│  │  Schema:                                        │     │
│  │                                                 │     │
│  │  TABLE: ingestion_queue                         │     │
│  │  ┌──────────────────────────────────────────┐  │     │
│  │  │ id            BIGSERIAL PRIMARY KEY       │  │     │
│  │  │ device        VARCHAR(50) NOT NULL        │  │     │
│  │  │ source        VARCHAR(200) NOT NULL       │  │     │
│  │  │ data_type     VARCHAR(50) NOT NULL        │  │     │
│  │  │ content       JSONB NOT NULL              │  │     │
│  │  │ content_hash  VARCHAR(64) NOT NULL        │  │     │
│  │  │ device_time   TIMESTAMPTZ NOT NULL        │  │     │
│  │  │ ingested_at   TIMESTAMPTZ DEFAULT now()   │  │     │
│  │  │ status        VARCHAR(20) DEFAULT 'new'   │  │     │
│  │  │ processed_at  TIMESTAMPTZ                 │  │     │
│  │  │ UNIQUE(content_hash)                      │  │     │
│  │  └──────────────────────────────────────────┘  │     │
│  │  INDEX: idx_status (status)                    │     │
│  │  INDEX: idx_device_time (device, device_time)  │     │
│  │  INDEX: idx_data_type (data_type)              │     │
│  │                                                 │     │
│  │  TABLE: device_registry                        │     │
│  │  ┌──────────────────────────────────────────┐  │     │
│  │  │ device_id      VARCHAR(50) PRIMARY KEY    │  │     │
│  │  │ device_name    VARCHAR(100) NOT NULL      │  │     │
│  │  │ device_type    VARCHAR(30) NOT NULL       │  │     │
│  │  │ os             VARCHAR(30) NOT NULL       │  │     │
│  │  │ location       VARCHAR(100)               │  │     │
│  │  │ agent_version  VARCHAR(50)                │  │     │
│  │  │ last_seen      TIMESTAMPTZ                │  │     │
│  │  │ is_active      BOOLEAN DEFAULT true       │  │     │
│  │  │ apps           JSONB                      │  │     │
│  │  │ credentials    JSONB                      │  │     │
│  │  └──────────────────────────────────────────┘  │     │
│  │                                                 │     │
│  │  TABLE: mobility_map                           │     │
│  │  ┌──────────────────────────────────────────┐  │     │
│  │  │ email          VARCHAR(100) PRIMARY KEY   │  │     │
│  │  │ owner          VARCHAR(100) NOT NULL      │  │     │
│  │  │ email_type     VARCHAR(30) NOT NULL       │  │     │
│  │  │ devices        JSONB NOT NULL              │  │     │
│  │  └──────────────────────────────────────────┘  │     │
│  │                                                 │     │
│  │  TABLE: audit_trail                            │     │
│  │  ┌──────────────────────────────────────────┐  │     │
│  │  │ log_id         BIGSERIAL PRIMARY KEY       │  │     │
│  │  │ timestamp      TIMESTAMPTZ NOT NULL        │  │     │
│  │  │ device         VARCHAR(50)                 │  │     │
│  │  │ action_type    VARCHAR(30) NOT NULL        │  │     │
│  │  │ action_detail  TEXT                        │  │     │
│  │  │ priority       VARCHAR(5)                  │  │     │
│  │  │ status         VARCHAR(15) NOT NULL        │  │     │
│  │  │ data_affected  JSONB                       │  │     │
│  │  │ correlation_ids JSONB                      │  │     │
│  │  │ integrity_hash VARCHAR(64)                 │  │     │
│  │  └──────────────────────────────────────────┘  │     │
│  │  INDEX: idx_audit_time (timestamp)             │     │
│  │  INDEX: idx_audit_device (device, timestamp)   │     │
│  │  INDEX: idx_audit_priority (priority)          │     │
│  │                                                 │     │
│  │  TABLE: correlations                           │     │
│  │  ┌──────────────────────────────────────────┐  │     │
│  │  │ correlation_id VARCHAR(80) PRIMARY KEY    │  │     │
│  │  │ type           VARCHAR(30) NOT NULL       │  │     │
│  │  │ devices        JSONB NOT NULL              │  │     │
│  │  │ evidence       JSONB NOT NULL              │  │     │
│  │  │ relationship   TEXT NOT NULL              │  │     │
│  │  │ confidence     VARCHAR(10) NOT NULL        │  │     │
│  │  │ created_at     TIMESTAMPTZ DEFAULT now()   │  │     │
│  │  └──────────────────────────────────────────┘  │     │
│  │                                                 │     │
│  │  TABLE: training_status                        │     │
│  │  ┌──────────────────────────────────────────┐  │     │
│  │  │ batch_id       VARCHAR(50) PRIMARY KEY    │  │     │
│  │  │ batch_date     DATE NOT NULL              │  │     │
│  │  │ chunks_created INTEGER NOT NULL           │  │     │
│  │  │ duplicates     INTEGER NOT NULL           │  │     │
│  │  │ p0_found       INTEGER NOT NULL           │  │     │
│  │  │ p1_found       INTEGER NOT NULL           │  │     │
│  │  │ lora_adapter   VARCHAR(200)               │  │     │
│  │  │ train_status   VARCHAR(20) NOT NULL       │  │     │
│  │  │ trained_at     TIMESTAMPTZ                │  │     │
│  │  └──────────────────────────────────────────┘  │     │
│  └────────────────────────────────────────────────┘     │
│                                                          │
│  ┌────────────────────────────────────────────────┐     │
│  │  Python FastAPI Server (port 8443)              │     │
│  │                                                 │     │
│  │  Endpoints:                                     │     │
│  │  POST /ingest       — devices push data         │     │
│  │  GET  /pull         — main PC pulls new data    │     │
│  │  GET  /status       — check device health       │     │
│  │  POST /register     — register/update device    │     │
│  │  POST /audit        — log agent action          │     │
│  │  GET  /audit/query  — query audit trail         │     │
│  │  POST /correlation  — store correlation         │     │
│  │  GET  /training/status — check training batch   │     │
│  │  POST /training/status — update training batch  │     │
│  │                                                 │     │
│  │  Auth: API key (single key, shared by all       │     │
│  │  devices — private agent, no per-user auth)     │     │
│  └────────────────────────────────────────────────┘     │
│                                                          │
│  ┌────────────────────────────────────────────────┐     │
│  │  Nginx (reverse proxy, HTTPS/TLS)              │     │
│  │  Port 443 → 8443 (FastAPI)                     │     │
│  │  SSL cert: Let's Encrypt (auto-renew)           │     │
│  └────────────────────────────────────────────────┘     │
│                                                          │
│  Storage: 100 GB SSD (enough for months of ingestion)   │
│  RAM: 4 GB (PostgreSQL + FastAPI)                        │
│  CPU: 2 vCPU                                             │
│  OS: Ubuntu 22.04 LTS                                    │
│  Cost: ~$24/month                                        │
└──────────────────────────────────────────────────────────┘
```

---

### 3. MAIN PC (Processing & Training Hub)

```
┌──────────────────────────────────────────────────────────┐
│                  WINDOWS PC (abcom)                      │
│                                                          │
│  ┌────────────────────────────────────────────────┐     │
│  │  EKA Agent Training Daemon (Python)             │     │
│  │                                                 │     │
│  │  Runs daily at 01:00 (after devices push)       │     │
│  │                                                 │     │
│  │  Step 1: PULL                                   │     │
│  │    → curl https://VPS:443/pull?status=new       │     │
│  │    → Gets all new ingestion_queue items         │     │
│  │    → Saves to D:\training-data\daily_ingestion\ │     │
│  │                                                 │     │
│  │  Step 2: PROCESS (10-step chunk creation)       │     │
│  │    → For each ingested item:                    │     │
│  │      1. Extract raw content                     │     │
│  │      2. Split into chunks                       │     │
│  │      3. Auto-categorize                         │     │
│  │      4. Extract hidden data (regex)             │     │
│  │      5. Classify priority (P0-P3)               │     │
│  │      6. Attach custody chain                    │     │
│  │      7. Generate input/output pair              │     │
│  │      8. Log to audit_trail                      │     │
│  │      9. SHA-256 dedup check                     │     │
│  │     10. Append to training file                 │     │
│  │                                                 │     │
│  │  Step 3: MARK PROCESSED                         │     │
│  │    → POST https://VPS:443/pull/mark-processed   │     │
│  │    → Updates ingestion_queue.status = 'processed│     │
│  │                                                 │     │
│  │  Step 4: TRAIN (LoRA fine-tune)                 │     │
│  │    → Load today's new chunks                    │     │
│  │    → Run LoRA fine-tuning (QLoRA if GPU)        │     │
│  │    → Save adapter: D:\training-data\adapters\   │     │
│  │    → adapter_YYYY-MM-DD.bin (50-200 MB)         │     │
│  │                                                 │     │
│  │  Step 5: EMBED (Vector DB update)               │     │
│  │    → Generate embeddings for new chunks         │     │
│  │    → Add to ChromaDB vector store               │     │
│  │    → D:\training-data\vector_db\                 │     │
│  │                                                 │     │
│  │  Step 6: REPORT                                 │     │
│  │    → Push training_status to central DB         │     │
│  │    → Print daily summary to terminal            │     │
│  │    → Log to audit_trail                         │     │
│  └────────────────────────────────────────────────┘     │
│                                                          │
│  Storage:                                                │
│  D:\training-data\                                       │
│    ├── agent_training_chunks_with_learning.jsonl (5.1GB)│
│    ├── daily_ingestion\YYYY-MM-DD\<device>\*.jsonl      │
│    ├── adapters\adapter_YYYY-MM-DD.bin                   │
│    ├── vector_db\chroma\                                  │
│    └── rollback\YYYY-MM-DD\ (30-day retention)          │
└──────────────────────────────────────────────────────────┘
```

---

## Daily Timeline

```
00:00  ┌── DEVICES (parallel) ──────────────────────────┐
       │ S24 Ultra:    Scan WhatsApp, calls, contacts    │
       │ Windows PC:   Scan files, Chrome, Gmail         │
       │ VivoBook:     Scan Firebase, Chrome, logs       │
       │ JP Backup:    Scan Google Drive changes         │
       │ JP Server:    Scan analytics, Django, logs      │
       │ Termux S24:   Scan Firestore, daemon logs       │
       └──────────────────────────────────────────────────┘
00:30  ┌── PUSH ────────────────────────────────────────┐
       │ All devices push JSON to VPS:443/ingest          │
       │ VPS stores in ingestion_queue (PostgreSQL)       │
       │ Duplicate content_hash items auto-rejected       │
       └──────────────────────────────────────────────────┘
01:00  ┌── MAIN PC PULLS ───────────────────────────────┐
       │ PC fetches all status='new' items from VPS       │
       │ Saves to D:\training-data\daily_ingestion\       │
       └──────────────────────────────────────────────────┘
01:15  ┌── CHUNK CREATION (10-step pipeline) ───────────┐
       │ Extract → Chunk → Categorize → Hidden data      │
       │ → Priority → Custody → Input/Output             │
       │ → Audit → Dedup → Append                        │
       └──────────────────────────────────────────────────┘
01:45  ┌── MARK PROCESSED ──────────────────────────────┐
       │ PC updates VPS: ingestion_queue.status=processed│
       └──────────────────────────────────────────────────┘
02:00  ┌── LoRA FINE-TUNING ────────────────────────────┐
       │ Train on today's new chunks only                 │
       │ Save adapter (50-200 MB)                         │
       └──────────────────────────────────────────────────┘
02:30  ┌── VECTOR DB UPDATE ────────────────────────────┐
       │ Embed new chunks → add to ChromaDB               │
       └──────────────────────────────────────────────────┘
03:00  ┌── REPORT ──────────────────────────────────────┐
       │ Push training_status to VPS                      │
       │ Print terminal summary                           │
       │ Log to audit_trail                               │
       └──────────────────────────────────────────────────┘
```

---

## Database Schema Summary

| Table | Purpose | Growth | Key columns |
|---|---|---|---|
| `ingestion_queue` | Raw data from devices | ~1,000-5,000 rows/day | device, data_type, content (JSONB), content_hash (UNIQUE) |
| `device_registry` | Device info + credentials | Static (6 rows) | device_id, apps (JSONB), credentials (JSONB) |
| `mobility_map` | Email → device mapping | Static (24 rows) | email, devices (JSONB) |
| `audit_trail` | Every agent action | ~100-1,000 rows/day | timestamp, device, action_type, priority |
| `correlations` | Cross-device links | Grows with use | type, devices (JSONB), evidence (JSONB) |
| `training_status` | Daily training batches | 1 row/day | batch_date, chunks_created, lora_adapter |

**Estimated DB growth:** ~50-200 MB/month (structured data only; raw content is JSONB but compressed by PostgreSQL)

**Training data (5.1 GB) stays on D: drive** — NOT in the database. The database only holds the ingestion queue and structured metadata.

---

## Why Vultr VPS + PostgreSQL?

| Requirement | How it's met |
|---|---|
| Single central collection point | VPS is one server, one IP, one DB |
| All devices push, no local DB | Devices use curl POST — no DB driver needed |
| Main PC pulls and trains | PC fetches via GET /pull endpoint |
| Daily continuous ingestion | Cron on devices + cron on PC |
| No per-device maintenance | Devices only run a curl script; all logic on VPS + PC |
| Scales with new devices | Just add device to device_registry, push starts |
| Full control, no vendor rules | Your server, your PostgreSQL, your rules |
| Paid is OK | ~$24/month for VPS (2 vCPU, 4GB RAM, 100GB SSD) |
| Already have Vultr | Vultr CLI configured on your PC |
| PostgreSQL is robust | ACID, JSONB for flexible forensic data, FTS for search |

---

## What's NOT in the Database

| Data | Where it lives | Why |
|---|---|---|
| Training JSONL (5.1 GB) | D: drive on main PC | Too large for DB, file-based is faster |
| LoRA adapters | D:\training-data\adapters\ | Binary files, not DB data |
| Vector DB (ChromaDB) | D:\training-data\vector_db\ | Local on PC for fast search |
| Call recording audio | D:\training-data\daily_ingestion\ | Binary files |
| Daily batch backups | D:\training-data\rollback\ | File-based for rollback |

The database is **only the coordination layer** — it routes data from devices to PC and stores structured metadata. All heavy data stays on the main PC's D: drive.