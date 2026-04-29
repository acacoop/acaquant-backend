"""Discovery one-shot de pyRofex.get_detailed_instruments().

Lista TODOS los instruments que ROFEX expone (futures, options, spreads,
ETFs, todo) y los agrupa por CFI code. Para cada CFI persiste:
  - count total
  - underlyings únicos (ordenados alfabéticamente)
  - 5 samples con ticker / maturity / underlying

Sirve para identificar qué CFI usar al extender el motor a productos
nuevos (agro, opciones, etc.) antes de codear filtros a ciegas.

Persiste a `Manager.PyRofexDiscovery` con `_id="current"`. Idempotente:
cada corrida sobrescribe el doc.

Uso:
    python -m scripts.discovery_pyrofex

Requiere sesión pyRofex (lee `config.Config` user/pass/account).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime

import pyRofex

from core.mongo import get_mongo_client
from core.rofex_session import inicializar_sesion

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("DiscoveryPyRofex")


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

    by_cfi: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "underlyings": set(), "samples": []}
    )

    # 20 samples por CFI alcanza para ver formato de ticker, naming de mes/año,
    # variantes de underlying. Más que eso satura la UI.
    SAMPLES_PER_CFI = 20

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

    by_cficode = []
    for cficode, g in sorted(by_cfi.items(), key=lambda x: -x[1]["count"]):
        by_cficode.append({
            "cficode":     cficode,
            "count":       g["count"],
            "underlyings": sorted(g["underlyings"]),
            "samples":     g["samples"],
        })

    doc = {
        "_id":               "current",
        "generated_at":      datetime.now(UTC),
        "total_instruments": total,
        "by_cficode":        by_cficode,
    }

    client = get_mongo_client()
    client["Manager"]["PyRofexDiscovery"].replace_one(
        {"_id": "current"}, doc, upsert=True,
    )
    logger.info(
        "Persistidos %d CFI groups (total %d instruments) a Manager.PyRofexDiscovery",
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
