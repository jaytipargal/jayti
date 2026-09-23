"""Arrange jtagent sandbox files per device. No secrets, no Chrome DBs."""

from __future__ import annotations

import json
from pathlib import Path

DEVICES = (
    "samsung_s24_ultra",
    "asus_vivobook",
    "windows_pc_abcom",
)

SANDBOX_FACTS = {
    "agent": "jtagent",
    "hf_user": "jtagent",
    "hf_org": "go4garage01",
    "hf_model": "go4garage01/jt-agent-model",
    "hf_dataset": "go4garage01/jt-agent-data",
    "drive_folder_id": "1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB",
    "drive_folder_url": "https://drive.google.com/drive/folders/1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB",
    "colab_session": "jt-agent-gpu",
    "email": "jaytipargal.jp@gmail.com",
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
                    "model": SANDBOX_FACTS["hf_model"],
                    "dataset": SANDBOX_FACTS["hf_dataset"],
                    "note": "Full 8B-class shards stay on the Hub; Colab CPU trains GPT-2 LoRA.",
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
            "input": "Which devices belong to jaytipargal.jp@gmail.com in this sandbox?",
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
            "input": "Can this Colab CPU session full-finetune go4garage01/jt-agent-model?",
            "output": (
                "No. Hub storage is about 29.5 GB of safetensor shards. This sandbox "
                "trains GPT-2 LoRA (eka_train default) and stores adapters under "
                "/content/TAN/jtagent/adapters."
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
            "colab_session": facts.get("colab_session", SANDBOX_FACTS["colab_session"]),
            "gpu": facts.get("gpu"),
            "adapter": adapter,
            "adapters_by_category": adapters_by_category,
            "adapter_drive_path": adapter_drive_path,
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
                f"- Push: `{pack['push_command']}`\n"
                f"- Integrity: {pack['integrity']}\n"
            "- Attached id: only the Drive owner (jaytipargl.jp@gmail.com) can remove it. The id owner cannot.\n"
            ),
            encoding="utf-8",
        )
        (dest / "pack.jsonl").write_text(
            json.dumps(pack, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        layout[name] = str(dest)
    return layout
