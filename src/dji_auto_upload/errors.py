"""Typed exceptions raised by the offload pipeline.

Each one carries a stage name so the top-level handler can label crash reports.
"""

from __future__ import annotations


class OffloadError(Exception):
    """Base class. Carries the stage where the failure happened."""

    stage: str = "unknown"

    def __init__(self, message: str, *, stage: str | None = None) -> None:
        super().__init__(message)
        if stage is not None:
            self.stage = stage


class ConfigError(OffloadError):
    stage = "config"


class DetectError(OffloadError):
    stage = "detect"


class MountError(OffloadError):
    stage = "mount"


class InventoryError(OffloadError):
    stage = "inventory"


class CopyError(OffloadError):
    stage = "copy"


class UploadError(OffloadError):
    stage = "upload"


class CleanupError(OffloadError):
    stage = "cleanup"


class DroneDisconnected(CopyError):
    """Source device vanished mid-copy. Stage is retained for replug-to-resume."""

    stage = "copy"


class InsufficientSpace(OffloadError):
    stage = "precheck"


class AlreadyRunning(OffloadError):
    """Another offload pass holds the lock. Caller should exit silently rc=0."""

    stage = "lock"
