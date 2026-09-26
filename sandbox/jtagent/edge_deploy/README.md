# Edge inference deploy (VivoBook + Samsung S24)

Runs the QLoRA-fine-tuned jtagent (exported to GGUF by
`sandbox/jtagent/export_gguf.py`, pushed to the TAN Drive folder under
`jtagent/gguf/`) on the physical devices. The ~4.5 GB Q4_K_M 7-8B model lives on
the **VivoBook**; the small VPS keeps the retrieval-grounded GPT-2 responder.

## VivoBook (Ollama) — dual-boot

The VivoBook boots both Windows and Linux; use whichever matches the session.
Both scripts pull the newest GGUF from Drive, write a `Modelfile` from
`vivobook/Modelfile.template`, and `ollama create jtagent`:

```bash
# Linux / WSL
bash vivobook/pull_and_build.sh
ollama run jtagent
```

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File .\vivobook\pull_and_build.ps1
ollama run jtagent
```

Prereqs: Ollama installed, and an rclone remote `jtagent_tan` rooted at the TAN
folder. Two ways to authorize it:

- **Headless (recommended for devices):** a Google Cloud **service-account key**
  from the `info@kailash-ai.com` project (Drive API enabled; `jaytipargal.jp` is
  admin). Share the TAN folder with the service account's email (reader is enough
  to pull), copy its JSON key to the device, then:
  ```bash
  bash configure_rclone_sa.sh /path/to/sa-key.json          # Linux / WSL / Termux
  ```
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\configure_rclone_sa.ps1 C:\path\sa-key.json
  ```
  No browser, no token refresh — works unattended and inside Termux.
- **Interactive:** `rclone config reconnect jtagent_tan:` once (browser OAuth as
  a TAN writer such as `jaytipargal.jp`).

## Samsung S24 (Termux + llama.cpp)

A 4.5 GB Q4 model is heavy for a phone. Either export a smaller quant (e.g.
`--quant Q3_K_M` / a ≤3B base) for the S24, or have the phone query the
VivoBook/VPS. To run locally:

1. Authorize rclone headlessly (service-account steps above), then pull the
   newest GGUF: `bash s24/pull.sh` (defaults to `~/jtagent/gguf`; override with
   `JTAGENT_GGUF_DIR`).
2. Install the `llama.cpp` `llama-cli` binary in Termux.
3. `bash s24/termux-run.sh "$(bash s24/pull.sh --print-newest)" "your prompt"`.

> `.pte` / ExecuTorch mobile binaries are a separate export pass when the mobile
> runtime toolchain is available.
