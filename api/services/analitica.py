"""Capa de servicio — analítica Tier 2 sobre data existente.

Dos herramientas que operan encima de `renta_fija.listar_curva` y de los cierres
persistidos (SQL `mercado.snapshots_cierre_hist`):

- `snapshot_curva_historico(curva, fecha)` — curva entera como cerró en un día pasado.
- `calcular_pendiente_curva(curva, metrica, fecha_comparacion)` — slope en bps ± comparación.

(liquidez_secundario eliminado 2026-06-22; el fallback a TimeSales del snapshot también.
El cierre histórico migró de Mongo `Trading.SnapshotsCierre` a SQL — cutover 2026-06-24.)
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from psycopg.rows import dict_row

from api.cache import cached
from api.services.renta_fija import _CURVAS_VALIDAS, listar_curva
from core import curvas_sql
from core.postgres import get_pool


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _f(v):
    """numeric (Decimal) → float, preservando None (shape idéntico al path Mongo)."""
    return float(v) if v is not None else None


@cached(ttl=300)
def snapshot_curva_historico(curva: str, fecha: str) -> list[dict]:
    """Curva entera tal como cerró un día pasado.

    Lee SQL `mercado.snapshots_cierre_hist` (poblada por jobs/snapshot_cierre.py;
    Trading.SnapshotsCierre Mongo migrada → dropeada, cutover 2026-06-24). Si es HOY y
    aún no cerró, fallback a Trading.MarketSnapshot (live, Mongo). Para fechas viejas sin
    cierre persistido → []. (El fallback a TimeSales se eliminó 2026-06-22.)

    Devuelve mismo shape que antes (ticker, ticker_corto, precio, TEA,
    TEM, paridad, duration, etc.). Si un bono no operó ese día, no
    aparece en el resultado.
    """
    if curva not in _CURVAS_VALIDAS:
        return []

    fecha_str = str(fecha)[:10]

    # Metadata estática de Trading.Curvas (necesaria sea cual sea la fuente).
    # cer_emision lo necesita descomposicion_retorno (curva CER) y NO está
    # en SnapshotsCierre — siempre lo joineamos con Curvas.
    curva_docs = curvas_sql.por_curva(curva)
    if not curva_docs:
        return []
    meta_by_ticker = {d["ticker"]: d for d in curva_docs if d.get("ticker")}

    try:
        fecha_dt = datetime.fromisoformat(fecha_str).replace(tzinfo=UTC)
    except ValueError:
        return []

    # ── Camino primario: leer el cierre del día de SQL (snapshots_cierre_hist) ──
    snap_rows = _q(
        "SELECT ticker, ticker_corto, tipo, fecha_vencimiento, ultimo_precio, "
        "tea, tem, paridad, duration, mod_duration, convexity "
        "FROM mercado.snapshots_cierre_hist WHERE fecha = %s AND curva = %s",
        (fecha_dt.date(), curva),
    )

    if snap_rows:
        out: list[dict] = []
        for r in snap_rows:
            ticker = r.get("ticker")
            m = meta_by_ticker.get(ticker, {})
            vto_raw = m.get("fecha_vencimiento") or r.get("fecha_vencimiento")
            meses = None
            try:
                if isinstance(vto_raw, datetime):
                    vto = vto_raw if vto_raw.tzinfo else vto_raw.replace(tzinfo=UTC)
                else:
                    vto = datetime.fromisoformat(str(vto_raw)[:10]).replace(tzinfo=UTC)
                meses = round((vto - fecha_dt).days / 30.44, 1)
            except Exception:
                pass
            entry = {
                "ticker":             ticker,
                "ticker_corto":       r.get("ticker_corto") or m.get("ticker_corto"),
                "tipo":               r.get("tipo") or m.get("tipo"),
                "fecha_vencimiento":  str(vto_raw)[:10] if vto_raw else None,
                "meses_al_vto":       meses,
                # SQL devuelve numeric → float (igual shape que el path Mongo).
                "ultimo_precio":      _f(r.get("ultimo_precio")),
                "tea":                _f(r.get("tea")),
                "tem":                _f(r.get("tem")),
                "paridad":            _f(r.get("paridad")),
                "duration":           _f(r.get("duration")),
                "mod_duration":       _f(r.get("mod_duration")),
                "convexity":          _f(r.get("convexity")),
                # El cierre no guarda timestamp del último trade — la fecha del
                # cierre alcanza (los consumers que usaban ts_ultimo_trade solo
                # mostraban la fecha).
                "ts_ultimo_trade":    fecha_str,
            }
            if curva == "cer":
                cupon = m.get("cupon_anual")
                entry["is_zero_coupon"] = (cupon is None) or (float(cupon) == 0.0)
                cer_em = m.get("cer_emision")
                if cer_em:
                    entry["cer_emision"] = float(cer_em)
            out.append(entry)
        out.sort(key=lambda x: x.get("fecha_vencimiento") or "9999")
        return out

    # ── Fallback A: día corriente sin cierre persistido todavía (cron
    # snapshot_cierre corre 20:25 UTC). Leemos MarketSnapshot directo,
    # que es la misma fuente que el cron usa al cierre — sólo que live.
    if fecha_str == date.today().isoformat():
        # SQL-only (mercado.market_snapshot). Query directa de las columnas que se
        # usan — NO trae el `book` jsonb (order book depth-5) que snapshot_docs
        # arrastraba sin necesidad (perf 2026-06-29). Shape compat: {ticker, metrics, updated_at}.
        _MS_COLS = (("last_price", "last_price"), ("tea", "TEA"), ("tem", "TEM"),
                    ("paridad", "paridad"), ("duration", "duration"),
                    ("mod_duration", "mod_duration"), ("convexity", "convexity"))
        ms_rows = []
        with get_pool().connection() as _conn, _conn.cursor(row_factory=dict_row) as _cur:
            _cur.execute(
                "SELECT ticker, updated_at, "
                + ", ".join(c for c, _ in _MS_COLS)
                + " FROM mercado.market_snapshot WHERE ticker = ANY(%s) AND last_price > 0",
                (list(meta_by_ticker.keys()),),
            )
            for _r in _cur.fetchall():
                metrics = {k: float(_r[c]) for c, k in _MS_COLS if _r.get(c) is not None}
                ms_rows.append({"ticker": _r["ticker"], "updated_at": _r["updated_at"],
                                "metrics": metrics})
        if ms_rows:
            out = []
            for r in ms_rows:
                ticker = r.get("ticker")
                m = meta_by_ticker.get(ticker, {})
                metrics = r.get("metrics") or {}
                vto_raw = m.get("fecha_vencimiento")
                meses = None
                try:
                    if isinstance(vto_raw, datetime):
                        vto = vto_raw if vto_raw.tzinfo else vto_raw.replace(tzinfo=UTC)
                    else:
                        vto = datetime.fromisoformat(str(vto_raw)[:10]).replace(tzinfo=UTC)
                    meses = round((vto - fecha_dt).days / 30.44, 1)
                except Exception:
                    pass
                ts_last = r.get("updated_at")
                entry = {
                    "ticker":             ticker,
                    "ticker_corto":       m.get("ticker_corto"),
                    "tipo":               m.get("tipo"),
                    "fecha_vencimiento":  str(vto_raw)[:10] if vto_raw else None,
                    "meses_al_vto":       meses,
                    "ultimo_precio":      metrics.get("last_price"),
                    "tea":                metrics.get("TEA"),
                    "tem":                metrics.get("TEM"),
                    "paridad":            metrics.get("paridad"),
                    "duration":           metrics.get("duration"),
                    "mod_duration":       metrics.get("mod_duration"),
                    "convexity":          metrics.get("convexity"),
                    "ts_ultimo_trade":    (
                        ts_last.isoformat() if isinstance(ts_last, datetime) else ts_last
                    ),
                }
                if curva == "cer":
                    cupon = m.get("cupon_anual")
                    entry["is_zero_coupon"] = (cupon is None) or (float(cupon) == 0.0)
                    cer_em = m.get("cer_emision")
                    if cer_em:
                        entry["cer_emision"] = float(cer_em)
                out.append(entry)
            out.sort(key=lambda x: x.get("fecha_vencimiento") or "9999")
            return out

    # Fallback TimeSales ELIMINADO (2026-06-22): para fechas viejas sin SnapshotsCierre
    # se devuelve vacío. SnapshotsCierre es la fuente histórica; TimeSales pasa a ser
    # intraday/última-sesión (TTL corto). El gap se cubre con backfill de SnapshotsCierre.
    return []


_METRICAS_PENDIENTE = ("tea", "tem", "duration")


@cached(ttl=60)
def calcular_pendiente_curva(
    curva: str,
    metrica: str = "tea",
    fecha_comparacion: str | None = None,
    dias_min_corto: int = 30,
) -> dict:
    """Pendiente de una curva (valor largo − valor corto).

    "Corto" = menor duration entre los bonos con vencimiento ≥ `dias_min_corto`
    días. "Largo" = mayor duration. El resultado se expresa en basis points (bps).
    Opcionalmente compara con la curva del día `fecha_comparacion` y devuelve el
    delta de pendiente (útil para detectar empinamiento/aplanamiento).

    `dias_min_corto` (default 30) excluye bonos a punto de vencer del anchor
    "corto" — sus TEAs son ruidosas (microestructura de fin de plazo) e
    inflan artificialmente el spread. Bajalo a 0 si querés incluir todo.

    metrica ∈ {tea, tem, duration}. Default tea.
    """
    if curva not in _CURVAS_VALIDAS:
        return {"error": f"curva inválida: {curva}"}
    metrica = metrica.lower()
    if metrica not in _METRICAS_PENDIENTE:
        return {"error": f"metrica inválida: {metrica}"}

    ahora = listar_curva(curva=curva, ordenar_por="duration")
    meses_min = dias_min_corto / 30.0
    validos = [
        b for b in ahora
        if b.get(metrica) is not None
        and b.get("duration")
        and (b.get("meses_al_vto") or 0) >= meses_min
    ]
    if len(validos) < 2:
        return {
            "error": f"insuficientes instrumentos con metrica + duration "
                     f"+ vto ≥ {dias_min_corto} días",
        }

    corto_now = validos[0]
    largo_now = validos[-1]
    valor_corto_now = float(corto_now[metrica])
    valor_largo_now = float(largo_now[metrica])
    pendiente_actual_bps = round((valor_largo_now - valor_corto_now) * 10000, 0)

    out: dict = {
        "curva":                   curva,
        "metrica":                 metrica,
        "pendiente_actual_bps":    pendiente_actual_bps,
        "corto": {
            "ticker":   corto_now.get("ticker_corto") or corto_now.get("ticker"),
            "duration": corto_now.get("duration"),
            metrica:    valor_corto_now,
        },
        "largo": {
            "ticker":   largo_now.get("ticker_corto") or largo_now.get("ticker"),
            "duration": largo_now.get("duration"),
            metrica:    valor_largo_now,
        },
    }

    if fecha_comparacion:
        hist = snapshot_curva_historico(curva=curva, fecha=fecha_comparacion)
        hist_validos = [b for b in hist if b.get(metrica) is not None and b.get("duration")]
        if len(hist_validos) >= 2:
            hist_validos.sort(key=lambda b: b["duration"])
            corto_hist = hist_validos[0]
            largo_hist = hist_validos[-1]
            pend_hist_bps = round(
                (float(largo_hist[metrica]) - float(corto_hist[metrica])) * 10000, 0
            )
            delta = round(pendiente_actual_bps - pend_hist_bps, 0)
            if delta > 10:
                interpretacion = "empinamiento"
            elif delta < -10:
                interpretacion = "aplanamiento"
            else:
                interpretacion = "sin cambio material"
            out.update({
                "fecha_comparacion":          fecha_comparacion,
                "pendiente_comparacion_bps":  pend_hist_bps,
                "delta_bps":                  delta,
                "interpretacion":             interpretacion,
            })
        else:
            out["fecha_comparacion"] = fecha_comparacion
            out["error_comparacion"] = "insuficientes instrumentos en fecha pedida"

    return out


# liquidez_secundario + _clasificar_liquidez ELIMINADOS (2026-06-22): leían TimeSales
# sobre 20 días, solo se exponían por MCP (sin uso en el front) → se borran para que
# TimeSales quede intraday/última-sesión y habilitar TTL corto.
