"""Loader genérico de archivos markdown con cache por mtime.

Usado por `estrategia.py` (framework) y `estrategias.py` (catálogo) para
evitar releer del FS en cada request. Sólo se relee si cambió el mtime del
archivo en disco — así las ediciones manuales del equipo se reflejan
inmediatamente sin restart, pero requests sucesivos no pagan I/O.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_caches: dict[Path, dict] = {}


def load_md(path: Path) -> str:
    """Devuelve el contenido del archivo o '' si no existe / falla.

    En caso de error de lectura, devuelve el último contenido cacheado si
    existe (para no romper la respuesta del agente por un I/O transitorio).
    """
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return ""
    except Exception as e:
        logger.warning("No pude stat %s: %s", path.name, e)
        return _caches.get(path, {}).get("content", "")

    cache = _caches.get(path)
    if cache and cache["mtime"] >= mtime and cache["content"]:
        return cache["content"]

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("No pude leer %s: %s", path.name, e)
        return _caches.get(path, {}).get("content", "")

    _caches[path] = {"mtime": mtime, "content": content}
    return content
