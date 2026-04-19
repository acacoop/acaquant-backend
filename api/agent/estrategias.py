"""Loader del catálogo técnico de estrategias (docs/asistente/estrategias.md).

Es el mapa estrategia → datos → fórmulas que el modelo consulta cuando el
usuario pregunta por una estructura concreta (bullet, butterfly, carry,
covered call, etc.). Lo traté separado de `estrategia.md` (que es el ADN /
framework) porque tienen ciclos de edición distintos: el framework cambia poco,
el catálogo técnico puede crecer con tiempo.

Re-lee del disco si cambia el mtime (edición inmediata sin restart).
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE = Path(__file__).resolve().parent.parent.parent
ESTRATEGIAS_PATH = _BASE / "docs" / "asistente" / "estrategias.md"

_cache: dict = {"mtime": 0.0, "content": ""}


def load_estrategias() -> str:
    """Devuelve el contenido de estrategias.md o '' si no existe."""
    try:
        mtime = ESTRATEGIAS_PATH.stat().st_mtime
    except FileNotFoundError:
        return ""
    except Exception as e:
        logger.warning("No pude stat estrategias.md: %s", e)
        return ""

    if _cache["mtime"] >= mtime and _cache["content"]:
        return _cache["content"]

    try:
        content = ESTRATEGIAS_PATH.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("No pude leer estrategias.md: %s", e)
        return _cache["content"]

    _cache["mtime"] = mtime
    _cache["content"] = content
    return content
