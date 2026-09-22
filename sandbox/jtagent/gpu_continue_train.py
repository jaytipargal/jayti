"""Continue jtagent GPU training without waiting for a human."""
import subprocess
import sys
from datetime import date
from pathlib import Path

print("GPU_CONTINUE_START")
subprocess.check_call(
    [sys.executable, "-m", "pip", "install", "-q", "transformers", "peft", "datasets", "accelerate", "torchao>=0.16"]
)
import torch

print("cuda", torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)

sys.path.insert(0, "/content/jayti/scripts")
sys.path.insert(0, "/content/jayti/sandbox/jtagent")
import eka_train
from pack_devices import pack_devices, seed_chunks, write_jsonl

root = Path("/content/TAN/jtagent")
chunks_path = root / "training" / "sandbox_chunks.jsonl"
kb = root / "hf" / "model-meta" / "knowledge_base.txt"
extra = []
if kb.exists():
    text = kb.read_text(encoding="utf-8", errors="replace")[:2000]
    extra.append(
        {
            "input": "Summarize the jtagent knowledge base.",
            "output": text,
            "category": "kb",
            "title": "knowledge-base",
        }
    )
pad = [{"input": f"pad-{i}", "output": "ok"} for i in range(10)]
write_jsonl(chunks_path, pad + seed_chunks() + extra)

eka_train.TRAINING_FILE = str(chunks_path)
eka_train.ADAPTER_DIR = str(root / "adapters")
eka_train.NUM_EPOCHS = 3
eka_train.BATCH_SIZE = 1
eka_train.MAX_SEQ_LEN = 256
chunks = eka_train.load_training_data(date_str=None, batch_size=50)
path = eka_train.train_lora(chunks, date.today().isoformat() + "-gpu3")
print("adapter", path)
pack_devices(root, extra={"adapter": path, "colab_session": "jt-agent-gpu", "gpu": "T4"})
print("GPU_CONTINUE_DONE")
