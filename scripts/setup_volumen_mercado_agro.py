"""scripts/setup_volumen_mercado_agro.py — crea/mantiene CashFlow.VolumenMercadoAgro.

Volumen TOTAL del mercado de futuros agropecuarios (SOJA/TRIGO/MAIZ), MENSUAL,
cargado a MANO porque no está apificado. Sirve para calcular nuestro market
share:
    share = nuestras_toneladas / toneladas_mercado   (por mes + commodity)
Nuestras toneladas salen de CashFlow.Operaciones (ver /ops/agro).

La colección guarda 1 doc por (periodo, commodity) — shape PLANO, matchea 1:1 con
el `commodity` de Operaciones:
    {"periodo": "2025-07", "commodity": "SOJA", "toneladas": 3342900.0,
     "actualizado_en": <datetime>}
Índice único (periodo, commodity) → upsert idempotente (re-cargar pisa el valor).

Para CARGAR datos: pegá los meses en MESES abajo, en el MISMO formato en que los
tenés (un dict por mes con Trigo/Maiz/Soja). El script los aplana y upsertea;
`total` se ignora (se deriva sumando).

Uso:
    python -m scripts.setup_volumen_mercado_agro            # crea colección + índice y upsertea MESES
    python -m scripts.setup_volumen_mercado_agro --dry-run  # muestra qué haría, no escribe
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime

from pymongo import ASCENDING, UpdateOne
from pymongo.errors import CollectionInvalid

from core.mongo import get_mongo_client

# nombre del grano (como lo cargás) → commodity canónico (como está en Operaciones)
_GRANO_A_COMMODITY = {"Soja": "SOJA", "Trigo": "TRIGO", "Maiz": "MAIZ"}

# ── DATOS A CARGAR ───────────────────────────────────────────────────────────
# Un dict por mes (en toneladas). Pegá acá nuevos meses y re-corré el script.
MESES: list[dict] = [
    {"mes": "2025-07", "Trigo": 626000,  "Maiz": 2210925, "Soja": 3342900, "total": 6179825},
    {"mes": "2025-08", "Trigo": 714100,  "Maiz": 1917845, "Soja": 2903550, "total": 5535495},
    {"mes": "2025-09", "Trigo": 1254140, "Maiz": 1647505, "Soja": 8158345, "total": 11059990},
    {"mes": "2025-10", "Trigo": 1021960, "Maiz": 1155060, "Soja": 6115870, "total": 8292890},
    {"mes": "2025-11", "Trigo": 1452520, "Maiz": 2070280, "Soja": 3420940, "total": 6943740},
    {"mes": "2025-12", "Trigo": 1550260, "Maiz": 2331475, "Soja": 2847765, "total": 6729500},
    {"mes": "2026-01", "Trigo": 710780,  "Maiz": 3224750, "Soja": 2653195, "total": 6588725},
    {"mes": "2026-02", "Trigo": 948610,  "Maiz": 2754055, "Soja": 2856625, "total": 6559290},
    {"mes": "2026-03", "Trigo": 1112760, "Maiz": 3680995, "Soja": 4386040, "total": 9179795},
    {"mes": "2026-04", "Trigo": 1227180, "Maiz": 2297335, "Soja": 7182150, "total": 10706665},
    {"mes": "2026-05", "Trigo": 1523750, "Maiz": 2913075, "Soja": 5689265, "total": 10126090},
]


def _aplanar(meses: list[dict]) -> list[tuple[str, dict]]:
    """Cada mes anidado → un doc plano por (periodo, commodity)."""
    out: list[tuple[str, dict]] = []
    now = datetime.now(UTC)
    for m in meses:
        periodo = str(m.get("mes") or "").strip()
        if len(periodo) != 7 or periodo[4] != "-":
            raise ValueError(f"mes inválido (esperaba YYYY-MM): {periodo!r}")
        for grano, commodity in _GRANO_A_COMMODITY.items():
            val = m.get(grano)
            if val is None:
                continue
            doc = {"periodo": periodo, "commodity": commodity,
                   "toneladas": round(float(val), 0), "actualizado_en": now}
            out.append((f"{periodo}/{commodity}", doc))
    return out


def main() -> int:
    dry = "--dry-run" in sys.argv
    db = get_mongo_client()["CashFlow"]

    # 1) Crear la colección (idempotente) — existe aunque MESES esté vacío.
    try:
        if not dry:
            db.create_collection("VolumenMercadoAgro")
        print("colección VolumenMercadoAgro: creada")
    except CollectionInvalid:
        print("colección VolumenMercadoAgro: ya existía")
    col = db["VolumenMercadoAgro"]

    # 2) Índice único (periodo, commodity).
    if not dry:
        col.create_index([("periodo", ASCENDING), ("commodity", ASCENDING)],
                          unique=True, name="periodo_commodity_unico")
    print("índice único (periodo, commodity): ok")

    # 3) Upsert de MESES (si hay).
    filas = _aplanar(MESES)
    if not filas:
        print("MESES vacío → no se carga nada (colección lista para llenar).")
        return 0

    ops = [UpdateOne({"periodo": d["periodo"], "commodity": d["commodity"]},
                     {"$set": d}, upsert=True) for _, d in filas]

    if dry:
        print(f"\n[DRY-RUN] {len(ops)} filas a upsertear:")
        for etiqueta, d in filas:
            print(f"   {etiqueta} = {d['toneladas']:,.0f} t")
        return 0

    res = col.bulk_write(ops, ordered=False)
    print(f"\nupsert: {res.upserted_count} nuevos · {res.modified_count} actualizados "
          f"· {len(ops)} filas ({len(MESES)} meses)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
