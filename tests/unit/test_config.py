from __future__ import annotations

import pytest

from dji_auto_upload.config import (
    TelegramCredentials,
    load,
    save_credentials,
    write_default_config,
)
from dji_auto_upload.errors import ConfigError
from dji_auto_upload.paths import AppPaths


def test_load_returns_defaults_when_no_files(tmp_app_paths: AppPaths) -> None:
    cfg = load(tmp_app_paths)
    assert cfg.remote.name == "gphotos"
    assert cfg.retention.stage_days == 2
    assert cfg.retention.drone_days == 0  # safe default: never touch the drone unless opted in
    assert cfg.behaviour.delete_drone_files is False  # drone-side deletion is strictly opt-in
    assert cfg.notifier.enabled is False


def test_load_reads_user_overrides(tmp_app_paths: AppPaths) -> None:
    tmp_app_paths.config_file.write_text(
        """
[remote]
name = "dropbox"
path_template = "DJI/{date}"

[retention]
stage_days = 30
drone_days = 0
""",
        encoding="utf-8",
    )
    cfg = load(tmp_app_paths)
    assert cfg.remote.name == "dropbox"
    assert cfg.remote.path_template == "DJI/{date}"
    assert cfg.retention.stage_days == 30
    assert cfg.retention.drone_days == 0


def test_load_credentials_separate_file(tmp_app_paths: AppPaths) -> None:
    save_credentials(tmp_app_paths.credentials_file, TelegramCredentials(bot_token="t", chat_id="c"))
    cfg = load(tmp_app_paths)
    assert cfg.telegram.bot_token == "t"
    assert cfg.telegram.chat_id == "c"
    assert cfg.telegram.configured is True


def test_load_raises_on_corrupt_config(tmp_app_paths: AppPaths) -> None:
    tmp_app_paths.config_file.write_text("this is = = not toml ===\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load(tmp_app_paths)


def test_write_defaults_idempotent(tmp_app_paths: AppPaths) -> None:
    write_default_config(tmp_app_paths.config_file)
    tmp_app_paths.config_file.write_text("# user edits\nstage_days = 99\n", encoding="utf-8")
    write_default_config(tmp_app_paths.config_file)
    assert "user edits" in tmp_app_paths.config_file.read_text()


def test_inhibit_sleep_defaults_on_and_can_be_disabled(tmp_app_paths: AppPaths) -> None:
    # Default on: "walk away" should not be defeated by an idle-sleeping laptop.
    assert load(tmp_app_paths).behaviour.inhibit_sleep is True

    tmp_app_paths.config_file.write_text(
        "[behaviour]\ninhibit_sleep = false\n", encoding="utf-8"
    )
    assert load(tmp_app_paths).behaviour.inhibit_sleep is False


def test_stage_dir_defaults_to_the_platform_location(tmp_app_paths: AppPaths) -> None:
    cfg = load(tmp_app_paths)
    assert cfg.paths.stage_dir == tmp_app_paths.data_dir / "stage"


def test_stage_dir_can_be_pointed_somewhere_else(tmp_app_paths: AppPaths, tmp_path) -> None:
    """People want footage on a drive with room, or somewhere they can find it."""
    target = tmp_path / "big-drive" / "DJI"
    tmp_app_paths.config_file.write_text(
        f'[paths]\nstage_dir = "{target.as_posix()}"\n', encoding="utf-8"
    )
    cfg = load(tmp_app_paths)
    assert cfg.paths.stage_dir == target


def test_empty_stage_dir_falls_back_to_the_default(tmp_app_paths: AppPaths) -> None:
    tmp_app_paths.config_file.write_text('[paths]\nstage_dir = ""\n', encoding="utf-8")
    cfg = load(tmp_app_paths)
    assert cfg.paths.stage_dir == tmp_app_paths.data_dir / "stage"


def test_stage_dir_expands_a_tilde(tmp_app_paths: AppPaths) -> None:
    tmp_app_paths.config_file.write_text(
        '[paths]\nstage_dir = "~/DroneFootage"\n', encoding="utf-8"
    )
    cfg = load(tmp_app_paths)
    assert "~" not in str(cfg.paths.stage_dir)
    assert cfg.paths.stage_dir.is_absolute()


def test_a_windows_path_in_literal_quotes_round_trips(tmp_app_paths: AppPaths) -> None:
    """In TOML double quotes a backslash starts an escape, so `"D:\\DJI"` is a
    parse error; single-quoted literal strings are what users must write."""
    tmp_app_paths.config_file.write_text("[paths]\nstage_dir = 'D:\\DJI'\n", encoding="utf-8")
    cfg = load(tmp_app_paths)
    assert str(cfg.paths.stage_dir).replace("/", "\\").endswith("D:\\DJI")
