"""Run the API and static frontend together for local development."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

from app.main import app  # noqa: E402

# API routes are registered before this catch-all static mount.
app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")


if __name__ == "__main__":
    port = int(os.getenv("DEV_PORT", "8080"))
    uvicorn.run(app, host="127.0.0.1", port=port)
