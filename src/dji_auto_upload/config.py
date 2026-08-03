"""TOML config + credentials loader, with sane defaults and round-trip writes.

config.toml is user-edited and 0644; credentials.toml is wizard-managed and 0600.
We keep them separate so users can `git ignore`-style think about the trust
boundary without ceremony.
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.toml_document import TOMLDocument

from .errors import ConfigError
from .paths import AppPaths, get_paths

DEFAULT_CONFIG_TOML = """\
# dji-auto-upload configuration. Edit freely — comments survive `dji-auto-upload setup` re-runs.

[remote]
# Any rclone remote name from `rclone listremotes`. Run `rclone config` to add one.
name = "gphotos"
# Where on the remote to put each day's footage. {date} is replaced with YYYY-MM-DD.
# - Google Photos:  "album/DJI-{date}"   (each date becomes its own album)
# - Drive/Dropbox/etc.: "DJI/{date}"
path_template = "album/DJI-{date}"

[retention]
# How many days to keep local copies after they've been uploaded. 0 = never delete.
stage_days = 2
# How many days of footage to keep on the drone itself. 0 = never delete from drone.
# Default 0: we NEVER touch your drone unless you explicitly opt in during setup.
drone_days = 0

[detect]
vendor_ids   = ["2ca3"]                      # DJI USB vendor ID
volume_labels = ["DJI", "DJIMEDIA"]
dcim_subdirs = ["100MEDIA", "DJI_001"]       # preferred order; fallback = first dir under DCIM
extensions   = ["mp4", "mov", "jpg", "dng", "raw"]
# Sidecar files (telemetry, low-res proxies) are never uploaded, but they are
# only deleted from the drone together with their video once IT is uploaded.
sidecar_extensions = ["srt", "lrf"]
# true = trigger only on vendor-ID / volume-label match. false = also accept
# any volume with a DCIM folder (catches new DJI models on day one, but will
# also offload e.g. a GoPro card).
strict_detect = false

[behaviour]
disk_headroom_mb   = 512
copy_timeout_sec   = 1800                    # per file
upload_timeout_sec = 3600                    # give up if rclone goes silent this long
upload_transfers   = 4
upload_retries     = 3
verify_after_copy  = true                    # size check; mtime is preserved either way
delete_drone_files = false                   # master switch for drone-side cleanup (opt-in)
eject_when_done    = true                    # eject/unmount the drone when the run finishes
# Hold an OS sleep inhibitor while a run is in progress, so an idle laptop does
# not suspend mid-upload after you walk away. Only blocks *idle* sleep; closing
# the lid still suspends. Released as soon as the run finishes.
inhibit_sleep      = true

[notifier]
enabled = false                              # set to true once Telegram credentials are configured
backend = "telegram"
events  = ["start", "done_copy", "done_upload", "done", "fail"]

[paths]
# Where footage is kept on this computer while it uploads. Leave empty for the
# platform default (Windows: %LOCALAPPDATA%, macOS: ~/Library/Application
# Support, Linux: ~/.local/share — or /var/lib when run by the system trigger).
# Point it at a big drive or a folder you can actually find, e.g.
#   Windows: 'D:\\DJI'      macOS/Linux: '~/Videos/DJI'
# Use single quotes on Windows: in double quotes a backslash starts an escape
# and TOML will reject the line.
# Files are deleted from here once retention.stage_days has passed AND the
# upload is confirmed, so this is a staging area, not your archive.
stage_dir = ""

[logging]
level        = "INFO"
max_bytes    = 5000000
backup_count = 5
"""

DEFAULT_CREDENTIALS_TOML = """\
# Telegram bot credentials. Keep this file 0600.
# Get a token from @BotFather (/newbot). chat_id can be auto-discovered by
# `dji-auto-upload setup` after you message your bot once.

