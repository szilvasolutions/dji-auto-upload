"""The support bundle is meant to be pasted into an issue by a non-developer,
so the one thing it must never do is leak their Telegram credentials."""

from __future__ import annotations

from pathlib import Path

from dji_auto_upload.config import (
    BehaviourConfig,
    Config,
    DetectConfig,
    LoggingConfig,
    NotifierConfig,
    RemoteConfig,
    RetentionConfig,
    TelegramCredentials,
)
from dji_auto_upload.diagnostics import build_report, write_report
from dji_auto_upload.paths import AppPaths

TOKEN = "123456:SUPERSECRETTOKEN"
CHAT = "987654321"


def _cfg(paths: AppPaths) -> Config:
    return Config(
        remote=RemoteConfig(name="gdrive", path_template="DJI/{date}"),
        retention=RetentionConfig(),
        detect=DetectConfig(),
        behaviour=BehaviourConfig(),
        notifier=NotifierConfig(enabled=True),
        logging=LoggingConfig(),
        telegram=TelegramCredentials(bot_token=TOKEN, chat_id=CHAT),
        paths=paths,
    )


def test_bundle_never_contains_the_bot_token_or_chat_id(tmp_app_paths: AppPaths) -> None:
    # Worst case: the secrets are also sitting in the config file we inline.
    tmp_app_paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_app_paths.config_file.write_text(
        f'[telegram]\nbot_token = "{TOKEN}"\nchat_id = "{CHAT}"\n', encoding="utf-8"
    )
    report = build_report(_cfg(tmp_app_paths))

    assert TOKEN not in report
    assert CHAT not in report
    assert "<redacted>" in report


def test_bundle_reports_credential_presence_without_the_values(
    tmp_app_paths: AppPaths,
) -> None:
    report = build_report(_cfg(tmp_app_paths))
    assert "credentials configured: True" in report
    assert "deliberately not included" in report


def test_bundle_includes_what_support_actually_needs(tmp_app_paths: AppPaths) -> None:
    report = build_report(_cfg(tmp_app_paths))
    for section in ("environment", "paths", "rclone", "auto-trigger", "local stage"):
        assert f"----- {section}" in report
    assert "dji-auto-upload" in report


def test_write_report_returns_the_path_it_wrote(tmp_app_paths: AppPaths, tmp_path: Path) -> None:
    out = tmp_path / "bundle.txt"
    assert write_report(_cfg(tmp_app_paths), out) == out
    assert out.read_text(encoding="utf-8").startswith("dji-auto-upload support bundle")


def test_write_report_defaults_next_to_the_log(tmp_app_paths: AppPaths) -> None:
    path = write_report(_cfg(tmp_app_paths))
    assert path.parent == tmp_app_paths.log_dir
    assert path.name.startswith("dji-auto-upload-diagnostics-")


def test_bundle_includes_macos_watcher_logs_when_present(tmp_app_paths: AppPaths) -> None:
    (tmp_app_paths.log_dir).mkdir(parents=True, exist_ok=True)
    (tmp_app_paths.log_dir / "watcher.err.log").write_text("boom on macos\n", encoding="utf-8")
    report = build_report(_cfg(tmp_app_paths))
    assert "macos watcher stderr" in report
    assert "boom on macos" in report
