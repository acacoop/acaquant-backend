"""Capa de servicio — analítica Tier 2 sobre data existente.

Dos herramientas que operan encima de `renta_fija.listar_curva` y de los cierres
persistidos (`Trading.SnapshotsCierre`):

- `snapshot_curva_historico(curva, fecha)` — curva entera como cerró en un día pasado.
- `calcular_pendiente_curva(curva, metrica, fecha_comparacion)` — slope en bps ± comparación.

(liquidez_secundario eliminado 2026-06-22; el fallback a TimeSales del snapshot también.)
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from api.cache import cached
from api.db import get_db_trading
from api.services.renta_fija import _CURVAS_VALIDAS, listar_curva


@cached(ttl=300)
def snapshot_curva_historico(curva: str, fecha: str) -> list[dict]:
    """Curva entera tal como cerró un día pasado.

    Lee Trading.SnapshotsCierre (poblada por jobs/snapshot_cierre.py +
    backfill_snapshots_cierre). Si es HOY y aún no cerró, fallback a MarketSnapshot
    (live). Para fechas viejas sin cierre persistido → []. (El fallback a TimeSales
    se eliminó 2026-06-22: TimeSales pasa a intraday/última-sesión, TTL corto.)

    Devuelve mismo shape que antes (ticker, ticker_corto, precio, TEA,
    TEM, paridad, duration, etc.). Si un bono no operó ese día, no
    aparece en el resultado.
    """
    if curva not in _CURVAS_VALIDAS:
        return []

    db = get_db_trading()
    fecha_str = str(fecha)[:10]

    # Metadata estática de Trading.Curvas (necesaria sea cual sea la fuente).
    # cer_emision lo necesita descomposicion_retorno (curva CER) y NO está
    # en SnapshotsCierre — siempre lo joineamos con Curvas.
    curva_docs = list(db["Curvas"].find(
        {"curva": curva},
        {"_id": 0, "ticker": 1, "ticker_corto": 1, "tipo": 1,
         "fecha_vencimiento": 1, "fecha_emision": 1,
         "cupon_anual": 1, "cer_emision": 1},
    ))
    if not curva_docs:
        return []
    meta_by_ticker = {d["ticker"]: d for d in curva_docs if d.get("ticker")}

    try:
        fecha_dt = datetime.fromisoformat(fecha_str).replace(tzinfo=UTC)
    except ValueError:
        return []

    # ── Camino primario: leer SnapshotsCierre del día ──
    snap_rows = list(db["SnapshotsCierre"].find(
        {"ts_cierre": fecha_str, "curva": curva},
        {"_id": 0,
         "ticker": 1, "ticker_corto": 1, "tipo": 1,
         "fecha_vencimiento": 1, "ultimo_precio": 1,
         "tea": 1, "tem": 1, "paridad": 1,
         "duration": 1, "mod_duration": 1, "convexity": 1},
    ))

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
                "ultimo_precio":      r.get("ultimo_precio"),
                "tea":                r.get("tea"),
                "tem":                r.get("tem"),
                "paridad":            r.get("paridad"),
                "duration":           r.get("duration"),
                "mod_duration":       r.get("mod_duration"),
                "convexity":          r.get("convexity"),
                # SnapshotsCierre no guarda timestamp del último trade —
                # ts_cierre es el día. Los consumers que usaban ts_ultimo_trade
                # lo único que hacían era mostrar la fecha; ts_cierre alcanza.
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
        ms_rows = list(db["MarketSnapshot"].find(
            {
                "ticker":             {"$in": list(meta_by_ticker.keys())},
                "metrics.last_price": {"$gt": 0},
            },
            {"_id": 0,
             "ticker": 1, "updated_at": 1,
             "metrics.last_price": 1, "metrics.TEA": 1, "metrics.TEM": 1,
             "metrics.paridad": 1, "metrics.duration": 1,
             "metrics.mod_duration": 1, "metrics.convexity": 1},
        ))
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
