"""Find the just-plugged-in DJI volume on Linux / macOS / Windows.

Three signals, in priority order: USB vendor ID match, volume label match, and
the always-reliable cross-OS check — does the volume have a `DCIM/` directory
matching the expected layout?

The trigger usually passes us the device/mountpoint already, so this function
is the manual-`dji-auto-upload run` path and the macOS watcher's filtering helper.
"""

from __future__ import annotations

import logging
import shutil
import string
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import DetectConfig

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VolumeInfo:
    mountpoint: Path
    label: str | None = None
    vendor_id: str | None = None


def has_dji_dcim(volume: Path, dcim_subdirs: tuple[str, ...]) -> bool:
    dcim = volume / "DCIM"
    if not dcim.is_dir():
        return False
    if any((dcim / sub).is_dir() for sub in dcim_subdirs):
        return True
    return any(child.is_dir() for child in dcim.iterdir())


def is_dji_volume(vol: VolumeInfo, cfg: DetectConfig) -> bool:
    if vol.vendor_id and vol.vendor_id.lower() in {v.lower() for v in cfg.vendor_ids}:
        return True
    if vol.label:
        for needle in cfg.volume_labels:
            if needle.lower() in vol.label.lower():
                return True
    if cfg.strict_detect:
        # Strict mode: only vendor-ID / label matches count. No DCIM fallback,
        # so a random camera card never triggers an offload.
        return False
    return has_dji_dcim(vol.mountpoint, cfg.dcim_subdirs)


def find_dji_volume(cfg: DetectConfig) -> VolumeInfo | None:
    if sys.platform.startswith("linux"):
        cands = _enumerate_linux()
    elif sys.platform == "darwin":
        cands = _enumerate_macos()
    elif sys.platform == "win32":
        cands = _enumerate_windows()
    else:
        cands = []
    for v in cands:
        if is_dji_volume(v, cfg):
            return v
    return None


def _enumerate_linux() -> list[VolumeInfo]:
    """Use lsblk to find mounted block devices with their label + vendor."""
    if not shutil.which("lsblk"):
        return []
    try:
        proc = subprocess.run(
            ["lsblk", "-J", "-o", "NAME,LABEL,VENDOR,MOUNTPOINT,TYPE"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []

    import json
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []

    out: list[VolumeInfo] = []

    def walk(nodes: list[dict[str, Any]], parent_vendor: str | None) -> None:
        for n in nodes:
            vendor = (n.get("vendor") or parent_vendor or "").strip() or None
            mp = n.get("mountpoint")
            if mp:
                out.append(
                    VolumeInfo(
                        mountpoint=Path(mp),
                        label=n.get("label") or None,
                        vendor_id=vendor,
                    )
                )
            children = n.get("children") or []
            walk(children, vendor)

    walk(data.get("blockdevices", []), None)
    return out


def _enumerate_macos() -> list[VolumeInfo]:
    """Each entry under /Volumes is a mounted disk."""
    base = Path("/Volumes")
    if not base.is_dir():
        return []
    return [VolumeInfo(mountpoint=v, label=v.name) for v in base.iterdir() if v.is_dir()]


def _enumerate_windows() -> list[VolumeInfo]:
    """Walk drive letters A:..Z: that are mounted and report their labels."""
    out: list[VolumeInfo] = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:\\")
        if not root.exists():
            continue
        label = _windows_volume_label(root)
        out.append(VolumeInfo(mountpoint=root, label=label))
    return out


def _windows_volume_label(root: Path) -> str | None:  # pragma: no cover - Windows only
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        buf = ctypes.create_unicode_buffer(261)
        fs_buf = ctypes.create_unicode_buffer(261)
        ok = kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(str(root)),
            buf,
            wintypes.DWORD(261),
            None,
            None,
            None,
            fs_buf,
            wintypes.DWORD(261),
        )
        return buf.value if ok else None
    except Exception:
        return None
