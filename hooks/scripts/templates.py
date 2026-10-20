"""Tiny {{placeholder}} renderer for the markdown in hooks/text/.

All user-facing prose (the policy, the wrap-up routine, section headers) lives in
markdown files under hooks/text/, so it can be edited without touching Python.
"""
from pathlib import Path

TEXT_DIR = Path(__file__).resolve().parent.parent / "text"


def render(name, **values):
    """Read hooks/text/<name> and substitute {{key}} placeholders."""
    text = (TEXT_DIR / name).read_text()
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text
