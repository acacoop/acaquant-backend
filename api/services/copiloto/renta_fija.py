"""copiloto/renta_fija.py — vista RF (bonos ARG): curvas, fair value, forwards,
breakevens, carry/canje, spread de legislación, estrategia y reglas."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from api.cache import cached

from .base import _detectar_tickers, _pct

logger = logging.getLogger(__name__)

_CURVAS_RF = ("cer", "tasa_fija", "soberanos", "dolar_linked")
_CURVAS_FIT = ("cer", "tasa_fija")  # fair value solo existe para estas
_MAX_RESIDUO_REAL_BPS = 500  # residuo mayor = precio viejo/iliquidez → se excluye
# |carry USD| 14d mayor = dato roto → se excluye. OJO: se compara contra el
# valor TAL CUAL viene del service, que ya está EN PORCENTAJE.
_MAX_CARRY_REAL_PCT = 15


def _fetch_renta_fija(params: dict | None = None) -> list[dict]:
    """Una fila por bono de las 4 curvas de la vista, con el fair value
    mergeado (tea teórica + residuo) donde existe."""
    from api.services import fair_value, renta_fija

    fv_por_ticker: dict[str, dict] = {}
    for curva in _CURVAS_FIT:
        try:
            for b in (fair_value.get_fair_value_live(curva=curva) or {}).get("bonos") or []:
                fv_por_ticker[b.get("ticker_corto") or b.get("ticker")] = b
        except Exception as e:
            logger.warning("copiloto rf: fair value %s falló (%s)", curva, e)

    tc_por_corto: dict[str, float] = {}
    try:
        for r in renta_fija.get_renta_fija() or []:
            tc = (r.get("metrics") or {}).get("tc_breakeven")
            inst = str(r.get("instrumento") or "")
            if tc is not None and " - " in inst:
                tc_por_corto[inst.split(" - ")[2]] = tc
    except Exception as e:
        logger.warning("copiloto rf: tc_breakeven falló (%s)", e)

    filas = []
    for curva in _CURVAS_RF:
        try:
            bonos = renta_fija.listar_curva(curva=curva)
        except Exception as e:
            logger.warning("copiloto rf: listar_curva %s falló (%s)", curva, e)
            continue
        for b in bonos:
            f = dict(b)
            tipo = f.get("tipo")
            etiqueta = tipo if curva == "soberanos" and tipo else curva
            if f.get("cer_fijado"):
                etiqueta = "tasa_fija (CER fijado)"
            f["curva_label"] = etiqueta
            fv = fv_por_ticker.get(f.get("ticker_corto")) or {}
            f["tea_teorica"] = fv.get("tea_teorica")
            f["residuo_bps"] = fv.get("residuo_bps")
            f["tc_breakeven"] = tc_por_corto.get(f.get("ticker_corto"))
            # Los services devuelven TEA/TEM en FRACCIÓN (0.39 = 39%) — acá se
            # normaliza a % para el TSV (batería 2026-07-12: 'lecaps al 0.22%
            # TEA' era esto). paridad ya viene en escala 100.
            for k in ("tea", "tem", "tea_teorica"):
                if f.get(k) is not None:
                    f[k] = float(f[k]) * 100
            filas.append(f)
    return filas


def _teas_cierre_anterior() -> dict[str, float]:
    """{ticker_corto: TEA del último cierre persistido} — para el delta del
    día (mercado.snapshots_cierre_hist, escrito por jobs.snapshot_cierre)."""
    from core.postgres import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT ticker_corto, tea, fecha FROM mercado.snapshots_cierre_hist
            WHERE fecha = (SELECT max(fecha) FROM mercado.snapshots_cierre_hist
                           WHERE fecha < now()::date)
              AND tea IS NOT NULL
            """
        )
        filas = cur.fetchall()
    # tea persiste en FRACCIÓN → a % (misma normalización que el fetch)
    teas = {r[0]: float(r[1]) * 100 for r in filas if r[0]}
    teas["_fecha"] = str(filas[0][2]) if filas else None  # para el header del bloque
    return teas


def _movimientos_dia_rf(filas: list[dict]) -> list[str]:
    """[movimientos del día]: Δ de TEA hoy vs último cierre, por código —
    pedido del user: la curva se VE en el gráfico; lo que falta es cómo se
    MOVIÓ."""
    try:
        cierre = _teas_cierre_anterior()
    except Exception as e:
        logger.warning("copiloto rf: teas de cierre fallaron (%s)", e)
        return []
    fecha_cierre = cierre.pop("_fecha", None)
    deltas = []
    for f in filas:
        tk, tea = f.get("ticker_corto"), f.get("tea")
        prev = cierre.get(tk)
        if tea is None or prev is None:
            continue
        deltas.append((abs(tea - prev), f"{tk} {(tea - prev) * 100:+.0f}bps "
                                        f"({prev:.1f}%→{tea:.1f}%)"))
    if not deltas:
        return ["[movimientos del día] sin cierre previo para comparar"]
    deltas.sort(reverse=True)
    cab = f"[movimientos — TEA actual vs cierre del {fecha_cierre}, en bps" \
          if fecha_cierre else "[movimientos — TEA actual vs último cierre, en bps"
    return [cab + "; si es fin de semana/feriado los valores coinciden — decilo, no "
                  "hay rueda nueva] " + "; ".join(s for _, s in deltas[:12])]


