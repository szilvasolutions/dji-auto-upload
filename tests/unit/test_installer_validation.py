"""Trigger-input validation: config values are rendered into a root-owned udev
rule and a logon PowerShell script, so anything that could break out of its
quoting context must be rejected before install."""

from __future__ import annotations

import pytest

from dji_auto_upload.config import Config, DetectConfig
from dji_auto_upload.errors import ConfigError
from dji_auto_upload.installers import validate_trigger_inputs


def _cfg(vendor_ids: tuple[str, ...] = ("2ca3",), labels: tuple[str, ...] = ("DJI",)) -> Config:
    return Config(detect=DetectConfig(vendor_ids=vendor_ids, volume_labels=labels))


def test_default_config_is_valid() -> None:
    validate_trigger_inputs(Config())  # must not raise


def test_typical_custom_values_are_valid() -> None:
    validate_trigger_inputs(_cfg(("2ca3", "05ac"), ("DJI", "DJIMEDIA", "My Drone_1")))


@pytest.mark.parametrize(
    "bad_vid",
    ["2ca", "2ca3f", "2cg3", '2ca3", RUN+="/bin/evil', "", "2c a"],
)
def test_bad_vendor_ids_rejected(bad_vid: str) -> None:
    with pytest.raises(ConfigError):
        validate_trigger_inputs(_cfg(vendor_ids=(bad_vid,)))


@pytest.mark.parametrize(
    "bad_label",
    [
        'DJI", RUN+="/bin/evil',            # udev rule breakout
        "DJI'; Start-Process calc; '",      # PowerShell breakout
        "DJI`ncalc",                        # backtick escape
        "x" * 33,                           # over-long
        "",                                 # empty
        "DJI\nRUN",                         # newline
    ],
)
def test_bad_labels_rejected(bad_label: str) -> None:
    with pytest.raises(ConfigError):
        validate_trigger_inputs(_cfg(labels=(bad_label,)))
