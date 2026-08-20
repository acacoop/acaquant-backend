"""engines/estrategia.py — motor ESTRATEGIA QUANT (señal intradía con trazabilidad).

Doc vivo (LEER antes de tocar): docs/ESTRATEGIA_QUANT.md.

Cada INTERVALO (60s), para cada ticker de config.ESTRATEGIA_TICKERS:
  1. Lee inputs live: pivots ARS (OHLC última rueda), last/high/low del día
     (cedears_snapshot), pivots del índice de referencia.
  2. Calcula los 4 factores + score con quant.estrategia (puro) y los pesos
     ACTIVOS de estrategia.modelo_pesos (versionados).
  3. UPSERT a estrategia.eval_live (la zona LIVE de la vista lee de ahí).
  4. Si la señal es ACCIONABLE (|score| ≥ umbral y pasó el cooldown o cambió
     la dirección) → APPEND al ledger estrategia.senales (INMUTABLE).

El universo es FIJO (config) a propósito: el track-record exige emisión pareja
— si solo se registrara cuando el trader mira, el dataset quedaría sesgado.

Contexto que se calcula UNA vez al boot (el cron lo reinicia cada mañana):
  - índice de referencia por ticker = mayor |corr| diaria vs QQQ/SPY
    (mercado.precios_acciones, 60 ruedas).
  - costumbre (rango promedio ~20 ruedas, mercado.day_trading_stats).
  - zonas de confluencia USD (4 timeframes del subyacente, quant.pivot_points).

Uso: python -m engines.estrategia   (systemd motor_estrategia.service,
     cron L-V 13:20 → 20:05 UTC como los demás motores).
"""
from __future__ import annotations

import logging
import time
import traceback
from datetime import UTC, datetime

from config import (
    ESTRATEGIA_COOLDOWN_MIN,
    ESTRATEGIA_INDICES,
    ESTRATEGIA_SCORE_UMBRAL,
    ESTRATEGIA_TICKERS,
)
from core import estrategia_sql as db
from core.logs import configurar
from quant.estrategia import (
    factor_alineacion,
    factor_confluencia,
    factor_nafta_papel,
    factor_recorrido_indice,
    score_estrategia,
    zonas_confluencia,
)
from quant.pivot_points import calcular
from quant.rolling_stats import correlation, returns_from_prices

# El formato (con NIVEL) vive en core/logs — ver AV_AGENT.md §0.ac.
configurar()
logger = logging.getLogger("MotorEstrategia")

INTERVALO = 60  # segundos entre ciclos


# ─────────────────────────────────────────────
# Contexto diario (una vez al boot)
# ─────────────────────────────────────────────
def armar_contexto() -> dict:
    """Precalcula lo que no cambia durante la rueda. Tolerante a datos
    faltantes: un ticker sin historia queda con corr None (F2 no opina)."""
    tickers = [t.upper() for t in ESTRATEGIA_TICKERS]
    indices = [i.upper() for i in ESTRATEGIA_INDICES]
    master = db.master_cedears(tickers + indices)

    # Retornos diarios de los índices (subyacente USD).
    rets_idx: dict[str, list[float]] = {}
    for idx in indices:
        closes = db.closes_diarios(idx, n=90)
        rets_idx[idx] = returns_from_prices(closes) if len(closes) > 2 else []

    # Por ticker: corr vs cada índice → índice de referencia = mayor |corr|.
    indice_ref: dict[str, str] = {}
    corr_ref: dict[str, float | None] = {}
    zonas_usd: dict[str, list[dict]] = {}
    for tk in tickers:
        underlying = (master.get(tk) or {}).get("underlying") or tk
        closes = db.closes_diarios(underlying, n=90)
        rets = returns_from_prices(closes) if len(closes) > 2 else []
        mejor_idx, mejor_corr = indices[0], None
        for idx in indices:
            ri = rets_idx.get(idx) or []
            n = min(len(rets), len(ri), 60)
            if n < 20:
                continue
            c = correlation(rets[-n:], ri[-n:])
            if c is not None and (mejor_corr is None or abs(c) > abs(mejor_corr)):
                mejor_idx, mejor_corr = idx, c
        indice_ref[tk] = mejor_idx
        corr_ref[tk] = mejor_corr

        # Zonas de confluencia USD: 4 timeframes del subyacente.
        try:
            from quant.pivot_points import obtener_4_timeframes
            res = obtener_4_timeframes(underlying)
            frames = {tf: (f or {}).get("levels") or {}
                      for tf, f in (res.get("frames") or {}).items()}
            zonas_usd[tk] = zonas_confluencia(frames)
        except Exception as e:
            logger.warning("confluencia %s: %s", tk, e)
            zonas_usd[tk] = []

    costumbre = db.rango_promedio(tickers)
    logger.info(
        "contexto: %d tickers, corr ok=%d, costumbre ok=%d",
        len(tickers), sum(1 for v in corr_ref.values() if v is not None), len(costumbre),
    )
    return {
        "tickers": tickers,
        "indices": indices,
        "master": master,
        "indice_ref": indice_ref,
        "corr_ref": corr_ref,
        "zonas_usd": zonas_usd,
        "costumbre": costumbre,
    }


