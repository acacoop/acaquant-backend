"""copiloto/renta_variable.py — vista RV (CEDEARs/ADRs): fetch, enriquecido,
screenings, rankings, detalle por ticker, fundamentals, pulso y reglas."""
from __future__ import annotations

import logging

from api.cache import cached

from .base import _celda, _detectar_tickers, _num, _pct, _wavg, _zona

logger = logging.getLogger(__name__)

def _fetch_cedears(params: dict | None = None) -> list[dict]:
    from api.services import scanner_sql

    return scanner_sql.get_cedears_scanner()


# ── Zonas de pivots (contexto implícito para TODO el universo) ──────────────
#
# Pedido de la mesa (2026-07-11): el asistente tiene que saber DÓNDE está
# parado cada papel respecto de sus pivots anual y mensual sin que se lo
# pregunten por ticker. Calcular los 187 vía get_pivot_points sería carísimo;
# en cambio, 2 queries agregadas sobre mercado.precios_acciones arman la vela
# del período previo de todo el universo y acá se clasifica la ZONA
# (">R3", "R2-R3", …) que entra como columna del TSV.

@cached(ttl=900)
def _velas_periodo_previo() -> dict:
    """{ticker: {"anual": levels, "mensual": levels}} para todo lo que haya en
    mercado.precios_acciones — H/L/C del año y mes calendario previos en dos
    queries agregadas (baratas e indexadas), no una por ticker."""
    from core.postgres import get_pool
    from quant.pivot_points import calcular

    queries = {
        "anual": """
            SELECT ticker, max(high), min(low),
                   (array_agg(close ORDER BY fecha DESC))[1]
            FROM mercado.precios_acciones
            WHERE fecha >= date_trunc('year', now()) - interval '1 year'
              AND fecha <  date_trunc('year', now())
              AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL
            GROUP BY ticker
        """,
        "mensual": """
            SELECT ticker, max(high), min(low),
                   (array_agg(close ORDER BY fecha DESC))[1]
            FROM mercado.precios_acciones
            WHERE fecha >= date_trunc('month', now()) - interval '1 month'
              AND fecha <  date_trunc('month', now())
              AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL
            GROUP BY ticker
        """,
    }
    out: dict[str, dict] = {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        for periodo, q in queries.items():
            cur.execute(q)
            for tk, h, low, c in cur.fetchall():
                out.setdefault(tk, {})[periodo] = calcular(
                    high=float(h), low=float(low), close=float(c)
                )
    return out


@cached(ttl=3600)
def _extremos_serie() -> dict:
    """{underlying: {"max": x, "min": y}} — máximo/mínimo histórico relevado:
    serie diaria viva (desde 2024) MERGEADA con mercado.precios_extremos_hist
    (extremos 2005→2024 destilados por scripts/backfill_extremos_hist.py — la
    decisión del user: la historia profunda no se carga, se destila en ≤2
    valores por papel y solo si superan a los de la serie)."""
    from core.postgres import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.ticker,
                   GREATEST(s.mx, coalesce(h.max_high, s.mx)),
                   LEAST(s.mn, coalesce(h.min_low, s.mn))
            FROM (
                SELECT ticker, max(high) AS mx, min(low) AS mn
                FROM mercado.precios_acciones
                WHERE high IS NOT NULL AND low IS NOT NULL
                GROUP BY ticker
            ) s
            LEFT JOIN mercado.precios_extremos_hist h USING (ticker)
            """
        )
        return {tk: {"max": float(mx), "min": float(mn)} for tk, mx, mn in cur.fetchall()}


@cached(ttl=900)
def _extremos_del_anio(under: str) -> dict | None:
    """Máximo/mínimo del AÑO EN CURSO del subyacente, con sus fechas, desde la
    serie diaria (mercado.precios_acciones). El dato que faltaba en el caso
    RKLB (2026-07-20). kwargs-only por el @cached."""
    from core.postgres import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT max(high), min(low),
                   (SELECT fecha FROM mercado.precios_acciones
                    WHERE ticker = %(t)s AND fecha >= date_trunc('year', current_date)
                    ORDER BY high DESC NULLS LAST LIMIT 1),
                   (SELECT fecha FROM mercado.precios_acciones
                    WHERE ticker = %(t)s AND fecha >= date_trunc('year', current_date)
                    ORDER BY low ASC NULLS LAST LIMIT 1)
            FROM mercado.precios_acciones
            WHERE ticker = %(t)s AND fecha >= date_trunc('year', current_date)
            """,
            {"t": under},
        )
        fila = cur.fetchone()
    if not fila or fila[0] is None or fila[1] is None:
        return None
    mx, mn, f_mx, f_mn = fila
    return {"max": float(mx), "min": float(mn),
            "fecha_max": f_mx.isoformat() if f_mx else "-",
            "fecha_min": f_mn.isoformat() if f_mn else "-"}


