"""Loader del destilado estratégico (docs/asistente/estrategia.md).

El archivo es editable por el equipo sin tocar código. El asistente lo relee
automáticamente cuando el mtime cambia, así los ajustes son inmediatos.

Cache en memoria: no releemos del FS en cada request, solo si hubo cambio.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# docs/asistente/estrategia.md desde la raíz del proyecto
_BASE = Path(__file__).resolve().parent.parent.parent
ESTRATEGIA_PATH = _BASE / "docs" / "asistente" / "estrategia.md"

_cache: dict = {"mtime": 0.0, "content": ""}


def load_estrategia() -> str:
    """Devuelve el contenido del estrategia.md o '' si no existe / falla."""
    try:
        mtime = ESTRATEGIA_PATH.stat().st_mtime
    except FileNotFoundError:
        return ""
    except Exception as e:
        logger.warning("No pude stat estrategia.md: %s", e)
        return ""

    if _cache["mtime"] >= mtime and _cache["content"]:
        return _cache["content"]

    try:
        content = ESTRATEGIA_PATH.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("No pude leer estrategia.md: %s", e)
        return _cache["content"]  # devuelvo el cache viejo si hay

    _cache["mtime"] = mtime
    _cache["content"] = content
    return content
