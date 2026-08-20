#!/usr/bin/env python3
"""
EKA Agent — LoRA Fine-Tuning Pipeline
Trains the agent on new daily chunks using LoRA (Low-Rank Adaptation).

Usage:
  python eka_train.py --daily                 # Train on today's new chunks
  python eka_train.py --date 2026-08-20       # Train on specific date's chunks
  python eka_train.py --batch-size 100        # Train on 100 most recent chunks
  python eka_train.py --full-retrain           # Full retraining (not recommended daily)
  python eka_train.py --list-adapters          # List all trained adapters
  python eka_train.py --rollback 2026-08-19    # Rollback to a specific date's adapter

Requirements:
  pip install torch transformers peft datasets accelerate bitsandbytes

Note: Without CUDA GPU, training uses CPU (slower but functional).
With GPU, QLoRA (4-bit quantization) is used for efficiency.
"""
import os
import sys
import json
import argparse
from datetime import datetime, timezone, date
from pathlib import Path

# ─── Config ───
TRAINING_FILE = r"D:\training-data\agent_training_chunks_with_learning.jsonl"
ADAPTER_DIR = r"D:\training-data\adapters"
ROLLBACK_DIR = r"D:\training-data\rollback"
DAILY_INGESTION_DIR = r"D:\training-data\daily_ingestion"
STATE_DIR = os.path.join(os.path.expanduser("~"), ".eka_agent")

# Model config — using a small model for CPU compatibility
# Can be changed to a larger model if GPU available
BASE_MODEL = "gpt2"  # 124M params, proven CPU-safe on this system
LORA_R = 16        # LoRA rank
LORA_ALPHA = 32    # LoRA scaling
LORA_DROPOUT = 0.05
BATCH_SIZE = 2
LEARNING_RATE = 2e-4
NUM_EPOCHS = 3
MAX_SEQ_LEN = 512

def today_str():
    return date.today().isoformat()

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def check_gpu():
    """Check if CUDA GPU is available."""
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False

