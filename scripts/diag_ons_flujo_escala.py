"""scripts/diag_ons_flujo_escala.py — ONs con flujos mal escalados.

Read-only. Un bono con valor_nominal=100 debería tener flujos "por 100 VN":
la Σ de amortizaciones ≈ 100 (devuelve el capital) y cada cupón es chico. Si
la Σ de amort es muchísimo mayor (ej. 144.600), el flujo se cargó en ESCALA
PESO (se metió un monto absoluto en vez del por-100-VN) → da TEA falsa o "—".

Lista las ONs ordenadas por cuán lejos está la Σ amort de ~100, marcando las
sospechosas (Σ amort > 150 o algún flujo individual > 200 con VN=100).

Correr:  python -m scripts.diag_ons_flujo_escala
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read


def main() -> None:
    trading = get_mongo_client_read()["Trading"]
    ons = list(trading["Curvas"].find(
        {"curva": {"$regex": "^on"}},
        {"_id": 0, "ticker_corto": 1, "emisor": 1, "moneda_flujo": 1,
         "valor_nominal": 1, "flujos": 1}))
    print(f"{len(ons)} ONs en Curvas\n")

    filas = []
    for o in ons:
        vn = float(o.get("valor_nominal") or 100)
        flujos = o.get("flujos") or []
        amorts = [float(f.get("amortizacion") or 0) for f in flujos]
        suma = sum(amorts)
        maxa = max(amorts) if amorts else 0
        # Esperado: Σ amort ≈ vn (100). Sospechoso si se va lejos.
        sospechoso = suma > vn * 1.5 or maxa > vn * 2
        filas.append((o, suma, maxa, sospechoso))

    filas.sort(key=lambda x: -x[1])  # peor escala primero

    print(f"{'ticker':<9}{'emisor':<20}{'mon':<5}{'VN':>5}{'Σamort':>14}{'maxamort':>14}  flag")
    print("-" * 78)
    n_sosp = 0
    for o, suma, maxa, sosp in filas:
        if sosp:
            n_sosp += 1
        flag = "  ⚠ MAL ESCALADO" if sosp else ""
        print(f"{o.get('ticker_corto', ''):<9}{(o.get('emisor') or '')[:18]:<20}"
              f"{(o.get('moneda_flujo') or ''):<5}{float(o.get('valor_nominal') or 100):>5.0f}"
              f"{suma:>14,.2f}{maxa:>14,.2f}{flag}")

    print("\n" + "=" * 60)
    print(f"{n_sosp}/{len(ons)} ONs con flujo MAL ESCALADO (Σamort >> VN).")
    print("Esas tienen el amortizacion en escala peso (monto absoluto) en vez de")
    print("por 100 VN → hay que recargar el flujo de la fuente con la columna correcta.")
    print("\n✅ diag read-only completo — nada se escribió.")


if __name__ == "__main__":
    main()
