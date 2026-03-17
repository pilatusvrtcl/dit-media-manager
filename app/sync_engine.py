from __future__ import annotations

import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

from .models import AppConfig, FileResult, SyncReport, SyncSummary, format_size
from .utils import hash_file, is_file_active, is_mount_available, try_mount_smb_destination, try_mount_smb_source

StatusCallback = Callable[[str, str], None]
ResultCallback = Callable[[FileResult], None]

SKIP_FILE_NAMES = {
    ".ds_store",
    "rootca.crt",
    "magician launcher.exe",
}

SKIP_DIR_NAMES = {
    "magician launcher.app",
}


class ImportAlreadyExistsError(RuntimeError):
    pass


class DestinationUnavailableError(RuntimeError):
    pass


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
        self._single_file_reserved = False

    def run(
        self,
        last_24h_only: bool,
        force_overwrite: bool = False,
        one_file_only: bool = False,
    ) -> SyncReport:
        started = time.time()
        report = SyncReport(rows=[], summary=SyncSummary())

        mounted_destination = try_mount_smb_destination(self.config)
        if self.config.destination_smb is not None and mounted_destination is None:
            raise DestinationUnavailableError(
                "Destination SMB is not available or could not be mounted. "
                "Check destination network/mount settings and try again."
            )

        if mounted_destination:
            destination_root_text = str(self.config.destination_root)
            if not destination_root_text.startswith(str(mounted_destination)):
                self.config.destination_root = mounted_destination

        already_imported_recording = self.find_already_imported_recording_name()
        if already_imported_recording and not force_overwrite:
            raise ImportAlreadyExistsError(
                f"Recording {already_imported_recording} appears to be already imported for all sources. "
                "Enable overrule to import again."
            )

        pull_folder = datetime.now().strftime("%Y-%m-%d_%H-%M")
        destination_base = self._resolve_destination_base()
        run_destination_root = destination_base / pull_folder
        self._ensure_destination_folder(run_destination_root)

        with ThreadPoolExecutor(max_workers=len(self.config.sources)) as pool:
            futures = [
                pool.submit(
                    self._sync_source,
                    source,
                    last_24h_only,
                    report,
                    run_destination_root,
                    one_file_only,
                )
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

    def _sync_source(
        self,
        source,
        last_24h_only: bool,
        report: SyncReport,
        run_destination_root: Path,
        one_file_only: bool,
    ) -> None:
        root = source.effective_root
        source_dest_root = run_destination_root / source.name

        if not is_mount_available(root):
            self._emit_status(source.name, "Mounting")
            mounted_root = try_mount_smb_source(source)
            if mounted_root:
                root = mounted_root
                self._emit_status(source.name, "Mounted")
            else:
                self._emit_status(source.name, "Offline")
                return

        self._emit_status(source.name, "Scanning")
        threshold = time.time() - (24 * 60 * 60)

        for dirpath, dirnames, filenames in os.walk(root):
            # Prune ignored directories so os.walk does not descend into them.
            dirnames[:] = [d for d in dirnames if not self._should_skip_dir_name(d)]
            base_dir = Path(dirpath)
            for filename in filenames:
                if self._should_skip_file_name(filename):
                    continue

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

                if self._already_imported_for_source(source.name, rel_path, source_size):
                    self._add_row(
                        report,
                        FileResult(
                            file_name=str(rel_path),
                            source=source.name,
                            size_bytes=source_size,
                            status="Skipped",
                            detail="Already imported (previous pull)",
                        ),
                    )
                    continue

                if one_file_only and not self._reserve_single_file_slot():
                    self._emit_status(source.name, "Idle")
                    return

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

                if one_file_only:
                    self._emit_status(source.name, "Idle")
                    return

        self._emit_status(source.name, "Idle")

    def find_already_imported_recording_name(self) -> str | None:
        atem_source = next((s for s in self.config.sources if s.source_type == "atem_iso"), None)
        if atem_source is None:
            return None

        atem_root = atem_source.effective_root
        if not is_mount_available(atem_root):
            mounted_root = try_mount_smb_source(atem_source)
            if mounted_root:
                atem_root = mounted_root
            else:
                return None

        try:
            rec_dirs = [
                item
                for item in atem_root.iterdir()
                if item.is_dir() and item.name.startswith("REC_")
            ]
        except OSError:
            return None

        if not rec_dirs:
            return None

        rec_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        latest_name = rec_dirs[0].name
        if self._recording_present_for_all_sources(latest_name):
            return latest_name
        return None

    def _recording_present_for_all_sources(self, recording_name: str) -> bool:
        base = self.config.destination_root
        for source in self.config.sources:
            if not self._recording_present_for_source(base, source.name, recording_name):
                return False
        return True

    def _recording_present_for_source(self, base: Path, source_name: str, recording_name: str) -> bool:
        candidate_roots = [base / source_name]
        try:
            candidate_roots.extend(
                item / source_name
                for item in base.iterdir()
                if item.is_dir()
            )
        except OSError:
            pass

        for source_root in candidate_roots:
            if not source_root.exists() or not source_root.is_dir():
                continue

            if (source_root / recording_name).exists():
                return True

            try:
                for entry in source_root.iterdir():
                    if entry.name.startswith(recording_name):
                        return True
            except OSError:
                continue

        return False

    def _already_imported_for_source(self, source_name: str, rel_path: Path, source_size: int) -> bool:
        base = self.config.destination_root
        candidate_roots = [base / source_name]
        try:
            candidate_roots.extend(
                item / source_name
                for item in base.iterdir()
                if item.is_dir()
            )
        except OSError:
            pass

        for source_root in candidate_roots:
            candidate = source_root / rel_path
            if not candidate.exists() or not candidate.is_file():
                continue
            try:
                if candidate.stat().st_size == source_size:
                    return True
            except OSError:
                continue
        return False

    def _reserve_single_file_slot(self) -> bool:
        with self._lock:
            if self._single_file_reserved:
                return False
            self._single_file_reserved = True
            return True

    def _ensure_destination_folder(self, run_destination_root: Path) -> None:
        for attempt in range(2):
            try:
                run_destination_root.mkdir(parents=True, exist_ok=True)
                return
            except OSError as exc:
                if attempt == 0 and self.config.destination_smb is not None:
                    mounted_destination = try_mount_smb_destination(self.config)
                    if mounted_destination:
                        destination_root_text = str(self.config.destination_root)
                        if not destination_root_text.startswith(str(mounted_destination)):
                            self.config.destination_root = mounted_destination
                        # Rebuild target path after remount and retry once.
                        run_destination_root = self._resolve_destination_base() / run_destination_root.name
                        continue
                details = self._destination_diagnostics(run_destination_root.parent)
                raise DestinationUnavailableError(
                    f"Unable to create destination folder: {run_destination_root}\n{exc}\n{details}"
                ) from exc

    def _resolve_destination_base(self) -> Path:
        candidates = [self.config.destination_root]
        try:
            children = [
                entry
                for entry in self.config.destination_root.iterdir()
                if entry.is_dir() and not entry.name.startswith(".")
            ]
            children.sort(key=self._safe_mtime, reverse=True)
            candidates.extend(children)
        except OSError:
            pass

        for candidate in candidates:
            if self._can_create_probe(candidate):
                if candidate != self.config.destination_root:
                    self.config.destination_root = candidate
                return candidate

        details = self._destination_diagnostics(self.config.destination_root)
        raise DestinationUnavailableError(
            "No writable destination base path found on mounted remote storage.\n"
            f"Configured destination: {self.config.destination_root}\n{details}"
        )

    def _safe_mtime(self, path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def _can_create_probe(self, base_path: Path) -> bool:
        if not base_path.exists() or not base_path.is_dir():
            return False
        probe_path = base_path / f".dit_write_probe_{os.getpid()}"
        try:
            probe_path.mkdir(parents=False, exist_ok=False)
            probe_path.rmdir()
            return True
        except OSError:
            return False

    def _destination_diagnostics(self, destination_base: Path) -> str:
        exists = destination_base.exists()
        is_dir = destination_base.is_dir() if exists else False
        is_mount = os.path.ismount(destination_base) if exists else False
        writable = os.access(destination_base, os.W_OK) if exists else False
        return (
            "Destination diagnostics: "
            f"exists={exists}, is_dir={is_dir}, is_mount={is_mount}, writable={writable}"
        )

    def _should_skip_dir_name(self, name: str) -> bool:
        return name.lower() in SKIP_DIR_NAMES

    def _should_skip_file_name(self, name: str) -> bool:
        lowered = name.lower()
        if lowered in SKIP_FILE_NAMES:
            return True
        # Skip macOS metadata sidecar files.
        return name.startswith("._")

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
