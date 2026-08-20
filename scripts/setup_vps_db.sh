#!/bin/bash
# EKA Agent Database Setup Script
# Run on VPS as postgres user

set -e

DB_NAME="eka_agent"
DB_USER="eka_agent"
DB_PASS="YOUR_DB_PASSWORD_HERE"
API_KEY="YOUR_API_KEY_HERE"

echo "=== Creating database and user ==="
sudo -u postgres psql <<SQLEOF
CREATE DATABASE ${DB_NAME};
CREATE USER ${DB_USER} WITH ENCRYPTED PASSWORD '${DB_PASS}';
GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
ALTER USER ${DB_USER} SET client_encoding TO 'utf8';
ALTER USER ${DB_USER} SET default_transaction_isolation TO 'read committed';
ALTER USER ${DB_USER} SET timezone TO 'UTC';
SQLEOF

echo "=== Creating schema (6 tables) ==="
sudo -u postgres psql -d ${DB_NAME} <<SQLEOF
-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. INGESTION QUEUE — raw data pushed by devices
CREATE TABLE ingestion_queue (
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
CREATE INDEX idx_ingest_status ON ingestion_queue(status);
CREATE INDEX idx_ingest_device_time ON ingestion_queue(device, device_time);
CREATE INDEX idx_ingest_data_type ON ingestion_queue(data_type);
CREATE INDEX idx_ingest_priority ON ingestion_queue(priority);

-- 2. DEVICE REGISTRY — device info + credentials
CREATE TABLE device_registry (
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

-- 3. MOBILITY MAP — email to device mapping
CREATE TABLE mobility_map (
    email          VARCHAR(100) PRIMARY KEY,
    owner          VARCHAR(100) NOT NULL,
    email_type     VARCHAR(30) NOT NULL,
    devices        JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. AUDIT TRAIL — every agent action
CREATE TABLE audit_trail (
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
CREATE INDEX idx_audit_time ON audit_trail(timestamp);
CREATE INDEX idx_audit_device ON audit_trail(device, timestamp);
CREATE INDEX idx_audit_priority ON audit_trail(priority);
CREATE INDEX idx_audit_action ON audit_trail(action_type);
CREATE INDEX idx_audit_status ON audit_trail(status);

-- 5. CORRELATIONS — cross-device data links
CREATE TABLE correlations (
    correlation_id  VARCHAR(80) PRIMARY KEY,
    type             VARCHAR(30) NOT NULL,
    devices          JSONB NOT NULL,
    evidence         JSONB NOT NULL,
    relationship     TEXT NOT NULL,
    confidence       VARCHAR(10) NOT NULL,
    timestamp_correlation TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_corr_type ON correlations(type);
CREATE INDEX idx_corr_devices ON correlations USING gin(devices);

-- 6. TRAINING STATUS — daily training batch records
CREATE TABLE training_status (
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
CREATE INDEX idx_train_date ON training_status(batch_date);
CREATE INDEX idx_train_status ON training_status(train_status);

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO ${DB_USER};
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO ${DB_USER};

SQLEOF

echo "=== Seeding device registry ==="
sudo -u postgres psql -d ${DB_NAME} <<SQLEOF
INSERT INTO device_registry (device_id, device_name, device_type, os, location, apps, credentials) VALUES
('samsung_s24_ultra', 'Samsung S24 Ultra', 'phone', 'android', 'India', '[
    {"app":"whatsapp","auth":"whatsapp_hash_key"},
    {"app":"chrome_browser","auth":"chrome_machine_id_sync"},
    {"app":"gmail","auth":"google_account_oauth"},
    {"app":"firebase","auth":"firebase_service_account"},
    {"app":"termux","auth":"termux_session"},
    {"app":"adb","auth":"adb_debug_bridge"},
    {"app":"instagram","auth":"instagram_session"},
    {"app":"samsung_account","auth":"samsung_account_token"}
]', '[]'),
('windows_pc_abcom', 'Windows PC (abcom)', 'desktop', 'windows', 'India', '[
    {"app":"chrome_browser","auth":"chrome_machine_id_sync"},
    {"app":"whatsapp","auth":"whatsapp_web_desktop"},
    {"app":"gmail","auth":"google_account_oauth"},
    {"app":"firebase","auth":"firebase_service_account"},
    {"app":"api_keys","auth":"plaintext_api_keys"},
    {"app":"ssh","auth":"ssh_key_pair"},
    {"app":"docker","auth":"docker_daemon"},
    {"app":"python","auth":"local_execution"},
    {"app":"git","auth":"git_credentials"}
]', '[]'),
('asus_vivobook', 'Asus VivoBook', 'laptop', 'linux', 'India', '[
    {"app":"firebase","auth":"firebase_service_account"},
    {"app":"sustained_telemetry","auth":"daemon_auth"},
    {"app":"chrome_browser","auth":"chrome_sync"}
]', '[]'),
('jp_drivebackup', 'JP DriveBackup', 'server', 'linux', 'India', '[
    {"app":"google_drive_sync","auth":"google_oauth"},
    {"app":"django","auth":"django_user_fixture"}
]', '[]'),
('jp_birthday_site_server', 'JP Birthday Site Server', 'server', 'linux', 'India', '[
    {"app":"ssh","auth":"ssh_key"},
    {"app":"analytics","auth":"analytics_api"},
    {"app":"django_site","auth":"django_admin"}
]', '[]'),
('termux_s24', 'Termux S24', 'phone_agent', 'android', 'India', '[
    {"app":"firestore_daemon","auth":"firestore_sdk_service_account"},
    {"app":"shell","auth":"termux_shell"}
]', '[]');

-- Seed mobility map (24 emails)
INSERT INTO mobility_map (email, owner, email_type, devices) VALUES
('jaytipargal.jp@gmail.com', 'Jayti Pargal', 'personal', '["samsung-s24-ultra","windows-pc-abcom","jp-drivebackup"]'),
('pargaljayati.06@gmail.com', 'Jayti Pargal', 'personal', '["samsung-s24-ultra","windows-pc-abcom"]'),
('santoshpargal@yahoo.com', 'Santosh Pargal', 'family', '["windows-pc-abcom"]'),
('jayti@jaytipargal.in', 'Jayti Pargal', 'business', '["jp-birthday-site-server"]'),
('ja**4@outlook.com', 'Jayti Pargal', 'personal', '["windows-pc-abcom"]'),
('vivek@go4garage.com', 'Vivek Gupta', 'business', '["windows-pc-abcom","asus-vivobook","samsung-s24-ultra"]'),
('vivek.gupta@go4garage.com', 'Vivek Gupta', 'business', '["windows-pc-abcom","asus-vivobook"]'),
('go4garageofficial@gmail.com', 'Go4Garage', 'business', '["windows-pc-abcom","asus-vivobook"]'),
('operation@go4garage.com', 'Go4Garage', 'business', '["windows-pc-abcom"]'),
('accounts@go4garage.com', 'Go4Garage', 'business', '["windows-pc-abcom"]'),
('mahantesh@go4garage.com', 'Mahantesh', 'employee', '["windows-pc-abcom"]'),
('kapil.rajput1@go4garage.com', 'Kapil Rajput', 'employee', '["windows-pc-abcom"]'),
('praveen.yadav@go4garage.com', 'Praveen Yadav', 'employee', '["windows-pc-abcom"]'),
('aditya.shukla@go4garage.com', 'Aditya Shukla', 'employee', '["windows-pc-abcom"]'),
('ragavan@go4garage.com', 'Ragavan', 'employee', '["windows-pc-abcom"]'),
('dheeraj.giri@go4garage.com', 'Dheeraj Giri', 'employee', '["windows-pc-abcom"]'),
('sanjeev.maurya@go4garage.com', 'Sanjeev Maurya', 'employee', '["windows-pc-abcom"]'),
('ankit.yadav@go4garage.com', 'Ankit Yadav', 'employee', '["windows-pc-abcom"]'),
('niwas@go4garage.com', 'Niwas', 'employee', '["windows-pc-abcom"]'),
('devesh.rawat@go4garage.com', 'Devesh Rawat', 'employee', '["windows-pc-abcom"]'),
('hitesh.pathak@go4garage.com', 'Hitesh Pathak', 'employee', '["windows-pc-abcom"]'),
('sandeep.yadav@go4garage.com', 'Sandeep Yadav', 'employee', '["windows-pc-abcom"]'),
('rohit.jangra@go4garage.com', 'Rohit Jangra', 'employee', '["windows-pc-abcom"]'),
('vivek@eka-agent.com', 'Vivek Gupta', 'business', '["samsung-s24-ultra","asus-vivobook","windows-pc-abcom"]');

SQLEOF

echo "=== Verifying tables ==="
sudo -u postgres psql -d ${DB_NAME} -c "\dt"
echo "=== Device count ==="
sudo -u postgres psql -d ${DB_NAME} -c "SELECT count(*) FROM device_registry;"
echo "=== Email count ==="
sudo -u postgres psql -d ${DB_NAME} -c "SELECT count(*) FROM mobility_map;"

echo "=== Saving config ==="
cat > /root/eka_agent_db_config.txt <<EOF
Database: ${DB_NAME}
User: ${DB_USER}
Password: ${DB_PASS}
API_KEY: ${API_KEY}
Host: YOUR_VPS_IP_HERE
Port: 5432
Tables: ingestion_queue, device_registry, mobility_map, audit_trail, correlations, training_status
EOF

echo "=== Setup complete ==="
cat /root/eka_agent_db_config.txt