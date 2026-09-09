"""`agente/tasa_1816.py` — LA LISTA DE PRIORIDAD. Doc: `AGENT.md` §5.

Pedido del user (2026-08-24), sobre los bonos que operan y no tienen TEA:

> *«Poner el ticker en un listado, y ese listado ir cada 15 minutos a buscar la
> TEA, la duration y el precio a 1816. Esta lista es una sumatoria de tickers
> que se consultan DURANTE EL DÍA y se purga al día siguiente a las 9am.»*

**PATRÓN TAMAR**, y eso decide lo único que había que decidir:

  · **NO se escribe en `mercado.market_snapshot`.** Esa tabla es del MOTOR:
    Primary, live, cada 5 segundos. Esto es 1816, con delay. Mezclarlas dejaría
    dos fuentes escribiendo la misma celda y ninguna forma de saber cuál ganó.
  · Va a **tabla propia** (`agente.tasa_1816`).
  · **Se juntan en la LECTURA**, y cada fila viaja diciendo **de dónde salió su
    tasa y de cuándo es**. La mesa deja de ver `--` y ve un número que sabe leer.

Con eso la habilidad deja de ser «te aviso que falta algo» y pasa a **tapar el
agujero**.
"""
from __future__ import annotations

import logging

from core.postgres import get_pool

logger = logging.getLogger(__name__)

# ⚠️ **LOS NOMBRES SALEN DE `jobs/tamar_1816`, QUE YA FUNCIONA — no se adivinan.**
#
# La API **rechaza la llamada ENTERA con HTTP 400 si UN campo no existe**, así
# que un nombre inventado no degrada: apaga la habilidad completa. La primera
# versión pidió `tir` y `precio` y se llevó puesto todo el barrido del cierre.
#
# Y cuesta `tickers × campos` créditos, así que se piden los cuatro que la vista
# usa de verdad y ninguno más: `spread` es de TAMAR y acá no se mira.
CAMPOS = ("tea", "tna", "duration", "precioClean")


def pendientes() -> list[str]:
    """Los tickers de la lista. Sale de los hallazgos ABIERTOS de
    `bono_sin_tasa` — la lista NO es una tabla aparte que haya que mantener
    sincronizada: es una consulta sobre la única tabla de hallazgos.

    Es lo que evita el problema de siempre: dos lugares diciendo qué bonos
    faltan, y uno de los dos quedándose viejo.
    """
    from agente import tipos
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT sujeto FROM agente.hallazgos "
                    " WHERE habilidad = 'bono_sin_tasa' AND estado = ANY(%s)",
                    (list(tipos.ABIERTOS),))
        return sorted(r[0] for r in cur.fetchall())


