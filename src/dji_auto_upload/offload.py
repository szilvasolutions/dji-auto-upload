"""Pipeline orchestrator — the Python port of dji-auto-upload.sh.

Stages: detect → inventory → precheck → copy (per date) → upload (per date) →
cleanup. Each stage is a method on OffloadRun; the public entry point is
`OffloadRun(...).execute()`. Notifications fire at the same points the bash
version emits them.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .cleanup import (
    delete_files,
    drone_clock_sane,
    files_older_than,
    prune_stage,
    select_deletable,
)
from .config import Config
from .copy import copy_files
from .errors import (
    DroneDisconnected,
    InsufficientSpace,
    OffloadError,
)
from .inventory import FileInfo, group_by_date, walk_dcim
from .ledger import append_to_ledger, files_needing_upload, ledger_path, read_ledger
from .logging_setup import tail_log
from .notifier import Notifier, escape
from .platform_glue import eject_volume, inhibit_sleep, remount_ro, remount_rw
from .stage import existing_stage_dirs, stage_dir_for
from .upload import remote_configured, remote_reachable, upload_files

log = logging.getLogger(__name__)


@dataclass
class OffloadRun:
    config: Config
    notifier: Notifier
    volume: Path
    dcim: Path
    dry_run: bool = False

    # Every media folder on the card. Defaults to [dcim] so existing callers
    # keep working; the CLI passes the full list from find_dcim_dirs().
    dcim_dirs: list[Path] | None = None

    stage_base: Path = field(init=False)
    new_files_by_date: dict[date, list[FileInfo]] = field(init=False, default_factory=dict)
    total_new_count: int = 0
    total_new_bytes: int = 0
    copied_ok: int = 0

    def __post_init__(self) -> None:
        self.stage_base = self.config.paths.stage_dir
        if not self.dcim_dirs:
            self.dcim_dirs = [self.dcim]

    # ---- Public API ----------------------------------------------------

    def execute(self) -> None:
        if self.dry_run:
            self._dry_run_report()
            return
        # "Plug in and walk away" is exactly when a laptop idle-sleeps and
        # freezes the transfer, so hold a sleep inhibitor for the whole run.
        with inhibit_sleep(enabled=self.config.behaviour.inhibit_sleep):
            self.notifier.send(
                "start",
                f"🛸 DJI volume detected at <code>{escape(str(self.volume))}</code>. "
                "Starting offload…",
            )
            self._inventory()
            self._pre_copy_prune()
            self._precheck_disk_space()
            self._copy()
            self._upload()
            self._cleanup_drone()
            ejected = self._eject()
            self._post_run_prune()
            done_msg = "🏁 Offload run finished."
            if ejected:
                done_msg += " Drone ejected — safe to unplug. 🔌"
            self.notifier.send("done", done_msg)

    # ---- Stages --------------------------------------------------------

    def _inventory(self) -> None:
        dirs = self.dcim_dirs or [self.dcim]
        log.info("stage=inventory dcim=%s", ", ".join(str(p) for p in dirs))
        all_files: list[FileInfo] = []
        for media_dir in dirs:
            all_files.extend(walk_dcim(media_dir, self.config.detect.extensions))
        if not all_files:
            # An empty card is the normal state after a successful offload with
            # drone cleanup on — not a failure. Carry on so any still-pending
            # staged uploads get retried, then finish quietly.
            log.info("no media files on %s — nothing new to copy", self.dcim)
            self.new_files_by_date = {}
            self.total_new_count = 0
            self.total_new_bytes = 0
            self.notifier.send(
                "info", "ℹ️ No media on the drone — checking for pending uploads."
            )
            return

        by_date = group_by_date(all_files)
        new_by_date: dict[date, list[FileInfo]] = {}
        total_new_count = 0
        total_new_bytes = 0

        for d, files in by_date.items():
            stage = stage_dir_for(self.stage_base, d)
            pending = []
            for fi in files:
                dest = stage / fi.path.name
                if not dest.exists() or dest.stat().st_size != fi.size:
                    pending.append(fi)
                    total_new_count += 1
                    total_new_bytes += fi.size
            if pending:
                new_by_date[d] = pending

        self.new_files_by_date = new_by_date
        self.total_new_count = total_new_count
        self.total_new_bytes = total_new_bytes

        summary = ", ".join(
            f"{d} ({len(files)})" for d, files in sorted(new_by_date.items())
        )
        mb = total_new_bytes // 1024 // 1024

        if total_new_count == 0:
            self.notifier.send(
                "info",
                "ℹ️ Nothing new to copy — checking uploads for existing local stage.",
            )
        else:
            self.notifier.send(
                "copy",
                f"📥 Copying <b>{total_new_count}</b> file(s) (~{mb} MB) "
                f"across <b>{len(new_by_date)}</b> recording date(s): {summary}",
            )

    def _pre_copy_prune(self) -> None:
        """Self-heal: free space taken by ancient stages before checking precheck."""
        log.info("stage=prune_stage_pre")
        prune_stage(self.stage_base, self.config.retention.stage_days)

    def _precheck_disk_space(self) -> None:
        if self.total_new_bytes <= 0:
            return
        log.info("stage=precheck")
        free = shutil.disk_usage(self.stage_base).free
        need = self.total_new_bytes + self.config.behaviour.disk_headroom_mb * 1024 * 1024
        if free < need:
            free_mb = free // 1024 // 1024
            need_mb = need // 1024 // 1024
            raise InsufficientSpace(
                f"not enough free space on {self.stage_base}: "
                f"have {free_mb} MB, need {need_mb} MB"
            )

    def _copy(self) -> None:
        if not self.new_files_by_date:
            return
        log.info("stage=copy")
        progress_count = 0

        def _on_progress(fi: FileInfo) -> None:
            nonlocal progress_count
            progress_count += 1
            log.info(
                "copied %d/%d: %s (%.1f MB)",
                progress_count, self.total_new_count, fi.name, fi.size / 1024 / 1024,
            )

        for d, files in self.new_files_by_date.items():
            stage = stage_dir_for(self.stage_base, d)
            try:
                result = copy_files(
                    files,
                    stage,
                    verify=self.config.behaviour.verify_after_copy,
                    timeout_sec=self.config.behaviour.copy_timeout_sec,
                    on_progress=_on_progress,
                )
            except DroneDisconnected:
                # Stage is retained — replug to resume.
                self.notifier.send(
                    "fail",
                    f"❌ Drone disconnected during copy after {self.copied_ok}/"
                    f"{self.total_new_count} file(s). Stage retained — replug to resume.",
                )
                raise
            self.copied_ok += result.copied

        # Tally what's actually on disk for the post-copy notification.
        total_files = 0
        total_mb = 0
        dates = sorted(self.new_files_by_date.keys())
        for d in dates:
            stage = stage_dir_for(self.stage_base, d)
            for f in stage.iterdir():
                if f.is_file() and not f.name.startswith("."):
                    total_files += 1
                    total_mb += f.stat().st_size // 1024 // 1024
        self.notifier.send(
            "done_copy",
            f"✅ Local copy done. <b>{total_files}</b> file(s), {total_mb} MB on disk "
            f"across {len(dates)} date(s).",
        )

    def _upload(self) -> None:
        log.info("stage=upload")
        remote_name = self.config.remote.name
        if not remote_configured(remote_name):
            raise OffloadError(
                f"rclone remote {remote_name!r} is not configured — "
                f"run `rclone config`, or fix the name in your config file",
                stage="upload",
            )
        # A slow probe is not a broken remote. A cold cloud remote can take tens
        # of seconds to answer, and failing the whole run on that would strand
        # footage that rclone (with its own retries) would have uploaded fine.
        if not remote_reachable(remote_name):
            log.warning(
                "remote %r did not answer the reachability probe in time — "
                "continuing anyway; rclone will report the real error if there is one",
                remote_name,
            )

        all_dates = [d.name for d in existing_stage_dirs(self.stage_base)]
        ok: list[str] = []
        skipped: list[str] = []
        failed: list[tuple[str, int, int, int]] = []

        # First pass: figure out what to upload per date.
        per_date: dict[str, list[str]] = {}
        for d_name in all_dates:
            stage = self.stage_base / d_name
            pending = files_needing_upload(stage)
            if pending:
                per_date[d_name] = pending
            else:
                skipped.append(f"DJI-{d_name}")

        if not per_date:
            self.notifier.send(
                "info",
                f"☁️ Nothing to upload — all <b>{len(all_dates)}</b> album(s) already uploaded.",
            )
            return

        total = sum(len(v) for v in per_date.values())
        summary = ", ".join(f"DJI-{d} ({len(per_date[d])})" for d in sorted(per_date))
        self.notifier.send(
            "upload",
            f"☁️ Uploading <b>{total}</b> file(s) across {len(per_date)} of "
            f"{len(all_dates)} album(s): {summary}",
        )

        for d_name, basenames in per_date.items():
            stage = self.stage_base / d_name
            remote_path = self.config.remote.path_template.format(date=d_name)
            result = upload_files(
                stage,
                basenames,
                remote_path,
                remote=self.config.remote,
                behaviour=self.config.behaviour,
            )
            album = f"DJI-{d_name}"
            # Ledger every confirmed file, even from a partially-failed batch —
            # the per-file dedup is what keeps Google Photos duplicate-free.
            if result.succeeded:
                append_to_ledger(stage, result.succeeded)
                # Bump sentinel mtime so prune retention starts ticking from now.
                ledger_path(stage).touch()
            if result.rc == 0:
                ok.append(f"{album} ({len(result.succeeded)})")
            else:
                failed.append((album, result.rc, len(result.succeeded), len(result.failed)))

        if failed:
            ok_str = ", ".join(ok) if ok else "none"
            sk_str = ", ".join(skipped) if skipped else "none"
            fail_str = ", ".join(
                f"{a} (exit {rc}, {n_ok} uploaded, {n_bad} failed)"
                for a, rc, n_ok, n_bad in failed
            )
            raise OffloadError(
                f"upload partially failed — OK: {ok_str}; skipped: {sk_str}; failed: {fail_str}",
                stage="upload",
            )

        self.notifier.send(
            "done_upload",
            "☁️ Upload complete. New: " + (", ".join(ok) if ok else "none")
            + ". Skipped: " + (", ".join(skipped) if skipped else "none") + ".",
        )

    def _cleanup_drone(self) -> None:
        if not self.config.behaviour.delete_drone_files:
            return
        days = self.config.retention.drone_days
        if days <= 0:
            return
        log.info("stage=cleanup_drone")
        candidates = [
            f for d in (self.dcim_dirs or [self.dcim]) for f in files_older_than(d, days)
        ]
        if not candidates:
            self.notifier.send(
                "cleanup", f"🧹 No files older than {days} day(s) on drone."
            )
            return
        # Sanity-check every file on the card, not just the age-eligible ones:
        # a future-dated file is by definition never a candidate, yet it is the
        # clearest evidence the RTC is wrong.
        if not drone_clock_sane(self._all_drone_files()):
            self.notifier.send(
                "cleanup",
                "🧹 Drone clock looks wrong (files dated in the future or >10 years "
                "ago) — skipping drone cleanup this run.",
            )
            return

        # Age makes a file a candidate; only ledger-confirmed uploads (or their
        # sidecars) are actually deleted. This is what makes "we only delete
        # what's safely in the cloud" literally true, file by file.
        uploaded = self._ledgered_basenames()
        to_delete = select_deletable(
            candidates, uploaded, self.config.detect.sidecar_extensions
        )
        kept = len(candidates) - len(to_delete)
        if not to_delete:
            self.notifier.send(
                "cleanup",
                f"🧹 {len(candidates)} old file(s) on drone, but none confirmed "
                "uploaded — nothing deleted.",
            )
            return

        if not remount_rw(self.volume):
            self.notifier.send(
                "cleanup",
                "🧹 Could not remount drone read-write — skipping cleanup this run.",
            )
            return
        try:
            n = delete_files(to_delete)
        finally:
            remount_ro(self.volume)

        msg = f"🧹 Removed <b>{n}</b> uploaded file(s) older than {days} day(s) from drone."
        if kept:
            msg += f" Kept {kept} file(s) not yet confirmed uploaded."
        self.notifier.send("cleanup", msg)

    def _all_drone_files(self) -> list[Path]:
        """Every file in every media folder on the card, regardless of extension."""
        out: list[Path] = []
        for d in self.dcim_dirs or [self.dcim]:
            try:
                out.extend(f for f in d.iterdir() if f.is_file())
            except OSError:
                continue
        return out

    def _ledgered_basenames(self) -> set[str]:
        """Union of every stage dir's .uploaded ledger."""
        out: set[str] = set()
        for d in existing_stage_dirs(self.stage_base):
            out |= read_ledger(d)
        return out

    def _eject(self) -> bool:
        if not self.config.behaviour.eject_when_done:
            return False
        ok = eject_volume(self.volume)
        if not ok:
            log.warning(
                "could not auto-eject %s — eject manually before unplugging", self.volume
            )
        return ok

    def _post_run_prune(self) -> None:
        log.info("stage=prune_stage_post")
        prune_stage(self.stage_base, self.config.retention.stage_days)

    # ---- Dry run --------------------------------------------------------

    def _dry_run_report(self) -> list[str]:
        """Compute the full plan (copy / upload / delete) without changing
        anything, print it, and return the lines (for tests)."""
        self._inventory()

        lines: list[str] = [f"DRY RUN — no changes will be made. Volume: {self.volume}"]

        if self.new_files_by_date:
            for d, files in sorted(self.new_files_by_date.items()):
                mb = sum(f.size for f in files) // 1024 // 1024
                lines.append(f"COPY   {d}: {len(files)} file(s), ~{mb} MB")
        else:
            lines.append("COPY   nothing new to copy")

        pending_any = False
        for stage in existing_stage_dirs(self.stage_base):
            pending = files_needing_upload(stage)
            if pending:
                pending_any = True
                lines.append(f"UPLOAD {stage.name}: {len(pending)} staged file(s) pending")
        if self.new_files_by_date:
            lines.append("UPLOAD plus every file copied above")
        elif not pending_any:
            lines.append("UPLOAD nothing pending")

        behaviour = self.config.behaviour
        days = self.config.retention.drone_days
        if behaviour.delete_drone_files and days > 0:
            candidates = files_older_than(self.dcim, days)
            if candidates and drone_clock_sane(self._all_drone_files()):
                deletable = select_deletable(
                    candidates, self._ledgered_basenames(),
                    self.config.detect.sidecar_extensions,
                )
                lines.append(
                    f"DELETE drone: {len(deletable)} of {len(candidates)} old file(s) "
                    "are confirmed uploaded and would be deleted"
                )
                lines.extend(f"       - {f.name}" for f in deletable[:20])
            else:
                lines.append("DELETE drone: nothing eligible")
        else:
            lines.append("DELETE drone: disabled (delete_drone_files=false or drone_days=0)")

        for line in lines:
            log.info("dry-run: %s", line)
        print("\n".join(lines))
        return lines


def report_failure(notifier: Notifier, exc: BaseException, log_file: Path | None) -> None:
    """Top-level handler — fires `fail` notify with the last 25 log lines."""
    stage = getattr(exc, "stage", "unknown")
    tail = tail_log(log_file, n=25) if log_file else ""
    safe_tail = escape(tail) if tail else ""
    body = (
        f"❌ DJI offload failed in stage <code>{escape(stage)}</code>: "
        f"{escape(type(exc).__name__)}: {escape(str(exc))}"
    )
    if safe_tail:
        body += f"\nLast log lines:\n<pre>{safe_tail}</pre>"
    notifier.send("fail", body)
