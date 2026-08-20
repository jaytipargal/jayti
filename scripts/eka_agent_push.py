#!/usr/bin/env python3
"""
EKA Agent — Device Push Script
Runs on each device to collect delta data and push to central VPS.

Usage on each device:
  python eka_agent_push.py --device samsung_s24_ultra
  python eka_agent_push.py --device windows_pc_abcom
  python eka_agent_push.py --device asus_vivobook
  python eka_agent_push.py --device jp_drivebackup
  python eka_agent_push.py --device jp_birthday_site_server
  python eka_agent_push.py --device termux_s24

Set up as cron job (runs daily at 00:00):
  0 0 * * * python /path/to/eka_agent_push.py --device <device_name> >> /var/log/eka_agent_push.log 2>&1
"""
import os
import sys
import json
import hashlib
import platform
import subprocess
import argparse
from datetime import datetime, timezone
from pathlib import Path

# ─── Config ───
VPS_URL = "https://agent.urgaa.in"
API_KEY = "YOUR_API_KEY_HERE"
LAST_SYNC_FILE = "/etc/eka_agent/last_sync"  # or ~/.eka_agent/last_sync on Windows
STATE_DIR = os.path.expanduser("~/.eka_agent")

def get_last_sync(device):
    """Read last sync timestamp for this device."""
    sync_file = os.path.join(STATE_DIR, f"last_sync_{device}")
    try:
        with open(sync_file, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "1970-01-01T00:00:00Z"

def save_last_sync(device, timestamp):
    """Save last sync timestamp."""
    os.makedirs(STATE_DIR, exist_ok=True)
    sync_file = os.path.join(STATE_DIR, f"last_sync_{device}")
    with open(sync_file, "w") as f:
        f.write(timestamp)

def compute_hash(content):
    """Compute SHA-256 hash of content."""
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()

def push_items(items, device):
    """Push items to VPS ingestion endpoint."""
    if not items:
        print(f"  [{device}] No new data to push.")
        return {"inserted": 0, "duplicates": 0, "errors": 0}

    payload = {"items": items}
    payload_file = os.path.join(STATE_DIR, f"push_payload_{device}.json")
    os.makedirs(STATE_DIR, exist_ok=True)

    with open(payload_file, "w") as f:
        json.dump(payload, f, default=str)

    cmd = [
        "curl", "-s", "-X", "POST",
        f"{VPS_URL}/ingest",
        "-H", "Content-Type: application/json",
        "-H", f"X-API-Key: {API_KEY}",
        "-d", f"@{payload_file}"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    try:
        response = json.loads(result.stdout)
        print(f"  [{device}] Push result: inserted={response.get('inserted',0)}, "
              f"duplicates={response.get('duplicates',0)}, "
              f"errors={response.get('errors',0)}")
        return response
    except json.JSONDecodeError:
        print(f"  [{device}] Push ERROR: {result.stdout[:200]}")
        return {"inserted": 0, "duplicates": 0, "errors": 1}

    # Clean up payload file
    os.remove(payload_file)

# ─── Device Collectors ───

def collect_samsung_s24_ultra(last_sync):
    """Collect new data from Samsung S24 Ultra via Termux."""
    items = []

    # WhatsApp messages — query msgstore.db
    try:
        whatsapp_db = os.path.expanduser("~/storage/shared/WhatsApp/Databases/msgstore.db")
        if os.path.exists(whatsapp_db):
            # Use sqlite3 to query new messages since last_sync
            cmd = ["sqlite3", whatsapp_db,
                   f"SELECT json_object('table_name','messages','data',json_object("
                   "'_id',_id,'from_me',from_me,'key_id',key_id,'text',text,"
                   "'timestamp',timestamp,'recipient_id',recipient_id,"
                   "'sender_jid',sender_jid)) "
                   f"FROM messages WHERE timestamp/1000 > strftime('%s','{last_sync}');"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            for line in result.stdout.strip().split("\n"):
                if line:
                    try:
                        data = json.loads(line)["data"]
                        content = {"type": "whatsapp_message", **data}
                        items.append({
                            "device": "samsung_s24_ultra",
                            "source": "whatsapp_msgstore",
                            "data_type": "whatsapp_chat",
                            "content": content,
                            "device_time": datetime.fromtimestamp(
                                data.get("timestamp", 0)/1000, tz=timezone.utc
                            ).isoformat() if data.get("timestamp") else datetime.now(timezone.utc).isoformat(),
                            "content_hash": compute_hash(content),
                        })
                    except (json.JSONDecodeError, KeyError):
                        pass
    except Exception as e:
        print(f"  [whatsapp] Error: {e}")

    # Call logs — query contacts2.db
    try:
        contacts_db = os.path.expanduser("~/storage/shared/com.android.contacts/databases/contacts2.db")
        if os.path.exists(contacts_db):
            cmd = ["sqlite3", contacts_db,
                   f"SELECT json_object('number',number,'date',date,'duration',duration,"
                   "'type',type,'name',name) FROM calls WHERE date/1000 > strftime('%s','{last_sync}');"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            for line in result.stdout.strip().split("\n"):
                if line:
                    try:
                        data = json.loads(line)
                        content = {"type": "call_log", **data}
                        items.append({
                            "device": "samsung_s24_ultra",
                            "source": "contacts2_db",
                            "data_type": "call_recordings",
                            "content": content,
                            "device_time": datetime.fromtimestamp(
                                data.get("date", 0)/1000, tz=timezone.utc
                            ).isoformat() if data.get("date") else datetime.now(timezone.utc).isoformat(),
                            "content_hash": compute_hash(content),
                        })
                    except json.JSONDecodeError:
                        pass
    except Exception as e:
        print(f"  [call_logs] Error: {e}")

    # New call recordings — file scan
    try:
        recordings_dir = os.path.expanduser("~/storage/shared/Recordings/CallRecordings")
        if os.path.exists(recordings_dir):
            last_ts = datetime.fromisoformat(last_sync.replace("Z", "+00:00")).timestamp()
            for f in Path(recordings_dir).iterdir():
                if f.stat().st_mtime > last_ts:
                    content = {"type": "call_recording", "filename": f.name,
                                "size_bytes": f.stat().st_size,
                                "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()}
                    items.append({
                        "device": "samsung_s24_ultra",
                        "source": "call_recordings_dir",
                        "data_type": "call_recordings",
                        "content": content,
                        "device_time": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
                        "content_hash": compute_hash(content),
                    })
    except Exception as e:
        print(f"  [recordings] Error: {e}")

    # SMS messages
    try:
        sms_db = "/data/data/com.android.providers.telephony/databases/mmssms.db"
        cmd = ["su", "-c", f"sqlite3 {sms_db} "
                f"SELECT json_object('address',address,'body',body,'date',date,'type',type) "
                f"FROM sms WHERE date/1000 > strftime('%s','{last_sync}');"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        for line in result.stdout.strip().split("\n"):
            if line:
                try:
                    data = json.loads(line)
                    content = {"type": "sms", **data}
                    items.append({
                        "device": "samsung_s24_ultra",
                        "source": "sms_db",
                        "data_type": "sms_messages",
                        "content": content,
                        "device_time": datetime.fromtimestamp(
                            data.get("date", 0)/1000, tz=timezone.utc
                        ).isoformat() if data.get("date") else datetime.now(timezone.utc).isoformat(),
                        "content_hash": compute_hash(content),
                    })
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        print(f"  [sms] Error: {e}")

    # New installed apps
    try:
        result = subprocess.run(["pm", "list", "packages", "-3"], capture_output=True, text=True, timeout=15)
        current_apps = set(line.replace("package:", "").strip() for line in result.stdout.split("\n") if line.strip())
        apps_file = os.path.join(STATE_DIR, "known_apps_samsung_s24_ultra")
        try:
            with open(apps_file) as f:
                known_apps = set(f.read().strip().split("\n"))
        except FileNotFoundError:
            known_apps = set()

        new_apps = current_apps - known_apps
        for app in new_apps:
            content = {"type": "new_app_install", "package": app}
            items.append({
                "device": "samsung_s24_ultra",
                "source": "pm_list",
                "data_type": "app_install",
                "content": content,
                "device_time": datetime.now(timezone.utc).isoformat(),
                "content_hash": compute_hash(content),
                "priority": "P1",
            })

        with open(apps_file, "w") as f:
            f.write("\n".join(current_apps))
    except Exception as e:
        print(f"  [apps] Error: {e}")

    return items


def collect_windows_pc_abcom(last_sync):
    """Collect new data from Windows PC."""
    items = []
    last_ts = datetime.fromisoformat(last_sync.replace("Z", "+00:00")).timestamp()

    # Watched directories
    watch_dirs = [
        os.path.expanduser("~/Desktop"),
        os.path.expanduser("~/Documents"),
        os.path.expanduser("~/Downloads"),
    ]

    for watch_dir in watch_dirs:
        if not os.path.exists(watch_dir):
            continue
        for root, dirs, files in os.walk(watch_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    mtime = os.path.getmtime(fpath)
                    if mtime > last_ts:
                        fsize = os.path.getsize(fpath)
                        content = {
                            "type": "file_change",
                            "path": fpath,
                            "filename": fname,
                            "size_bytes": fsize,
                            "modified": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                        }
                        items.append({
                            "device": "windows_pc_abcom",
                            "source": "file_scan",
                            "data_type": "file_change",
                            "content": content,
                            "device_time": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                            "content_hash": compute_hash(content),
                        })
                except OSError:
                    pass

    # Chrome history
    try:
        chrome_history = os.path.expanduser(
            "~/AppData/Local/Google/Chrome/User Data/Default/History"
        )
        if os.path.exists(chrome_history):
            import sqlite3 as sq
            conn = sq.connect(chrome_history)
            cur = conn.cursor()
            cur.execute(
                f"SELECT url, title, visit_count, last_visit_time FROM urls "
                f"WHERE last_visit_time > (strftime('%s','{last_sync}') * 1000000 - 11644473600000000)"
            )
            for row in cur.fetchall():
                content = {"type": "chrome_history", "url": row[0], "title": row[1],
                           "visit_count": row[2]}
                items.append({
                    "device": "windows_pc_abcom",
                    "source": "chrome_history",
                    "data_type": "browser_data",
                    "content": content,
                    "device_time": datetime.now(timezone.utc).isoformat(),
                    "content_hash": compute_hash(content),
                })
            conn.close()
    except Exception as e:
        print(f"  [chrome] Error: {e}")

    return items


def collect_asus_vivobook(last_sync):
    """Collect new data from Asus VivoBook (run locally on VivoBook)."""
    items = []
    last_ts = datetime.fromisoformat(last_sync.replace("Z", "+00:00")).timestamp()

    # Scan home directory for new files
    for root, dirs, files in os.walk(os.path.expanduser("~")):
        # Skip hidden dirs and common large dirs
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("cache", ".cache", "snap")]
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                mtime = os.path.getmtime(fpath)
                if mtime > last_ts and os.path.getsize(fpath) < 10_000_000:  # skip files > 10MB
                    content = {
                        "type": "file_change",
                        "path": fpath,
                        "filename": fname,
                        "size_bytes": os.path.getsize(fpath),
                        "modified": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                    }
                    items.append({
                        "device": "asus_vivobook",
                        "source": "file_scan",
                        "data_type": "file_change",
                        "content": content,
                        "device_time": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                        "content_hash": compute_hash(content),
                    })
            except OSError:
                pass

    # System logs
    log_dirs = ["/var/log"]
    for log_dir in log_dirs:
        if os.path.exists(log_dir):
            for fname in os.listdir(log_dir):
                fpath = os.path.join(log_dir, fname)
                if os.path.isfile(fpath):
                    try:
                        mtime = os.path.getmtime(fpath)
                        if mtime > last_ts:
                            content = {"type": "log_change", "path": fpath,
                                       "filename": fname,
                                       "modified": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()}
                            items.append({
                                "device": "asus_vivobook",
                                "source": "log_scan",
                                "data_type": "system_logs",
                                "content": content,
                                "device_time": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                                "content_hash": compute_hash(content),
                            })
                    except OSError:
                        pass

    return items


def collect_jp_drivebackup(last_sync):
    """Collect new data from JP DriveBackup (Google Drive sync)."""
    items = []
    last_ts = datetime.fromisoformat(last_sync.replace("Z", "+00:00")).timestamp()

    # Scan backup directories
    backup_dirs = ["/backup", "/mnt/google-drive", os.path.expanduser("~/GoogleDrive")]
    for bdir in backup_dirs:
        if not os.path.exists(bdir):
            continue
        for root, dirs, files in os.walk(bdir):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    mtime = os.path.getmtime(fpath)
                    if mtime > last_ts:
                        content = {
                            "type": "drive_backup_file",
                            "path": fpath,
                            "filename": fname,
                            "size_bytes": os.path.getsize(fpath),
                            "modified": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                        }
                        items.append({
                            "device": "jp_drivebackup",
                            "source": "drive_sync_scan",
                            "data_type": "drive_backup",
                            "content": content,
                            "device_time": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                            "content_hash": compute_hash(content),
                        })
                except OSError:
                    pass

    return items


def collect_jp_birthday_site_server(last_sync):
    """Collect new data from JP Birthday Site Server (via SSH or local)."""
    items = []
    last_ts = datetime.fromisoformat(last_sync.replace("Z", "+00:00")).timestamp()

    # Django analytics
    try:
        django_db = "/var/www/jaytibirthday/db.sqlite3"
        if os.path.exists(django_db):
            import sqlite3 as sq
            conn = sq.connect(django_db)
            cur = conn.cursor()
            # Try common Django analytics tables
            try:
                cur.execute(f"SELECT * FROM analytics_event WHERE created_at > '{last_sync}'")
                for row in cur.fetchall():
                    content = {"type": "analytics_event", "data": list(row)}
                    items.append({
                        "device": "jp_birthday_site_server",
                        "source": "django_analytics",
                        "data_type": "analytics_data",
                        "content": content,
                        "device_time": datetime.now(timezone.utc).isoformat(),
                        "content_hash": compute_hash(content),
                    })
            except Exception:
                pass  # Table might not exist
            conn.close()
    except Exception as e:
        print(f"  [analytics] Error: {e}")

    # Nginx access logs
    log_file = "/var/log/nginx/access.log"
    if os.path.exists(log_file):
        try:
            mtime = os.path.getmtime(log_file)
            if mtime > last_ts:
                content = {"type": "access_log_change", "path": log_file,
                           "modified": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()}
                items.append({
                    "device": "jp_birthday_site_server",
                    "source": "nginx_access_log",
                    "data_type": "access_logs",
                    "content": content,
                    "device_time": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                    "content_hash": compute_hash(content),
                })
        except OSError:
            pass

    # Site content
    site_dir = "/var/www/jaytibirthday"
    if os.path.exists(site_dir):
        for root, dirs, files in os.walk(site_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    mtime = os.path.getmtime(fpath)
                    if mtime > last_ts and os.path.getsize(fpath) < 5_000_000:
                        content = {
                            "type": "site_file_change",
                            "path": fpath,
                            "filename": fname,
                            "modified": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                        }
                        items.append({
                            "device": "jp_birthday_site_server",
                            "source": "site_scan",
                            "data_type": "site_content",
                            "content": content,
                            "device_time": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                            "content_hash": compute_hash(content),
                        })
                except OSError:
                    pass

    return items


def collect_termux_s24(last_sync):
    """Collect new data from Termux on S24 (Firestore daemon logs)."""
    items = []
    last_ts = datetime.fromisoformat(last_sync.replace("Z", "+00:00")).timestamp()

    # Daemon logs
    log_dirs = [os.path.expanduser("~/.eka-agent/logs"), os.path.expanduser("~/logs")]
    for log_dir in log_dirs:
        if os.path.exists(log_dir):
            for fname in os.listdir(log_dir):
                fpath = os.path.join(log_dir, fname)
                if os.path.isfile(fpath):
                    try:
                        mtime = os.path.getmtime(fpath)
                        if mtime > last_ts:
                            content = {"type": "daemon_log", "path": fpath,
                                       "filename": fname,
                                       "modified": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()}
                            items.append({
                                "device": "termux_s24",
                                "source": "daemon_log_scan",
                                "data_type": "daemon_logs",
                                "content": content,
                                "device_time": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                                "content_hash": compute_hash(content),
                            })
                    except OSError:
                        pass

    return items


# ─── Device Registry ───
COLLECTORS = {
    "samsung_s24_ultra": collect_samsung_s24_ultra,
    "windows_pc_abcom": collect_windows_pc_abcom,
    "asus_vivobook": collect_asus_vivobook,
    "jp_drivebackup": collect_jp_drivebackup,
    "jp_birthday_site_server": collect_jp_birthday_site_server,
    "termux_s24": collect_termux_s24,
}


def main():
    parser = argparse.ArgumentParser(description="EKA Agent — Device Data Push")
    parser.add_argument("--device", required=True, choices=list(COLLECTORS.keys()),
                        help="Device name to collect from")
    parser.add_argument("--dry-run", action="store_true", help="Collect but don't push")
    args = parser.parse_args()

    device = args.device
    last_sync = get_last_sync(device)
    now = datetime.now(timezone.utc).isoformat()

    print(f"=== EKA Agent Push — {device} ===")
    print(f"  Last sync: {last_sync}")
    print(f"  Current:   {now}")
    print(f"  Collecting delta data...")

    collector = COLLECTORS[device]
    items = collector(last_sync)

    print(f"  Collected {len(items)} new items.")

    if args.dry_run:
        print(f"  [DRY RUN] Skipping push.")
        for item in items[:5]:
            print(f"    {item['data_type']}: {item['content_hash'][:16]}...")
        if len(items) > 5:
            print(f"    ... and {len(items)-5} more")
        return

    result = push_items(items, device)

    total_pushed = result.get("inserted", 0)
    if total_pushed > 0 or len(items) == 0:
        save_last_sync(device, now)
        print(f"  Updated last_sync to {now}")

    print(f"=== Done — {device} ===")


if __name__ == "__main__":
    main()