def _baratos_caros_rf() -> list[str]:
    """[baratos y caros vs la curva]: ranking determinista de residuos del
    fair value (positivo = paga MÁS que la curva = barato)."""
    from api.services import fair_value

    partes = []
    for curva in _CURVAS_FIT:
        try:
            fv = fair_value.get_fair_value_live(curva=curva) or {}
        except Exception as e:
            logger.warning("copiloto rf: fv %s falló (%s)", curva, e)
            continue
        # SOLO señales reales (directiva del user): los residuos absurdos
        # (>500bps = precio viejo / bono que no opera) se EXCLUYEN acá — el
        # modelo nunca los ve como candidatos.
        bonos = [
            b for b in fv.get("bonos") or []
            if b.get("residuo_bps") is not None
            and abs(b["residuo_bps"]) <= _MAX_RESIDUO_REAL_BPS
        ]
        if not bonos:
            continue
        bonos.sort(key=lambda b: -b["residuo_bps"])
        r2 = fv.get("r2")
        partes.append(
            f"{curva} (fit r²={r2:.2f})"
            + (" — fit flojo, cautela" if r2 and r2 < 0.9 else "")
            + " · baratos: "
            + ", ".join(f"{b.get('ticker_corto')} {b['residuo_bps']:+.0f}bps"
                        for b in bonos[:3])
            + " · caros: "
            + ", ".join(f"{b.get('ticker_corto')} {b['residuo_bps']:+.0f}bps"
                        for b in bonos[-3:][::-1])
        )
    return (["[baratos y caros vs la curva — residuo del fair value; positivo = rinde "
             "MÁS que la curva. Ya FILTRADO por código: los residuos >500bps (precio "
             "viejo/iliquidez) no aparecen — todo lo listado es señal operable]"]
            + partes) if partes else []


def _forwards_rf(filas: list[dict], pregunta: str, historial: list[dict]) -> list[str]:
    """[forwards]: los pares más desarbitrados contra su historia (z por
    código) + el forward puntual si la pregunta nombra dos bonos."""
    from api.services import mercado_hist_sql

    try:
        # solo curvas de ESTA vista — las ONs tienen su propia vista y sus
        # forwards contaminaban el bloque (diag 2026-07-12: on_energia z±3.5)
        docs = {d.get("curva"): d for d in mercado_hist_sql.get_forwards() or []
                if not str(d.get("curva") or "").startswith("on")}
        zs = {d.get("curva"): d for d in mercado_hist_sql.get_forwards_zscore() or []
              if not str(d.get("curva") or "").startswith("on")}
    except Exception as e:
        logger.warning("copiloto rf: forwards fallaron (%s)", e)
        return []
    extremos = []
    for curva, doc in docs.items():
        stats = (zs.get(curva) or {}).get("stats") or {}
        for largo, fila_m in (doc.get("matrix") or {}).items():
            for corto, fwd in (fila_m or {}).items():
                st = (stats.get(largo) or {}).get(corto) or {}
                media, desvio = st.get("media"), st.get("desvio")
                if fwd is None or media is None or not desvio:
                    continue
                z = (fwd - media) / desvio
                if abs(z) >= 1.5:
                    # tasas en fracción → % para el contexto
                    extremos.append((abs(z), f"{corto}→{largo} ({curva}) fwd {fwd * 100:.1f}% "
                                             f"vs media {media * 100:.1f}% · z {z:+.1f}"))
    extremos.sort(reverse=True)
    partes = []
    if extremos:
        partes.append("[forwards desarbitrados — z contra su propia historia] "
                      + "; ".join(s for _, s in extremos[:5]))
    # par puntual si nombró dos bonos de la misma curva
    nombrados = [f["ticker_corto"] for f in _detectar_tickers(
        [{"ticker_corto": t} for d in docs.values() for t in d.get("tickers") or []],
        pregunta, historial)]
    if len(nombrados) >= 2:
        a, b = nombrados[0], nombrados[1]
        for curva, doc in docs.items():
            m = doc.get("matrix") or {}
            fwd = (m.get(b) or {}).get(a) or (m.get(a) or {}).get(b)
            if fwd is not None:
                tasas = doc.get("tasas") or {}
                partes.append(
                    f"[forward {a}↔{b} ({curva})] implícito {fwd * 100:.2f}% · "
                    f"spot {a} {tasas.get(a, 0) * 100:.2f}% · {b} {tasas.get(b, 0) * 100:.2f}%"
                )
                break
    return partes


def _rem_promedio_hasta(serie: list[dict], fecha_vto: str | None) -> float | None:
    """Promedio mensual geométrico del REM desde hoy hasta el mes del
    vencimiento (%). serie viene de rem_sql.breakeven_acumulado (fracciones)."""
    if not fecha_vto or not serie:
        return None
    for item in serie:
        fin = item.get("fin_mes")
        if fin and str(fin) >= str(fecha_vto)[:10]:
            v = item.get("promedio_mensual_acum")
            return v * 100 if v is not None else None
    ultimo = serie[-1].get("promedio_mensual_acum")
    return ultimo * 100 if ultimo is not None else None


