"""api/services/av_agent_espejo.py — POR QUÉ un bono «no está en assets».

Doc madre: **`docs/AV_AGENT.md`** §0.by.

⚠️ **DE DÓNDE VIENE, y es un bug de los que no fallan.** El user (2026-08-22),
mirando PLC5O y S13N6 marcados con `sin_espejo_en_assets`:

> *«El problema es que no está en tenencias… ¿qué tiene que ver la paridad y la
> valuación? Si es solamente meter un asset en un lugar donde hoy no figura.
> **Además LO VEO EN TENENCIAS (AuM), los dos están.** Claramente está bugueada
> esta feature porque los bonos SÍ están. Justamente no tiene nada que ver con
> 1816 agregar algo en AuM.»*

Tenía razón en las dos cosas, y son dos bugs distintos:

**(1) El texto del hallazgo afirmaba algo falso.** Decía *«no entra al AuM ni a
Portfolios»*. Verificado en el código: el join de Portfolios va por **UNIDAD**
(`portfolio_sql`: `JOIN portafolio.assets a ON a.unidad = v.unidad`) y el AuM
sale de `portafolio.tenencia`, que es la fuente única. **El detector, en cambio,
mira la columna `ticker`** (`av_agent`: `SELECT DISTINCT upper(btrim(ticker))
FROM portafolio.assets`). Son dos campos distintos de la misma tabla: un bono
puede tener su fila (y verse perfecto en AuM, que es lo que él ve) con el
`ticker` vacío o escrito distinto.

Es REGLA #9 otra vez: **se mide una cosa y se afirma otra**, y no falla nada —
la pantalla contesta con seguridad usando el dato equivocado.

Lo que `assets.ticker` SÍ gobierna es el otro join, el de la cadena del AuM:
`mercado.curvas.ticker → portafolio.assets.ticker → unidad`. O sea **atribuir la
tenencia a su CURVA**: flujos, acreencias, vistas de renta fija. Grave, pero no
lo que decía.

**(2) El botón corría la cadena equivocada.** `sin_espejo_en_assets` se emite con
tipo `tasa_sospechosa`, y la acción salía del TIPO → DIAGNOSTICAR abría la cadena
del arreglo de curvas: 1816, paridad, cronograma, XIRR. Veinte pasos sobre la
tasa de un bono cuyo problema es una fila de catálogo.

    Es EXACTAMENTE el bug de los BOPREALes (§ACCION_POR_REGLA): la acción salía
    del tipo y no de la causa. Ya estaba el mecanismo para arreglarlo
    (`ACCION_POR_REGLA`) y a esta regla no se lo habían puesto.

TRES CAUSAS, que se arreglan distinto y hoy se veían iguales
=============================================================

    sin_fila     no hay fila en `portafolio.assets` → falta el alta del título
    sin_ticker   la fila EXISTE y `ticker` está vacío → falta UN campo
    otro_ticker  la fila existe con otro `ticker` → se escriben distinto

`sin_ticker` es la que más importa distinguir: decirle *«dá de alta el título»*
a alguien que ya lo tiene dado de alta es mandarlo a crear un duplicado.

**Cero red y cero créditos**: todo sale de `portafolio.assets`,
`portafolio.tenencia` y `mercado.curvas`. 1816 no tiene nada que ver con esto.
"""
from __future__ import annotations

import logging

