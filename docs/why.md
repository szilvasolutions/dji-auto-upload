# Why this project exists (and the bash → Python rewrite story)

## The problem

I fly a DJI drone several times a week. Every flight, I want three things to
happen *automatically*:

1. New footage gets copied off the drone.
2. The footage lands in my cloud library, properly grouped.
3. Old footage on the drone is (optionally) deleted so the card never fills up.

The vendor app does (1) but only when I open it, and only over Wi-Fi at the
speed of a wet napkin. Vendor cloud sync does (2) only for clips it can
re-encode. Nothing does (3).

So I built a pipeline. The first version was a 506-line bash script that ran
on my Proxmox host: udev fired on a USB vendor-ID match, the script mounted
the drone read-only, rsynced new files to a per-date staging directory,
called `rclone copy` with a deduplication ledger, then deleted the older
clips off the drone. Telegram pinged me at every stage.

This is the public, cross-platform Python rewrite of that pipeline.

## Why rewrite at all?

The bash version was *good*. It survived my edge cases. But it was Linux-only
and root-only; the install procedure was 12 manual steps; the Telegram and
n8n integrations were hard-coded for my homelab. Friends asked if they could
run it on their Macs. I couldn't say yes without a rewrite.

The Python version had to:
- Run on Linux, macOS, and Windows from one codebase.
- Install in three commands by a non-engineer.
- Keep every edge-case fix the bash version earned painfully.

## Lessons that survived the rewrite

These were dearly-bought in the bash version. They're now in code, with tests.

### 1. The `.uploaded` ledger pattern

Google Photos has no server-side dedup — every duplicate upload creates a
duplicate album entry. So the pipeline tracks per-file uploads in a
`.uploaded` text file (one basename per line) inside each stage directory.
`rclone copy --files-from <ledger-diff>` ensures already-uploaded files are
*never sent twice*, even if you replug the same card a hundred times.

The ledger file's mtime doubles as the prune sentinel: a stage directory is
only eligible for deletion if `.uploaded` exists *and* its mtime is past the
retention cutoff. Un-uploaded data is never lost to retention.

[`src/dji_auto_upload/ledger.py`](../src/dji_auto_upload/ledger.py)

### 2. systemd-udevd's seccomp filter blocks `mount(2)`

The first version of the udev rule dispatched the script directly via `RUN+=`.
On every plug-in: `mount: permission denied`. After hours of debugging it
turned out that `systemd-udevd.service` ships with a `SystemCallFilter` that
omits the legacy `mount` syscall entirely. Anything spawned via `RUN+=` runs
in the udev worker's process tree and inherits the filter, so every `mount(2)`
returns `EPERM`.

**Fix**: dispatch via `systemd-run` (no `--scope`, with `--collect`). The
script then runs as a child of PID 1, no inherited filter, no 180-second udev
event timeout, and journald gets the logs for free.

`--collect` matters too. Without it, a transient unit that exits non-zero
lingers in `failed` state, and the next `systemd-run --unit=<same-name>` exits
1 ("Unit already exists") — the script silently never fires on the next
replug. With `--collect`, systemd garbage-collects the unit on exit.

The Python rewrite preserves this exact pattern in
[`installers/templates/99-dji-auto-upload.rules.j2`](../src/dji_auto_upload/installers/templates/99-dji-auto-upload.rules.j2).

### 3. Drone "off but charging" mode enumerates as USB Mass Storage

Power the drone off while it's plugged in, and ~4 seconds later udev fires a
`block add` event. The gadget re-enumerates, but the storage isn't backed by
drone flash anymore — reads return `EIO`. The bash script learned to do a
512-byte `dd` probe before any user-facing notification; on EIO it exits
silently.

The Python version preserves this on Linux only — macOS and Windows don't
enumerate the drone in that mode, so the guard isn't needed there.

### 4. rsync vs atomic-rename trade-off

The bash version uses `rsync --partial-dir=.rsync-partial` so half-files live
in a hidden directory and never masquerade as complete. The Python version
swaps this for `shutil.copy2` to `<dest>.part`, then `os.replace` on success.
The atomic-rename pattern works identically on Linux/macOS/Windows.

