"""core/tamar_1816_sql.py — escritura de `mercado.tamar_1816`. Una sola.

Doc madre: `docs/RENTA_FIJA.md` paso 18 · `docs/AGENT.md`.

**Qué guarda esa tabla.** La TEA y el **MARGEN** de los bonos cuya tasa NO
calculamos nosotros (hoy los TAMAR): se traen de 1816 porque son notas de tasa
PROMEDIO — la parte ya observada está congelada y la futura hay que proyectarla.
PK `(ticker, pata)` porque un dual tiene dos patas que rinden distinto de verdad.

**Por qué vive acá y no dentro del job.** Lo escribía `jobs/tamar_1816` y solo
él. Cuando el AV Agent tuvo que dar de alta un TAMAR se encontró con que el bono
nacía **sin tasa y sin margen** hasta que el cron corriera —hasta 30 minutos, o
hasta el día siguiente fuera de rueda— y la alternativa era reescribir el INSERT.
Dos implementaciones del mismo upsert terminan siempre igual: una se queda vieja.

De un TAMAR **el margen es el número que mira la mesa**, así que un alta que lo
deja vacío está a medio hacer.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Las columnas de la tabla que llegan de 1816. `actualizado_en` lo pone la base.
CAMPOS = ("ticker", "pata", "ticker_1816", "tea", "tna", "spread",
          "precio_clean", "duration", "paridad", "fecha_operacion")


def upsert(filas: list[dict]) -> int:
    """Upsert por `(ticker, pata)`. Idempotente: re-correr no duplica."""
    if not filas:
        return 0
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO mercado.tamar_1816 (ticker,pata,ticker_1816,tea,tna,spread,"
            "precio_clean,duration,paridad,fecha_operacion) VALUES "
            "(%(ticker)s,%(pata)s,%(ticker_1816)s,%(tea)s,%(tna)s,%(spread)s,"
            "%(precio_clean)s,%(duration)s,%(paridad)s,%(fecha_operacion)s) "
            "ON CONFLICT (ticker,pata) DO UPDATE SET "
            "ticker_1816 = EXCLUDED.ticker_1816, tea = EXCLUDED.tea, "
            "tna = EXCLUDED.tna, spread = EXCLUDED.spread, "
            "precio_clean = EXCLUDED.precio_clean, duration = EXCLUDED.duration, "
            "paridad = EXCLUDED.paridad, fecha_operacion = EXCLUDED.fecha_operacion, "
            "actualizado_en = now()",
            filas,
        )
    return len(filas)


def sembrar_desde_1816(ticker: str, pata: str) -> dict:
    """Trae de 1816 la tasa y el MARGEN de un ticker y los deja escritos.
    **Nunca levanta** — devuelve qué pasó.

    Lo llama el AV Agent al dar de alta un bono cuya tasa viene de 1816: sin
    esto el bono queda escrito y su celda de tasa vacía hasta la próxima corrida
    del cron, que fuera de rueda puede ser mañana.

    Usa `indicadores_vigentes`, o sea que **retrocede día hábil por día hábil**
    hasta encontrar una rueda con datos: al alta le sirve el ÚLTIMO dato que
    exista, no específicamente el de hoy.
    """
    from core import mercado_1816
    campos = ["tea", "tna", "spread", "precioClean", "duration", "paridad"]
    tk = mercado_1816.normalizar_ticker(ticker)
    try:
        resp = mercado_1816.indicadores_vigentes([tk], campos)
    except Exception as e:
        return {"ok": False, "error": f"1816 no respondió: {e}"[:200]}
    if not resp:
        return {"ok": False, "error": "1816 no tiene datos de este ticker en las "
                                      "últimas 5 ruedas"}
    v = (resp.get("instrumentos") or {}).get(tk) or {}
    if v.get("tea") is None:
        # Sin TEA no se escribe la fila. Dejar una en NULL sería peor que no
        # tenerla: la vista mostraría el bono "con dato" y el dato sería nada.
        return {"ok": False, "sin_dato": True,
                "error": f"1816 conoce {tk} pero no publicó tasa al "
                         f"{resp.get('fechaOperacion')} — la fila se va a llenar "
                         "sola cuando el job encuentre una rueda con datos"}
    fila = {"ticker": tk, "pata": pata, "ticker_1816": tk,
            "tea": v.get("tea"), "tna": v.get("tna"), "spread": v.get("spread"),
            "precio_clean": v.get("precioClean"), "duration": v.get("duration"),
            "paridad": v.get("paridad"),
            "fecha_operacion": resp.get("fechaOperacion")}
    try:
        upsert([fila])
    except Exception as e:
        return {"ok": False, "error": f"no se pudo escribir mercado.tamar_1816: "
                                      f"{type(e).__name__}"}
    return {"ok": True, "tea": v.get("tea"), "spread": v.get("spread"),
            "fecha_operacion": resp.get("fechaOperacion"), "pata": pata}
