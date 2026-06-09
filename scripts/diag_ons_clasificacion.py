"""scripts/diag_ons_clasificacion.py — clasificación vs escala de TODAS las ONs.

Read-only. La TEA de una ON sólo sale bien si la escala del PRECIO y la del
FLUJO coinciden, y eso depende de `moneda_flujo`:

    USD → precio ÷ MEP → USD   ·  flujo por 100 VN (~100)
    DL  → precio ÷ A3500 → USD ·  flujo por 100 VN (~100)
    ARS → precio tal cual (peso) · flujo en la MISMA escala peso que el precio

La fuente de verdad del TIPO de bono es `Valuaciones.Assets.CARTERA` (HD/DL/…),
que mantiene el user. Este diag cruza, por cada ON:

    ticker · CARTERA (Assets) · moneda_flujo (Curvas) · Σ flujo · precio (snap)

y marca:
  - MONEDA?  → moneda_flujo no condice con CARTERA (DL→DL, HD→USD).
  - ESCALA?  → moneda ARS pero precio y Σ flujo en escalas distintas
               (uno ~100, el otro grande) → XIRR no converge o da fake.

No concluye nada solo: muestra el cuadro para decidir CON datos, no a ojo.

Correr:  python -m scripts.diag_ons_clasificacion
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read


def _escala(x: float) -> str:
    """Etiqueta gruesa de escala para comparar precio vs flujo."""
    if x <= 0:
        return "0"
    if x < 5:
        return "~1"        # por 1 VN
    if x < 500:
        return "~100"      # por 100 VN
    return "PESO"          # monto peso absoluto (miles)


def main() -> None:
    cli = get_mongo_client_read()
    trading = cli["Trading"]
    val = cli["Valuaciones"]

    ons = list(trading["Curvas"].find(
        {"curva": {"$regex": "^on"}},
        {"_id": 0, "ticker": 1, "ticker_corto": 1, "emisor": 1,
         "curva": 1, "moneda_flujo": 1, "flujos": 1}))
    print(f"{len(ons)} ONs en Trading.Curvas\n")

    # CARTERA por TICKER (uppercase) desde Assets.
    cartera = {}
    for a in val["Assets"].find(
            {"TICKER": {"$exists": True}}, {"_id": 0, "TICKER": 1, "CARTERA": 1}):
        t = (a.get("TICKER") or "").upper()
        if t:
            cartera[t] = (a.get("CARTERA") or "").upper()

    print(f"{'ticker':<9}{'CARTERA':<9}{'mon_flujo':<10}{'Σflujo':>12}"
          f"{'esc_flj':>9}{'precio':>14}{'esc_pre':>9}  flags")
    print("-" * 92)

    n_moneda = n_escala = 0
    for o in sorted(ons, key=lambda x: x.get("ticker_corto", "")):
        tc = (o.get("ticker_corto") or "").upper()
        cart = cartera.get(tc, "—")
        mon = (o.get("moneda_flujo") or "USD").upper()
        flujos = o.get("flujos") or []
        sflujo = sum(float(f.get("amortizacion") or 0) + float(f.get("interes") or 0)
                     for f in flujos)

        snap = trading["MarketSnapshot"].find_one(
            {"ticker": o.get("ticker")}, {"_id": 0, "metrics.last_price": 1})
        precio = float(((snap or {}).get("metrics") or {}).get("last_price") or 0)

        esc_flj = _escala(sflujo)
        esc_pre = _escala(precio)

        flags = []
        # ¿moneda_flujo condice con CARTERA?
        esperada = {"HD": "USD", "DL": "DL"}.get(cart)
        if esperada and mon != esperada:
            flags.append(f"MONEDA?(→{esperada})")
            n_moneda += 1
        # ¿ARS con precio y flujo en escalas distintas?
        if mon == "ARS" and precio > 0 and sflujo > 0 and esc_flj != esc_pre:
            flags.append("ESCALA?")
            n_escala += 1

        print(f"{tc:<9}{cart:<9}{mon:<10}{sflujo:>12,.1f}{esc_flj:>9}"
              f"{precio:>14,.1f}{esc_pre:>9}  {' '.join(flags)}")

    print("\n" + "=" * 60)
    print(f"MONEDA? (moneda_flujo ≠ lo que dice CARTERA): {n_moneda}")
    print(f"ESCALA? (ARS con precio y flujo en escalas distintas): {n_escala}")
    print("\nMONEDA? → cambiar moneda_flujo (DL/USD) según CARTERA.")
    print("ESCALA? → el flujo está en otra escala que el precio; o el bono es")
    print("          DL/HD (y va convertido) o hay que recargar el flujo a la")
    print("          escala peso real. Decidir con CARTERA, no a ojo.")
    print("\n✅ diag read-only — nada se escribió.")


if __name__ == "__main__":
    main()