Trade-off accepted: rsync resumes a half-file from a byte offset; we re-copy
from scratch. For drone clips ≤ 5 GB on USB-3, the redo costs seconds. The
portability win pays for it.

[`src/dji_auto_upload/copy.py`](../src/dji_auto_upload/copy.py)

### 5. Disk-space precheck with headroom

If the user's `/var/lib` is 80% full, the offload should fail *before*
copying anything, not stall halfway through with a half-corrupt stage. The
pipeline checks `shutil.disk_usage(stage_base).free` against the inventory's
total bytes plus a 512 MB headroom; if there's not enough, we abort cleanly,
notify, and leave the drone untouched.

A self-healing pre-copy prune runs first, so a backlog of old uploaded stages
can free space for today before the precheck fires.

### 6. EXIT-trap-equivalent failure reporting

The bash version had `trap cleanup_and_report EXIT` to catch every uncaught
exit (set -u trips, signals, kill -9). The Python equivalent is a top-level
`try/except Exception` in `cli.run` that fires a `fail` Telegram with
`traceback.format_exc()` plus the last 25 log lines, then re-raises for the
exit code. Same UX, half the code.

[`src/dji_auto_upload/cli.py`](../src/dji_auto_upload/cli.py)
[`src/dji_auto_upload/offload.py`](../src/dji_auto_upload/offload.py) (`report_failure`)

### 7. Single-flight via lock

udev / launchd / Task Scheduler can fire double for one plug-in event (USB
re-enumeration during driver load is normal). The bash version used `flock`;
the Python version uses `filelock`, which is one library that does the right
thing on POSIX and Windows. If the lock is held: silent rc=0 exit. The user
should never know.

## What was *removed* for the public version

- **n8n webhook integration.** The bash version POSTs to two webhooks
  (drone-postprocess for flight stats, drone-card-health for SD-card MB/s
  trend tracking). Both are tied to my private n8n. Gone.
- **`postprocess-flight.sh`.** ffprobe/ffmpeg/jq pipeline that emits a JSON
  flight summary. Gone — depends on the webhook.
- **The exfat module load.** A kernel concern, documented in the README's
  prerequisites instead.
- **All hardcoded credentials.** Telegram tokens, chat IDs, Google Cloud
  OAuth client IDs. The setup wizard collects everything; the wizard's
  Telegram step even auto-discovers your chat ID by watching `getUpdates`
  for the next message you send to your bot.

## Architecture trade-offs

| Decision | Chose | Alternative | Why |
|---|---|---|---|
| Config format | TOML | YAML, JSON | stdlib parser, comments survive, no YAML footguns |
| CLI framework | Typer | Click, argparse | type-hint-driven, gives you `--help` + autocomplete free |
| File copy | shutil + atomic rename | rsync subprocess | one less external dep, identical on three OSes |
| Linux trigger | udev + systemd-run | direct udev RUN+= | escapes seccomp filter (lesson 2) |
| macOS trigger | LaunchAgent + `/Volumes` poll | DiskArbitration (pyobjc) | events needed a heavy native dep and could not be CI-tested |
| Windows trigger | Scheduled Task + PowerShell poll | WMI events, pywin32 | WMI event actions failed three ways in the field, all silently |
| Lock | filelock | manual flock/msvcrt | one library, three platforms |
| Notifier | Telegram via requests | python-telegram-bot SDK | one POST, no need for the full client |
| Logging | RotatingFileHandler | logrotate | zero external setup |
| Min Python | 3.10 | 3.9 | `match`, modern union syntax, broad availability in 2026 |

## What I'd do differently if I started fresh

- **Type-checked config schema with Pydantic.** I rolled my own
  validation; Pydantic would be a few lines and give better error messages.
- **`asyncio` for the upload phase.** Each per-date `rclone copy` is its
  own subprocess; running them concurrently with bounded parallelism would
  cut wall-clock time on multi-date plug-ins. Trade-off: log interleaving
  becomes harder to read.
- **State machine library.** The pipeline is implicitly a state machine
  (init → mount → inventory → copy → upload → cleanup → done/fail). A
  library like `transitions` would make the diagram in `docs/architecture.md`
  enforceable in code.

These were considered and dropped — for a single-developer hobby tool, they
add maintenance weight without paying for it. But they're the first things
I'd reach for if this grew teams.
