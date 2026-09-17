# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

There is no build step or linter config; the only tooling is pytest.

```bash
pip install -r requirements-dev.txt          # test deps only (fastapi, pydantic, httpx, numpy, pytest)
pytest                                       # full suite (testpaths=tests, see pytest.ini)
pytest tests/test_redact.py                  # one file
pytest tests/test_vector_db.py::test_name    # one test
pytest --cov=scripts --cov=. --cov-report=term-missing
```

The suite is fully offline. `tests/conftest.py` installs stub modules into `sys.modules` for `chromadb`, `torch`, `faiss`, `sentence_transformers`, `transformers`, `peft`, `datasets` and `psycopg2` *before* any target module is imported, and puts both the repo root and `scripts/` on `sys.path` (tests do `import eka_agent_pull`, not `scripts.eka_agent_pull`). Do not install the ML stack to run tests. `numpy` is deliberately real (the FAISS offset-index tests round-trip `np.int64` arrays). A test that needs a heavy boundary to behave should monkeypatch the concrete module global (e.g. `faiss_index`, `get_collection`, `subprocess.run`) rather than rely on the permissive `_Stub`.

Known-but-unfixed defects are pinned with `@pytest.mark.known_bug` + `@pytest.mark.xfail(strict=True)` asserting the intended behavior; drop both markers when the bug is fixed (see `tests/test_known_bugs.py`).

## Architecture

The repo was renamed "EKA Agent" → "Jayti Agent" in the README only; code, services, and file names still use `eka_`. It contains two related systems that share no code at runtime:

### 1. Ingestion + continuous-learning pipeline (`scripts/`)

Devices → VPS → main PC, run on a nightly schedule (cron / Windows Task Scheduler via `setup_windows_tasks.bat`):

- `eka_agent_push.py` — runs on each of 6 devices (S24/Termux, Windows PCs, drive backup, birthday-site server). One `collect_<device_name>(last_sync)` function per device; selected with `--device` (`--dry-run` collects without pushing). Pushes deltas to `POST /ingest`.
- `eka_agent_server.py` — the original ingestion API (PostgreSQL, 6 tables from `setup_vps_db.sh`, `verify_api_key`, SHA-256 `content_hash` dedup). **Not what runs in production:** the VPS serves ingestion from `server/jayti-hub/` (Jayti Hub, 127.0.0.1:8443, per-device `X-Device-Id` + `X-Api-Key` checked against an `api_keys` table). `config/eka-agent-api.service` also binds 8443 and must stay disabled.
- `eka_agent_pull.py` — main PC: `GET /pull` → save per-device JSONL under a dated ingestion dir → `POST /pull/mark` → turn items into input/output training chunks → trigger training/vector update → report to `/training/status` and `/audit`.
- `eka_train.py` — LoRA fine-tuning (GPT-2 base) on a day's chunks, dated adapters with `--list-adapters` / `--rollback DATE`.
- `eka_vector_db.py` — ChromaDB indexing of the training JSONL with resumable state (`last_indexed_line`); supports `--json` output.

The push and pull clients talk HTTP by shelling out to `curl` via `subprocess.run` (writing JSON payloads to temp files for `-d @file`), not via `requests`/`urllib` — tests fake `subprocess.run` accordingly. VPS responses that aren't valid JSON degrade to an error dict / `{}` rather than raising.

### 2. RAG query agent (repo root + `scripts/`)

Two-process design: a FAISS retrieval server on :8100 and an agent server on :8000 that calls it over HTTP (`urllib`).

- Retrieval: `scripts/eka_retrieval_server.py` (local Windows, `D:\training-data\faiss_index`) and `eka_retrieval_server_vps.py` (VPS, `/opt/eka-agent/faiss_index`) are near-duplicates differing mainly in paths/thread counts — change both. ~1.15M docs, `all-MiniLM-L6-v2` 384-dim `IndexFlatIP`. To stay within 3.8 GB RAM the doc store is **not** loaded: `doc_offsets.bin` holds int64 byte offsets into `doc_store.jsonl`, and docs are read by seeking.
- Generation: `eka_agent_cloud.py` (VPS, Anthropic Messages API, key from `ANTHROPIC_API_KEY`, model from `ANTHROPIC_MODEL`) vs `scripts/eka_agent_deploy.py` (local LoRA-tuned Qwen2.5-0.5B). Both expose `/health`, `/query`, `/query/stream` (SSE), `/raw`.
- Deployment: `eka-agent.service` / `eka-retrieval.service` (systemd, `/opt/eka-agent/venv`). `eka_client.sh` is the curl-based client described in `DEPLOYMENT_GUIDE.md`; it sends `X-API-Key` from `EKA_API_KEY`.

### VPS / nginx (`nginx/`, `server/`)

The files under `nginx/` and `server/` mirror what is deployed on the VPS, so change them here first and then copy them over. Line endings are pinned to LF in `.gitattributes`. `agent.jaytipargal.tech` (primary; DNS in Vercel) and `agent.urgaa.in` (legacy alias) both include `nginx/snippets/eka-agent-locations.conf`:
- `/` goes to Jayti Hub on :8443.
- `/retrieve/` goes to jayti-retrieval on :8444, which authenticates each device itself.
- `/retrieval/` goes to :8100 and `/agent/` to :8000. Both require an `X-API-Key` matching the `map` in `/etc/nginx/conf.d/eka-agent-api-key.conf`, which lives only on the server; only an example is committed. The same key is `AGENT_API_KEY` in the jayti-dashboard Vercel project (jpbir repo). The `/health` routes need no key.

ufw allows only ports 22, 80 and 443.

### Corpus redaction

`scripts/eka_scan_secrets.py` is the single source of truth for credential detection; `scripts/eka_redact.py` imports the same `scan_secrets` for both redaction and the final verify-rescan, so the two cannot drift. Its guarantees (typed `[REDACTED-<TYPE>]` placeholders, whole PEM blocks removed, untouched lines byte-identical) are enumerated in its docstring and asserted in `tests/test_redact.py`.

## Secrets and `.gitignore`

Config is import-time module constants, with placeholders `YOUR_API_KEY_HERE` / `YOUR_DB_PASSWORD_HERE` (push, pull, server, `setup_vps_db.sh`, `deploy_push_agent.sh`) — never commit real values. `.gitignore` excludes `*.json` and any path containing `secret`, `credential`, `password`, `api_key` or `token`; a new source file matching those patterns (e.g. `eka_scan_secrets.py`) needs an explicit `!` exception or it will silently not be tracked.
