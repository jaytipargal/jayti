"""Arrange jtagent sandbox files per device. No secrets, no Chrome DBs."""

from __future__ import annotations

import json
from pathlib import Path

from device_bind import DRIVE_OWNER_EMAIL

DEVICES = (
    "samsung_s24_ultra",
    "asus_vivobook",
    "windows_pc_abcom",
)

# The base we actually fine-tune (QLoRA) and export to GGUF for the devices.
# The 29.5GB go4garage01/jt-agent-model repo is an MLX/Qwen3-VL bundle and is
# NOT trainable on Colab — kept only as reference, never a train target.
TRAIN_BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"

SANDBOX_FACTS = {
    "agent": "jtagent",
    "hf_user": "jtagent",
    "hf_org": "go4garage01",
    "hf_model": "go4garage01/jt-agent-model",
    "hf_dataset": "go4garage01/jt-agent-data",
    "hf_lora_repo": "go4garage01/jt-agent-lora",
    "train_base_model": TRAIN_BASE_MODEL,
    "drive_folder_id": "1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB",
    "drive_folder_url": "https://drive.google.com/drive/folders/1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB",
    "colab_session": "jt-agent-gpu",
    # Single source of truth for the Drive owner id (also used by device_bind).
    "email": DRIVE_OWNER_EMAIL,
}


