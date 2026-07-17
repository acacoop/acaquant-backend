"""copiloto/home.py — vista HOME (panorama del mercado): watchlist + briefing +
pulsos reusados de RF y RV, futuros DLR y reglas. La única vista que cruza otros
dominios (reusa fetch de renta_fija y renta_variable)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from .base import _celda
from .renta_fija import (
    _carry_canje_rf,
    _fetch_renta_fija,
    _retornos_precio_curva,
    _teas_cierre_anterior,
)
from .renta_variable import _fetch_cedears, _pulso_por_rubro

logger = logging.getLogger(__name__)

def _renta_fija_pulso() -> list[str]:
    """[renta fija hoy]: TEA promedio por curva y TRAMO (corto <6m / medio /
    largo >18m) + Δ promedio vs el último cierre en bps. El segmento más
    operado de la plaza — sin esto el pulso de HOME estaba ciego a los bonos
    (feedback del shadow 2026-07-12: 'no sabe de la renta fija, que en ARGY
    es donde más a full está'). Reusa los fetch cacheados de la vista RF."""
    try:
        filas = _fetch_renta_fija()
        cierre = _teas_cierre_anterior()
    except Exception as e:
        logger.warning("copiloto home: pulso RF falló (%s)", e)
        return []
    cierre.pop("_fecha", None)
    grupos: dict[tuple, list] = {}
    for f in filas:
        base = str(f.get("curva_label") or "").split(" (")[0]
        tea, m = f.get("tea"), f.get("meses_al_vto")
        if not base or tea is None or m is None:
            continue
        tramo = "corto" if m < 6 else ("medio" if m <= 18 else "largo")
        prev = cierre.get(f.get("ticker_corto"))
        grupos.setdefault((base, tramo), []).append(
            (tea, (tea - prev) * 100 if prev is not None else None)
        )
    por_curva: dict[str, list[str]] = {}
    orden = {"corto": 0, "medio": 1, "largo": 2}
    for (base, tramo), vals in sorted(grupos.items(),
                                      key=lambda kv: (kv[0][0], orden[kv[0][1]])):
        teas = [t for t, _ in vals]
        deltas = [d for _, d in vals if d is not None]
        seg = f"{tramo} {sum(teas) / len(teas):.1f}%"
        if deltas:
            seg += f" ({sum(deltas) / len(deltas):+.0f}bps hoy)"
        por_curva.setdefault(base, []).append(seg)
    if not por_curva:
        return []
    return (["[renta fija hoy — TEA promedio por curva y tramo; entre paréntesis el Δ "
             "vs el último cierre en bps (negativo = comprime = sube el precio). Si es "
             "fin de semana/feriado los Δ son ~0: decí que no hubo rueda nueva]"]
            + [f"{curva}: " + " · ".join(segs) for curva, segs in sorted(por_curva.items())])


def _renta_variable_pulso() -> list[str]:
    """[renta variable hoy]: el pulso por rubro del tablero de CEDEARs (el
    MISMO agregado determinista de la vista RV) — el segmento ACCIONES."""
    try:
        return _pulso_por_rubro(_fetch_cedears())
    except Exception as e:
        logger.warning("copiloto home: pulso RV falló (%s)", e)
        return []


def _fetch_home(params: dict | None = None) -> list[dict]:
    """Watchlist completa: métricas ARGY (MEP/CCL/canje/oficial/riesgo país/
    cauciones, con anclas día/7d/MTD/YTD ya calculadas) + home.market_quotes
    (futuros globales, índices, monedas). Cada parte en su try: si una fuente
    falla, la otra sigue."""
    filas: list[dict] = []
    try:
        from api.services import argy

        for m in argy.get_argy_with_returns() or []:
            filas.append({
                "symbol": m.get("label"), "grupo": "ARGENTINA",
                "last": m.get("value"), "unit": m.get("unit"),
                "pct_day": m.get("ret_day"), "ret_7d": m.get("ret_7d"),
                "ret_mtd": m.get("ret_mtd"), "ret_ytd": m.get("ret_ytd"),
            })
    except Exception as e:
        logger.warning("copiloto home: argy falló (%s)", e)
    try:
        from api.services import market_sql

        for q in market_sql.quotes() or []:
            filas.append({
                "symbol": q.get("symbol"), "grupo": q.get("grupo") or "-",
                "last": q.get("last"), "unit": None,
                "pct_day": q.get("pct_day"), "ret_7d": q.get("ret_7d"),
                "ret_mtd": q.get("ret_mtd"), "ret_ytd": q.get("ret_ytd"),
            })
    except Exception as e:
        logger.warning("copiloto home: market_quotes falló (%s)", e)
    return filas


def _briefing_bloque() -> list[str]:
    """[briefing de apertura]: el MISMO payload determinista del modal de las
    10:00 (briefing.py) — los follow-ups de esa tabla se responden con el
    número exacto que el usuario acaba de ver."""
    from api.services import briefing

    try:
        b = briefing.briefing_hoy() or {}
    except Exception as e:
        logger.warning("copiloto home: briefing falló (%s)", e)
        return []

    def linea(r: dict) -> str:
        seg = [f"hoy {_celda(r.get('hoy'))}"]
        for k, et in (("ret_1d", "1d"), ("ret_wtd", "sem"), ("ret_mtd", "mes")):
            v = r.get(k)
            seg.append(f"{et} {v:+.2f}%" if v is not None else f"{et} -")
        base = f"{r.get('label')}: " + " · ".join(seg)
        if r.get("stale"):
            base += " (dato viejo ⚠)"
        return base

    partes = [f"[briefing de apertura — la tabla del modal de las 10:00, {b.get('fecha')}]"]
    grupo = None
    for r in b.get("futuros") or []:
        if r.get("grupo") != grupo:
            grupo = r.get("grupo")
            partes.append(f"futuros {grupo}:")
        partes.append("  " + linea(r))
    for titulo, key in (("dólar oficial", "oficial"), ("dólares financieros", "financieros")):
        filas_b = b.get(key) or []
        if filas_b:
            partes.append(f"{titulo}: " + " | ".join(linea(r) for r in filas_b))
    pagan = b.get("pagan_hoy") or []
    partes.append(
        "bonos en cartera que pagan hoy: "
        + (", ".join(str(p.get("ticker")) + (f" ({p.get('emisor')})" if p.get("emisor") else "")
                     for p in pagan) if pagan else "ninguno")
    )
    return partes


def _futuros_dlr_bloque() -> list[str]:
    """[futuros DLR]: la curva ROFEX con su devaluación implícita (TNA lineal
    en %, convención del terminal Rofex), calculada por el motor. El snapshot
    live viene VACÍO fuera de rueda (verificado con diag_contexto en finde,
    batería 2026-07-12) → fallback al último CIERRE persistido, con la fecha
    declarada en el header."""
    from api.services import mercado_hist_sql

    lineas: list[str] = []
    origen = "live"
    try:
        for d in mercado_hist_sql.get_futuros_dlr() or []:
            px = d.get("last") or d.get("closing")
            if px is None:
                continue
            s = f"{d.get('ticker')} ({d.get('dias_a_vto')}d): {float(px):.1f}"
            tna = d.get("tasa_implicita_tna")
            if tna is not None:
                s += f" · TNA implícita {float(tna):.1f}%"
            lineas.append(s)
    except Exception as e:
        logger.warning("copiloto home: futuros DLR live fallaron (%s)", e)

    if not lineas:
        try:
            from api.services import derivados

            desde = (datetime.now(UTC).date() - timedelta(days=7)).isoformat()
            docs = derivados.get_historico_futuros_dlr(desde=desde) or []  # @cached → kwargs
            ultima = max((str(d.get("fecha"))[:10] for d in docs), default=None)
            hoy = datetime.now(UTC).date().isoformat()
            for d in docs:
                if str(d.get("fecha"))[:10] != ultima:
                    continue
                if str(d.get("vencimiento") or "") <= hoy.replace("-", ""):
                    continue
                px = d.get("precio_cierre")
                if px is None:
                    continue
                s = f"{d.get('ticker')} ({d.get('dias_a_vto')}d): {float(px):.1f}"
                tna = d.get("tasa_implicita_tna_cierre")
                if tna is not None:
                    s += f" · TNA implícita {float(tna):.1f}%"
                lineas.append(s)
            origen = f"cierre del {ultima}"
        except Exception as e:
            logger.warning("copiloto home: futuros DLR históricos fallaron (%s)", e)

    if not lineas:
        return []
    return [f"[futuros DLR ROFEX ({origen}) — precio del dólar futuro y devaluación "
            "implícita (TNA lineal %)]"] + lineas[:12]


def _extras_home(
    filas: list[dict], pregunta: str, historial: list[dict], params: dict | None = None
) -> list[str]:
    # Un bloque por SEGMENTO del mercado (renta fija · acciones · dólar futuro
    # · briefing global) — la narración recorre segmentos, jamás los mezcla.
    partes: list[str] = []
    partes.extend(_renta_fija_pulso())
    try:
        partes.extend(_retornos_precio_curva())
    except Exception as e:
        logger.warning("copiloto home: retornos por curva fallaron (%s)", e)
    partes.extend(_carry_canje_rf())
    partes.extend(_renta_variable_pulso())
    partes.extend(_futuros_dlr_bloque())
    partes.extend(_briefing_bloque())
    return partes


_REGLAS_HOME = """Sos el copiloto de la HOME — el panorama general del mercado. Acá se \
pregunta "¿qué está pasando?": tu trabajo es DESCRIBIR el pulso del día segmento por \
segmento, no el detalle fino (para eso están las otras vistas — derivá como siempre). \
DESCRIBIR Y COMPARAR lo que dicen los bloques: sí. Explicar por qué se movió o armar una \
historia que cruce segmentos por causa: no (regla de oro del sistema).

Columnas de la tabla (la watchlist): instrumento · grupo (ARGENTINA, índices, energía, \
metales, granos, cripto, monedas, futuros ROFEX…) · valor (con su unidad: $ = pesos; \
% = el instrumento ES una tasa o brecha) · var_dia% · ret_7d% · ret_mes% · ret_año%.
- CANJE: la brecha CCL/MEP en %. Se ABRE cuando el CCL le gana al MEP (tensión, demanda \
de girar dólares afuera); se CIERRA cuando convergen. Su var_dia% es variación relativa \
del valor, no puntos.
- RIESGO PAIS: en puntos básicos (bps). CAUCION: tasas en % anual.
- DOLAR OFICIAL: mayorista MAE en vivo. Si sus plazos largos vienen "-" es porque el \
histórico todavía no existe — decilo tal cual, jamás lo estimes.

Bloques después de la tabla — UNO POR SEGMENTO del mercado:
- [renta fija hoy]: TEA promedio por curva y TRAMO con su Δ vs el cierre en bps — acá \
leés si comprime la parte corta o la larga, y si el movimiento es de los CER, la tasa \
fija, los soberanos o los dollar-linked (son curvas DISTINTAS, nombralas por separado).
- [retorno de PRECIO por curva] y [carry y canje]: los bonos en trazo grueso por ventana.
- [pulso por rubro]: el segmento ACCIONES (CEDEARs/ADRs, retornos en USD por rubro, ya \
ponderados).
- [futuros DLR ROFEX]: la curva de dólar futuro con la devaluación implícita (TNA lineal).
- [briefing de apertura]: la tabla del modal de las 10:00 — futuros globales agrupados, \
dólar oficial y financieros, y los bonos en cartera que pagan hoy.

EL MERCADO NO ES UNO — SEGMENTÁ SIEMPRE: renta fija, acciones, dólares/tasas y \
commodities/índices son mundos distintos que JAMÁS se mezclan en una misma conclusión \
("mandan los granos" no dice nada de los bonos ni del dólar). Para describir el día o \
responder "¿cómo viene el mercado?", recorré los segmentos EN ESTE ORDEN, 2-3 frases \
por segmento, salteando los que no tengan nada para decir:
1. RENTA FIJA (lo más operado de la plaza): de [renta fija hoy] — qué curva se mueve y \
en qué tramo.
2. ACCIONES: son DOS cosas distintas y NO se explican una con la otra. El MERVAL de la \
tabla son acciones argentinas: su número y sus plazos, NADA MÁS — jamás le atribuyas \
sectores ni "liderado por X" (esa info no está en tus datos). El [pulso por rubro] es de \
CEDEARs/ADRs (acciones de EE.UU.): otro mundo, con sus rubros. Nunca expliques el MERVAL \
con los rubros de CEDEARs ni al revés.
3. DÓLARES Y TASAS: MEP/CCL/oficial, el canje (¿se abre o se cierra?) y las cauciones.
4. COMMODITIES E ÍNDICES GLOBALES: de la watchlist y el briefing, POR GRUPO (granos ≠ \
energía ≠ metales ≠ índices ≠ cripto).
5. Cierre: UNA frase con qué mirar en la rueda; los bonos que pagan hoy solo si hay.

PROHIBIDO PROMEDIAR GRUPOS A MANO: si querés decir "los granos vienen fuertes", nombrá \
el que más se mueve con SU número exacto de la tabla ("maíz +7.6%") — nunca un promedio \
o cifra de grupo que no esté precalculada en los bloques.

LA ESTRUCTURA POR SEGMENTOS ES SOLO PARA PREGUNTAS DE PANORAMA ("¿cómo viene el \
mercado?", "narrame el día"). Una pregunta PUNTUAL (el MERVAL, un dólar, un futuro, el \
riesgo país) se responde PUNTUAL: su dato con sus plazos y listo — jamás recorras los \
demás segmentos que nadie pidió.

LÍMITES DE ESTA VISTA (cuándo derivar y cuándo no):
- El [pulso por rubro] es SOLO para el segmento acciones del panorama. Si la pregunta es \
DEDICADA a acciones/CEDEARs/rubros/papeles ("¿qué compro?", "¿cómo están las acciones?", \
"¿qué rubros empujan?"), respondé el titular en 1-2 frases y DERIVÁ a Renta Variable con \
el marcador — una recomendación o análisis de papeles puntuales JAMÁS se intenta desde \
acá, ni pidiéndole la lista al usuario.
- UN DATO FALTANTE DE ESTA VISTA JAMÁS ES MOTIVO DE DERIVACIÓN. Las cauciones, los \
futuros DLR y todo lo de la watchlist son de ACÁ: si el valor está en "-" o un bloque no \
aparece (ej. fin de semana, fuera de horario), decí que el instrumento existe pero sin \
dato en este momento — no lo mandes a otra vista (las otras tampoco lo tienen) ni \
inventes vistas que no están en tu lista.
- Derivá SOLO cuando la pregunta cae de lleno en el dominio de otra vista de tu lista; \
ante la duda, respondé con lo tuyo y no derives.

REGLA DE HONESTIDAD — la más importante de esta vista: tus datos dicen QUÉ se movió, \
nunca POR QUÉ. Ante un "¿por qué subió/bajó?" respondés el movimiento con sus plazos y \
aclarás que el motivo no está en tus datos. PROHIBIDO inventar causas macro, políticas o \
noticias de memoria. Describir y cruzar sí; explicar causas no."""


# Biblioteca de consultas de mesa (libro de finanzas, elegidas por el user
# 2026-07-11): chips de un click en el panel. El prompt curado vive ACÁ,
# versionado — es conocimiento institucional, no texto libre del usuario.
