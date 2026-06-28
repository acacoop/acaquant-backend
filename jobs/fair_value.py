"""fair_value.py — fit cuadrático + residuos + z-scores diarios.

Encadenado al cron de snapshot_cierre. Por cada curva:

  1. Lee el cierre del día de SQL `mercado.snapshots_cierre_hist`
     (Trading.SnapshotsCierre Mongo migrada → dropeada, cutover 2026-06-24;
     snapshot_cierre escribe esa tabla justo antes en el mismo cron-chain).
  2. Filtra el universo del fit:
       - dias_al_vto >= 15
       - total_nominals_dia >= --vol-min (default 50M)
       - tea y duration not None
       - dias_desde_emision >= 5
       - is_zero_coupon == True (solo CER — los con cupón viejo desvirtúan
         el OLS porque sus paridades distintas a Lecers de duration similar
         meten ruido estructural, no mispricing).
  3. Ajusta cuadrática TEA = β₀ + β₁·d + β₂·d² (quant.curve_fit).
     Persiste β + R² en SQL mercado.fit_params (PK curva, ts_cierre).
  4. Para CADA bono con tea+duration disponibles (filtrado o no, salvo TEA
     null que se omite por imposibilidad de calcular residuo):
       - tea_teorica = β₀ + β₁·d + β₂·d²
       - residuo_bps = (tea_obs − tea_teorica) · 10000
  5. z_estatico = residuo / σ(residuos del UNIVERSO FILTRADO).
     (Bonos fuera del universo se valúan con la misma σ — su z queda en
      la misma escala que los del universo.)
  6. z_temporal: media + desvío de los últimos 30 cierres (≤30 filas ya que
     el cierre es diario), leídos de SQL mercado.fair_value_residuos. n_obs<20 → NULL.
  7. Persiste todo en SQL mercado.fair_value_residuos (PK curva, ticker, ts_cierre).

Uso:
    python -m jobs.fair_value
    python -m jobs.fair_value --fecha 2026-04-25 --vol-min 100_000_000
    python -m jobs.fair_value --dry
"""
from __future__ import annotations

import argparse
import logging
import statistics
import sys
from datetime import UTC, date, datetime, timedelta

from core.pg_mirror import write_native
from core.postgres import get_pool
from quant.curve_fit import fit_quadratic

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("FairValue")


def _leer_snapshot_cierre(curva: str, fecha_str: str) -> list[dict]:
    """Cierre del día desde SQL `mercado.snapshots_cierre_hist`, con el shape que
    espera el resto del job (lo que antes traía Trading.SnapshotsCierre):
    numéricos → float, fechas → ISO 'YYYY-MM-DD' (los helpers _dias_al_vto/
    _dias_desde_emision parsean strings). Filtra (fecha, curva)."""
    from psycopg.rows import dict_row

    def _f(v):
        return float(v) if v is not None else None

    def _iso(v):
        return v.isoformat() if v is not None else None

    out: list[dict] = []
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT ticker, ticker_corto, tea, tem, paridad, duration, mod_duration, "
            "convexity, total_nominals_dia, is_zero_coupon, fecha_vencimiento, "
            "fecha_emision FROM mercado.snapshots_cierre_hist "
            "WHERE fecha = %s AND curva = %s",
            (date.fromisoformat(fecha_str), curva),
        )
        for r in cur.fetchall():
            out.append({
                "ticker":             r["ticker"],
                "ticker_corto":       r["ticker_corto"],
                "tea":                _f(r["tea"]),
                "tem":                _f(r["tem"]),
                "paridad":            _f(r["paridad"]),
                "duration":           _f(r["duration"]),
                "mod_duration":       _f(r["mod_duration"]),
                "convexity":          _f(r["convexity"]),
                "total_nominals_dia": _f(r["total_nominals_dia"]),
                "is_zero_coupon":     r["is_zero_coupon"],
                "fecha_vencimiento":  _iso(r["fecha_vencimiento"]),
                "fecha_emision":      _iso(r["fecha_emision"]),
            })
    return out

CURVAS_V1 = ("tasa_fija", "cer")
DIAS_AL_VTO_MIN = 15
DIAS_DESDE_EMISION_MIN = 5
VOL_MIN_DEFAULT = 50_000_000
VENTANA_TEMPORAL_DIAS = 30
N_OBS_TEMPORAL_MIN = 20
R2_WARNING_THRESHOLD = 0.85