@cached(ttl=900)
def _retornos_ruedas() -> dict:
    """{underlying: {"r30": pct, "r45": pct}} — retornos a 30/45 RUEDAS (días
    de mercado) desde mercado.precios_acciones. Lente de trading pedido por la
    mesa (2026-07-11): complementa semana/mes/año calendario. Una sola query
    agregada para todo el universo."""
    from core.postgres import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH s AS (
                SELECT ticker, close,
                       row_number() OVER (PARTITION BY ticker ORDER BY fecha DESC) AS rn
                FROM mercado.precios_acciones
                WHERE close IS NOT NULL AND close > 0
                  AND fecha >= (now() - interval '120 days')::date
            )
            SELECT ticker,
                   max(close) FILTER (WHERE rn = 1),
                   max(close) FILTER (WHERE rn = 31),
                   max(close) FILTER (WHERE rn = 46)
            FROM s GROUP BY ticker
            """
        )
        out: dict[str, dict] = {}
        for tk, c0, c30, c45 in cur.fetchall():
            if not c0:
                continue
            out[tk] = {
                "r30": (float(c0) / float(c30) - 1) * 100 if c30 else None,
                "r45": (float(c0) / float(c45) - 1) * 100 if c45 else None,
            }
    return out


def _enriquecer_cedears(filas: list[dict]) -> list[dict]:
    """Suma piv_anual/piv_mensual (zona vs pivots del período previo) y
    ret_30r/ret_45r (retornos por ruedas). COPIA las filas: vienen del cache
    del scanner y mutarlas contaminaría lo que ve el tablero."""
    zonas = _velas_periodo_previo()
    try:
        ruedas = _retornos_ruedas()
    except Exception as e:
        logger.warning("copiloto: retornos por ruedas fallaron (%s)", e)
        ruedas = {}
    try:
        extremos = _extremos_serie()
    except Exception as e:
        logger.warning("copiloto: extremos de serie fallaron (%s)", e)
        extremos = {}
    out = []
    for f in filas:
        f = dict(f)
        under = str(f.get("underlying") or f.get("ticker_corto") or "").upper()
        z = zonas.get(under) or {}
        last = f.get("adr_last")
        f["piv_anual"] = _zona(last, z["anual"]) if last and z.get("anual") else None
        f["piv_mensual"] = _zona(last, z["mensual"]) if last and z.get("mensual") else None
        r = ruedas.get(under) or {}
        f["ret_30r"] = r.get("r30")
        f["ret_45r"] = r.get("r45")
        e = extremos.get(under) or {}
        f["max_serie"] = e.get("max")
        f["min_serie"] = e.get("min")
        f["dist_max"] = (float(last) / e["max"] - 1) * 100 if last and e.get("max") else None
        out.append(f)
    return out


def _screenings(filas: list[dict]) -> list[str]:
    """Screenings de mesa YA FILTRADOS por código (rezagados, zona de decisión,
    techos rotos). El modelo filtrando 187 filas × 3 condiciones respondía
    distinto en cada corrida y derramaba correcciones (shadow 2026-07-11) —
    la pertenencia a cada lista la decide el código, siempre igual."""
    def val(f: dict, campo: str) -> float | None:
        v = f.get(campo)
        return float(v) if v is not None else None

    rez, rebotes, decision, sin_techo, ia_dia = [], [], [], [], []
    for f in filas:
        anio, r15 = val(f, "adr_ret_ytd_pct"), val(f, "adr_ret_15r_pct")
        sem, tk = val(f, "adr_ret_wtd_pct"), f.get("ticker_corto")
        if not tk:
            continue
        if anio is not None and anio < 0 and sem is not None and sem > 0:
            if r15 is not None and r15 > 0:
                rez.append((r15, f"{tk} año {anio:+.1f}%, 15 ruedas {r15:+.1f}%, "
                                 f"semana {sem:+.1f}%"))
            else:
                rebotes.append(tk)
        if f.get("piv_anual") in ("PP-R1", "S1-PP") or f.get("piv_mensual") in ("PP-R1", "S1-PP"):
            decision.append((val(f, "total_money") or 0, tk))
        if f.get("piv_anual") == ">R3":
            sin_techo.append((anio or 0, tk))
        dia = val(f, "adr_vs_1d_pct")
        if f.get("es_ia") and dia is not None:
            ia_dia.append((dia, f"{tk} {dia:+.1f}%"))

    rez.sort(reverse=True)
    decision.sort(reverse=True)
    sin_techo.sort(reverse=True)
    ia_dia.sort(reverse=True)
    return [
        "[screenings ya calculados — la pertenencia la define el CÓDIGO; para "
        "'rezagados', 'zona de decisión' o 'rompieron techos' usá ESTAS listas tal cual]",
        "rezagados repuntando de verdad (año<0, 15 ruedas>0 y semana>0): "
        + ("; ".join(s for _, s in rez[:10]) if rez else "ninguno hoy"),
        "rebotes de corto (año<0 y semana>0, pero 15 ruedas todavía negativas): "
        + (", ".join(rebotes[:10]) if rebotes else "ninguno"),
        "en zona de decisión (equilibrio anual o mensual), por liquidez: "
        + (", ".join(t for _, t in decision[:10]) if decision else "ninguno"),
        "rompieron todos los techos del año pasado: "
        + (", ".join(t for _, t in sin_techo[:10]) if sin_techo else "ninguno"),
        "papeles de IA destacados HOY (solo ia=si, mejores del día): "
        + ("; ".join(s for _, s in ia_dia[:5]) if ia_dia else "sin datos"),
    ]


def _rankings(filas: list[dict]) -> list[str]:
    """Tops YA ordenados por código. El modelo ordenando 187 filas a ojo se
    comía al líder (shadow: faltó SNDK en el top del año) — esto lo hace
    determinista."""
    def top(campo: str, titulo: str, peor: bool = False) -> str:
        vals = sorted(
            ((f[campo], f.get("ticker_corto")) for f in filas if f.get(campo) is not None),
            reverse=not peor,
        )[:5]
        return f"{titulo}: " + ", ".join(f"{t} {v:+.2f}%" for v, t in vals)

    return [
        "[rankings ya calculados — para 'los que más/menos…' usá ESTOS, no ordenes a mano]",
        top("adr_ret_ytd_pct", "top año"),
        top("adr_ret_ytd_pct", "peores año", peor=True),
        top("adr_ret_mtd_pct", "top mes"),
        top("adr_ret_wtd_pct", "top semana"),
        top("adr_vs_1d_pct", "top día"),
    ]


# ── Bloques de detalle por ticker (vista renta_variable) ────────────────────
#
# La vista completa incluye datos POR TICKER (pivots, quant, retornos,
# fundamentals) que no pueden ir para los ~200 papeles en cada pregunta
# (explosión de tokens). Solución determinista, sin LLM: se detectan los
# tickers MENCIONADOS en la pregunta (match contra el universo) y solo esos
# bloques entran al contexto. Cap de 3 tickers por pregunta.

_MAX_ITEMS_FUNDAMENTALS = 20  # filas por estado contable (income/balance/…)


def _detalle_ticker(f: dict) -> list[str]:
    """Pivots + quant + retornos del subyacente. Cada bloque en su try:
    si un service falla, el detalle sale incompleto pero la pregunta sigue."""
    from api.services import scanner_sql

    tk = f["ticker_corto"]
    partes = [f"[detalle {tk} — subyacente {f.get('underlying') or tk} en USD]"]
    try:
        pv = scanner_sql.get_pivot_points(ticker=tk) or {}
        if pv.get("last") is not None:
            partes.append(f"último precio subyacente: {_num(pv['last'])} USD")
    except Exception as e:
        logger.warning("copiloto: last de %s falló (%s)", tk, e)
    # extremos del AÑO en curso (v1.69, caso RKLB: preguntaron "máximo del año",
    # el dato no existía en el contexto y el modelo re-etiquetó el R3 anual
    # como máximo — se le da el dato REAL para que no tenga que inventar)
    try:
        ex = _extremos_del_anio(under=str(f.get("underlying") or tk).upper())
        if ex:
            partes.append(
                f"extremos del AÑO en curso (desde el 1 de enero): máximo {_num(ex['max'])}"
                f" USD ({ex['fecha_max']}) · mínimo {_num(ex['min'])} USD ({ex['fecha_min']})"
            )
    except Exception as e:
        logger.warning("copiloto: extremos del año de %s fallaron (%s)", tk, e)
    try:
        for marco in ("diario", "semanal", "mensual", "anual"):
            fr = (pv.get("frames") or {}).get(marco)
            lv = (fr or {}).get("levels")
            if lv:
                partes.append(
                    f"pivots {marco}: PP {_num(lv.get('pp'))}"
                    f" · R1 {_num(lv.get('r1'))} R2 {_num(lv.get('r2'))} R3 {_num(lv.get('r3'))}"
                    f" · S1 {_num(lv.get('s1'))} S2 {_num(lv.get('s2'))} S3 {_num(lv.get('s3'))}"
                )
    except Exception as e:
        logger.warning("copiloto: pivots de %s fallaron (%s)", tk, e)
    try:
        q = scanner_sql.get_quant_stats(ticker=tk) or {}
        if q.get("n_observations"):
            partes.append(
                f"quant ({q['n_observations']} ruedas): beta SPY {_num(q['beta']['spy'])}"
                f" QQQ {_num(q['beta']['qqq'])} · corr SPY {_num(q['corr']['spy'])}"
                f" QQQ {_num(q['corr']['qqq'])}"
                f" · vol anualizada 30d {_pct(q['vol']['d30'], 1)} 60d {_pct(q['vol']['d60'], 1)}"
                f" · z-score último retorno 30d {_num(q['zscore']['d30'])}"
                f" 60d {_num(q['zscore']['d60'])}"
            )
    except Exception as e:
        logger.warning("copiloto: quant de %s falló (%s)", tk, e)
    try:
        rets = (scanner_sql.get_ticker_returns(ticker=tk) or {}).get("returns") or []
        if rets:
            partes.append(
                "últimos 15 retornos diarios del subyacente (viejo→nuevo): "
                + ", ".join(_pct(r) for r in rets[-15:])
            )
    except Exception as e:
        logger.warning("copiloto: retornos de %s fallaron (%s)", tk, e)
    partes.extend(_fundamentals_ticker(f))
    return partes


def _fundamentals_ticker(f: dict) -> list[str]:
    """Fundamentals Refinitiv (anual) si el subyacente está en research.companies.
    Cobertura hoy mínima (se amplía cargando empresas) — si no está, no aparece."""
    try:
        from api.services import research_fundamentals as rf

        under = str(f.get("underlying") or f.get("ticker_corto") or "").upper()
        comp = next(
            (c for c in rf.list_companies() if str(c.get("ticker") or "").upper() == under),
            None,
        )
        if not comp:
            return []
        a = rf.get_analisis(ric=comp["ric"], freq="FY") or {}
        partes = [f"[fundamentals {under} — {comp.get('nombre')} (Refinitiv, anual)]"]
        mk = a.get("market") or {}
        if mk:
            partes.append(
                f"mercado: precio {_num(mk.get('price'))} {mk.get('currency') or ''}"
                f" · market cap {_num(mk.get('market_cap'))} · 52w {_num(mk.get('low_52w'))}"
                f"-{_num(mk.get('high_52w'))} · div yield {_num(mk.get('div_yield'))}"
            )
        periodos = a.get("periodos") or []
        if periodos:
            partes.append("períodos: " + " | ".join(str(p) for p in periodos))
        for stmt in ("income", "balance", "cashflow", "ratios"):
            filas_t = (a.get("tablas") or {}).get(stmt) or []
            if filas_t:
                partes.append(f"{stmt}:")
                partes.extend(
                    f"  {r.get('item')}: " + " | ".join(_num(v) for v in (r.get("valores") or []))
                    for r in filas_t[:_MAX_ITEMS_FUNDAMENTALS]
                )
        margenes = a.get("margenes") or []
        if margenes:
            partes.append(
                "márgenes % (bruto/ebitda/operativo/neto): "
                + " | ".join(
                    f"{m.get('periodo')} {m.get('bruto')}/{m.get('ebitda')}"
                    f"/{m.get('operativo')}/{m.get('neto')}"
                    for m in margenes
                )
            )
        return partes
    except Exception as e:
        logger.warning("copiloto: fundamentals de %s fallaron (%s)", f.get("ticker_corto"), e)
        return []


def _pulso_por_rubro(filas: list[dict]) -> list[str]:
    """Agregado determinista por rubro. Regla de oro 1: la IA no promedia 187
    filas mentalmente — para preguntas de mercado/sector recibe los números ya
    calculados y solo los narra."""
    grupos: dict[str, list[dict]] = {}
    for f in filas:
        grupos.setdefault(f.get("rubro") or f.get("sector") or "OTROS", []).append(f)
    orden = sorted(
        grupos.items(),
        key=lambda kv: -sum(f.get("adr_dollar_vol") or 0 for f in kv[1]),
    )
    lineas = [
        "[pulso por rubro — retornos del subyacente USD, ponderados por volumen USD]",
        "rubro\tn\t1d%\twtd%\tmtd%\tytd%",
    ]
    for rubro, fs in orden:
        lineas.append("\t".join([
            _celda(rubro), str(len(fs)),
            _celda(_wavg(fs, "adr_vs_1d_pct")), _celda(_wavg(fs, "adr_ret_wtd_pct")),
            _celda(_wavg(fs, "adr_ret_mtd_pct")), _celda(_wavg(fs, "adr_ret_ytd_pct")),
        ]))
    return lineas


def _extras_renta_variable(
    filas: list[dict], pregunta: str, historial: list[dict], params: dict | None = None
) -> list[str]:
    partes: list[str] = []
    try:
        from api.services import scanner

        ccl = scanner.get_ccl_live() or {}
        if ccl.get("value") is not None:
            linea = f"[CCL live] {_num(ccl['value'])} ARS/USD"
            if ccl.get("vs_1d_pct") is not None:
                linea += f" · vs cierre anterior {ccl['vs_1d_pct']:+.2f}%"
            partes.append(linea)
    except Exception as e:
        logger.warning("copiloto: CCL live falló (%s)", e)
    try:
        partes.extend(_pulso_por_rubro(filas))
    except Exception as e:
        logger.warning("copiloto: pulso por rubro falló (%s)", e)
    try:
        partes.extend(_rankings(filas))
    except Exception as e:
        logger.warning("copiloto: rankings fallaron (%s)", e)
    try:
        partes.extend(_screenings(filas))
    except Exception as e:
        logger.warning("copiloto: screenings fallaron (%s)", e)
    for f in _detectar_tickers(filas, pregunta, historial):
        partes.extend(_detalle_ticker(f))
    return partes


# Registro de vistas: qué datos, qué columnas (subset relevante — menos tokens),
# qué módulo RBAC gatea, y las reglas del dominio que evitan que el modelo
# invente qué significa una columna (corrección silenciosa = el riesgo #1).
# `extras` (opcional): callable(filas, pregunta, historial) → bloques adicionales
# después de la tabla (CCL, detalle por ticker mencionado, fundamentals).
_REGLAS_RENTA_VARIABLE = """Significado de las columnas (tablero de CEDEARs, mercado argentino):
- ticker: el CEDEAR en BYMA. subyacente: la acción en NY. ratio: CEDEARs por 1 acción.
- ia: "si" = pertenece a la cadena de valor de INTELIGENCIA ARTIFICIAL. Para preguntas \
sobre acciones/papeles de IA usá SIEMPRE esta columna, no el nombre de la empresa.
- rubro: clasificación granular (más fina que sector).
- precio_ars/compra/venta/vwap: el CEDEAR en pesos. precio_usd_ny: la acción en USD en NY.
- var_apertura%: CEDEAR contra la apertura de hoy. var_dia%: contra el cierre anterior. \
var_dia_usd%: la variación del día en dólares (descuenta el CCL).
- ny_hoy% / ny_dia%: la acción en NY, hoy contra apertura / contra cierre anterior.
- ret_semana% / ret_7d% / ret_mes% / ret_año%: retornos de la acción en USD — semana en \
curso, 7 días, mes en curso, y AÑO CALENDARIO en curso (desde el 1° de enero).
- ret_15ruedas% / ret_30ruedas% / ret_45ruedas%: retornos por RUEDAS (días de mercado) — \
el lente de trading para leer tramos cortos y medios sin depender del calendario.
- REPUNTE (definición de la mesa): un papel repunta cuando venía cayendo en el tramo \
largo (año o 45 ruedas) Y se dio vuelta con consistencia en el corto (15 ruedas y semana \
positivas). Una semana verde aislada NO es repunte — llamalo "rebote de corto" y aclaralo.
- spread%: costo de entrar/salir (menor = más líquido). nominales/monto_ars: lo operado \
del CEDEAR. monto_usd_ny: lo operado de la acción en NY.
- zona_piv_año / zona_piv_mes: dónde está parado el papel respecto de los pivots del \
período previo (contexto de posición, ver abajo cómo usarlo).
- max_hist_usd / min_hist_usd: máximo y mínimo HISTÓRICOS del subyacente relevados desde \
2005. Al hablar decí "máximo histórico (desde 2005)". dist_al_max%: cuán lejos está del \
máximo (0 = en máximos históricos; -30 = un 30% abajo). Muy útil para "¿ya corrió \
demasiado?".