def refrescar() -> dict:
    """Le pide a 1816 la tasa de todos los de la lista. **Una llamada, no una
    por ticker.**

    `indicadores_vigentes` trae los campos de la última rueda CON DATOS,
    retrocediendo día hábil por hábil: sin eso, un domingo devuelve todo en
    `null` y parecería que el campo no existe.
    """
    from core import mercado_1816

    tickers = pendientes()
    if not tickers:
        return {"ok": True, "tickers": 0, "escritos": 0}
    if not mercado_1816.disponible():
        return {"ok": False, "error": "1816 no está configurado"}

    # ⚠️ **A QUÉ DÓLAR (2026-09-09, §0.ez).** Estas tasas van DERECHO a la vista
    # de Renta Fija como la TEA del bono, sin que nadie las convierta. Con el
    # default de la API (`ars`), un bono que paga en dólares vuelve calculado al
    # CCL de 1816 — o sea que la mesa veía una TEA al CCL al lado de las que el
    # motor calcula al MEP, y no falla nada: es un número plausible.
    # Una llamada por moneda, porque `/indicadores` lleva UNA.
    por_mon = mercado_1816.por_moneda(tickers)
    fecha, inst, moneda_de = None, {}, {}
    for moneda, tks in sorted(por_mon.items()):
        try:
            d = mercado_1816.indicadores_vigentes(tks, list(CAMPOS),
                                                  fecha=fecha, moneda=moneda) or {}
        except Exception as e:
            logger.warning("tasa_1816: 1816 no contestó en %s (%s)", moneda, e)
            continue
        # La respuesta viene como `{instrumentos: {TICKER: {...}}, fechaOperacion}`.
        # Es el mismo shape que parsea `jobs/tamar_1816`: se lee de ahí y no se
        # inventa una forma paralela.
        fecha = fecha or d.get("fechaOperacion")
        inst.update(d.get("instrumentos") or {})
        moneda_de.update(dict.fromkeys(tks, moneda))
    if not inst:
        return {"ok": False, "error": "1816 no contestó en ninguna moneda"}

    escritos, sin_dato = 0, []
    with get_pool().connection() as conn, conn.cursor() as cur:
        for tk in tickers:
            v = inst.get(tk) or {}
            if v.get("tea") is None:
                # **No se escribe una fila de nulls.** Dejar la anterior es más
                # honesto que pisarla con vacío, y `pedido_at` delata si quedó
                # vieja. Lo que 1816 no trajo se CUENTA, no se silencia.
                sin_dato.append(tk)
                continue
            cur.execute(
                "INSERT INTO agente.tasa_1816 "
                " (ticker, pata, tea, duration, precio, fecha_1816, moneda, pedido_at) "
                "VALUES (%s,'',%s,%s,%s,%s,%s, now()) "
                "ON CONFLICT (ticker, pata) DO UPDATE SET "
                "  tea = EXCLUDED.tea, duration = EXCLUDED.duration, "
                "  precio = EXCLUDED.precio, fecha_1816 = EXCLUDED.fecha_1816, "
                "  moneda = EXCLUDED.moneda, pedido_at = now()",
                (tk, _num(v.get("tea")), _num(v.get("duration")),
                 _num(v.get("precioClean")), fecha, moneda_de.get(tk, "ars")))
            escritos += 1
    return {"ok": True, "tickers": len(tickers), "escritos": escritos,
            "sin_dato": sin_dato, "fecha_1816": fecha}


def purgar() -> int:
    """Se vacía al día siguiente. Lo llama el motor cuando ve que cambió el día
    ART: una tasa de ayer mostrada sin decirlo es peor que no mostrar nada."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM agente.tasa_1816 WHERE pedido_at < "
                    "  ((current_date || ' 09:00')::timestamp "
                    "   AT TIME ZONE 'America/Argentina/Buenos_Aires')")
        return cur.rowcount or 0


def tasas() -> dict[str, dict]:
    """Para la LECTURA de la vista de curvas. **Cada fila dice de dónde salió.**

    Se juntan acá y no escribiendo en `market_snapshot` porque esa tabla es del
    motor: dos fuentes en la misma celda y nadie sabe cuál ganó.
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT ticker, tea, duration, precio, fecha_1816, "
                        "       pedido_at, moneda FROM agente.tasa_1816")
            return {r[0]: {"tea": _f(r[1]), "duration": _f(r[2]),
                           "precio": _f(r[3]),
                           "tea_fuente": "1816",
                           "tea_fecha": r[4].isoformat() if r[4] else None,
                           "tea_pedida_at": r[5].isoformat() if r[5] else None,
                           # A qué dólar se pidió. Viaja con el dato por la misma
                           # razón que `tea_fuente` y `tea_fecha`: un número que
                           # no dice de dónde ni en qué unidad sale obliga a
                           # adivinar, y adivinar fue el bug (§0.ez).
                           "tea_moneda": r[6]}
                    for r in cur.fetchall()}
    except Exception as e:
        logger.warning("tasa_1816: no pude leer las tasas (%s)", e)
        return {}


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _f(v):
    return float(v) if v is not None else None