def _dias_al_vto(fecha_vto: str | None, fecha_ref: date) -> int | None:
    if not fecha_vto:
        return None
    try:
        d = date.fromisoformat(fecha_vto[:10])
    except ValueError:
        return None
    return (d - fecha_ref).days


def _dias_desde_emision(fecha_emi: str | None, fecha_ref: date) -> int | None:
    if not fecha_emi:
        return None
    try:
        d = date.fromisoformat(fecha_emi[:10])
    except ValueError:
        return None
    return (fecha_ref - d).days


def _en_universo_fit(
    bono: dict, curva: str, fecha_ref: date, vol_min: float,
) -> bool:
    """Filtros del universo que afecta el ajuste OLS (β)."""
    tea = bono.get("tea")
    dur = bono.get("duration")
    if tea is None or dur is None or dur <= 0:
        return False

    dvto = _dias_al_vto(bono.get("fecha_vencimiento"), fecha_ref)
    if dvto is None or dvto < DIAS_AL_VTO_MIN:
        return False

    vol = bono.get("total_nominals_dia") or 0
    if vol < vol_min:
        return False

    # fecha_emision puede ser None para algunos lecaps (no hay emision en
    # Trading.Curvas) — en ese caso no aplicamos el filtro (asumimos OK).
    demi = _dias_desde_emision(bono.get("fecha_emision"), fecha_ref)
    if demi is not None and demi < DIAS_DESDE_EMISION_MIN:
        return False

    return not (curva == "cer" and not bono.get("is_zero_coupon"))


def _residuos_historicos(curva: str, ticker: str, fecha_ref: date) -> list[float]:
    """Últimos VENTANA_TEMPORAL_DIAS residuos del bono, EXCLUYENDO el día actual
    (que todavía no se persistió). Lee de SQL `mercado.fair_value_residuos`
    (decomiso 2026-06-28). Orden no importa para media/desvío.
    """
    desde = fecha_ref - timedelta(days=int(VENTANA_TEMPORAL_DIAS * 1.7))
    hasta = fecha_ref - timedelta(days=1)
    out: list[float] = []
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT residuo_bps FROM mercado.fair_value_residuos "
            "WHERE curva = %s AND ticker = %s AND ts_cierre >= %s AND ts_cierre <= %s "
            "ORDER BY ts_cierre DESC LIMIT %s",
            (curva, ticker, desde, hasta, VENTANA_TEMPORAL_DIAS),
        )
        for (v,) in cur.fetchall():
            if v is not None:
                try:
                    out.append(float(v))
                except (TypeError, ValueError):
                    continue
    return out