def _breakevens_rf() -> list[str]:
    """[breakevens + señal]: cada par lecap-CER con su inflación implícita YA
    CRUZADA contra el REM del mismo horizonte (la resta la hace código —
    regla TIPS clásica: esperás inflación > breakeven → CER; < → tasa fija)."""
    from api.services import mercado_hist_sql, rem_sql

    try:
        docs = mercado_hist_sql.get_breakevens() or []
        pares = (docs[0] if docs else {}).get("pares") or []  # devuelve LISTA de docs
    except Exception as e:
        logger.warning("copiloto rf: breakevens fallaron (%s)", e)
        return []
    if not pares:
        return []
    serie_rem: list[dict] = []
    informe = None
    try:
        rem = rem_sql.breakeven_acumulado() or {}
        serie_rem, informe = rem.get("serie") or [], rem.get("informe")
    except Exception as e:
        logger.warning("copiloto rf: REM falló (%s)", e)

    lineas = []
    for p in pares:
        be = p.get("breakeven_mensual")
        if be is None:
            continue
        be = float(be) * 100  # el motor lo guarda en fracción
        base = f"{p.get('lecap')}/{p.get('cer')} a {p.get('dias')}d: mercado {be:.2f}%/mes"
        rem_pm = _rem_promedio_hasta(serie_rem, p.get("fecha_vencimiento"))
        if rem_pm is not None:
            diff = be - rem_pm
            if diff > 0.15:
                senal = "breakeven CARO → favorece TASA FIJA si el REM acierta"
            elif diff < -0.15:
                senal = "breakeven BARATO → favorece CER si el REM acierta"
            else:
                senal = "en línea con el REM"
            base += f" vs REM {rem_pm:.2f}%/mes · diff {diff:+.2f}pp · {senal}"
        lineas.append(base)
    if not lineas:
        return []
    cab = ("[breakevens lecap-CER + señal vs REM"
           + (f" (informe {informe})" if informe else "") + "] ")
    return [cab + "; ".join(lineas)]


def _resumen_curvas_rf(filas: list[dict]) -> list[str]:
    """[curvas por tramo]: TEA promedio corto/medio/largo y empinamiento por
    curva — la base determinista para 'corto o largo' (carry & rolldown)."""
    grupos: dict[str, list[dict]] = {}
    for f in filas:
        base = str(f.get("curva_label") or "").split(" (")[0]
        if base and f.get("tea") is not None and f.get("meses_al_vto") is not None:
            grupos.setdefault(base, []).append(f)
    lineas = []
    for curva, fs in sorted(grupos.items()):
        def prom(sel: list[dict]) -> float | None:
            teas = [b["tea"] for b in sel]
            return sum(teas) / len(teas) if teas else None

        corto = prom([b for b in fs if b["meses_al_vto"] < 6])
        medio = prom([b for b in fs if 6 <= b["meses_al_vto"] <= 18])
        largo = prom([b for b in fs if b["meses_al_vto"] > 18])
        seg = [f"corto {corto:.1f}%" if corto is not None else None,
               f"medio {medio:.1f}%" if medio is not None else None,
               f"largo {largo:.1f}%" if largo is not None else None]
        linea = f"{curva} ({len(fs)}): " + " · ".join(s for s in seg if s)
        if corto is not None and largo is not None:
            linea += f" · empinamiento {largo - corto:+.1f}pp"
        lineas.append(linea)
    return (["[curvas por tramo — TEA promedio; empinamiento = largo − corto]"]
            + lineas) if lineas else []


# Herramientas de la vista ESTRATEGIA disponibles para el copiloto RF
# (pedido del user 2026-07-12): la página es otra, pero el ANÁLISIS es de
# renta fija — son services puros de mercado y se activan bajo demanda
# (al nombrar bonos), así no engordan el contexto base.


def _comparar_bonos_rf(fa: dict, fb: dict) -> list[str]:
    """[comparar A vs B]: flujos de invertir ARS 1.000.000 hoy en cada uno
    (service comparar_inversion, el de la vista Estrategia)."""
    from api.services import comparar_inversion

    a, b = fa.get("ticker_corto"), fb.get("ticker_corto")
    try:
        # @cached → SIEMPRE kwargs (batería 2026-07-12: posicional explotaba)
        r = comparar_inversion.comparar(
            a_id=f"curvas:{a}", b_id=f"curvas:{b}", monto=1_000_000.0, moneda_input="ARS",
        )
    except Exception as e:
        logger.warning("copiloto rf: comparar %s/%s falló (%s)", a, b, e)
        return []
    if not r or r.get("error"):
        return []
    lineas = [f"[comparar {a} vs {b} — flujos de invertir ARS 1000000 hoy]"]
    for lado, tk in (("a", a), ("b", b)):
        p = r.get(lado) or {}
        flujos = p.get("flujos") or []
        total = sum(x.get("monto") or 0 for x in flujos)
        det = (f"{tk}: cobra {total:.0f} {p.get('moneda') or ''} en {len(flujos)} pagos"
               + (f" hasta {p.get('vencimiento')}" if p.get("vencimiento") else ""))
        if p.get("cer_proyectado"):
            det += " (flujos CER proyectados con CER constante, no con inflación)"
        lineas.append(det)
    warns = (r.get("meta") or {}).get("warnings") or []
    if warns:
        lineas.append("advertencias: " + ", ".join(str(w) for w in warns))
    return lineas


