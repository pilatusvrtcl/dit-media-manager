from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SourceConfig:
    name: str
    mount_path: Path
    source_type: str
    ip_address: str = ""
    subfolder: str = ""

    @property
    def effective_root(self) -> Path:
        if self.subfolder:
            return self.mount_path / self.subfolder
        return self.mount_path


@dataclass
class SyncOptions:
    last_24h_only: bool
    active_probe_seconds: float
    active_min_age_seconds: float
    hash_algorithm: str
    retry_on_checksum_fail: int


@dataclass
class RsyncConfig:
    binary: str
    flags: list[str] = field(default_factory=list)


@dataclass
class AppConfig:
    destination_root: Path
    sources: list[SourceConfig]
    sync_options: SyncOptions
    rsync: RsyncConfig
    ui_refresh_seconds: float = 2.0


@dataclass
class FileResult:
    file_name: str
    source: str
    size_bytes: int
    status: str
    detail: str = ""


@dataclass
class SyncSummary:
    total_data_bytes: int = 0
    new_files: int = 0
    elapsed_seconds: float = 0.0


@dataclass
class SyncReport:
    rows: list[FileResult] = field(default_factory=list)
    summary: SyncSummary = field(default_factory=SyncSummary)


def format_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num_bytes)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)}{unit}"
            return f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{num_bytes}B"


def parse_config(raw: dict[str, Any]) -> AppConfig:
    sources = [
        SourceConfig(
            name=entry["name"],
            mount_path=Path(entry["mount_path"]),
            source_type=entry.get("type", "camera"),
            ip_address=entry.get("ip_address", ""),
            subfolder=entry.get("subfolder", ""),
        )
        for entry in raw["sources"]
    ]
    sync_section = raw.get("sync", {})
    rsync_section = raw.get("rsync", {})
    ui_section = raw.get("ui", {})
    return AppConfig(
        destination_root=Path(raw["destination_root"]),
        sources=sources,
        sync_options=SyncOptions(
            last_24h_only=bool(sync_section.get("last_24h_default", False)),
            active_probe_seconds=float(sync_section.get("active_file_probe_seconds", 2.0)),
            active_min_age_seconds=float(sync_section.get("active_file_min_age_seconds", 15.0)),
            hash_algorithm=str(sync_section.get("hash_algorithm", "md5")),
            retry_on_checksum_fail=int(sync_section.get("retry_on_checksum_fail", 1)),
        ),
        rsync=RsyncConfig(
            binary=str(rsync_section.get("binary", "rsync")),
            flags=list(rsync_section.get("flags", ["-a", "--partial", "--inplace"])),
        ),
        ui_refresh_seconds=float(ui_section.get("refresh_seconds", 2.0)),
    )
