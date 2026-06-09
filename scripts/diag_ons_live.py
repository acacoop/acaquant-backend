"""scripts/diag_ons_live.py — ¿están cotizando las ONs (curva='on')?

Read-only. Cruza las ONs cargadas en Trading.Curvas (curva='on') contra
Trading.MarketSnapshot para confirmar que tras el restart de los motores:
  - motor_rofex las está SUSCRIBIENDO (hay book / last_price).
  - motor_curvas las está ENRIQUECIENDO (hay TEA / duration / paridad).

Imprime TEA/duration LIVE por ON para validar contra la calculadora de la
mesa (ahora con precio real, no el ONSnapshot viejo de abril).

Correr:  python -m scripts.diag_ons_live
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.mongo import get_mongo_client_read


def _edad_seg(updated_at) -> float | None:
    if not isinstance(updated_at, datetime):
        return None
    ts = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts).total_seconds()


def main() -> None:
    cli = get_mongo_client_read()
    trading = cli["Trading"]

    ons = list(trading["Curvas"].find(
        {"curva": "on"},
        {"_id": 0, "ticker": 1, "ticker_corto": 1, "emisor": 1, "moneda_flujo": 1}))
    print(f"ONs en Trading.Curvas (curva='on'): {len(ons)}\n")
    if not ons:
        print("⚠️  No hay docs curva='on'. ¿Corriste el seed --commit?")
        return

    tickers = [o["ticker"] for o in ons]
    snaps = {s["ticker"]: s for s in trading["MarketSnapshot"].find(
        {"ticker": {"$in": tickers}},
        {"_id": 0, "ticker": 1, "book": 1, "metrics": 1, "updated_at": 1})}

    cotizan = enriquecidas = 0
    print(f"{'asset':<8}{'emisor':<20}{'last':>10}{'TEA%':>8}{'dur':>7}"
          f"{'parid':>8}{'edad':>9}  estado")
    print("-" * 88)
    for o in sorted(ons, key=lambda x: (x.get("emisor") or "", x["ticker_corto"])):
        s = snaps.get(o["ticker"])
        if not s:
            print(f"{o['ticker_corto']:<8}{(o.get('emisor') or '')[:18]:<20}"
                  f"{'—':>10}{'—':>8}{'—':>7}{'—':>8}{'—':>9}  SIN SNAPSHOT")
            continue
        m = s.get("metrics") or {}
        book = s.get("book") or {}
        last = m.get("last_price")
        tiene_precio = bool(last and last > 0) or bool(book.get("bids") or book.get("offers"))
        tea = m.get("TEA")
        tiene_tea = isinstance(tea, (int, float))
        if tiene_precio:
            cotizan += 1
        if tiene_tea:
            enriquecidas += 1
        edad = _edad_seg(s.get("updated_at"))
        edad_s = f"{edad:.0f}s" if edad is not None else "—"
        last_s = f"{last:.2f}" if isinstance(last, (int, float)) else "—"
        tea_s = f"{tea*100:.2f}" if tiene_tea else "—"
        dur_s = f"{m.get('duration'):.2f}" if isinstance(m.get("duration"), (int, float)) else "—"
        par_s = f"{m.get('paridad'):.1f}" if isinstance(m.get("paridad"), (int, float)) else "—"
        estado = "✅ cotiza+enriq" if (tiene_precio and tiene_tea) else \
                 ("cotiza, sin TEA" if tiene_precio else "snapshot sin precio")
        print(f"{o['ticker_corto']:<8}{(o.get('emisor') or '')[:18]:<20}"
              f"{last_s:>10}{tea_s:>8}{dur_s:>7}{par_s:>8}{edad_s:>9}  {estado}")

    print("\n" + "=" * 60)
    print(f"Cotizando (precio):     {cotizan}/{len(ons)}")
    print(f"Enriquecidas (TEA):     {enriquecidas}/{len(ons)}")
    print("\nNota: una ON sin precio aún puede ser que no operó todavía hoy. "
          "Re-corré en un rato para ver más activas.")


if __name__ == "__main__":
    main()
