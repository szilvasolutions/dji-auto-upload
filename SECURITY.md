# Security

## Reporting

If you find a security problem, please report it privately through
[GitHub's advisory form](https://github.com/szilvasolutions/dji-auto-upload/security/advisories/new)
rather than opening a public issue. I'll reply as soon as I can, and I'm happy
to credit you when it's fixed.

## What this tool touches

Worth knowing if you're reviewing it:

* **Cloud credentials are rclone's, not ours.** The tool never sees your cloud
  password or token. It shells out to `rclone`, which keeps its own config.
* **Telegram credentials** (if you enable notifications) live in
  `credentials.toml`, written mode 0600, separate from the main config.
* **`diagnose` redacts** the Telegram token and chat ID before writing its
  bundle. It does include local paths and media filenames, and says so at the
  top of the file.
* **Trigger inputs are validated.** Values from the config are rendered into a
  root-owned udev rule on Linux and a logon script on Windows, so vendor IDs,
  volume labels and paths are checked before they're written.
* **Removable media is mounted `nosuid,nodev,noexec`** on Linux, read-only
  except during the brief cleanup step.

## Deleting things

The tool only deletes a file from your device once a file matching its name and
byte size is confirmed at the destination. If you have found a way to make it
delete something that was not confirmed, that is a security-class bug to me and
I'd like to hear about it.
