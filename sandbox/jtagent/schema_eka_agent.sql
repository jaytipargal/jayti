-- Jayti / EKA Agent schema (6 tables). Same shape as scripts/setup_vps_db.sh.
-- Use on VPS eka_agent OR a dedicated Neon DB — NEVER the Global Devices gd-catalog.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS ingestion_queue (
    id            BIGSERIAL PRIMARY KEY,
    device        VARCHAR(50) NOT NULL,
    source        VARCHAR(200) NOT NULL,
    data_type     VARCHAR(50) NOT NULL,
    content       JSONB NOT NULL,
    content_hash  VARCHAR(64) NOT NULL UNIQUE,
    device_time   TIMESTAMPTZ NOT NULL,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    status        VARCHAR(20) NOT NULL DEFAULT 'new',
    processed_at  TIMESTAMPTZ,
    priority      VARCHAR(5) DEFAULT NULL,
    hidden_data   JSONB DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_ingest_status ON ingestion_queue(status);
CREATE INDEX IF NOT EXISTS idx_ingest_device_time ON ingestion_queue(device, device_time);
CREATE INDEX IF NOT EXISTS idx_ingest_data_type ON ingestion_queue(data_type);
CREATE INDEX IF NOT EXISTS idx_ingest_priority ON ingestion_queue(priority);

CREATE TABLE IF NOT EXISTS device_registry (
    device_id      VARCHAR(50) PRIMARY KEY,
    device_name    VARCHAR(100) NOT NULL,
    device_type    VARCHAR(30) NOT NULL,
    os             VARCHAR(30) NOT NULL,
    location       VARCHAR(100),
    agent_version  VARCHAR(50),
    last_seen      TIMESTAMPTZ,
    is_active      BOOLEAN NOT NULL DEFAULT true,
    apps           JSONB NOT NULL DEFAULT '[]'::jsonb,
    credentials    JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mobility_map (
    email          VARCHAR(100) PRIMARY KEY,
    owner          VARCHAR(100) NOT NULL,
    email_type     VARCHAR(30) NOT NULL,
    devices        JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_trail (
    log_id         BIGSERIAL PRIMARY KEY,
    timestamp      TIMESTAMPTZ NOT NULL DEFAULT now(),
    agent_session  VARCHAR(100),
    device         VARCHAR(50),
    action_type    VARCHAR(30) NOT NULL,
    action_detail  TEXT,
    input_ref      TEXT,
    output_ref     TEXT,
    data_affected  JSONB,
    priority       VARCHAR(5),
    correlation_ids JSONB,
    integrity_hash VARCHAR(64),
    duration_ms    INTEGER,
    status         VARCHAR(15) NOT NULL,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_trail(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_device ON audit_trail(device, timestamp);

CREATE TABLE IF NOT EXISTS correlations (
    correlation_id  VARCHAR(80) PRIMARY KEY,
    type             VARCHAR(30) NOT NULL,
    devices          JSONB NOT NULL,
    evidence         JSONB NOT NULL,
    relationship     TEXT NOT NULL,
    confidence       VARCHAR(10) NOT NULL,
    timestamp_correlation TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS training_status (
    batch_id        VARCHAR(50) PRIMARY KEY,
    batch_date      DATE NOT NULL,
    chunks_created  INTEGER NOT NULL DEFAULT 0,
    duplicates      INTEGER NOT NULL DEFAULT 0,
    p0_found        INTEGER NOT NULL DEFAULT 0,
    p1_found        INTEGER NOT NULL DEFAULT 0,
    lora_adapter    VARCHAR(200),
    train_status    VARCHAR(20) NOT NULL DEFAULT 'pending',
    trained_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_train_date ON training_status(batch_date);
CREATE INDEX IF NOT EXISTS idx_train_status ON training_status(train_status);

-- Sandbox devices (no secrets in credentials)
INSERT INTO device_registry (device_id, device_name, device_type, os, location, apps, credentials) VALUES
('samsung_s24_ultra', 'Samsung S24 Ultra', 'phone', 'android', 'India',
 '[{"app":"termux","auth":"termux_session"}]'::jsonb, '[]'::jsonb),
('windows_pc_abcom', 'Windows PC (abcom) G4G-LAPTOP Lenovo 82KA NOT_ASUS', 'desktop', 'windows', 'India',
 '[{"app":"python","auth":"local_execution"}]'::jsonb, '[]'::jsonb),
('asus_vivobook', 'Asus VivoBook', 'laptop', 'linux', 'India',
 '[{"app":"sustained_telemetry","auth":"daemon_auth"}]'::jsonb, '[]'::jsonb)
ON CONFLICT (device_id) DO UPDATE SET is_active = true, updated_at = now();
