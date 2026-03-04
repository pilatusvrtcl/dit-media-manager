from __future__ import annotations

import json
import platform
import shutil
import subprocess
import tempfile
import tkinter as tk
import tkinter.messagebox as mbox
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Optional

from app.utils import resource_path

APP_BUNDLE_NAME = "DIT Media Manager.app"
DEFAULT_GITHUB_REPO = "pilatusvrtcl/dit-media-manager"
STATE_DIR = Path.home() / "Library" / "Application Support" / "DIT Media Launcher"
STATE_FILE = STATE_DIR / "state.json"


def load_settings() -> dict[str, Any]:
    candidate = Path("settings.json")
    if not candidate.exists():
        bundled = resource_path("settings.json")
        if bundled.exists():
            candidate = bundled
    with candidate.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)


def fetch_latest_release(repo: str) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "dit-media-launcher",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def pick_release_asset(release: dict[str, Any], name_contains: str) -> Optional[dict[str, Any]]:
    assets = release.get("assets", [])
    machine = platform.machine().lower()

    zip_assets = [asset for asset in assets if str(asset.get("name", "")).lower().endswith(".zip")]
    if name_contains:
        filtered = [asset for asset in zip_assets if name_contains.lower() in str(asset.get("name", "")).lower()]
        if filtered:
            zip_assets = filtered

    if not zip_assets:
        return None

    for asset in zip_assets:
        asset_name = str(asset.get("name", "")).lower()
        if machine in asset_name:
            return asset

    return zip_assets[0]


def find_installed_app(preferred_install_dir: str = "") -> Optional[Path]:
    candidates: list[Path] = []
    if preferred_install_dir:
        candidates.append(Path(preferred_install_dir).expanduser() / APP_BUNDLE_NAME)

    candidates.extend(
        [
            Path("/Applications") / APP_BUNDLE_NAME,
            Path.home() / "Applications" / APP_BUNDLE_NAME,
            Path(__file__).resolve().parent.parent / "dist" / APP_BUNDLE_NAME,
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def install_downloaded_app(zip_url: str, install_dir: str) -> Path:
    target_dir = Path(install_dir).expanduser() if install_dir else (Path.home() / "Applications")
    target_dir.mkdir(parents=True, exist_ok=True)
    target_app = target_dir / APP_BUNDLE_NAME

    with tempfile.TemporaryDirectory(prefix="dit_launcher_") as temp_dir:
        temp_path = Path(temp_dir)
        zip_path = temp_path / "release.zip"
        extract_dir = temp_path / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)

        urllib.request.urlretrieve(zip_url, zip_path)

        with zipfile.ZipFile(zip_path, "r") as archive:
            archive.extractall(extract_dir)

        app_candidates = list(extract_dir.rglob(APP_BUNDLE_NAME))
        if not app_candidates:
            raise RuntimeError("Downloaded release does not contain DIT Media Manager.app")

        source_app = app_candidates[0]
        if target_app.exists():
            shutil.rmtree(target_app)

        shutil.copytree(source_app, target_app)

    return target_app


def launch_app(app_path: Path) -> None:
    subprocess.Popen(["open", str(app_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class LauncherWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("DIT Media Launcher")
        self.root.geometry("520x220")
        self.root.configure(bg="#111111")
        self.root.resizable(False, False)

        self.status_var = tk.StringVar(value="Checking updates...")

        frame = tk.Frame(self.root, bg="#111111", padx=16, pady=16)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame,
            text="DIT Media Launcher",
            bg="#111111",
            fg="#FFFFFF",
            font=("SF Pro", 18, "bold"),
        ).pack(anchor=tk.W)

        tk.Label(
            frame,
            text="Updates app from GitHub Releases, then launches it.",
            bg="#111111",
            fg="#D8D8D8",
            font=("SF Pro", 11),
        ).pack(anchor=tk.W, pady=(4, 16))

        tk.Label(
            frame,
            textvariable=self.status_var,
            bg="#111111",
            fg="#FFCC33",
            wraplength=480,
            justify=tk.LEFT,
            font=("SF Pro", 11, "bold"),
        ).pack(anchor=tk.W)

        actions = tk.Frame(frame, bg="#111111")
        actions.pack(fill=tk.X, pady=(18, 0))

        self.retry_button = tk.Button(
            actions,
            text="Retry",
            command=self.run,
            bg="#FFCC33",
            fg="#111111",
            relief="flat",
            bd=0,
            padx=14,
            pady=6,
            font=("SF Pro", 10, "bold"),
            cursor="hand2",
        )
        self.retry_button.pack(side=tk.LEFT)

        tk.Button(
            actions,
            text="Quit",
            command=self.root.destroy,
            bg="#2A2A2A",
            fg="#FFFFFF",
            relief="flat",
            bd=0,
            padx=14,
            pady=6,
            font=("SF Pro", 10),
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(10, 0))

        self.root.after(250, self.run)

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)
        self.root.update_idletasks()

    def run(self) -> None:
        try:
            settings = load_settings()
            update_settings = settings.get("updates", {})
            github_repo = str(update_settings.get("github_repo", "")).strip() or DEFAULT_GITHUB_REPO
            asset_name_contains = str(update_settings.get("asset_name_contains", "DIT Media Manager")).strip()
            install_dir = str(update_settings.get("install_dir", str(Path.home() / "Applications")))

            self._set_status("Checking latest release on GitHub...")
            release = fetch_latest_release(github_repo)
            latest_tag = str(release.get("tag_name", "")).strip()
            state = load_state()
            installed_tag = str(state.get("installed_tag", "")).strip()

            installed_app = find_installed_app(install_dir)
            needs_install = not installed_app or (latest_tag and latest_tag != installed_tag)

            if needs_install:
                asset = pick_release_asset(release, asset_name_contains)
                if not asset:
                    raise RuntimeError("No matching .zip asset found in latest GitHub release.")
                download_url = str(asset.get("browser_download_url", ""))
                if not download_url:
                    raise RuntimeError("Release asset is missing download URL.")

                self._set_status(f"Downloading {asset.get('name', 'release')}...")
                installed_app = install_downloaded_app(download_url, install_dir)
                state["installed_tag"] = latest_tag
                save_state(state)
                self._set_status(f"Installed {latest_tag or 'latest'} successfully.")
            else:
                self._set_status("Already up to date. Launching app...")

            if not installed_app:
                raise RuntimeError("Installation path could not be resolved.")

            launch_app(installed_app)
            self.root.after(700, self.root.destroy)
        except Exception as exc:
            self._set_status(f"Launcher error: {exc}")
            mbox.showerror("Launcher Error", str(exc))


def main() -> None:
    root = tk.Tk()
    LauncherWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