def _sensibilidad_rf(ticker: str) -> list[str]:
    """[sensibilidad TICKER]: precio objetivo ante escenarios de TIR (solo
    soberanos; upside de PRECIO, sin carry)."""
    from api.services import sensibilidad

    try:
        # modo RELATIVA con shocks: el trader pregunta "¿y si comprime 2
        # puntos?" — los escenarios vienen como ±pp, no como TIRs absolutas
        # (shadow: el modelo restaba TIR−2 a mano → verificación lo bloqueaba)
        # el service trabaja en FRACCIONES (defaults 0.04..0.11) → los shocks
        # también: ±0.005/0.01/0.02 = ±0.5/1/2 puntos porcentuales
        filas_s = sensibilidad.sensibilidad_retorno_total(
            curva="soberanos", modo="relativa",
            tirs=(-0.02, -0.01, -0.005, 0.005, 0.01, 0.02),
        )
    except Exception as e:
        logger.warning("copiloto rf: sensibilidad falló (%s)", e)
        return []
    row = next((x for x in filas_s or [] if x.get("ticker") == ticker), None)
    if not row:
        return []
    escenarios = ", ".join(
        f"TIR {e['shock_pp'] * 100:+.1f}pp (a {e.get('tir') * 100:.1f}%) → precio "
        f"{e.get('precio_objetivo'):.1f} ({_pct(e.get('upside'))})"
        for e in (row.get("escenarios") or [])
        if e.get("shock_pp") is not None and e.get("precio_objetivo") is not None
    )
    if not escenarios:
        return []
    tea_act = row.get("tea_actual")
    cab = f"[sensibilidad {ticker} — precio hoy {row.get('precio_actual')}"
    if tea_act is not None:
        cab += f", TIR {tea_act * 100:.1f}%"
    cab += f", dur {row.get('duration')}]"
    return [f"{cab} {escenarios} (upside de PRECIO solamente, sin carry)"]


def _descomposicion_rf(ticker: str, etiqueta: str) -> list[str]:
    """[descomposición TICKER 30d]: qué explicó el retorno del último mes —
    carry, rolldown y movimiento de tasa (curvas cer/tasa_fija)."""
    from api.services import descomposicion_retorno

    curva = "cer" if etiqueta.startswith("cer") else "tasa_fija"
    hoy = datetime.now(UTC).date()
    try:
        r = descomposicion_retorno.descomposicion_realizada(
            desde=(hoy - timedelta(days=30)).isoformat(), hasta=hoy.isoformat(),
            curva=curva,
        )
    except Exception as e:
        logger.warning("copiloto rf: descomposición %s falló (%s)", ticker, e)
        return []
    if not r or r.get("error"):
        return []
    # Claves VERIFICADAS contra descomposicion_realizada: `bonos[]`, cada uno
    # con ticker/ticker_corto (no hay `detalle` ni `label` — eran fallbacks
    # inventados que tapaban un cambio de shape en vez de exponerlo).
    bono = next((x for x in (r.get("bonos") or [])
                 if ticker in (x.get("ticker_corto"), x.get("ticker"))), None)
    if not bono or bono.get("r_total") is None:
        return []
    linea = (f"[descomposición {ticker} — últimos 30 días] retorno {_pct(bono['r_total'])}"
             f" = carry {_pct(bono.get('carry'))} + rolldown {_pct(bono.get('rolldown'))}"
             f" + Δtasa {_pct(bono.get('cambio_tasa'))}")
    if bono.get("cer_accrual") is not None:
        linea += f" + ajuste CER {_pct(bono.get('cer_accrual'))}"
    return [linea]


