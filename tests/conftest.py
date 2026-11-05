"""Every test runs against a throw-away copy of the project, in demo mode, with no API keys."""
import importlib
import os
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture()
def office(tmp_path, monkeypatch):
    """A fresh copy of the office (own database + output folder). Returns the imported `agency` package."""
    for name in ("agency", "config.toml"):
        src = REPO / name
        (shutil.copytree if src.is_dir() else shutil.copy)(src, tmp_path / name)
    for k in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DEMO", "1")
    monkeypatch.setenv("DEMO_DELAY", "0")   # demo replies arrive instantly in tests
    monkeypatch.syspath_prepend(str(tmp_path))
    for mod in [m for m in sys.modules if m == "agency" or m.startswith("agency.")]:
        del sys.modules[mod]
    import agency  # noqa: F401
    pkg = importlib.import_module("agency")
    for sub in ("core", "llm", "agents", "runner", "formats", "privacy"):
        importlib.import_module(f"agency.{sub}")
    pkg.core.CFG.setdefault("render", {})["pdf_engine"] = "basic"   # deterministic: no browser needed in CI
    yield pkg
    for mod in [m for m in sys.modules if m == "agency" or m.startswith("agency.")]:
        del sys.modules[mod]