def procesar_curva(
    curva: str, fecha_str: str, vol_min: float, dry: bool,
) -> dict:
    fecha_ref = date.fromisoformat(fecha_str)

    snap = _leer_snapshot_cierre(curva, fecha_str)
    if not snap:
        logger.warning(
            "[%s %s] sin cierre en mercado.snapshots_cierre_hist — corre snapshot_cierre primero",
            curva, fecha_str,
        )
        return {"curva": curva, "n_universo": 0, "n_residuos": 0}

    universo = [b for b in snap if _en_universo_fit(b, curva, fecha_ref, vol_min)]
    if len(universo) < 3:
        logger.warning(
            "[%s %s] universo del fit con %d bonos (<3 mínimo cuadrática)",
            curva, fecha_str, len(universo),
        )
        return {"curva": curva, "n_universo": len(universo), "n_residuos": 0}

    fit = fit_quadratic(
        [float(b["duration"]) for b in universo],
        [float(b["tea"]) for b in universo],
    )
    if fit is None:
        logger.error("[%s %s] fit cuadrático falló (XᵀX singular)", curva, fecha_str)
        return {"curva": curva, "n_universo": len(universo), "n_residuos": 0}

    if fit.r2 < R2_WARNING_THRESHOLD:
        logger.warning(
            "[%s %s] R² bajo: %.3f (esperado ≥%.2f). Universo n=%d",
            curva, fecha_str, fit.r2, R2_WARNING_THRESHOLD, fit.n,
        )

    # Residuos del universo filtrado para sigma estática del día.
    residuos_universo: list[float] = []
    universo_tickers = {b["ticker"] for b in universo}
    for b in universo:
        residuo = (float(b["tea"]) - fit.predict(float(b["duration"]))) * 10000
        residuos_universo.append(residuo)

    if len(residuos_universo) >= 2:
        sigma_dia = statistics.stdev(residuos_universo)  # muestral
    else:
        sigma_dia = 0.0

    # Sanity OLS: media de residuos del universo cerca de cero (con intercepto).
    media_universo = statistics.fmean(residuos_universo)
    if abs(media_universo) > 5.0:
        logger.warning(
            "[%s %s] OLS sanity: media residuos universo = %.2f bps (esperado ~0)",
            curva, fecha_str, media_universo,
        )

    if not dry:
        write_native(
            "mercado.fit_params", ["curva", "ts_cierre"],
            [{
                "ts_cierre": fecha_ref,
                "curva": curva,
                "updated_at": datetime.now(UTC),
                "beta0": fit.beta0, "beta1": fit.beta1, "beta2": fit.beta2,
                "r2": fit.r2,
                "n_bonos_universo": fit.n,
                "vol_min_aplicado": vol_min,
                "sigma_dia_bps": sigma_dia,
                "media_residuos_universo_bps": media_universo,
            }],
        )

    # Residuos para todos los bonos del snapshot con tea+duration disponibles
    # (filtrado o no — los que no tienen tea no se valúan).
    res_rows: list[dict] = []
    for b in snap:
        tea = b.get("tea")
        dur = b.get("duration")
        if tea is None or dur is None or dur <= 0:
            continue
        residuo_bps = (float(tea) - fit.predict(float(dur))) * 10000
        z_estatico = residuo_bps / sigma_dia if sigma_dia > 1e-9 else None

        residuos_hist = _residuos_historicos(curva, b["ticker"], fecha_ref)
        n_obs = len(residuos_hist) + 1  # +1 por el de hoy que estamos guardando
        if n_obs >= N_OBS_TEMPORAL_MIN and len(residuos_hist) >= N_OBS_TEMPORAL_MIN - 1:
            # Calculamos sobre histórico + hoy.
            serie = residuos_hist + [residuo_bps]
            media_t = statistics.fmean(serie)
            try:
                desv_t = statistics.stdev(serie)
            except statistics.StatisticsError:
                desv_t = 0.0
            z_temporal = (residuo_bps - media_t) / desv_t if desv_t > 1e-9 else None
        else:
            z_temporal = None

        en_universo = b["ticker"] in universo_tickers

        res_rows.append({
            "ts_cierre":      fecha_ref,
            "curva":          curva,
            "ticker":         b["ticker"],
            "ticker_corto":   b.get("ticker_corto"),
            "duration":       float(dur),
            "tea_obs":        float(tea),
            "tea_teorica":    fit.predict(float(dur)),
            "residuo_bps":    residuo_bps,
            "z_estatico":     z_estatico,
            "z_temporal":     z_temporal,
            "n_obs":          n_obs,
            "en_universo":    en_universo,
        })

    n_persistidos = len(res_rows)
    if not dry and res_rows:
        write_native(
            "mercado.fair_value_residuos", ["curva", "ticker", "ts_cierre"], res_rows,
        )

    logger.info(
        "[%s %s] universo=%d β=(%.4f, %.4f, %.4f) R²=%.3f σ=%.1fbps residuos=%d",
        curva, fecha_str, fit.n, fit.beta0, fit.beta1, fit.beta2, fit.r2,
        sigma_dia, n_persistidos,
    )
    return {
        "curva": curva, "n_universo": fit.n, "n_residuos": n_persistidos,
        "r2": fit.r2, "sigma_dia": sigma_dia,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fecha", help="YYYY-MM-DD (default: hoy UTC)")
    parser.add_argument(
        "--vol-min", type=float, default=VOL_MIN_DEFAULT,
        help=f"Volumen nominal mínimo del día (default {VOL_MIN_DEFAULT:,.0f})",
    )
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    if args.fecha:
        try:
            fecha_d = date.fromisoformat(args.fecha)
        except ValueError:
            raise SystemExit(f"--fecha inválida: {args.fecha}") from None
    else:
        fecha_d = datetime.now(UTC).date()
    fecha_str = fecha_d.isoformat()

    # SQL-native (decomiso 2026-06-28): escribe mercado.{fit_params,fair_value_residuos}.
    # Los índices/PK los garantiza sql/schema.sql (no se crean acá).
    for curva in CURVAS_V1:
        procesar_curva(curva, fecha_str, args.vol_min, args.dry)

    if args.dry:
        logger.info("(--dry: no se escribió en SQL)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
