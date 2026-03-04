from __future__ import annotations

import tkinter.messagebox as mbox
from pathlib import Path

from app.gui import run_app
from app.utils import load_config


def main() -> None:
    try:
        config = load_config(Path("settings.json"))
    except Exception as exc:
        mbox.showerror("Configuration Error", f"Unable to load settings.json\n\n{exc}")
        return
    run_app(config)


if __name__ == "__main__":
    main()
