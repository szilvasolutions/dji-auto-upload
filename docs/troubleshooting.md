# Troubleshooting

## "rclone: command not found"

Install rclone: <https://rclone.org/install/>. Then re-run `dji-auto-upload setup`.

## `dji-auto-upload run` says "no DJI volume detected"

Three signals the detector tries, in order:

1. **USB vendor ID match.** DJI's vendor ID is `2ca3` for cameras and
   drones. If your hardware uses a different vendor ID, add it to
   `[detect].vendor_ids` in `config.toml`.
2. **Volume label match.** If your drone's card is labelled, edit
   `[detect].volume_labels`.
3. **DCIM folder presence.** A volume is treated as DJI if it has a
   `DCIM/` directory containing at least one subdirectory.

If all three miss, pass the device path explicitly:

```bash
dji-auto-upload run --device /dev/sdc1     # Linux
dji-auto-upload run --device /Volumes/DJI  # macOS
dji-auto-upload run --device E:\           # Windows
```

## Linux: "mount: permission denied" in journalctl

This was the original bash version's biggest bug too. systemd-udevd ships
with a `SystemCallFilter` that blocks the legacy `mount(2)` syscall. The
udev rule we install dispatches via `systemd-run` (no `--scope`, with
`--collect`) to escape the filter. If you see the error:

1. Verify the rule contains `systemd-run` and not a direct exec:
   ```bash
   cat /etc/udev/rules.d/99-dji-auto-upload.rules
   ```
2. Reload udev: `sudo udevadm control --reload && sudo udevadm trigger`
3. Replug the drone.

## Linux: silent failure on subsequent plug-ins

If you hit a script crash, the transient systemd unit may be stuck in
`failed` state. Future replugs would silently fail to start. The udev
rule uses `--collect` to garbage-collect on exit, but if you ever need to
clear a stale unit manually:

```bash
sudo systemctl reset-failed dji-auto-upload-sdc1.service   # or whatever %k
```

## macOS: LaunchAgent loaded but nothing happens

```bash
launchctl print gui/$UID/com.dji-auto-upload.watcher
```

Look for `state = running`. If the state is `not running` or it's been
exiting and KeepAlive-restarting, the `pyobjc-framework-DiskArbitration`
extra may not be installed:

```bash
pip install "dji-auto-upload[macos]"
# or
pip install pyobjc-framework-DiskArbitration
```

Watcher logs:

```bash
tail -f ~/Library/Logs/dji-auto-upload/watcher.err.log
```

## Windows: Scheduled Task installed but not firing

Verify it's enabled and currently running:

```powershell
Get-ScheduledTask -TaskName "DJI Auto Upload Watcher"
schtasks /query /tn "DJI Auto Upload Watcher" /v
```

If it shows ready but not running, sign out and back in (the trigger is
"At log on"), or run manually:

```powershell
schtasks /run /tn "DJI Auto Upload Watcher"
```

PowerShell ExecutionPolicy can also block the watcher script. The Scheduled
Task action sets `-ExecutionPolicy Bypass`, but if the task fails to launch,
verify with:

```powershell
Get-ExecutionPolicy -List
```

## Telegram: messages not arriving

```bash
dji-auto-upload test-notify
```

If that fails:

- Token wrong → re-run `dji-auto-upload setup`.
- Chat ID wrong → make sure you've messaged the bot at least once. The
  setup wizard auto-discovers the chat ID by polling `getUpdates` for 30
  seconds while you message your bot — re-run setup if you skipped it.
- Bot blocked / removed from group → unblock or re-add.

## "Insufficient space on stage_base" abort

The pipeline aborts before any copy if free space is less than (new bytes
+ 512 MB headroom). The pre-copy self-heal already pruned eligible old
stages; if you still hit this, you genuinely have a backlog. Either:

- Free space manually.
- Lower `[retention].stage_days` and run `dji-auto-upload prune`.
- Move `data_dir` to a bigger volume by overriding the platform default
  (Linux: set `XDG_DATA_HOME=/path/to/big/disk`).

## "Drone disconnected during copy"

The drone went away mid-copy (you yanked it, the cable popped, the drone
went to sleep mid-transfer). The local stage is retained — replug and the
next run will skip the already-complete files (size match) and resume the
rest.

## My stage dir isn't being pruned

By design, a stage dir is only eligible for prune if `.uploaded` exists
*and* its mtime is past retention. To force a prune:

```bash
touch -d "30 days ago" ~/.local/share/dji-auto-upload/stage/2026-04-01/.uploaded
dji-auto-upload prune
```

To force a re-upload of a date (bypassing the ledger):

```bash
rm ~/.local/share/dji-auto-upload/stage/2026-04-01/.uploaded
# then plug in the drone (or run manually)
```

## Where are the logs?

| OS | Path |
|---|---|
| Linux (user) | `~/.local/state/dji-auto-upload/dji-auto-upload.log` |
| Linux (udev/root) | `/var/log/dji-auto-upload/dji-auto-upload.log` |
| macOS | `~/Library/Logs/dji-auto-upload/dji-auto-upload.log` |
| Windows | `%LOCALAPPDATA%\dji-auto-upload\logs\dji-auto-upload.log` |

Plus journald on Linux:

```bash
journalctl -u "dji-auto-upload@*" -f
```
