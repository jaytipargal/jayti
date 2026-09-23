"""Attached device ids can be removed only by the Drive owner.

The id owner, the agent, and every other account cannot remove one.
"""

from __future__ import annotations

DRIVE_OWNER_EMAIL = "jaytipargl.jp@gmail.com"
ATTACHED_DEVICE_IDS = (
    "samsung_s24_ultra",
    "asus_vivobook",
    "windows_pc_abcom",
)


class DetachForbidden(PermissionError):
    pass


def detach_device(
    device_id: str,
    *,
    actor_email: str,
    owner_explicit: bool,
    as_drive_owner: bool = False,
) -> str:
    """Detach an attached id only for an explicit Drive-owner action."""
    if device_id not in ATTACHED_DEVICE_IDS:
        raise DetachForbidden(f"{device_id} is not an attached id")
    if not as_drive_owner or actor_email != DRIVE_OWNER_EMAIL or not owner_explicit:
        raise DetachForbidden(
            "only the Drive owner can remove an attached id; the id owner cannot"
        )
    return device_id
