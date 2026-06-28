"""Discovery one-shot de pyRofex.get_detailed_instruments() — SQL-NATIVE.

Lista TODOS los instruments que ROFEX expone (futures, options, spreads,
ETFs, todo) y los agrupa por CFI code. Para cada CFI persiste:
  - count total
  - underlyings únicos (ordenados alfabéticamente)
  - 20 samples con ticker / maturity / underlying (vista resumen del panel)
  - el detalle COMPLETO de todos los instruments (drill-down por CFI)

Sirve para:
  - identificar qué CFI usar al extender el motor a productos nuevos.
  - validar que un ticker existe en pyRofex ANTES de operarlo / suscribirlo
    (los readers de `operar`, `ordenes`, `manager/instrumentos` y el universo
    del motor_portfolio leen estas tablas — si están vacías, el panel de
    Instrumentos queda sin data y `/api/operar/order-book` no puede validar).

Persistencia SQL (decommission de Mongo — la fuente es Postgres):
  - `manager.pyrofex_instruments` (PK = cficode): el set ENTERO se reemplaza
    de forma atómica (TRUNCATE + INSERT) → los CFI que ya no existen se borran.
  - `manager.pyrofex_discovery` (PK id='current'): resumen singleton (totales
    + por CFI). Idempotente: cada corrida sobrescribe la fila.

Antes escribía Mongo `Manager.PyRofexInstruments` / `Manager.PyRofexDiscovery`
(ELIMINADAS). Read-only sobre pyRofex; el único write es a SQL.

Uso:
    python -m scripts.discovery_pyrofex

Requiere sesión pyRofex (lee `config.Config` user/pass/account).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime

import pyRofex

from core.pg_mirror import replace_native, write_native
from core.rofex_session import inicializar_sesion

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("DiscoveryPyRofex")

# 20 samples por CFI para el resumen (pyrofex_discovery, vista principal del
# panel). El detalle completo (todos los instruments) va a pyrofex_instruments.
SAMPLES_PER_CFI = 20


def _ticker_de(inst: dict) -> str:
    sym = inst.get("symbol")
    if isinstance(sym, str) and sym:
        return sym
    iid = inst.get("instrumentId") or {}
    return iid.get("symbol") if isinstance(iid.get("symbol"), str) else "?"


def main() -> None:
    if not inicializar_sesion():
        logger.error("No pude iniciar sesión pyRofex — abortando.")
        return

    res = pyRofex.get_detailed_instruments()
    if not res or res.get("status") != "OK":
        logger.error("get_detailed_instruments() falló: %s", res)
        return

    instruments = res.get("instruments") or []
    total = len(instruments)
    logger.info("Total instruments recibidos: %d", total)
    if total == 0:
        logger.error("0 instruments recibidos — NO toco SQL (evito vaciar las tablas).")
        return

    by_cfi: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "underlyings": set(), "samples": [], "all": []}
    )

    for inst in instruments:
        cficode = inst.get("cficode") or "?"
        underlying = inst.get("underlying") or "?"
        ticker = _ticker_de(inst)
        mat = inst.get("maturityDate") or inst.get("maturity_date") or ""

        g = by_cfi[cficode]
        g["count"] += 1
        g["underlyings"].add(underlying)
        if len(g["samples"]) < SAMPLES_PER_CFI:
            g["samples"].append({
                "ticker":     ticker,
                "maturity":   mat,
                "underlying": underlying,
            })
        # Todos los campos relevantes para el drill-down (mismo shape que escribía
        # el writer Mongo → los readers no cambian de contrato).
        g["all"].append({
            "ticker":           ticker,
            "maturity":         mat,
            "underlying":       underlying,
            "marketSegmentId":  inst.get("marketSegmentId"),
            "currency":         inst.get("currency"),
            "tickSize":         inst.get("tickSize") or inst.get("priceVariation"),
            "lowLimitPrice":    inst.get("lowLimitPrice"),
            "highLimitPrice":   inst.get("highLimitPrice"),
            "minPriceIncrement": inst.get("minPriceIncrement"),
            "minTradeVol":      inst.get("minTradeVol"),
            "maxTradeVol":      inst.get("maxTradeVol"),
            "instrumentPricePrecision": inst.get("instrumentPricePrecision"),
            "instrumentSizePrecision":  inst.get("instrumentSizePrecision"),
            "contractMultiplier": inst.get("contractMultiplier"),
            "putOrCall":        inst.get("putOrCall"),
            "strikePrice":      inst.get("strikePrice"),
        })

    # ── pyrofex_instruments: 1 fila por CFI con el detalle completo ──────────
    # Swap atómico del set entero (los CFI que desaparecieron se borran).
    inst_rows = [
        {
            "cficode":     cficode,
            "count":       g["count"],
            "underlyings": sorted(g["underlyings"]),
            "instruments": g["all"],
        }
        for cficode, g in by_cfi.items()
    ]
    n_inst = replace_native("manager.pyrofex_instruments", inst_rows)
    logger.info("Persistido detalle completo a manager.pyrofex_instruments (%d CFI)", n_inst)

    # ── pyrofex_discovery: resumen singleton (por CFI, ordenado por count) ───
    by_cficode = [
        {
            "cficode":     cficode,
            "count":       g["count"],
            "underlyings": sorted(g["underlyings"]),
            "samples":     g["samples"],
        }
        for cficode, g in sorted(by_cfi.items(), key=lambda x: -x[1]["count"])
    ]
    write_native(
        "manager.pyrofex_discovery",
        ["id"],
        [{
            "id":                "current",
            "total_instruments": total,
            "by_cficode":        by_cficode,
            "generated_at":      datetime.now(UTC),
        }],
    )
    logger.info(
        "Persistidos %d CFI groups (total %d instruments) a manager.pyrofex_discovery",
        len(by_cficode), total,
    )

    # Resumen al stdout para ver de un vistazo qué hay.
    print("\n────────── DISCOVERY pyRofex ──────────")
    print(f"{'CFI':<10} {'COUNT':>6}  UNDERLYINGS")
    for g in by_cficode:
        unders_str = ", ".join(g["underlyings"][:4])
        if len(g["underlyings"]) > 4:
            unders_str += f", … (+{len(g['underlyings']) - 4})"
        print(f"{g['cficode']:<10} {g['count']:>6}  {unders_str}")


if __name__ == "__main__":
    main()