def _precio_puntapunta_rf(nombrados: list[dict]) -> list[str]:
    """[precio punta a punta TICKER]: retorno de PRECIO real (cierre a cierre)
    de cada bono nombrado en ventanas 7d/14d/30d, desde snapshots_cierre_hist.
    Es el dato crudo 'precio final / precio inicial − 1' — distinto de la
    descomposición (que es un modelo). Nace del fallo real 2026-07-16: se pedía
    'performance de TZXD6 en junio' / 'punta a punta' y NO existía ese número en
    el contexto → el modelo lo inventaba y hasta reusaba el precio de otro bono."""
    tickers = [str(f.get("ticker_corto")) for f in nombrados if f.get("ticker_corto")]
    if not tickers:
        return []
    from core.postgres import get_pool

    hoy = datetime.now(UTC).date()
    cortes = {"7d": hoy - timedelta(days=7), "14d": hoy - timedelta(days=14),
              "30d": hoy - timedelta(days=30)}
    lineas: list[str] = []
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT ticker_corto, fecha, ultimo_precio
                FROM mercado.snapshots_cierre_hist
                WHERE ticker_corto = ANY(%s) AND fecha >= now()::date - 45
                  AND ultimo_precio IS NOT NULL AND ultimo_precio > 0
                ORDER BY ticker_corto, fecha
                """,
                (tickers,),
            )
            series: dict[str, list] = {}
            for tk, fecha, px in cur.fetchall():
                series.setdefault(tk, []).append((fecha, float(px)))
    except Exception as e:
        logger.warning("copiloto rf: precio punta a punta falló (%s)", e)
        return []
    for tk in tickers:
        pts = series.get(tk)
        if not pts:
            continue
        f_ult, px_ult = pts[-1]
        segs = [f"cierre {f_ult:%d/%m} {px_ult:.2f}"]
        for etiqueta, corte in cortes.items():
            base = next(((f, px) for f, px in reversed(pts) if f <= corte), None)
            if base:
                f_base, px_base = base
                segs.append(f"{etiqueta} (desde {f_base:%d/%m} {px_base:.2f}) "
                            f"{(px_ult / px_base - 1) * 100:+.2f}%")
        lineas.append(f"[precio punta a punta {tk} — retorno de PRECIO cierre a cierre] "
                      + " · ".join(segs))
    return lineas


@cached(ttl=900)
def _retornos_precio_curva() -> list[str]:
    """[retorno de precio por curva] — mediana por curva en 7d/14d/MTD desde
    los cierres históricos. Pedido del user: el tablero de /retorno mapeado
    acá. HONESTIDAD: es retorno de PRECIO (en bullets cortos ≈ retorno total;
    en hard dollar excluye cupones — la cifra exacta vive en /retorno)."""
    from statistics import median

    from core.postgres import get_pool

    hoy = datetime.now(UTC).date()
    cortes = {"7d": hoy - timedelta(days=7), "14d": hoy - timedelta(days=14),
              "MTD": hoy.replace(day=1)}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT curva, ticker_corto, fecha, ultimo_precio
            FROM mercado.snapshots_cierre_hist
            WHERE fecha >= now()::date - 50 AND ultimo_precio IS NOT NULL
              AND ultimo_precio > 0
            ORDER BY curva, ticker_corto, fecha
            """
        )
        series: dict[tuple, list] = {}
        for curva, tk, fecha, px in cur.fetchall():
            series.setdefault((curva, tk), []).append((fecha, float(px)))

    rets: dict[str, dict[str, list[float]]] = {}
    for (curva, _tk), pts in series.items():
        ultimo = pts[-1][1]
        for etiqueta, corte in cortes.items():
            base = next((px for f, px in reversed(pts) if f <= corte), None)
            if base:
                rets.setdefault(curva, {}).setdefault(etiqueta, []).append(
                    (ultimo / base - 1) * 100)
    lineas = []
    for curva in sorted(rets):
        seg = [f"{et} {median(vals):+.1f}%" for et in ("7d", "14d", "MTD")
               if (vals := rets[curva].get(et))]
        if seg:
            lineas.append(f"{curva} ({len(rets[curva].get('7d') or [])} bonos): "
                          + " · ".join(seg))
    return (["[retorno de PRECIO por curva — mediana por ventana; en bullets cortos "
             "≈ retorno total, en hard dollar EXCLUYE cupones]"] + lineas) if lineas else []


def _carry_canje_rf() -> list[str]:
    """[carry en USD] (últimos 14d vía MEP, por curva) + [canje] — los otros
    dos tableros de /retorno mapeados al copiloto."""
    partes = []
    desde = (datetime.now(UTC).date() - timedelta(days=14)).isoformat()
    try:
        from statistics import median

        from api.services import carry_trade

        for curva in ("tasa_fija", "cer"):
            r = carry_trade.serie_carry_trade(curva=curva, desde=desde) or {}
            # SOLO señales reales: |carry| > 15% en 14 días = precio viejo /
            # bono ilíquido (batería: PARP +464%) → se excluye del bloque.
            # ⚠ `carry_usd` YA VIENE EN % (carry_trade.py:235 lo redondea como
            # `carry_usd * 100`). Multiplicarlo de nuevo hacía dos daños a la
            # vez: el filtro dejaba pasar solo |carry| ≤ 0,15% (el bloque salía
            # casi siempre VACÍO) y lo que pasaba se imprimía 100× inflado.
            # Cazado por la auditoría de tools 2026-07-21.
            tabla = [t for t in r.get("tabla") or []
                     if t.get("carry_usd") is not None
                     and abs(t["carry_usd"]) <= _MAX_CARRY_REAL_PCT]
            if not tabla:
                continue
            vals = sorted(tabla, key=lambda t: t["carry_usd"])
            med = median(t["carry_usd"] for t in tabla)
            peor, mejor = vals[0], vals[-1]
            partes.append(
                f"carry USD 14d {curva} (filtrado a señales reales): mediana "
                f"{med:+.1f}% · mejor {mejor.get('ticker')} "
                f"{mejor['carry_usd']:+.1f}% · peor {peor.get('ticker')} "
                f"{peor['carry_usd']:+.1f}%"
            )
    except Exception as e:
        logger.warning("copiloto rf: carry falló (%s)", e)
    try:
        from api.services import canje as canje_svc

        s = (canje_svc.serie_canje(par="AL30") or {}).get("serie") or []
        if s:
            hoy_v = s[-1].get("canje")
            corte = (datetime.now(UTC).date() - timedelta(days=7)).isoformat()
            prev = next((x.get("canje") for x in reversed(s)
                         if str(x.get("fecha"))[:10] <= corte), None)
            if hoy_v is not None:
                linea = f"canje AL30 (CCL/MEP): hoy {hoy_v * 100:+.2f}%"
                if prev is not None:
                    linea += f" · hace 7d {prev * 100:+.2f}%"
                partes.append(linea)
    except Exception as e:
        logger.warning("copiloto rf: canje falló (%s)", e)
    return (["[carry y canje — tableros de /retorno]"] + partes) if partes else []


