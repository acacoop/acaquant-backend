"""core/curvas_catalogo.py — las curvas que se crean sin deploy.

Doc madre: `docs/AGENT.md`. Lee `mercado.curvas_catalogo`.

**Qué resuelve.** `curvas_ejes._pill_de_ajuste` era una función con ocho `if` que
devolvían un string: una TABLA disfrazada de código. Mientras lo fue, agregar una
curva era un deploy — y por eso `badlar`, `tpm` y `caucion` nunca la tuvieron,
dejando sus bonos cargados e **invisibles en toda la app** (no salen en la tabla,
ni en los forwards, ni en el fair value, y sin dar error).

**El catálogo SUMA, nunca PISA.** Las 5 curvas viejas (tasa_fija, cer,
hard_dolar, dolar_linked, tamar) siguen definidas en código, y esta tabla solo
puede agregar ajustes que hoy no caen en ninguna pill. Es la decisión que hace
que el cambio no pueda romper nada: **si esta tabla no responde, la vista de
renta fija anda exactamente igual que antes**. Un catálogo que puede redefinir
una curva existente es un catálogo que puede mover 129 bonos de tabla en silencio.

Cache in-process con TTL, igual que `core/curvas_sql`: `_pill_de_ajuste` se llama
una vez por bono por request y no puede pagar un roundtrip cada vez (~8,5 ms de
peaje fijo contra Supabase).
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_TTL = 60.0
_lock = threading.Lock()
_cache: dict = {"data": None, "ts": 0.0}

FUENTES = ("motor", "1816")
LADOS = ("ARS", "USD")


def _cargar() -> dict[str, dict]:
    """`{ajuste: fila}`. Ante CUALQUIER fallo devuelve `{}` — sin catálogo el
    sistema se comporta como antes de que existiera, que es el peor caso
    aceptable. Nunca levanta."""
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT ajuste, pill, display, lado, orden, fuente_valuacion, "
                        "nota FROM mercado.curvas_catalogo")
            cols = ["ajuste", "pill", "display", "lado", "orden",
                    "fuente_valuacion", "nota"]
            return {r[0]: dict(zip(cols, r, strict=False)) for r in cur.fetchall()}
    except Exception as e:
        logger.debug("curvas_catalogo: no se pudo leer (%s) — sigo sin catálogo", e)
        return {}


def todas() -> dict[str, dict]:
    """El catálogo cacheado (TTL 60s). NO mutar el dict devuelto."""
    ahora = time.monotonic()
    if _cache["data"] is not None and (ahora - _cache["ts"]) < _TTL:
        return _cache["data"]
    with _lock:
        if _cache["data"] is None or (time.monotonic() - _cache["ts"]) >= _TTL:
            _cache["data"] = _cargar()
            _cache["ts"] = time.monotonic()
        return _cache["data"]


def invalidar() -> None:
    """Fuerza recarga en la próxima lectura (tras crear una curva)."""
    _cache["data"] = None
    _cache["ts"] = 0.0


def de_ajuste(ajuste: str | None) -> dict | None:
    return todas().get((ajuste or "").strip().lower())


# Ajustes cuya tasa NO la calcula el motor y se TRAE de 1816, pero que están
# definidos en CÓDIGO y no en la tabla — así que `de_ajuste` no los ve.
#
# **TAMAR es el caso**: un TAMAR es una nota de tasa PROMEDIO (promedia la TAMAR
# de bancos privados entre T−10 de emisión y T−10 del vencimiento, + margen de
# licitación), la parte ya observada está congelada y la futura hay que
# proyectarla. Decisión tomada: NO la valuamos, la trae `jobs/tamar_1816`.
#
# Que faltara acá tenía una consecuencia concreta: el AV Agent veía la rama
# `otros` y concluía «este ajuste no tiene fórmula, la TEA va a quedar vacía»
# — cuando en realidad el TAMAR es el caso MÁS resuelto que tenemos. El sistema
# sabía la respuesta y no había forma de preguntársela.
TASA_EXTERNA_EN_CODIGO = {"tamar": "jobs/tamar_1816"}


def fuente_valuacion(ajuste: str | None) -> str | None:
    """`'motor'` | `'1816'` | None si nadie sabe de ese ajuste.

    Mira el catálogo Y los ajustes cuya tasa externa está definida en código: la
    pregunta *«¿de dónde sale la tasa de este bono?»* tiene que contestarse en UN
    lugar, sin importar dónde esté escrita la respuesta.
    """
    aj = (ajuste or "").strip().lower()
    if aj in TASA_EXTERNA_EN_CODIGO:
        return "1816"
    fila = de_ajuste(aj)
    return fila.get("fuente_valuacion") if fila else None


def job_de_la_tasa(ajuste: str | None) -> str:
    """Qué job trae la tasa de ese ajuste, o `''`. Sirve para poder DECIR quién
    la va a traer en vez de dejar al usuario averiguándolo."""
    return TASA_EXTERNA_EN_CODIGO.get((ajuste or "").strip().lower(), "")


def ajustes_de_1816() -> tuple[str, ...]:
    """Los ajustes cuya tasa se TRAE de 1816. Lo lee `jobs/tamar_1816` para saber
    qué más pedir: agregar una curva `1816` no debería requerir tocar ese job.

    Incluye los de código: si la tabla no responde, `tamar` sigue estando y el
    job no pierde su universo de siempre."""
    del_catalogo = {a for a, f in todas().items()
                    if f.get("fuente_valuacion") == "1816"}
    return tuple(sorted(del_catalogo | set(TASA_EXTERNA_EN_CODIGO)))


def crear(*, ajuste: str, lado: str, fuente_valuacion: str, display: str | None = None,
          orden: int = 99, nota: str = "", por: str = "") -> dict:
    """Da de alta una curva. Idempotente por `ajuste` (no pisa una existente).

    Valida contra los dominios del sistema: el `ajuste` tiene que ser uno de
    `curvas_ejes.AJUSTES` —si no, se estaría creando una curva para un eje que
    ningún bono puede tener— y NO puede ser uno que ya tenga pill en código: esta
    tabla suma curvas, no las redefine.
    """
    from core import curvas_ejes

    aj = (ajuste or "").strip().lower()
    if aj not in curvas_ejes.AJUSTES:
        raise ValueError(f"«{aj}» no es un ajuste válido ({', '.join(curvas_ejes.AJUSTES)})")
    if not curvas_ejes.ajuste_sin_curva(aj):
        raise ValueError(f"«{aj}» YA tiene curva en código: el catálogo suma "
                         "curvas nuevas, no redefine las existentes")
    lado = (lado or "").strip().upper()
    if lado not in LADOS:
        raise ValueError(f"lado «{lado}» inválido ({', '.join(LADOS)})")
    fuente = (fuente_valuacion or "").strip().lower()
    if fuente not in FUENTES:
        raise ValueError(f"fuente «{fuente}» inválida ({', '.join(FUENTES)})")

    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mercado.curvas_catalogo "
            "(ajuste, pill, display, lado, orden, fuente_valuacion, nota, creada_por) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (ajuste) DO NOTHING",
            (aj, aj, (display or aj).strip().upper(), lado, orden, fuente,
             nota or None, por or None))
        creada = (cur.rowcount or 0) > 0
    invalidar()
    return {"ok": True, "ajuste": aj, "creada": creada, "lado": lado,
            "fuente_valuacion": fuente}
