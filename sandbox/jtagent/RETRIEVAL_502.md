# Jayti retrieval 502 on `/retrieve/healthz`

## Symptom

- `https://agent.jaytipargal.tech/retrieve/healthz` → **502 Bad Gateway**
- `https://agent.jaytipargal.tech/healthz` (Jayti Hub, :8443) → **200**
- `https://agent.jaytipargal.tech/retrieval/health` (EKA FAISS, :8100) → **200**

nginx strips the `/retrieve/` prefix and proxies to `http://127.0.0.1:8444/` (see `nginx/snippets/eka-agent-locations.conf`). A 502 means **nothing is accepting connections on 127.0.0.1:8444** — not an auth failure (that would be 401 from the app once it is up).

## Root cause hypothesis

**`jayti-retrieval.service` is down, failed, or was never deployed.** Hub (:8443) and legacy EKA retrieval (:8100) are separate systemd units and remain healthy.

Likely secondary causes after restart:

| Cause | Signal |
|-------|--------|
| Missing `/etc/jayti/retrieval.env` | Unit starts but auth endpoints 503; healthz should still work if process runs |
| Missing `/opt/jayti/retrieval/retrieval_server.py` | systemd restart loop, import error in journal |
| Missing FAISS files under `/opt/jayti/retrieval/faiss_index/` | healthz OK; `/retrieve/retrieve` fails at first query |
| Missing `asyncpg` / `argon2-cffi` in `/opt/jayti/hub/venv` | healthz OK; authenticated routes 503/500 |
| OOM on 3.8 GB VPS | `dmesg` OOM killer, service exits after first heavy request |

Note: `/healthz` does **not** load FAISS or Postgres — if the process is running, local `curl http://127.0.0.1:8444/healthz` must return 200 even when the index is missing.

## Auth model (IP whitelist vs device keys)

Three different gates on the same host:

| Path | Port | Auth | IP-dependent? |
|------|------|------|----------------|
| `/` (Hub ingest/pull) | 8443 | `X-Device-Id` + `X-Api-Key` → Postgres `api_keys` (argon2) | No |
| `/retrieve/*` | 8444 | Same device-key auth inside `retrieval_server.py` | No |
| `/retrieval/*`, `/agent/*` | 8100, 8000 | Single shared `X-API-Key` via nginx `map` in `/etc/nginx/conf.d/eka-agent-api-key.conf` | No |

**Vultr API IP whitelist** (401 `Unauthorized IP address` from `vultr-cli` on Cloud Agent egress) is unrelated to Jayti HTTP auth. It blocks management API calls from rotating cloud IPs, not device traffic to `agent.jaytipargal.tech`.

**jtagent pipeline** (`sandbox/jtagent/deploy_smoke.py`, `segment_jsonl.py`) uses **device keys** against Hub and `/retrieve/retrieve`, not the nginx shared `EKA_API_KEY`.

### Recommended hardening (header-based, no IP whitelist)

1. Keep **device-key auth at the app layer** for Hub and Jayti retrieval (already implemented).
2. Do **not** add IP allowlists for device clients (phones, Colab, Cloud Agents change IP constantly).
3. Optional nginx improvement: forward device headers explicitly on `/retrieve/` (already proxied by default):
   ```nginx
   location /retrieve/ {
       proxy_pass http://127.0.0.1:8444/;
       proxy_set_header X-Device-Id $http_x_device_id;
       proxy_set_header X-Api-Key $http_x_api_key;
   }
   ```
4. Long term: migrate `/retrieval/` and `/agent/` off the single shared nginx key to the same per-device `api_keys` table, or terminate TLS with mTLS for admin-only legacy routes.

## Recovery

Paste-ready script (no secrets committed):

```bash
bash /path/to/jayti/sandbox/jtagent/vps_retrieval_recovery.sh
```

On the VPS after copying the script, or paste the file contents into the Vultr web console for instance `jayti-agent-db` (`9bcd867e-5779-484e-8c63-1edf8f505e05`).

## Automated fix from Cloud Agent

- **vultr-cli**: failed with `401 Unauthorized IP address` (API key IP whitelist).
- **SSH**: failed (`Permission denied (publickey)` — CLAUDE_KEY private key not on this VM).
- **Remote restart**: not possible without console or whitelisted IP.

After you run the console script, re-check:

```bash
curl -sS https://agent.jaytipargal.tech/retrieve/healthz
python sandbox/jtagent/deploy_smoke.py
```
