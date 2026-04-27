"""inspect_market_snapshot.py — inspeccionar el doc actual de un ticker.

Imprime el doc tal como vive AHORA en Trading.MarketSnapshot, con foco
en los campos analíticos (TEA, TEM, duration, mod_duration, convexity).
Útil para verificar que un backfill o un cambio del motor llegó al doc
sin depender del UI de Atlas (que cachea).

Acepta ticker corto (ej "T30A7") o ticker completo. Si es corto resuelve
contra Trading.Curvas.ticker_corto.

Uso:
    python -m scripts.inspect_market_snapshot T30A7
    python -m scripts.inspect_market_snapshot "MERV - XMEV - T30A7 - 24hs"
"""
from __future__ import annotations

import json
import sys

from core.mongo import get_mongo_client


def resolver_ticker_completo(client, instrumento: str) -> str | None:
    if " - " in instrumento:
        return instrumento
    doc = client["Trading"]["Curvas"].find_one(
        {"ticker_corto": instrumento}, {"_id": 0, "ticker": 1},
    )
    return doc.get("ticker") if doc else None


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.inspect_market_snapshot <ticker>", file=sys.stderr)
        sys.exit(2)

    instrumento = sys.argv[1].strip()
    client = get_mongo_client()

    ticker = resolver_ticker_completo(client, instrumento)
    if not ticker:
        print(f"No encontré ticker para {instrumento!r} en Trading.Curvas.")
        sys.exit(1)

    doc = client["Trading"]["MarketSnapshot"].find_one(
        {"ticker": ticker},
        {"_id": 0, "ticker": 1, "updated_at": 1, "metrics": 1},
    )

    if not doc:
        print(f"No hay doc en MarketSnapshot para {ticker!r}.")
        sys.exit(1)

    metrics = doc.get("metrics") or {}
    foco = {
        "TEA":          metrics.get("TEA"),
        "TEM":          metrics.get("TEM"),
        "duration":     metrics.get("duration"),
        "mod_duration": metrics.get("mod_duration"),
        "convexity":    metrics.get("convexity"),
        "paridad":      metrics.get("paridad"),
    }

    print(f"ticker     : {doc.get('ticker')}")
    print(f"updated_at : {doc.get('updated_at')}")
    print("\n-- métricas analíticas --")
    for k, v in foco.items():
        marca = "  " if v is not None else "❌"
        print(f"{marca} {k:<14} = {v!r}")

    if foco["mod_duration"] is None:
        print("\n⚠️  mod_duration NO está en el doc. Posibles causas:")
        print("   1. El backfill no se corrió (corré scripts.backfill_mod_duration).")
        print("   2. El doc no cumplía los filtros del backfill (TEA/duration nulos).")
        print("   3. La copia API (MarketSnapshotAPI) está vieja — la API lee MarketSnapshot,")
        print("      pero si mirás Atlas UI puede ser otra colección.")

    # Dump full por si hace falta más contexto.
    print("\n-- doc completo (json) --")
    print(json.dumps(doc, default=str, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
