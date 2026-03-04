from __future__ import annotations

import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from .models import AppConfig, FileResult, SyncReport, SyncSummary, format_size
from .utils import hash_file, is_file_active, is_mount_available

StatusCallback = Callable[[str, str], None]
ResultCallback = Callable[[FileResult], None]


class SyncEngine:
    def __init__(
        self,
        config: AppConfig,
        on_source_status: StatusCallback | None = None,
        on_file_result: ResultCallback | None = None,
    ) -> None:
        self.config = config
        self.on_source_status = on_source_status
        self.on_file_result = on_file_result
        self._lock = threading.Lock()

    def run(self, last_24h_only: bool) -> SyncReport:
        started = time.time()
        report = SyncReport(rows=[], summary=SyncSummary())
        self.config.destination_root.mkdir(parents=True, exist_ok=True)

        with ThreadPoolExecutor(max_workers=len(self.config.sources)) as pool:
            futures = [
                pool.submit(self._sync_source, source, last_24h_only, report)
                for source in self.config.sources
            ]
            for future in futures:
                future.result()

        report.summary.elapsed_seconds = time.time() - started
        return report

    def _emit_status(self, source_name: str, status: str) -> None:
        if self.on_source_status:
            self.on_source_status(source_name, status)

    def _emit_row(self, row: FileResult) -> None:
        if self.on_file_result:
            self.on_file_result(row)

    def _add_row(self, report: SyncReport, row: FileResult) -> None:
        with self._lock:
            report.rows.append(row)
            self._emit_row(row)

    def _sync_source(self, source, last_24h_only: bool, report: SyncReport) -> None:
        root = source.effective_root
        source_dest_root = self.config.destination_root / source.name

        if not is_mount_available(root):
            self._emit_status(source.name, "Offline")
            return

        self._emit_status(source.name, "Scanning")
        threshold = time.time() - (24 * 60 * 60)

        for dirpath, _, filenames in os.walk(root):
            base_dir = Path(dirpath)
            for filename in filenames:
                source_file = base_dir / filename
                rel_path = source_file.relative_to(root)
                destination_file = source_dest_root / rel_path

                try:
                    source_size = source_file.stat().st_size
                    source_mtime = source_file.stat().st_mtime
                except FileNotFoundError:
                    continue

                if last_24h_only and source_mtime < threshold:
                    continue

                if is_file_active(
                    source_file,
                    self.config.sync_options.active_probe_seconds,
                    self.config.sync_options.active_min_age_seconds,
                ):
                    self._add_row(
                        report,
                        FileResult(
                            file_name=str(rel_path),
                            source=source.name,
                            size_bytes=source_size,
                            status="Skipped",
                            detail="Active record detected",
                        ),
                    )
                    continue

                if destination_file.exists() and destination_file.stat().st_size == source_size:
                    self._add_row(
                        report,
                        FileResult(
                            file_name=str(rel_path),
                            source=source.name,
                            size_bytes=source_size,
                            status="Skipped",
                            detail="Already exists (same size)",
                        ),
                    )
                    continue

                destination_file.parent.mkdir(parents=True, exist_ok=True)
                self._emit_status(source.name, f"Copying {rel_path.name}")

                copied = self._copy_with_rsync(source_file, destination_file)
                if not copied:
                    self._add_row(
                        report,
                        FileResult(
                            file_name=str(rel_path),
                            source=source.name,
                            size_bytes=source_size,
                            status="Failed",
                            detail="rsync transfer failed",
                        ),
                    )
                    continue

                verified = self._verify_with_retry(source_file, destination_file)
                if verified:
                    with self._lock:
                        report.summary.new_files += 1
                        report.summary.total_data_bytes += source_size
                    self._add_row(
                        report,
                        FileResult(
                            file_name=str(rel_path),
                            source=source.name,
                            size_bytes=source_size,
                            status="Verified",
                            detail="Checksum OK",
                        ),
                    )
                else:
                    self._add_row(
                        report,
                        FileResult(
                            file_name=str(rel_path),
                            source=source.name,
                            size_bytes=source_size,
                            status="Failed",
                            detail="Checksum mismatch after retry",
                        ),
                    )

        self._emit_status(source.name, "Idle")

    def _copy_with_rsync(self, source_file: Path, destination_file: Path) -> bool:
        command = [
            self.config.rsync.binary,
            *self.config.rsync.flags,
            str(source_file),
            str(destination_file),
        ]
        proc = subprocess.run(command, capture_output=True, text=True)
        return proc.returncode == 0

    def _verify_with_retry(self, source_file: Path, destination_file: Path) -> bool:
        attempts = 1 + max(0, self.config.sync_options.retry_on_checksum_fail)
        for index in range(attempts):
            source_hash = hash_file(source_file, self.config.sync_options.hash_algorithm)
            destination_hash = hash_file(destination_file, self.config.sync_options.hash_algorithm)
            if source_hash == destination_hash:
                return True
            if index < attempts - 1:
                self._copy_with_rsync(source_file, destination_file)
        return False


def build_summary_text(report: SyncReport) -> str:
    elapsed = int(report.summary.elapsed_seconds)
    minutes, seconds = divmod(elapsed, 60)
    return (
        f"Total Data: {format_size(report.summary.total_data_bytes)} | "
        f"New Files: {report.summary.new_files} | "
        f"Time Elapsed: {minutes}m {seconds:02d}s"
    )