Cómo usar las zonas de pivots — SON CONTEXTO PARA TU LECTURA, NO VOCABULARIO: al usuario \
JAMÁS le digas "PP", "R1", "S2", "zona_piv" ni nomenclatura técnica, salvo que ÉL nombre \
pivots o niveles en su pregunta. Traducí la zona a lectura de mesa: ">R3" = rompió todos \
los techos del período; "R2-R3" = en plena tendencia alcista; "R1-R2" = subió y puede \
estirar a la próxima zona o enfriarse; "PP-R1"/"S1-PP" = en zona de equilibrio, punto de \
decisión; "S2-S1" = cayó y puede seguir o rebotar; "S3-S2" = tendencia bajista clara; \
"<S3" = perforó todos los pisos. Si tenés los niveles (bloque detalle), podés dar el \
PRECIO concreto ("tiene aire hasta la zona de 54"), nunca el nombre del nivel.

Bloques adicionales que pueden aparecer después de la tabla:
- [CCL live]: dólar contado con liquidación (ARS por USD), la referencia cambiaria del tablero.
- [pulso por rubro]: agregados YA CALCULADOS por rubro (retornos del subyacente en USD, \
ponderados por volumen). Para preguntas de mercado en general, sectores o rubros usá SIEMPRE \
estas líneas — NO promedies filas de la tabla a mano.
- [rankings ya calculados]: tops y peores del año/mes/semana/día ORDENADOS por código. \
Para cualquier "los que más/menos…" usá ESTOS — jamás ordenes las filas a mano.
- [detalle TICKER]: datos del subyacente en NY — pivots clásicos (PP punto pivote, R1-R3 \
resistencias, S1-S3 soportes; marcos diario/semanal/mensual/anual; en USD), quant (beta y \
correlación vs SPY y QQQ, volatilidad anualizada, z-score del último retorno) y últimos \
retornos diarios en %.
- [fundamentals TICKER]: estados contables anuales de Refinitiv (income/balance/cashflow/\
ratios, en la moneda indicada) + márgenes. Solo existe para las empresas cargadas en research.
- Los bloques de detalle SOLO se arman para los tickers nombrados en la pregunta. Si te piden \
pivots/beta/retornos/fundamentals de un papel y su bloque no está, pedile al usuario que \
nombre el ticker exacto en la pregunta."""

# ── Vista RENTA FIJA (página /renta-fija — curvas, fair value, forwards, BE) ─
#
# Acá el idioma es TEA/curva/forward/breakeven. El "caro o barato" del bono es
# su residuo contra el fit de fair value (el equivalente de la zona de pivots
# en trading). Todo precalculado por los motores; el copiloto narra.

_CHIPS_RENTA_VARIABLE = [
    {"label": "Papeles de IA",
     "pregunta": "En 5 líneas máximo y SOLO papeles de IA: ¿qué parte de la "
                 "cadena empuja hoy y cuál queda atrás? Cerrá con los 3 papeles "
                 "de IA más fuertes del día."},
    {"label": "Argentina",
     "pregunta": "¿Cómo vienen hoy los papeles argentinos? ¿El movimiento es "
                 "genuino en dólares o es efecto del CCL?"},
    {"label": "En zona de decisión",
     "pregunta": "¿Qué papeles líquidos están hoy en zona de decisión? Nombres y "
                 "una línea de lectura de conjunto."},
    {"label": "Rezagados repuntando",
     "pregunta": "Mostrame los rezagados que están repuntando de verdad. Tabla: "
                 "papel | año | 15 ruedas | semana, y una línea de qué tan sólido "
                 "es cada repunte."},
    {"label": "Voladores del año",
     "pregunta": "¿Qué papeles subieron más en el año? Top 5, y decime cuáles ya "
                 "rompieron todos los techos del año pasado."},
]

# Audiencia por rol RBAC (libro: "la audiencia manda más que el rol").
# El registro cambia según QUIÉN pregunta — decisión del user 2026-07-11.
