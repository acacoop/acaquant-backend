"""Loader del framework analítico (docs/asistente/estrategia.md).

Wrapper sobre `markdown_loader.load_md`. Editable por el equipo sin tocar
código — el loader releé cuando cambia el mtime.
"""
from __future__ import annotations

from pathlib import Path

from api.agent.markdown_loader import load_md

ESTRATEGIA_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "asistente" / "estrategia.md"


def load_estrategia() -> str:
    return load_md(ESTRATEGIA_PATH)
