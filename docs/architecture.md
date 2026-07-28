# Architecture

## State machine

```
                ┌──────────┐
                │  init    │
                └────┬─────┘
                     ▼
                ┌──────────┐
                │ detect   │── no DJI volume ──▶ exit (silent)
                └────┬─────┘
                     ▼
                ┌──────────┐
                │ mount    │── failure ──▶ fail notify ──▶ exit 1
                │ (Linux)  │
                └────┬─────┘
                     ▼
                ┌──────────┐
                │inventory │── no DCIM ──▶ fail notify ──▶ exit 1
                └────┬─────┘
                     ▼
                ┌──────────┐
                │precheck  │── insufficient space ──▶ fail notify ──▶ exit 1
                │ (disk)   │
                └────┬─────┘
                     ▼
                ┌──────────┐
   per date ──▶ │  copy    │── drone disconnected ──▶ fail notify (stage retained)
                └────┬─────┘                            └──▶ exit 1 (replug to resume)
                     │
                     ▼
                ┌──────────┐
   per date ──▶ │ upload   │── rclone fail ──▶ fail notify (stage retained, no ledger update)
                │  rclone  │                       └──▶ exit 1
                └────┬─────┘
                     │ rc=0
                     ▼
                ┌──────────┐
                │ ledger   │  append uploaded basenames + bump sentinel mtime
                │ append   │
                └────┬─────┘
                     ▼
                ┌──────────┐
                │ cleanup  │  age makes a file a candidate; it is deleted only
                │  drone   │  if the ledger proves it (or, for a sidecar, its
                │          │  paired video) was uploaded. Skipped entirely if
                │          │  delete_drone_files=false, drone_days<=0, or the
                └────┬─────┘  drone's clock is untrustworthy.
                     ▼
                ┌──────────┐
                │  eject   │  unmount/eject so the user can safely unplug
                └────┬─────┘  (behaviour.eject_when_done)
                     ▼
                ┌──────────┐
                │  prune   │  delete stage dirs whose .uploaded mtime is past
                │  stage   │  retention. Sentinel-gated, and skipped while any
                └────┬─────┘  file in the dir is still pending upload.
                     ▼
                ┌──────────┐
                │  done    │  fire `done` notify, release lock, exit 0
                └──────────┘
```

## Module responsibilities

| Module | Job |
|---|---|
| `cli.py` | Typer surface; `dji-auto-upload setup / run / install-trigger / …`. |
| `wizard.py` | Interactive setup. Detects rclone, picks/creates a remote, walks Telegram setup, optionally calls the OS installer. |
| `offload.py` | The orchestrator — `OffloadRun.execute()` runs the pipeline. |
| `inventory.py` | Walks the DCIM dir and groups files by mtime date. |
| `copy.py` | Source → stage transfer, atomic rename, size verification. |
| `upload.py` | rclone subprocess wrapper. `list_remotes`, `remote_reachable`, `upload_files`. |
| `ledger.py` | `.uploaded` ledger read/append + legacy 0-byte sentinel migration. |
| `cleanup.py` | Stage prune + drone-side file deletion. |
| `detect.py` | Cross-OS DJI volume detection (vendor / label / DCIM signal). |
| `notifier.py` | Telegram via HTTP, with a Null fallback for when notifications are off. |
| `lock.py` | Cross-platform single-flight via `filelock`. |
| `config.py` | TOML schema, defaults, atomic writes. |
| `paths.py` | Per-OS config/data/log/runtime dirs via `platformdirs`. |
| `installers/linux_udev.py` | Renders + installs the udev rule, reloads udevadm. |
| `installers/macos_launchd.py` | Installs the LaunchAgent and runs the resident DiskArbitration watcher. |
| `installers/windows_task.py` | Installs the Scheduled Task + PowerShell WMI watcher. |

## Layout on disk

```
~/.config/dji-auto-upload/                   (Linux; equivalents elsewhere)
├── config.toml                          # 0644, user-editable
└── credentials.toml                     # 0600, wizard-managed

~/.local/share/dji-auto-upload/
└── stage/
    ├── 2026-04-01/
    │   ├── DJI_001.MP4
    │   ├── DJI_002.JPG
    │   └── .uploaded                    # ledger; mtime drives prune retention
    └── 2026-04-02/
        ├── DJI_003.MP4
        └── .uploaded

~/.local/state/dji-auto-upload/
└── dji-auto-upload.log                      # rotated at 5 MB × 5 backups
```

## Why each design call

- **Per-date stages, not per-plug-in stages.** A single replug after a
  multi-day flight should produce one album per *recording* date, not one
  bucket. Grouping by file mtime (drone RTC is synced via DJI Fly) makes
  that automatic.
- **Atomic rename, not byte-level resume.** `shutil.copy2 → os.replace`
  works on three OSes with no extra deps. We accept restarting a single
  partial file because drone clips are bounded (≤ 5 GB) and the stage is
  local SSD.
- **Sentinel-gated prune.** The ledger file's existence and mtime are the
  *only* signals for retention. No ledger means upload was never confirmed,
  so the data is never deleted no matter how old. This is the bash
  version's most important invariant; tests pin it (`tests/unit/test_stage_prune.py`).
- **Two TOML files, not one.** Separating credentials from config makes
  `chmod 0600` natural and `git ignore` unambiguous.
- **udev → systemd-run, not direct exec.** Escapes systemd-udevd's seccomp
  filter (which blocks `mount(2)`) and gives journald logs for free.
- **macOS DiskArbitration, not WatchPaths.** WatchPaths fires on directory
  mtime changes, which is unreliable for mount events on modern macOS.
  DiskArbitration is the supported API and the watcher is idle-cheap.
- **Windows WMI in PowerShell, not pywin32.** PowerShell `Win32_VolumeChangeEvent`
  is built-in, robust, and runs unattended. Adding pywin32 just to get the
  same eventing buys us nothing.
