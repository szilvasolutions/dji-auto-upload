from __future__ import annotations

from unittest.mock import patch

from dji_auto_upload.upload import remote_configured


def test_unconfigured_remote_is_detected() -> None:
    with patch("dji_auto_upload.upload.list_remotes", return_value=["gdrive", "onedrive"]):
        assert remote_configured("gdrive")
        assert not remote_configured("gphotos")


def test_a_failed_listremotes_does_not_declare_the_remote_missing() -> None:
    """An empty list means `rclone listremotes` itself failed. Aborting the run
    on that would strand footage over a transient rclone hiccup."""
    with patch("dji_auto_upload.upload.list_remotes", return_value=[]):
        assert remote_configured("gdrive")
