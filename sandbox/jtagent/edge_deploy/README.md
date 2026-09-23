# Edge inference deploy stubs (Vivobook + Samsung S24)

These files are deployment templates for the artifacts produced under
`/content/TAN/jtagent/adapters/`.

## Vivobook (Ollama)

1. Pull the exported GGUF model to the VivoBook local filesystem.
2. Copy `vivobook/Modelfile.template` to `Modelfile` and set the local GGUF path.
3. Build + run:

```bash
ollama create jtagent-edge -f Modelfile
ollama run jtagent-edge
```

## Samsung S24 (Termux + llama.cpp)

1. Pull quantized GGUF artifact from Drive into Termux storage.
2. Install llama.cpp binary in Termux.
3. Run `s24/termux-run.sh` with the GGUF path.

> `.pte` / ExecuTorch binaries are generated in a separate export pass when the
> mobile runtime toolchain is available.
