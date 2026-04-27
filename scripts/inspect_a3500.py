"""inspect_a3500.py — diagnóstico del TC para dolar-linked.

El motor de curvas usa Valuaciones.DolarOficial casa="mayorista" (escrito
por jobs/dolar_api.py, cron 5 min) como TC intra-day para dolar-linked.
NO usa Trading.DOLAR (BCRA fixing diario).

Este script muestra el último valor disponible del mayorista y su edad,
para confirmar que el motor lo va a leer bien.

Uso:
    python -m scripts.inspect_a3500
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.mongo import get_mongo_client


def _fmt_dt(dt) -> str:
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


def main() -> None:
    client = get_mongo_client()

    print("─── TC dolar-linked: Valuaciones.DolarOficial casa='mayorista' ───")
    doc = client["Valuaciones"]["DolarOficial"].find_one(
        {"casa": "mayorista", "venta": {"$gt": 0}},
        {"_id": 0, "compra": 1, "venta": 1, "updated_at": 1, "fecha": 1},
        sort=[("updated_at", -1)],
    )
    if not doc:
        print("❌ Sin doc en Valuaciones.DolarOficial casa='mayorista'.")
        print("   El motor no va a poder enriquecer dolar-linked.")
        print("   Corré: python -m jobs.dolar_api")
        return

    compra = doc.get("compra")
    venta = doc.get("venta")
    if compra and venta and compra > 0 and venta > 0:
        mid = (float(compra) + float(venta)) / 2.0
        print(f"  compra: {compra:.4f}")
        print(f"  venta:  {venta:.4f}")
        print(f"  MID:    {mid:.4f}  ← el motor usa este")
    elif venta:
        print(f"  venta:  {float(venta):.4f}  ← el motor usa este (compra ausente)")

    print(f"  fecha:      {doc.get('fecha')}")
    print(f"  updated_at: {_fmt_dt(doc.get('updated_at'))}")

    ua = doc.get("updated_at")
    if isinstance(ua, datetime):
        if ua.tzinfo is None:
            ua = ua.replace(tzinfo=UTC)
        edad_min = (datetime.now(UTC) - ua).total_seconds() / 60.0
        print(f"  edad:       {edad_min:.1f} min")
        if edad_min > 30:
            print("\n⚠️  Más de 30 min sin actualizar — probable que el cron")
            print("   jobs.dolar_api esté caído. Corré: python -m jobs.dolar_api")
        else:
            print("\n✓ Dato fresco. El motor lo va a usar bien.")


if __name__ == "__main__":
    main()
