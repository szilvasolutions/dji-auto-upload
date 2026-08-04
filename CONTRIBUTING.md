# Contributing

Bug reports are genuinely useful, especially with a `dji-auto-upload diagnose`
bundle attached.

## Testers wanted

**macOS.** Everything is implemented and covered by CI, but nobody has run it
with a real DJI device on a real Mac. If you have both, trying it and reporting
back is the most useful contribution right now.

**Devices other than a Neo or Goggles.** Mini, Air, Mavic, Avata, Osmo. If
detection works, say so. If it doesn't, the `diagnose` output will show why.

## Working on the code

```bash
git clone https://github.com/szilvasolutions/dji-auto-upload
cd dji-auto-upload
pip install -e ".[dev]"
pytest
ruff check .
mypy src
```

CI runs those three on Linux, macOS and Windows across Python 3.10 to 3.13, so
it's worth running them before opening a PR.

## A note on the safety tests

`tests/integration/test_safety_features.py` covers the cases where footage could
be lost: same filename in two DCIM folders, a reused filename after a card
format, an interrupted upload, and deleting from the device. If you change
anything in `copy.py`, `ledger.py` or `cleanup.py`, those tests are the ones to
read first and the ones most likely to catch a mistake.

The rule the whole design rests on: nothing is deleted from a device until a
file matching its name and size is confirmed at the destination.
