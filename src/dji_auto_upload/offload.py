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
    files_older_than,
    prune_stage,
)
from .config import Config
from .copy import copy_files
from .errors import (
    DroneDisconnected,
    InsufficientSpace,
    InventoryError,
    OffloadError,
)
from .inventory import FileInfo, group_by_date, walk_dcim
from .ledger import append_to_ledger, files_needing_upload, ledger_path
from .logging_setup import tail_log
from .notifier import Notifier, escape
from .stage import existing_stage_dirs, stage_dir_for
from .upload import remote_reachable, upload_files

log = logging.getLogger(__name__)


@dataclass
class OffloadRun:
    config: Config
    notifier: Notifier
    volume: Path
    dcim: Path

    stage_base: Path = field(init=False)
    new_files_by_date: dict[date, list[FileInfo]] = field(init=False, default_factory=dict)
    total_new_count: int = 0
    total_new_bytes: int = 0
    copied_ok: int = 0

    def __post_init__(self) -> None:
        self.stage_base = self.config.paths.stage_dir

    # ---- Public API ----------------------------------------------------

    def execute(self) -> None:
        self.notifier.send(
            "start",
            f"🛸 DJI volume detected at <code>{escape(str(self.volume))}</code>. Starting offload…",
        )
        self._inventory()
        self._pre_copy_prune()
        self._precheck_disk_space()
        self._copy()
        self._upload()
        self._cleanup_drone()
        self._post_run_prune()
        self.notifier.send("done", "🏁 Offload run finished.")

    # ---- Stages --------------------------------------------------------

    def _inventory(self) -> None:
        log.info("stage=inventory dcim=%s", self.dcim)
        all_files = walk_dcim(self.dcim, self.config.detect.extensions)
        if not all_files:
            raise InventoryError(
                f"no media files found in {self.dcim} — not a DJI media volume?"
            )

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
        for d, files in self.new_files_by_date.items():
            stage = stage_dir_for(self.stage_base, d)
            try:
                result = copy_files(
                    files,
                    stage,
                    verify=self.config.behaviour.verify_after_copy,
                    timeout_sec=self.config.behaviour.copy_timeout_sec,
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
        if not remote_reachable(self.config.remote.name):
            raise OffloadError(
                f"rclone remote {self.config.remote.name!r} not reachable — "
                f"run `rclone config` or check credentials",
                stage="upload",
            )

        all_dates = [d.name for d in existing_stage_dirs(self.stage_base)]
        ok: list[str] = []
        skipped: list[str] = []
        failed: list[tuple[str, int]] = []

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
            if result.rc == 0:
                append_to_ledger(stage, result.succeeded)
                # Bump sentinel mtime so prune retention starts ticking from now.
                ledger_path(stage).touch()
                ok.append(f"{album} ({len(result.succeeded)})")
            else:
                failed.append((album, result.rc))

        if failed:
            ok_str = ", ".join(ok) if ok else "none"
            sk_str = ", ".join(skipped) if skipped else "none"
            fail_str = ", ".join(f"{a} (exit {rc})" for a, rc in failed)
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
        if self.config.retention.drone_days <= 0:
            return
        log.info("stage=cleanup_drone")
        old = files_older_than(self.dcim, self.config.retention.drone_days)
        if not old:
            self.notifier.send(
                "cleanup",
                f"🧹 No files older than {self.config.retention.drone_days} day(s) on drone.",
            )
            return
        n = delete_files(old)
        self.notifier.send(
            "cleanup",
            f"🧹 Removed <b>{n}</b> file(s) older than "
            f"{self.config.retention.drone_days} day(s) from drone.",
        )

    def _post_run_prune(self) -> None:
        log.info("stage=prune_stage_post")
        prune_stage(self.stage_base, self.config.retention.stage_days)


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