_PARES_LEGISLACION = (("AL30D", "GD30D"), ("AL35D", "GD35D"))


def _spread_legislacion_rf(filas: list[dict]) -> list[str]:
    """[spread de legislación]: TEA AL − TEA GD hoy y su promedio de 90 días
    (de snapshots_cierre_hist) — 'caro o barato contra lo normal' con datos,
    no con silencio (batería 2026-07-12: la pregunta quedaba bloqueada)."""
    from core.postgres import get_pool

    por_tk = {f.get("ticker_corto"): f for f in filas}
    lineas = []
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            for al, gd in _PARES_LEGISLACION:
                fa, fg = por_tk.get(al), por_tk.get(gd)
                if not fa or not fg or fa.get("tea") is None or fg.get("tea") is None:
                    continue
                hoy_bps = (fa["tea"] - fg["tea"]) * 100  # teas ya en %
                cur.execute(
                    """
                    SELECT avg((a.tea - g.tea) * 10000), count(*)
                    FROM mercado.snapshots_cierre_hist a
                    JOIN mercado.snapshots_cierre_hist g
                      ON g.fecha = a.fecha AND g.ticker_corto = %s
                    WHERE a.ticker_corto = %s
                      AND a.fecha >= now()::date - 90
                      AND a.tea IS NOT NULL AND g.tea IS NOT NULL
                    """,
                    (gd, al),
                )
                prom, n = cur.fetchone() or (None, 0)
                linea = f"{al}−{gd}: hoy {hoy_bps:+.0f}bps"
                if prom is not None and n:
                    linea += f" vs promedio 90d {float(prom):+.0f}bps ({n} ruedas)"
                lineas.append(linea)
    except Exception as e:
        logger.warning("copiloto rf: spread legislación falló (%s)", e)
    return (["[spread de legislación — prima ley local vs ley NY, en bps de TEA]"]
            + lineas) if lineas else []


def _estrategia_rf(filas: list[dict], pregunta: str, historial: list[dict]) -> list[str]:
    nombrados = _detectar_tickers(filas, pregunta, historial)
    partes: list[str] = []
    partes.extend(_precio_puntapunta_rf(nombrados[:2]))
    if len(nombrados) >= 2:
        partes.extend(_comparar_bonos_rf(nombrados[0], nombrados[1]))
    for f in nombrados[:2]:
        etiqueta = str(f.get("curva_label") or "")
        if f.get("tipo") in ("globales", "bonares"):
            partes.extend(_sensibilidad_rf(f.get("ticker_corto")))
        elif etiqueta.startswith(("cer", "tasa_fija")):
            partes.extend(_descomposicion_rf(f.get("ticker_corto"), etiqueta))
    return partes


def _extras_renta_fija(
    filas: list[dict], pregunta: str, historial: list[dict], params: dict | None = None
) -> list[str]:
    partes: list[str] = []
    try:
        from api.services.macro import get_ultimo_mep

        d = get_ultimo_mep() or {}  # devuelve {mep, ccl, canje, oficial, ...}
        if d.get("mep") is not None:
            linea = f"[dólares live] MEP {float(d['mep']):.2f}"
            if d.get("ccl") is not None:
                linea += f" · CCL {float(d['ccl']):.2f}"
            if d.get("oficial") is not None:
                linea += f" · oficial {float(d['oficial']):.2f}"
            partes.append(linea + " (el MEP es la referencia de los tc_breakeven)")
    except Exception as e:
        logger.warning("copiloto rf: MEP falló (%s)", e)
    partes.extend(_resumen_curvas_rf(filas))
    partes.extend(_movimientos_dia_rf(filas))
    partes.extend(_baratos_caros_rf())
    partes.extend(_forwards_rf(filas, pregunta, historial))
    partes.extend(_breakevens_rf())
    partes.extend(_spread_legislacion_rf(filas))
    try:
        partes.extend(_retornos_precio_curva())
    except Exception as e:
        logger.warning("copiloto rf: retornos por ventana fallaron (%s)", e)
    partes.extend(_carry_canje_rf())
    partes.extend(_estrategia_rf(filas, pregunta, historial))
    return partes


# ── TOOLS de la vista (primera vista de MERCADO con function calling, además
# de research — cierra el wiring pendiente de la auditoría 2026-07-21) ───────

def _curvas_descomponibles() -> list[str]:
    from api.services.descomposicion_retorno import curvas_soportadas
    return list(curvas_soportadas())


_HORIZONTE_DEFAULT, _HORIZONTE_MAX = 30, 365

_TOOLS_RENTA_FIJA = [
    {"type": "function", "function": {
        "name": "rendimiento_esperado",
        "description": "Qué rinde cada bono de una curva a un horizonte SI LA CURVA NO "
                       "SE MUEVE (carry + rolldown). Usala cuando pregunten qué conviene "
                       "comprar a X días, qué rinde más 'si no pasa nada', o pidan "
                       "comparar bonos por retorno esperado — el contexto solo trae la "
                       "foto de hoy, no la proyección.",
        "parameters": {"type": "object", "properties": {
            "curva": {"type": "string", "enum": _curvas_descomponibles()},
            "horizonte_dias": {
                "type": "integer",
                "description": f"días hacia adelante (default {_HORIZONTE_DEFAULT}, "
                               f"máx {_HORIZONTE_MAX})"}},
            "required": ["curva"]},
    }},
]