def load_training_data(date_str=None, batch_size=None):
    """Load training chunks — either from a specific date's batch or most recent."""
    chunks = []

    if date_str:
        # Load from daily ingestion rollback
        rollback_file = os.path.join(ROLLBACK_DIR, f"{date_str}_batch.jsonl")
        if os.path.exists(rollback_file):
            with open(rollback_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        chunks.append(json.loads(line))
            print(f"  Loaded {len(chunks)} chunks from {date_str} batch")
        else:
            print(f"  No batch found for {date_str}")
            return []
    else:
        # Load most recent N chunks from training file
        # Read from end of file
        all_lines = []
        with open(TRAINING_FILE, "r", encoding="utf-8", errors="replace") as f:
            # Skip instruction chunks (first 10)
            for i, line in enumerate(f):
                if i >= 10:
                    all_lines.append(line.strip())

        count = batch_size or 100
        recent_lines = all_lines[-count:]
        for line in recent_lines:
            if line:
                try:
                    chunks.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        print(f"  Loaded {len(chunks)} recent chunks (last {count} from file)")

    return chunks

def format_for_training(chunks):
    """Format chunks into input/output pairs for training."""
    formatted = []
    for chunk in chunks:
        input_text = chunk.get("input", "")
        output_text = chunk.get("output", "")
        if isinstance(output_text, str):
            try:
                parsed = json.loads(output_text)
                output_text = json.dumps(parsed, indent=2, ensure_ascii=False, default=str)
            except json.JSONDecodeError:
                pass

        formatted.append({
            "instruction": input_text,
            "output": output_text,
            "category": chunk.get("category", ""),
            "title": chunk.get("title", ""),
        })
    return formatted

def train_lora(chunks, date_label):
    """Run LoRA fine-tuning on the chunks."""
    has_gpu = check_gpu()
    print(f"  GPU available: {has_gpu}")
    print(f"  Base model: {BASE_MODEL}")
    print(f"  LoRA rank: {LORA_R}, alpha: {LORA_ALPHA}")
    print(f"  Batch size: {BATCH_SIZE}, Epochs: {NUM_EPOCHS}")
    print(f"  Learning rate: {LEARNING_RATE}")

    if len(chunks) == 0:
        print("  No chunks to train on. Skipping.")
        return None

    try:
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM, TrainingArguments
        from peft import LoraConfig, get_peft_model, TaskType
        from datasets import Dataset
    except ImportError as e:
        print(f"  ERROR: Missing dependencies: {e}")
        print(f"  Install with: pip install torch transformers peft datasets accelerate")
        return None

    # Format training data
    formatted = format_for_training(chunks)
    print(f"  Formatted {len(formatted)} training pairs")

    # Create dataset
    def format_example(ex):
        text = f"### Instruction:\n{ex['instruction']}\n\n### Response:\n{ex['output']}"
        return {"text": text}

    dataset = Dataset.from_list(formatted)
    dataset = dataset.map(format_example)

    # Load tokenizer and model
    print(f"  Loading tokenizer and model...")
    try:
        tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
        tokenizer.pad_token = tokenizer.eos_token
        model = AutoModelForCausalLM.from_pretrained(
            BASE_MODEL,
            trust_remote_code=True,
            torch_dtype=torch.float32,  # CPU needs float32
            device_map="auto" if has_gpu else None,
        )
    except Exception as e:
        print(f"  ERROR loading model: {e}")
        print(f"  Note: Model download requires internet connection.")
        print(f"  The model will be cached after first download.")
        return None

    # Configure LoRA
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=LORA_R,
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        target_modules=["c_attn", "c_proj"],  # GPT-2 attention modules
    )

    model = get_peft_model(model, lora_config)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"  Trainable params: {trainable:,} / {total:,} ({trainable/total*100:.2f}%)")

    # Tokenize
    def tokenize(ex):
        tokens = tokenizer(
            ex["text"],
            truncation=True,
            max_length=MAX_SEQ_LEN,
            padding="max_length",
        )
        tokens["labels"] = tokens["input_ids"].copy()
        return tokens

    tokenized = dataset.map(tokenize, remove_columns=dataset.column_names)

    # Training arguments
    output_dir = os.path.join(ADAPTER_DIR, f"adapter_{date_label}")
    os.makedirs(ADAPTER_DIR, exist_ok=True)

    training_args = TrainingArguments(
        output_dir=output_dir,
        num_train_epochs=NUM_EPOCHS,
        per_device_train_batch_size=BATCH_SIZE,
        save_steps=500,
        logging_steps=1,
        learning_rate=LEARNING_RATE,
        save_total_limit=1,
        remove_unused_columns=False,
        report_to="none",
        fp16=has_gpu,  # Only use fp16 with GPU
        dataloader_pin_memory=False,  # Disable pin_memory on CPU
        use_cpu=not has_gpu,  # Explicitly use CPU mode
    )

    # Train
    from transformers import Trainer
    print(f"  Starting training...")
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized,
    )

    trainer.train()

    # Save adapter
    adapter_path = os.path.join(output_dir, "final")
    model.save_pretrained(adapter_path)
    tokenizer.save_pretrained(adapter_path)

    adapter_size = 0
    for f in Path(adapter_path).rglob("*"):
        if f.is_file():
            adapter_size += f.stat().st_size

    print(f"  Adapter saved: {adapter_path}")
    print(f"  Adapter size: {adapter_size / (1024*1024):.1f} MB")

    # Log to training status
    log_file = os.path.join(STATE_DIR, "training_log.jsonl")
    os.makedirs(STATE_DIR, exist_ok=True)
    log_entry = {
        "date": date_label,
        "adapter_path": adapter_path,
        "adapter_size_mb": round(adapter_size / (1024*1024), 1),
        "chunks_trained": len(chunks),
        "epochs": NUM_EPOCHS,
        "batch_size": BATCH_SIZE,
        "lora_r": LORA_R,
        "lora_alpha": LORA_ALPHA,
        "gpu": has_gpu,
        "model": BASE_MODEL,
        "trained_at": now_iso(),
        "status": "completed",
    }
    with open(log_file, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

    return adapter_path

def list_adapters():
    """List all trained adapters."""
    log_file = os.path.join(STATE_DIR, "training_log.jsonl")
    if not os.path.exists(log_file):
        print("  No adapters trained yet.")
        return

    print("\n=== Trained Adapters ===")
    with open(log_file, "r") as f:
        for line in f:
            if line.strip():
                entry = json.loads(line)
                print(f"  {entry['date']} | chunks={entry['chunks_trained']} | "
                      f"size={entry['adapter_size_mb']}MB | gpu={entry['gpu']} | "
                      f"status={entry['status']}")

def rollback(date_str):
    """Rollback to a specific date's adapter."""
    log_file = os.path.join(STATE_DIR, "training_log.jsonl")
    if not os.path.exists(log_file):
        print("  No adapters trained yet.")
        return

    # Find the adapter for this date
    with open(log_file, "r") as f:
        for line in f:
            if line.strip():
                entry = json.loads(line)
                if entry["date"] == date_str:
                    adapter_path = entry["adapter_path"]
                    if os.path.exists(adapter_path):
                        # Set as current adapter
                        current_file = os.path.join(STATE_DIR, "current_adapter.txt")
                        with open(current_file, "w") as f:
                            f.write(adapter_path)
                        print(f"  Rolled back to adapter from {date_str}")
                        print(f"  Adapter: {adapter_path}")
                        return
                    else:
                        print(f"  Adapter not found: {adapter_path}")
                        return

    print(f"  No adapter found for {date_str}")

# ─── MAIN ───
def main():
    parser = argparse.ArgumentParser(description="EKA Agent — LoRA Fine-Tuning Pipeline")
    parser.add_argument("--daily", action="store_true", help="Train on today's chunks")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="Train on specific date's chunks")
    parser.add_argument("--batch-size", type=int, help="Train on N most recent chunks")
    parser.add_argument("--full-retrain", action="store_true", help="Full retrain (not recommended daily)")
    parser.add_argument("--list-adapters", action="store_true", help="List all trained adapters")
    parser.add_argument("--rollback", metavar="YYYY-MM-DD", help="Rollback to a specific date's adapter")
    args = parser.parse_args()

    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(ADAPTER_DIR, exist_ok=True)

    if args.list_adapters:
        list_adapters()
        return

    if args.rollback:
        rollback(args.rollback)
        return

    # Determine training data
    if args.daily:
        date_label = today_str()
        chunks = load_training_data(date_str=date_label)
    elif args.date:
        date_label = args.date
        chunks = load_training_data(date_str=date_label)
    elif args.batch_size:
        date_label = f"batch_{today_str()}"
        chunks = load_training_data(batch_size=args.batch_size)
    elif args.full_retrain:
        date_label = f"full_{today_str()}"
        chunks = load_training_data(batch_size=10000)
    else:
        parser.print_help()
        return

    print(f"=== EKA Agent LoRA Training — {date_label} ===")
    print(f"  Training chunks: {len(chunks)}")

    adapter_path = train_lora(chunks, date_label)

    if adapter_path:
        print(f"\n=== Training Complete ===")
        print(f"  Adapter: {adapter_path}")
        print(f"  Date: {date_label}")
        print(f"  Chunks trained: {len(chunks)}")
    else:
        print(f"\n=== Training Skipped/Failed ===")

if __name__ == "__main__":
    main()