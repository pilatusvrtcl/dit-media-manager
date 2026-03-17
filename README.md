# Automated DIT Media Manager (BRAW + ATEM ISO)

GUI-based macOS Python app for one-click sync from:
- Camera A network mount
- Camera B network mount
- ATEM ISO network mount

to a local RAID/SSD destination using `rsync` with checksum verification.

## Features

- Smart Sync (incremental): skips files already present with matching size
- Optional **Sync Last 24h** filtering
- Full ATEM ISO folder structure copy (including `.drp` and all nested files)
- Post-copy checksum verification (`md5` by default, `xxh64` optional via `xxhash`)
- Automatic retry once on checksum mismatch
- Active-record detection (size/mtime stability probe + minimum file age)
- Parallel sync across all 3 sources using multithreading
- Real-time dashboard + final table report:
  - File Name | Source | Size | Status (Verified / Skipped / Failed)
  - Summary with total data, new files, elapsed time

## Project Layout

- `app/main.py` — app entrypoint
- `app/launcher.py` — updater launcher (GitHub Releases + launch)
- `app/gui.py` — Tkinter GUI/dashboard/report window
- `app/sync_engine.py` — sync orchestration (`rsync`, hash verify, retry)
- `app/models.py` — config/report dataclasses
- `app/utils.py` — config loading, hashing, active file checks
- `settings.json` — mount and sync settings
- `build_app.sh` — one-command macOS app build
- `build_launcher.sh` — one-command launcher app build
- `DIT Media Manager.spec` — PyInstaller spec

## Configure

Edit `settings.json`:

- `destination_root`: local RAID/SSD path
- `sources[*].ip_address`: camera/ATEM IP for connectivity checks
- `sources[*].mount_path`: mounted network source paths
- `sources[*].smb_share` (optional): SMB share name for auto-mount over IP
- `sources[*].smb_username` (optional, default `guest`): SMB username
- `sources[*].smb_password` (optional): SMB password
- `sync.last_24h_default`: initial state of toggle
- `sync.hash_algorithm`: `md5` or `xxh64`
- `updates.github_repo`: GitHub repo in `owner/repo` format
- `updates.asset_name_contains`: zip filename filter for release asset
- `updates.install_dir`: target install location for downloaded `.app`

Dashboard status meanings:

- `Online (Mounted)`: mount path is accessible and ready to sync
- `Reachable (Not Mounted)`: device responds on network, but volume is not mounted
- `Offline`: neither mount nor network reachability detected

## Run (dev)

Use module mode from project root:

```bash
python3 -m app.main
```

## Build macOS .app

```bash
chmod +x build_app.sh
./build_app.sh
```

The script creates and uses a local `.venv-build` virtual environment automatically.

Output:

- `dist/DIT Media Manager.app`

Build launcher:

```bash
chmod +x build_launcher.sh
./build_launcher.sh
```

Output:

- `dist/DIT Media Launcher.app`

## Launcher Update Flow

1. Configure `updates.github_repo` in `settings.json`.
2. Publish a GitHub Release with a `.zip` asset that contains `DIT Media Manager.app`.
3. Run `DIT Media Launcher.app`.
4. Launcher checks latest release, downloads/installs if needed, then opens the app.

## GitHub Automation (Recommended)

This repo includes [`.github/workflows/release.yml`](.github/workflows/release.yml).

It will:
- Trigger on tag push (`v*`)
- Build both apps on macOS (`DIT Media Manager.app`, `DIT Media Launcher.app`)
- Zip both `.app` bundles
- Upload zip assets to the GitHub Release

Release command example:

```bash
git tag v1.0.0
git push origin v1.0.0
```

### Hotfix release now (`v1.0.9`)

Use this exact sequence to publish the fixed build (regression from `v1.0.6` is resolved):

```bash
git add app/gui.py app/launcher.py app/version.py README.md
git commit -m "Fix manager startup crash and release v1.0.9"
git push origin main

git tag v1.0.9
git push origin v1.0.9
```

After GitHub Actions attaches release assets, run `DIT Media Launcher.app` again. It will detect `v1.0.9`, download it, and replace the broken `v1.0.8` install.

Set this in `settings.json` for launcher updates:

```json
"updates": {
  "github_repo": "your-github-user-or-org/your-repo-name",
  "asset_name_contains": "DIT Media Manager",
  "install_dir": "~/Applications"
}
```

## Notes

- Ensure all network mounts are mounted in Finder before sync.
- If you prefer IP-only operation, set `smb_share` in `settings.json`. The app will try `mount_smbfs` automatically when a source is reachable but not mounted.
- Uses system `rsync`; interrupted transfers resume using partial data flags.
- Destination structure is namespaced per source (`<destination_root>/<source name>/...`) to avoid collisions.
- Building a working GUI `.app` requires Python with Tk support. If Homebrew Python lacks Tk, install Python from python.org and run `./build_app.sh` again.

## Custom Icons

Optional icon files:

- `assets/app_icon.icns` → used for `DIT Media Manager.app` bundle icon
- `assets/launcher_icon.icns` → used for `DIT Media Launcher.app` bundle icon
- `assets/app_icon.png` → used for runtime window icon in the manager UI

If these files are missing, builds still work and default icons are used.
