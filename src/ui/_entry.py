"""Entry point for `uv run ui` — launches Streamlit without importing the app."""

import subprocess
import sys
from pathlib import Path


def main() -> None:
    app = Path(__file__).resolve().parent / "streamlit_app.py"
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app)])
