"""Read-only descriptions of storage volumes that hold source mail."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from shutil import which
from typing import Any

from pydantic import BaseModel

DISKUTIL = "/usr/sbin/diskutil"
KEY_DEVICE_IDENTIFIER = "DeviceIdentifier"
KEY_FILESYSTEM_TYPE = "FilesystemType"
KEY_MOUNT_POINT = "MountPoint"
KEY_VOLUME_NAME = "VolumeName"
KEY_VOLUME_UUID = "VolumeUUID"
METADATA_CURRENT_MOUNT_PATH = "current_mount_path"


class SourceVolume(BaseModel):
    """A stable local-volume identity plus its latest OS metadata snapshot."""

    identity_json: str
    metadata_json: str
    mount_path: Path


def local_source_volume(path: Path) -> SourceVolume:
    """Describe the mounted volume containing *path* without changing it."""
    resolved = path.resolve()
    mount_path = local_mount_path(resolved)
    diskutil = _diskutil_info(resolved)
    device = str(os.stat(resolved).st_dev)
    volume_uuid = _text(diskutil.get(KEY_VOLUME_UUID))
    identity = {
        "kind": "local-volume",
        "stable_id": volume_uuid or f"device:{device}",
    }
    metadata = {
        "format": "mailarchiver/source-volume/v1",
        "kind": "local-volume",
        METADATA_CURRENT_MOUNT_PATH: str(mount_path),
        "device": device,
        "volume_label": _text(diskutil.get(KEY_VOLUME_NAME)),
        "volume_uuid": volume_uuid,
        "filesystem_type": _text(diskutil.get(KEY_FILESYSTEM_TYPE)),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "os": diskutil,
    }
    return SourceVolume(
        identity_json=_json(identity),
        metadata_json=_json(metadata),
        mount_path=mount_path,
    )


def local_mount_path(path: Path) -> Path:
    """Return the mount containing *path* without collecting volume metadata."""
    return _mount_path(path.resolve())


def _mount_path(path: Path) -> Path:
    current = path if path.is_dir() else path.parent
    while current.parent != current and not os.path.ismount(current):
        current = current.parent
    return current


def _diskutil_info(path: Path) -> dict[str, Any]:
    """Return the complete macOS diskutil plist when available, else an empty object."""
    command = DISKUTIL if Path(DISKUTIL).is_file() else which("diskutil")
    if command is None:
        return {}
    try:
        completed = subprocess.run(
            [command, "info", "-plist", str(path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        parsed = plistlib.loads(completed.stdout)
        return _json_safe(parsed) if isinstance(parsed, dict) else {}
    except (OSError, plistlib.InvalidFileException, subprocess.SubprocessError):
        return {}


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _text(value: object) -> str | None:
    return None if value is None or value == "" else str(value)