from api.services.av_agent_alta import (
    BLOQUEA,
    INFO,
    OK,
    REVISAR,
    _paso,
    _veredicto,
)
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Qué hacer con cada causa. `un_campo` separa lo que se completa de lo que se
# crea: son dos trabajos distintos y confundirlos genera un duplicado.
CAUSAS: dict[str, dict] = {
    "sin_fila": {
        "titulo": "No hay ficha del título",
        "arreglo": "Darlo de alta en Manager → TÍTULOS · ASSETS con la unidad "
                   "exacta de la tenencia.",
        "un_campo": False,
    },
    "sin_ticker": {
        "titulo": "La ficha existe y le falta el TICKER",
        "arreglo": "Completar el campo TICKER de esa fila. **No hay que dar de "
                   "alta nada** — crear otra ficha duplicaría el título.",
        "un_campo": True,
    },
    "otro_ticker": {
        "titulo": "La ficha tiene OTRO ticker",
        "arreglo": "Unificar la grafía: o se corrige el TICKER de la ficha, o el "
                   "de la curva. Los dos tienen que decir lo mismo.",
        "un_campo": True,
    },
    "ya_esta": {
        "titulo": "Ya está resuelto",
        "arreglo": "Ninguno — el hallazgo quedó viejo.",
        "un_campo": False,
    },
}


def _q(cur, sql: str, params: tuple) -> list[tuple]:
    cur.execute(sql, params)
    return cur.fetchall()


