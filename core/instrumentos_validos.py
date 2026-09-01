"""core/instrumentos_validos.py — LA validación de símbolos. Una sola, para todos.

Un símbolo que no existe en Primary no rompe nada ruidosamente: la suscripción se
rechaza y el papel se queda **sin precio, en silencio**. La vista sigue abriendo,
la fila sigue ahí, y el número está viejo.

Hasta ahora cada motor resolvía esto por su cuenta o no lo resolvía:
`engines/_universo_portfolio` validaba contra `manager.pyrofex_instruments` antes
de armar su universo, y el resto se suscribía a lo que tuviera guardado. Dos
criterios distintos para la misma pregunta terminan siempre igual — uno de los
dos se queda viejo y nadie sabe cuál.

Acá vive el criterio, UNA vez, y se aplica en el único lugar por el que pasan
TODAS las suscripciones: `core/websocket.py::agregar_suscripciones`. Un motor
nuevo lo hereda sin escribir una línea, y no hay forma de saltearlo por olvido.

**La fuente de verdad es `manager.pyrofex_instruments`** — el catálogo que baja
`scripts/discovery_pyrofex` de la propia Primary. No `mercado.curvas`, no
`portafolio.assets`: esos son NUESTROS, y el error que se busca es justamente que
lo nuestro no coincida con la realidad.

## Los dos modos de fallar, y por qué se elige este

Filtrar de más deja papeles sin precio. No filtrar deja pasar símbolos muertos,
que era el comportamiento de siempre. Ante la duda se elige **no filtrar**: si
Postgres no responde, o el catálogo está vacío porque el discovery nunca corrió,
`validos()` devuelve `None` y no se descarta nada. Un bug de infraestructura no
puede dejar la mesa sin precios.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

# El catálogo lo refresca `scripts/discovery_pyrofex` a las 12:15 UTC L-V (cron
# desde 2026-09-01 — antes era manual y la foto llegó a tener 17 días); los
# motores rearman su universo cada hora. 10 minutos es de sobra y evita una
# query por cada lote de suscripción (que llegan de a 50).
_TTL_S = 600.0
# Piso de sanidad. Primary publica ~12.900 símbolos: un catálogo de 30 filas está
# a medio escribir, y usarlo descartaría casi todo. Se prefiere no filtrar.
_MINIMO_CREIBLE = 100

_cache: dict = {"simbolos": None, "ts": 0.0}
_lock = threading.Lock()


def _leer() -> set[str]:
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT trim(i->>'ticker') "
                    "FROM manager.pyrofex_instruments p, "
                    "     jsonb_array_elements(p.instruments) AS i "
                    "WHERE i->>'ticker' IS NOT NULL")
        return {t for (t,) in cur.fetchall() if t}


def validos(*, forzar: bool = False) -> set[str] | None:
    """Los símbolos que Primary publica hoy, o `None` si no se puede saber.

    `None` NO es un conjunto vacío: significa "no hay criterio", y quien filtra
    tiene que dejar pasar todo. Distinguirlos es lo que evita que una caída de
    Postgres se convierta en una mesa sin precios.
    """
    ahora = time.monotonic()
    if not forzar and _cache["simbolos"] is not None and (ahora - _cache["ts"]) < _TTL_S:
        return _cache["simbolos"]
    with _lock:
        try:
            simbolos = _leer()
        except Exception as e:
            logger.warning("validación de símbolos: no pude leer el catálogo (%s) "
                           "— no se filtra nada", e)
            return None
        if len(simbolos) < _MINIMO_CREIBLE:
            logger.warning("validación de símbolos: el catálogo tiene %d símbolos "
                           "(< %d) — no se filtra nada. ¿Corrió discovery_pyrofex?",
                           len(simbolos), _MINIMO_CREIBLE)
            return None
        _cache["simbolos"] = simbolos
        _cache["ts"] = time.monotonic()
        return simbolos


def invalidar() -> None:
    """Forzar relectura del catálogo (tras correr el discovery)."""
    _cache["simbolos"] = None
    _cache["ts"] = 0.0


def filtrar(tickers: list[str], universo: set[str] | None) -> tuple[list[str], list[str]]:
    """`(a_suscribir, descartados)`. PURA — el universo se inyecta.

    Con `universo=None` no descarta nada: sin criterio confiable se mantiene el
    comportamiento de siempre. Preserva el ORDEN y no deduplica: quien llama
    arma sus lotes y este filtro no le puede cambiar el tamaño por su cuenta.
    """
    if universo is None:
        return list(tickers or []), []
    ok, fuera = [], []
    for t in tickers or []:
        (ok if t in universo else fuera).append(t)
    return ok, fuera
