"""Start the landing-page server from the repo checkout (Render runs commands from the repo root)."""

import os
import runpy
from pathlib import Path


def main() -> None:
    server = Path(os.getcwd()) / "server.py"
    runpy.run_path(str(server), run_name="__main__")