def seed_chunks() -> list[dict]:
    return [
        {
            "input": "Who is the sandbox personal agent?",
            "output": "jtagent, Hugging Face user jtagent in org go4garage01.",
            "category": "identity",
            "title": "agent-name",
        },
        {
            "input": "Which Hub model and dataset does jtagent use?",
            "output": json.dumps(
                {
                    "reference_model": SANDBOX_FACTS["hf_model"],
                    "dataset": SANDBOX_FACTS["hf_dataset"],
                    "train_base": SANDBOX_FACTS["train_base_model"],
                    "lora_repo": SANDBOX_FACTS["hf_lora_repo"],
                    "note": (
                        "go4garage01/jt-agent-model is an MLX/Qwen3-VL bundle kept for "
                        "reference only. jtagent is trained by QLoRA on "
                        + SANDBOX_FACTS["train_base_model"]
                        + " and exported to a Q4_K_M GGUF for the devices."
                    ),
                }
            ),
            "category": "hub",
            "title": "hf-repos",
        },
        {
            "input": "Which Drive folder is in this sandbox?",
            "output": (
                "Only folder "
                + SANDBOX_FACTS["drive_folder_id"]
                + " (TAN). Path on Colab: /content/TAN. Do not mount all of My Drive."
            ),
            "category": "drive",
            "title": "tan-folder",
        },
        {
            "input": "Which devices belong to jaytipargl.jp@gmail.com in this sandbox?",
            "output": json.dumps(
                {
                    "samsung_s24_ultra": "phone; Drive + Termux push agent",
                    "asus_vivobook": "ASUS VivoBook; Drive; Windows-MCP hardware when powered",
                    "windows_pc_abcom": "G4G-LAPTOP Lenovo 82KA; not ASUS; do not write ASUS liveness",
                }
            ),
            "category": "devices",
            "title": "device-map",
        },
        {
            "input": "How does a device push data into Jayti Hub?",
            "output": (
                "python eka_agent_push.py --device <name> with EKA_DEVICE_ID and "
                "EKA_DEVICE_KEY from device.env. Endpoints: samsung_s24_ultra, "
                "asus_vivobook, windows_pc_abcom, termux_s24."
            ),
            "category": "pipeline",
            "title": "push",
        },
        {
            "input": "Can this Colab session fine-tune go4garage01/jt-agent-model?",
            "output": (
                "No. That repo is a 29.5 GB MLX-quantized Qwen3-VL bundle (Apple-only, "
                "no HF config) and cannot load on Colab NVIDIA GPUs. jtagent trains by "
                "QLoRA on " + SANDBOX_FACTS["train_base_model"] + " (Colab A100), writes "
                "adapters under /content/TAN/jtagent/adapters, and exports a Q4_K_M GGUF "
                "under /content/TAN/jtagent/gguf. The 7-8B model runs on the VivoBook "
                "(Ollama); the 3.8 GB-RAM VPS keeps the light retrieval-grounded agent."
            ),
            "category": "train",
            "title": "cpu-limits",
        },
    ]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def pack_devices(root: Path, extra: dict | None = None) -> dict:
    root = Path(root)
    facts = dict(SANDBOX_FACTS)
    if extra:
        facts.update(extra)
    adapter = facts.get("adapter")
    adapters_by_category = facts.get("adapters_by_category") or {}
    if adapter:
        ap = Path(str(adapter))
        # Prefer .../adapters/adapter_<label>/final → jtagent/adapters/adapter_<label>/final
        parts = ap.parts
        if "adapters" in parts:
            idx = parts.index("adapters")
            adapter_drive_path = "jtagent/" + "/".join(parts[idx:])
        else:
            adapter_drive_path = f"jtagent/adapters/{ap.parent.name}/{ap.name}"
    else:
        adapter_drive_path = "jtagent/adapters/"
    gguf = facts.get("gguf")
    if gguf:
        gguf_drive_path = "jtagent/gguf/" + Path(str(gguf)).name
    else:
        gguf_drive_path = "jtagent/gguf/"
    # Where each device runs inference. The 7-8B GGUF is too big for the phone
    # and the small VPS, so it lives on the VivoBook; the others front it.
    serving = {
        "asus_vivobook": "ollama (local GGUF)",
        "windows_pc_abcom": "ollama (local GGUF)",
        "samsung_s24_ultra": "llama.cpp small quant, or query VivoBook/VPS",
    }
    layout = {}
    for name in DEVICES:
        dest = root / "devices" / name
        dest.mkdir(parents=True, exist_ok=True)
        pack = {
            "device": name,
            "agent": facts["agent"],
            "email": facts["email"],
            "drive_folder_id": facts["drive_folder_id"],
            "drive_folder_url": facts["drive_folder_url"],
            "hf_model": facts["hf_model"],
            "hf_dataset": facts["hf_dataset"],
            "hf_lora_repo": facts.get("hf_lora_repo", SANDBOX_FACTS["hf_lora_repo"]),
            "train_base_model": facts.get("train_base_model", SANDBOX_FACTS["train_base_model"]),
            "colab_session": facts.get("colab_session", SANDBOX_FACTS["colab_session"]),
            "gpu": facts.get("gpu"),
            "adapter": adapter,
            "adapters_by_category": adapters_by_category,
            "adapter_drive_path": adapter_drive_path,
            "gguf": gguf,
            "gguf_drive_path": gguf_drive_path,
            "serving": serving[name],
            "push_command": f"python scripts/eka_agent_push.py --device {name}",
            "physical_online": False,
            "liveness_note": "Do not claim physical hardware online without evidence from that device.",
            "integrity": {
                "asus_vivobook": "physical ASUS; liveness only from that hardware",
                "windows_pc_abcom": "Lenovo G4G-LAPTOP; NOT_ASUS; never write ASUS liveness from G4G",
                "samsung_s24_ultra": "ADB empty unless the phone is attached",
            }[name],
        }
        (dest / "README.md").write_text(
            (
                f"# {name}\n\n"
                f"Sandbox pack for jtagent. Drive folder `{facts['drive_folder_id']}`.\n\n"
                f"- Adapter: `{adapter or 'pending'}`\n"
                f"- GGUF: `{gguf or 'pending'}` (serve: {pack['serving']})\n"
                f"- Push: `{pack['push_command']}`\n"
                f"- Integrity: {pack['integrity']}\n"
                f"- Attached id: only the Drive owner ({facts['email']}) can remove it. "
                "The id owner cannot.\n"
            ),
            encoding="utf-8",
        )
        (dest / "pack.jsonl").write_text(
            json.dumps(pack, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        layout[name] = str(dest)
    return layout
