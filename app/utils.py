from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from .models import AppConfig, DestinationSmbConfig, SourceConfig, parse_config


USER_CONFIG_DIR = Path.home() / "Library" / "Application Support" / "DIT Media Manager"
USER_CONFIG_FILE = USER_CONFIG_DIR / "settings.user.json"


def resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base_path / relative_path


def load_config(config_file: Optional[Path] = None) -> AppConfig:
    candidate = config_file or Path("settings.json")
    if not candidate.exists():
        bundled = resource_path("settings.json")
        if bundled.exists():
            candidate = bundled
    with candidate.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    merged = raw
    if USER_CONFIG_FILE.exists():
        try:
            with USER_CONFIG_FILE.open("r", encoding="utf-8") as handle:
                user_raw = json.load(handle)
            merged = _deep_merge(raw, user_raw)
        except Exception:
            merged = raw

    return parse_config(merged)


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def get_user_config_file() -> Path:
    return USER_CONFIG_FILE


def save_user_overrides(
    config: AppConfig,
    ip_by_source: dict[str, str],
    mount_by_source: dict[str, str],
    destination_root: str,
) -> Path:
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing_payload: dict = {}
    if USER_CONFIG_FILE.exists():
        try:
            with USER_CONFIG_FILE.open("r", encoding="utf-8") as handle:
                existing_payload = json.load(handle)
        except Exception:
            existing_payload = {}

    payload = {
        "destination_root": destination_root,
        "sources": [
            {
                "name": source.name,
                "mount_path": mount_by_source.get(source.name, str(source.mount_path)),
                "type": source.source_type,
                "subfolder": source.subfolder,
                "ip_address": ip_by_source.get(source.name, source.ip_address),
            }
            for source in config.sources
        ]
    }

    merged_payload = _deep_merge(existing_payload, payload)
    with USER_CONFIG_FILE.open("w", encoding="utf-8") as handle:
        json.dump(merged_payload, handle, indent=2)
    return USER_CONFIG_FILE


def save_source_ip_overrides(config: AppConfig, ip_by_source: dict[str, str]) -> Path:
    mount_by_source = {source.name: str(source.mount_path) for source in config.sources}
    return save_user_overrides(
        config,
        ip_by_source=ip_by_source,
        mount_by_source=mount_by_source,
        destination_root=str(config.destination_root),
    )


def hash_file(path: Path, algorithm: str = "md5", chunk_size: int = 2 * 1024 * 1024) -> str:
    algo = algorithm.lower()
    if algo in {"xxh64", "xxhash"}:
        try:
            import xxhash  # type: ignore

            digest = xxhash.xxh64()
        except ImportError:
            digest = hashlib.md5()
    elif algo == "md5":
        digest = hashlib.md5()
    elif algo in hashlib.algorithms_available:
        digest = hashlib.new(algo)
    else:
        digest = hashlib.md5()

    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def is_mount_available(path: Path) -> bool:
    return path.exists() and os.access(path, os.R_OK)


def is_host_reachable(host: str, timeout_seconds: float = 1.0, port: int = 445) -> bool:
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def is_file_active(path: Path, probe_seconds: float, min_age_seconds: float) -> bool:
    try:
        initial = path.stat()
    except FileNotFoundError:
        return True

    now = time.time()
    if (now - initial.st_mtime) < min_age_seconds:
        return True

    time.sleep(max(probe_seconds, 0.25))
    try:
        later = path.stat()
    except FileNotFoundError:
        return True

    return initial.st_size != later.st_size or int(initial.st_mtime) != int(later.st_mtime)


def _safe_mount_dir_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in name.strip())
    return cleaned or "source"


def try_mount_smb_source(source: SourceConfig) -> Path | None:
    """Attempt to mount an SMB share for a source using its IP + smb_share fields."""
    if not source.ip_address or not source.smb_share:
        return None

    username = source.smb_username or "guest"
    password = source.smb_password or ""

    # mount_smbfs expects //user:pass@host/share syntax.
    userinfo = quote(username, safe="")
    if password:
        userinfo = f"{userinfo}:{quote(password, safe='')}"
    else:
        userinfo = f"{userinfo}:"

    smb_url = f"//{userinfo}@{source.ip_address}/{source.smb_share}"

    candidates = [source.mount_path]
    fallback = Path.home() / "Library" / "Caches" / "DIT Media Manager" / "mounts" / _safe_mount_dir_name(source.name)
    if fallback not in candidates:
        candidates.append(fallback)

    for mount_point in candidates:
        mounted_root = mount_point / source.subfolder if source.subfolder else mount_point
        # Reuse an existing mount instead of failing on "File exists".
        if os.path.ismount(mount_point) and is_mount_available(mounted_root):
            return mounted_root

        try:
            mount_point.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue

        proc = subprocess.run(
            ["mount_smbfs", smb_url, str(mount_point)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            if os.path.ismount(mount_point) and is_mount_available(mounted_root):
                return mounted_root
            continue

        if is_mount_available(mounted_root):
            return mounted_root

    return None


def try_mount_smb_destination(config: AppConfig) -> Path | None:
    destination = config.destination_smb
    if destination is None:
        return None

    host = destination.host.strip()
    share = destination.share.strip()
    if not host or not share:
        return None

    username = destination.username or "guest"
    password = destination.password or ""
    configured_mount = destination.mount_path
    fallback_mount = Path.home() / "Library" / "Caches" / "DIT Media Manager" / "mounts" / "destination"
    volume_mount = Path("/Volumes") / share

    mount_candidates: list[Path] = []
    if str(configured_mount).strip() != "":
        mount_candidates.append(configured_mount)
    if volume_mount not in mount_candidates:
        mount_candidates.append(volume_mount)
    if fallback_mount not in mount_candidates:
        mount_candidates.append(fallback_mount)

    userinfo = quote(username, safe="")
    if password:
        userinfo = f"{userinfo}:{quote(password, safe='')}"
    else:
        userinfo = f"{userinfo}:"
    smb_url = f"//{userinfo}@{host}/{share}"

    for mount_point in mount_candidates:
        if os.path.ismount(mount_point) and is_mount_available(mount_point):
            return mount_point

    mount_binary = shutil.which("mount_smbfs") or "/sbin/mount_smbfs"

    for mount_point in mount_candidates:
        try:
            mount_point.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue

        try:
            proc = subprocess.run(
                [mount_binary, smb_url, str(mount_point)],
                capture_output=True,
                text=True,
            )
        except OSError:
            continue

        if proc.returncode == 0 and is_mount_available(mount_point):
            return mount_point
        if os.path.ismount(mount_point) and is_mount_available(mount_point):
            return mount_point

    return None