# ─────────────────────────────────────────────
# Ciclo
# ─────────────────────────────────────────────
def _pct_vs_pp(last: float | None, pp: float | None) -> float | None:
    if not last or not pp or pp <= 0:
        return None
    return (last / pp - 1) * 100


def ciclo(ctx: dict) -> None:
    tickers: list[str] = ctx["tickers"]
    indices: list[str] = ctx["indices"]
    master: dict = ctx["master"]

    todos = tickers + [i for i in indices if i not in tickers]
    ohlc = db.ohlc_ultima_rueda(todos)
    largos = {tk: (master.get(tk) or {}).get("ticker") for tk in todos}
    live = db.snapshot_live([lg for lg in largos.values() if lg])

    # Pivots ARS + % vs PP de índices (una vez por ciclo, sirven a todos).
    piv: dict[str, dict] = {}
    for tk in todos:
        row = ohlc.get(tk)
        if row and row.get("high") and row.get("low") and row.get("close"):
            piv[tk] = dict(calcular(
                high=float(row["high"]), low=float(row["low"]), close=float(row["close"]),
            ))

    pesos_version, pesos = db.pesos_activos()
    ahora = datetime.now(UTC).isoformat()

    for tk in tickers:
        try:
            snap = live.get(largos.get(tk) or "") or {}
            last = snap.get("last")
            idx = ctx["indice_ref"].get(tk, indices[0])
            snap_idx = live.get(largos.get(idx) or "") or {}
            last_idx = snap_idx.get("last")

            # F1 — recorrido del índice.
            f1 = factor_recorrido_indice(
                last_idx or 0, piv.get(idx) or {},
                snap_idx.get("high"), snap_idx.get("low"),
            )
            # F2 — alineación (ponderada por corr diaria).
            pct_p = _pct_vs_pp(last, (piv.get(tk) or {}).get("pp"))
            pct_i = _pct_vs_pp(last_idx, (piv.get(idx) or {}).get("pp"))
            f2 = factor_alineacion(pct_p, pct_i, ctx["corr_ref"].get(tk))
            # F3 — nafta del papel.
            rango_hoy = None
            if snap.get("high") and snap.get("low") and snap["low"] > 0:
                rango_hoy = (snap["high"] / snap["low"] - 1) * 100
            dir_actual = 0 if pct_p is None else (1 if pct_p > 0 else -1 if pct_p < 0 else 0)
            f3 = factor_nafta_papel(rango_hoy, ctx["costumbre"].get(tk), dir_actual)
            # F4 — confluencia USD (precio USD live no disponible acá sin más
            # feeds: usamos el último close diario como proxy del nivel).
            underlying = (master.get(tk) or {}).get("underlying") or tk
            closes_u = ctx.setdefault("_closes_u", {})
            if tk not in closes_u:
                serie = db.closes_diarios(underlying, n=2)
                closes_u[tk] = serie[-1] if serie else None
            f4 = factor_confluencia(closes_u[tk], ctx["zonas_usd"].get(tk) or [])

            factores = {"recorrido_indice": f1, "alineacion": f2,
                        "nafta_papel": f3, "confluencia": f4}
            res = score_estrategia(factores, pesos)

            data = {
                **res,
                "indice_ref": idx,
                "precio": last,
                "pesos_version": pesos_version,
                "inputs": {
                    "pct_vs_pp_papel": pct_p, "pct_vs_pp_indice": pct_i,
                    "corr": ctx["corr_ref"].get(tk),
                    "rango_hoy_pct": rango_hoy,
                    "rango_prom_pct": ctx["costumbre"].get(tk),
                    "last_indice": last_idx,
                },
                "eval_at": ahora,
            }
            db.upsert_eval_live(tk, data)

            # Emisión al ledger: accionable + (cooldown vencido o cambio de dirección).
            if abs(res["score"]) >= ESTRATEGIA_SCORE_UMBRAL and last:
                previa = db.ultima_senal_reciente(tk, ESTRATEGIA_COOLDOWN_MIN)
                if previa is None or previa["direccion"] != res["direccion"]:
                    senal_id = db.insertar_senal(
                        ticker=tk, indice_ref=idx, direccion=res["direccion"],
                        score=res["score"], precio=last,
                        pesos_version=pesos_version,
                        factores={**factores, "inputs": data["inputs"]},
                    )
                    logger.info("señal #%d %s %s score=%.1f", senal_id, tk,
                                res["direccion"], res["score"])
        except Exception:
            logger.error("ciclo %s:\n%s", tk, traceback.format_exc())


def main() -> None:
    logger.info("MotorEstrategia arrancando — universo: %s", ESTRATEGIA_TICKERS)
    ctx = armar_contexto()
    while True:
        t0 = time.monotonic()
        try:
            ciclo(ctx)
        except Exception:
            logger.error("ciclo global:\n%s", traceback.format_exc())
        time.sleep(max(5.0, INTERVALO - (time.monotonic() - t0)))


if __name__ == "__main__":
    main()
