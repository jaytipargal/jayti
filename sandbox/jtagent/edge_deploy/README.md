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

Prereqs: `rclone config reconnect jtagent_tan` once (Drive OAuth), and Ollama
installed.

## Samsung S24 (Termux + llama.cpp)

A 4.5 GB Q4 model is heavy for a phone. Either export a smaller quant (e.g.
`--quant Q3_K_M` / a ≤3B base) for the S24, or have the phone query the
VivoBook/VPS. To run locally:

1. Pull the quantized GGUF from the synced Drive into Termux storage.
2. Install the `llama.cpp` `llama-cli` binary in Termux.
3. `bash s24/termux-run.sh /path/to/jt-agent.gguf "your prompt"`.

> `.pte` / ExecuTorch mobile binaries are a separate export pass when the mobile
> runtime toolchain is available.