def _ejecutar_tool_renta_fija(nombre: str, args: dict) -> str:
    """Ejecutor de las tools de RF. Resultados COMPACTOS; nunca levanta.

    El shape de `rolldown_esperado` está VERIFICADO contra el service, no
    adivinado con `or`: devuelve `bonos[]` con `total_esperado` en FRACCIÓN
    (y, sólo en CER, `total_esperado_ars`, que es el retorno que le importa a
    un peso). La versión anterior filtraba por un campo `total` que el service
    nunca emitió → la tool contestaba "sin datos" SIEMPRE, y el modelo lo
    tapaba improvisando con el contexto."""
    if nombre != "rendimiento_esperado":
        return f"herramienta desconocida: {nombre}"
    from api.services import descomposicion_retorno

    curva = str(args.get("curva") or _curvas_descomponibles()[0])
    dias = max(1, min(int(args.get("horizonte_dias") or _HORIZONTE_DEFAULT),
                      _HORIZONTE_MAX))
    r = descomposicion_retorno.rolldown_esperado(horizonte_dias=dias, curva=curva) or {}
    if r.get("error"):
        return str(r["error"])
    # En CER el total en pesos incluye el CER esperado del REM; el service usa
    # esa misma clave para ordenar.
    clave = "total_esperado_ars" if curva == "cer" else "total_esperado"

    def _valor(f: dict) -> float | None:
        v = f.get(clave)
        if v is None:
            v = f.get("total_esperado")
        return float(v) * 100 if v is not None else None      # fracción → %

    filas = [(f.get("ticker_corto") or f.get("ticker"), v)
             for f in (r.get("bonos") or [])
             if (v := _valor(f)) is not None]
    if not filas:
        return f"sin datos de rendimiento esperado para {curva}."
    filas.sort(key=lambda x: -x[1])
    en_pesos = " en ARS (incluye el CER esperado del REM)" if clave.endswith("_ars") else ""
    top = filas[:10]
    return (f"rendimiento esperado {curva} a {dias} días{en_pesos}, si la curva no se "
            f"mueve ({len(filas)} bonos, carry + rolldown): "
            + " · ".join(f"{t} {v:+.2f}%" for t, v in top)
            + f" || mejor {top[0][0]} {top[0][1]:+.2f}% · peor {filas[-1][0]} "
              f"{filas[-1][1]:+.2f}%")