[telegram]
bot_token = ""
chat_id   = ""
"""


@dataclass(frozen=True)
class RemoteConfig:
    name: str = "gphotos"
    path_template: str = "album/DJI-{date}"


@dataclass(frozen=True)
class RetentionConfig:
    stage_days: int = 2
    drone_days: int = 0


@dataclass(frozen=True)
class DetectConfig:
    vendor_ids: tuple[str, ...] = ("2ca3",)
    volume_labels: tuple[str, ...] = ("DJI", "DJIMEDIA")
    dcim_subdirs: tuple[str, ...] = ("100MEDIA", "DJI_001")
    extensions: tuple[str, ...] = ("mp4", "mov", "jpg", "dng", "raw")
    sidecar_extensions: tuple[str, ...] = ("srt", "lrf")
    strict_detect: bool = False


@dataclass(frozen=True)
class BehaviourConfig:
    disk_headroom_mb: int = 512
    copy_timeout_sec: int = 1800
    upload_timeout_sec: int = 3600
    upload_transfers: int = 4
    upload_retries: int = 3
    verify_after_copy: bool = True
    delete_drone_files: bool = False
    eject_when_done: bool = True
    inhibit_sleep: bool = True


@dataclass(frozen=True)
class NotifierConfig:
    enabled: bool = False
    backend: str = "telegram"
    events: tuple[str, ...] = ("start", "done_copy", "done_upload", "done", "fail")


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    max_bytes: int = 5_000_000
    backup_count: int = 5


@dataclass(frozen=True)
class TelegramCredentials:
    bot_token: str = ""
    chat_id: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass(frozen=True)
class Config:
    remote: RemoteConfig = field(default_factory=RemoteConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    detect: DetectConfig = field(default_factory=DetectConfig)
    behaviour: BehaviourConfig = field(default_factory=BehaviourConfig)
    notifier: NotifierConfig = field(default_factory=NotifierConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    telegram: TelegramCredentials = field(default_factory=TelegramCredentials)
    paths: AppPaths = field(default_factory=get_paths)


def _as_tuple_str(v: object, default: tuple[str, ...]) -> tuple[str, ...]:
    if v is None:
        return default
    if isinstance(v, list):
        return tuple(str(x) for x in v)
    raise ConfigError(f"expected list of strings, got {type(v).__name__}")


def _parse_config_doc(doc: TOMLDocument | dict[str, Any]) -> Config:
    remote_t = doc.get("remote", {})
    retention_t = doc.get("retention", {})
    detect_t = doc.get("detect", {})
    behaviour_t = doc.get("behaviour", {})
    notifier_t = doc.get("notifier", {})
    logging_t = doc.get("logging", {})

    return Config(
        remote=RemoteConfig(
            name=str(remote_t.get("name", "gphotos")),
            path_template=str(remote_t.get("path_template", "album/DJI-{date}")),
        ),
        retention=RetentionConfig(
            stage_days=int(retention_t.get("stage_days", 2)),
            drone_days=int(retention_t.get("drone_days", 0)),
        ),
        detect=DetectConfig(
            vendor_ids=_as_tuple_str(detect_t.get("vendor_ids"), ("2ca3",)),
            volume_labels=_as_tuple_str(detect_t.get("volume_labels"), ("DJI", "DJIMEDIA")),
            dcim_subdirs=_as_tuple_str(detect_t.get("dcim_subdirs"), ("100MEDIA", "DJI_001")),
            extensions=_as_tuple_str(detect_t.get("extensions"), ("mp4", "mov", "jpg", "dng", "raw")),
            sidecar_extensions=_as_tuple_str(detect_t.get("sidecar_extensions"), ("srt", "lrf")),
            strict_detect=bool(detect_t.get("strict_detect", False)),
        ),
        behaviour=BehaviourConfig(
            disk_headroom_mb=int(behaviour_t.get("disk_headroom_mb", 512)),
            copy_timeout_sec=int(behaviour_t.get("copy_timeout_sec", 1800)),
            upload_timeout_sec=int(behaviour_t.get("upload_timeout_sec", 3600)),
            upload_transfers=int(behaviour_t.get("upload_transfers", 4)),
            upload_retries=int(behaviour_t.get("upload_retries", 3)),
            verify_after_copy=bool(behaviour_t.get("verify_after_copy", True)),
            delete_drone_files=bool(behaviour_t.get("delete_drone_files", False)),
            eject_when_done=bool(behaviour_t.get("eject_when_done", True)),
            inhibit_sleep=bool(behaviour_t.get("inhibit_sleep", True)),
        ),
        notifier=NotifierConfig(
            enabled=bool(notifier_t.get("enabled", False)),
            backend=str(notifier_t.get("backend", "telegram")),
            events=_as_tuple_str(
                notifier_t.get("events"),
                ("start", "done_copy", "done_upload", "done", "fail"),
            ),
        ),
        logging=LoggingConfig(
            level=str(logging_t.get("level", "INFO")),
            max_bytes=int(logging_t.get("max_bytes", 5_000_000)),
            backup_count=int(logging_t.get("backup_count", 5)),
        ),
    )


def _parse_credentials(doc: TOMLDocument | dict[str, Any]) -> TelegramCredentials:
    tg = doc.get("telegram", {})
    return TelegramCredentials(
        bot_token=str(tg.get("bot_token", "")),
        chat_id=str(tg.get("chat_id", "")),
    )


def load(paths: AppPaths | None = None) -> Config:
    """Load config + credentials. Missing files yield defaults (no error)."""
    p = paths or get_paths()
    cfg = Config(paths=p)

    if p.config_file.is_file():
        try:
            doc = tomlkit.parse(p.config_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ConfigError(f"could not parse {p.config_file}: {exc}") from exc
        # A user-chosen staging folder has to be applied to the paths object
        # before anything reads cfg.paths.stage_dir.
        custom_stage = str(doc.get("paths", {}).get("stage_dir", "") or "").strip()
        if custom_stage:
            p = replace(p, stage_dir_override=Path(custom_stage).expanduser())
        cfg = replace(_parse_config_doc(doc), paths=p)

    if p.credentials_file.is_file():
        try:
            cdoc = tomlkit.parse(p.credentials_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ConfigError(f"could not parse {p.credentials_file}: {exc}") from exc
        cfg = replace(cfg, telegram=_parse_credentials(cdoc))

    return cfg


def write_default_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")


def write_default_credentials(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _atomic_write(path, DEFAULT_CREDENTIALS_TOML, mode=0o600)


def _chmod_600(path: Path) -> None:
    if sys.platform == "win32":
        return
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _atomic_write(path: Path, text: str, *, mode: int | None = None) -> None:
    """tempfile → os.replace. With `mode`, the temp file is created with that
    mode from the first byte, so secrets never transit through a 0644 window."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if mode is not None and sys.platform != "win32":
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    if mode is not None:
        _chmod_600(path)


def save_config_doc(path: Path, doc: TOMLDocument) -> None:
    """Atomic write: tempfile → os.replace. Preserves comments."""
    _atomic_write(path, tomlkit.dumps(doc))


def save_credentials(path: Path, creds: TelegramCredentials) -> None:
    doc = tomlkit.document()
    tg = tomlkit.table()
    tg["bot_token"] = creds.bot_token
    tg["chat_id"] = creds.chat_id
    doc["telegram"] = tg
    _atomic_write(path, tomlkit.dumps(doc), mode=0o600)


def load_config_doc(path: Path) -> TOMLDocument:
    """Load existing config as a tomlkit document for in-place edits."""
    if path.is_file():
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    return tomlkit.parse(DEFAULT_CONFIG_TOML)