def diagnosticar(ticker: str) -> dict:
    """La cadena de `sin_espejo_en_assets`. **Sin 1816, sin paridad, sin TEA.**"""
    tc = (ticker or "").strip().upper()
    if not tc:
        return {"ok": False, "error": "falta el ticker"}

    ps: list[dict] = []
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            ten = _q(cur,
                     "SELECT unidad, max(fecha)::text, sum(valuacion) "
                     "FROM portafolio.tenencia WHERE aum = 'si' "
                     "  AND fecha = (SELECT max(fecha) FROM portafolio.tenencia "
                     "               WHERE aum = 'si') "
                     "  AND upper(unidad) LIKE %s GROUP BY unidad", (f"%{tc}%",))
            por_tk = _q(cur, "SELECT unidad, ticker FROM portafolio.assets "
                             "WHERE upper(btrim(ticker)) = %s", (tc,))
            por_un = _q(cur, "SELECT unidad, ticker, cartera, emisor "
                             "FROM portafolio.assets WHERE upper(unidad) LIKE %s",
                        (f"%{tc}%",))
    except Exception as e:
        logger.warning("av_agent_espejo: no pude leer la base (%s)", e)
        return {"ok": False, "error": f"no pude leer la base: {str(e)[:160]}"}

    # ── 1. ¿La casa lo tiene? Es la premisa del hallazgo. ────────────────────
    if ten:
        plata = sum(float(r[2] or 0) for r in ten)
        ps.append(_paso(
            "tenencia", "¿La casa lo tiene?", OK,
            f"Sí: {len(ten)} unidad/es en la tenencia de hoy"
            + (f" por **{plata:,.0f}**" if plata else "")
            + ".\n" + "\n".join(f"· {r[0]}" for r in ten),
            tabla="portafolio.tenencia (aum='si')"))
    else:
        ps.append(_paso(
            "tenencia", "¿La casa lo tiene?", BLOQUEA,
            "**No aparece en la tenencia de hoy.** Si no lo tenemos, no le falta "
            "nada al catálogo: este hallazgo no debería existir.",
            tabla="portafolio.tenencia (aum='si')", nada_que_hacer=True))

    # ── 2. Lo que el detector mide de verdad ────────────────────────────────
    #
    # Va como PRUEBA y con el nombre del campo, porque es literalmente el
    # predicado que prendió la fila y nadie podía verlo desde la pantalla.
    ps.append(_paso(
        "predicado", "Qué mira el detector", OK if por_tk else REVISAR,
        f"La regla es `assets.ticker = '{tc}'`, y hoy devuelve "
        f"**{len(por_tk)} fila/s**."
        + ("" if por_tk else "  Por eso se prendió: **no hay ninguna ficha cuyo "
                             "campo TICKER diga eso**."),
        tabla="portafolio.assets (columna ticker)"))

    # ── 3. ¿Existe la ficha, aunque el ticker no coincida? ───────────────────
    if por_un:
        ps.append(_paso(
            "ficha", "¿Existe la ficha del título?", OK,
            f"Sí, {len(por_un)} fila/s en `portafolio.assets`:\n"
            + "\n".join(f"· unidad={r[0]} · ticker={r[1]!r} · cartera={r[2]!r} "
                        f"· emisor={r[3]!r}" for r in por_un),
            tabla="portafolio.assets (por unidad)"))
    else:
        ps.append(_paso(
            "ficha", "¿Existe la ficha del título?", REVISAR,
            "No hay ninguna fila de `portafolio.assets` cuya unidad contenga "
            f"«{tc}». Falta el alta del título.",
            tabla="portafolio.assets (por unidad)"))

    # ── 4. QUÉ SE ROMPE. Acá vivía la afirmación falsa. ─────────────────────
    ps.append(_paso(
        "rompe", "Qué se rompe de verdad", INFO,
        "**Sigue sumando al AuM.** El AuM sale de `portafolio.tenencia` y el "
        "join de Portfolios va por **unidad** "
        "(`JOIN portafolio.assets ON a.unidad = v.unidad`), no por el ticker.\n"
        "Lo que se rompe es el OTRO join, el que une la tenencia con su CURVA "
        "(`mercado.curvas.ticker → assets.ticker → unidad`): sin eso el bono no "
        "se puede atribuir a su curva y queda afuera de flujos, acreencias y las "
        "vistas de renta fija.",
        tabla="portfolio_sql · titulos_flujos"))

    # ── 5. La causa y el arreglo ────────────────────────────────────────────
    if not ten or por_tk:
        causa = "ya_esta"
    elif not por_un:
        causa = "sin_fila"
    elif any(not (r[1] or "").strip() for r in por_un):
        causa = "sin_ticker"
    else:
        causa = "otro_ticker"
    meta = CAUSAS[causa]
    paso = _paso(
        "causa", "⇒ LA CONCLUSIÓN",
        OK if causa == "ya_esta" else REVISAR,
        f"**{meta['titulo']}**\n\n{meta['arreglo']}",
        tabla="portafolio.assets", capa="veredicto",
        accion="/manager?tab=titulos",
        nada_que_hacer=(causa == "ya_esta"))

    # ── EL BOTÓN, cuando el arreglo es UN CAMPO y el valor no lo tipea nadie ──
    #
    # Medido el 2026-08-22: los 2 casos reales eran un TYPEO de un carácter
    # (`PLC5O`→`PLC50`, `S13N6`→`S13B6`) y el valor correcto ya estaba adentro de
    # la propia `unidad`, que la escribe Aunesa. Eso lo hace proponible con
    # certeza — no se adivina por parecido (REGLA #9 A), se lee de la fuente que
    # ninguna de las dos copias escribió.
    #
    # `sin_fila` NO lleva botón: dar de alta un título es cargar cartera, emisor,
    # clase y calificación, y nada de eso se deriva. Se hace a mano y está bien.
    if meta["un_campo"]:
        try:
            from api.services import av_agent_hacer as hacer
            a = hacer.ACCIONES["assets.ticker"]
            paso["hacer"] = {"accion": a.id, "titulo": a.titulo,
                             "campo": a.campo, "casos": 1,
                             "pendientes": len(hacer.pendientes(a.id))}
        except Exception as e:                       # el diagnóstico ya sirve solo
            logger.warning("av_agent_espejo: sin acción (%s)", e)
    ps.append(paso)

    for i, p in enumerate(ps, 1):
        p["n"] = i
    return {"ok": True, "ticker": tc, "causa": causa,
            "un_campo": meta["un_campo"], "pasos": ps,
            "veredicto": _veredicto(ps)}


__all__ = ["CAUSAS", "diagnosticar"]
# ⚠️ No se importa `NO_SE` a propósito, aunque las otras cadenas lo usen: acá
# **todo sale de nuestra base**. No hay ninguna llamada externa que pueda no
# contestar, así que un «no se pudo saber» sería un estado inalcanzable — y un
# estado que nunca se alcanza es peor que no tenerlo: sugiere que el diagnóstico
# depende de algo de afuera. No depende de nada.