_REGLAS_RENTA_FIJA = """Sos el copiloto de la vista RENTA FIJA (bonos ARG). El idioma acá es \
TEA, curva, forward, breakeven — usalo con naturalidad.

Columnas: ticker · curva (cer / tasa_fija / globales / bonares / dolar_linked; "CER fijado" \
= bono CER cuyo índice ya quedó fijado por el BCRA, rinde como tasa fija) · vence / meses · \
precio · tea% (efectiva anual) · tem% (mensual) · paridad% · dur (Macaulay, sensibilidad a \
tasa — no es plazo) · tc_breakeven (tipo de cambio al vencimiento que EMPATA el bono en \
pesos contra tener dólares hoy al MEP) · tea_fit% y residuo_bps (fair value: residuo = TEA \
observada − teórica; POSITIVO = rinde más que la curva = BARATO; negativo = caro) · \
nominales_dia (volumen).

Bloques: [movimientos del día] = Δ de TEA vs el último cierre, ya calculado. [baratos y \
caros] = ranking por residuo (ojo al r² del fit). [forwards] = tasa implícita entre dos \
vencimientos; z alto = lejos de su historia (candidato a arbitraje o a cambio de régimen — \
decí las dos lecturas). [breakevens] = inflación mensual que empata cada par lecap-CER: si \
el breakeven > expectativa (REM/IPC), el mercado paga por cobertura CER; si <, la tasa \
fija gana si la inflación acompaña. [dólares live] ancla los tc_breakeven.
[spread de legislación] = prima del ley local (AL) sobre el ley NY (GD) en bps de TEA, \
HOY y su promedio de 90 ruedas: "¿caro o barato contra lo normal?" se responde con ESOS \
dos números, jamás con memoria propia.
[retorno de PRECIO por curva] = mediana 7d/14d/MTD por curva — para "¿cómo vino X esta \
semana/el mes?". En bullets cortos ≈ retorno total; en hard dollar EXCLUYE cupones y \
tenés que aclararlo (el retorno total exacto vive en la vista /retorno).
[carry y canje] = carry en USD de los últimos 14d por curva (mediana + mejor/peor, vía \
MEP) y el canje CCL/MEP del AL30 hoy vs hace 7 días — el pulso de las coberturas. \
DEFINICIÓN (no la inviertas): canje = CCL/MEP − 1; se ABRE cuando el CCL sube más que el \
MEP (demanda de girar dólares afuera = señal de tensión); se CIERRA cuando convergen.

MARCO DE PORTFOLIO (para "¿lecap o CER?" y "¿corto o largo?" — es tu forma de razonar):
- TASA FIJA vs CER = la regla del breakeven (la misma de TIPS vs Treasuries): si la \
inflación esperada supera el breakeven del par, gana el CER; si queda por debajo, gana la \
tasa fija. La señal contra el REM ya viene CALCULADA en [breakevens + señal] — usala tal \
cual y aclarando el supuesto ("si el REM acierta…"). Recordá que el breakeven trae prima \
de riesgo e iliquidez: no es un pronóstico puro.
- CORTO vs LARGO = carry y rolldown contra duration: con curva EMPINADA (ver empinamiento \
en [curvas por tramo]), el tramo largo paga más carry y suma rolldown (el bono "rueda" \
hacia tasas menores al envejecer), pero con duration alta cada punto de tasa pega mucho \
más. Curva plana o invertida → el largo no paga el riesgo, el corto manda. Expectativa de \
compresión de tasas → favorece largo; incertidumbre alta → corto o CER.
- SIEMPRE presentá la decisión como trade-off con el supuesto explícito ("si esperás \
inflación arriba de X%/mes, CER corto; si creés en la desinflación del REM, la tasa fija \
larga paga carry + rolldown con la curva así de empinada") — jamás una orden.

Herramientas bajo demanda (aparecen cuando nombrás bonos — son las de la vista Estrategia, \
disponibles acá):
- [precio punta a punta TICKER] = el retorno de PRECIO REAL (cierre a cierre) del bono en \
7d/14d/30d, con la fecha y el precio de cada punta. Es la cuenta cruda "precio final / \
precio inicial − 1". Para "¿cómo rindió TZXD6 punta a punta / en el último mes?" la \
respuesta sale DE ACÁ, no de la descomposición. OJO — dos conceptos DISTINTOS que NO se \
mezclan: este es el precio puro; la [descomposición] es un MODELO que reparte el retorno en \
carry/rolldown/tasa/CER y puede NO dar el mismo número. Si te preguntan por un MES \
CALENDARIO exacto (ej. "junio") que no coincide con las ventanas móviles (7d/14d/30d), \
decílo: tenés ventanas móviles hasta el último cierre, no el mes calendario, y ofrecé la \
más cercana. JAMÁS uses el precio de un bono para responder por OTRO: si el bloque de un \
ticker no está, decí que no lo tenés y pará — nunca reutilices puntas de otro papel.
- [comparar A vs B]: los flujos de invertir ARS 1.000.000 HOY en cada bono — total a \
cobrar, cantidad de pagos, advertencias (cruce de moneda, flujos CER proyectados con CER \
constante). Es tu columna vertebral para cualquier "¿A o B?": flujos + residuos + forward \
implícito + el trade-off de plazos.
- [sensibilidad TICKER] (solo soberanos): precio objetivo ante escenarios de TIR. Es \
upside de PRECIO solamente — sin carry — y tenés que aclararlo siempre.
- [descomposición TICKER 30d] (pesos): qué explicó el retorno del último mes. Al narrarla \
usá los nombres de mesa con el técnico entre paréntesis SOLO la primera vez: "lo que \
devengó por el paso del tiempo (carry)", "lo que ganó por rodar hacia la parte corta de \
la curva (rolldown)", "el movimiento de tasas del mercado", "el arrastre inflacionario \
del índice (ajuste CER)". Es LA respuesta a "¿por qué subió/bajó tanto?".
- Si un dato NO existe para los bonos de la pregunta (ej. residuo de fair value en \
soberanos, descomposición en dollar-linked), NO menciones su ausencia — simplemente no lo \
uses. Solo aclarás que falta si el usuario lo pidió explícitamente.

Reglas de acá:
- El bloque [baratos y caros] ya viene FILTRADO: los residuos absurdos (precio viejo / \
bonos que no operan) NO aparecen — todo lo que ves ahí es señal real y operable. Si en la \
TABLA general ves un residuo_bps enorme que no figura en el bloque, es porque fue filtrado \
por sospechoso: no lo presentes como oportunidad.
- Formato de rankings acá: conclusión en UNA frase, tabla CHICA (top 3 por lado como \
mucho), y una lectura final que AGREGUE algo (el porqué probable, el riesgo) — nunca que \
repita lo que la tabla ya muestra.
- CER tiene settlement T-10 hábiles: si un bono no operó hoy, su TEA puede arrastrar el \
CER de ayer — ante algo raro en un CER ilíquido, mencioná esta salvedad.
- Comparar dos bonos = spot de ambos + el forward implícito entre ellos (si aparece el \
bloque) + residuos: quién está caro contra la curva. NUNCA "cuál es mejor" a secas: mostrá \
el trade-off (plazo/duration/curva).
- Duration alta = más sensible: aclaralo cuando recomiendes mirar la parte larga.
- JAMÁS consejo de inversión directo; ranking objetivo con criterio, como siempre."""
#
# La selección de tickers de las tarjetas es del USUARIO (vive en su
# browser) → viaja como PARÁMETRO (como la pregunta), se sanea acá, y el
# server busca los datos frescos de ESOS tickers en sus propios services.
# Los overrides de máx/mín/cierre también viajan (números validados): los
# pivots que ve la IA son EXACTAMENTE los que ve el trader en pantalla.

# DEBE matchear SLOTS de acaquant-web/src/components/trading-view.tsx (la grilla
# 4×3). Estaba en 8 mientras el frontend ya mandaba hasta 12 → las cards de la 9ª
# en adelante se truncaban en silencio y el copiloto juraba que no existían
# (fallo real 2026-07-17: "ASTS está en mis cards" y el modelo no la veía).
