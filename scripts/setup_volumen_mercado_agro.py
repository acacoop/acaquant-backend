"""scripts/setup_volumen_mercado_agro.py — crea/mantiene CashFlow.VolumenMercadoAgro.

Volumen TOTAL del mercado de futuros agropecuarios (SOJA/TRIGO/MAIZ), cargado a
MANO porque no está apificado. Sirve para calcular nuestro market share:
    share = nuestras_toneladas / toneladas_mercado   (por periodo + commodity)
Nuestras toneladas salen de CashFlow.Operaciones (ver /ops/agro).

Esquema (1 doc por periodo+commodity):
    {
      "periodo":    "2026-05",        # YYYY-MM (MENSUAL) — string
      "commodity":  "SOJA",           # SOJA | TRIGO | MAIZ
      "toneladas":  1234567.0,        # volumen TOTAL del mercado en ese mes
      "fuente":     "MATBA-ROFEX",    # de dónde salió (opcional, informativo)
      "actualizado_en": <datetime>,   # auditoría (lo setea el script)
    }
Índice único (periodo, commodity) → upsert idempotente; re-cargar pisa el valor.

Uso:
    python -m scripts.setup_volumen_mercado_agro            # crea colección + índice y upsertea DATOS
    python -m scripts.setup_volumen_mercado_agro --dry-run  # muestra qué haría, no escribe

Para cargar datos: agregá filas a DATOS abajo (o pasá un commodity en contratos
con su `unidad` y se convierte) y re-corré el script.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime

from pymongo import ASCENDING, UpdateOne
from pymongo.errors import CollectionInvalid

from core.mongo import get_mongo_client

_COMMS = {"SOJA", "TRIGO", "MAIZ"}

# ── DATOS A CARGAR ───────────────────────────────────────────────────────────
# Cada fila: periodo "YYYY-MM", commodity, y el volumen del mercado.
# - Si ya lo tenés en TONELADAS → usá "toneladas".
# - Si lo tenés en CONTRATOS → usá "contratos" + "unidad" ("full"=×100, "mini"=×10);
#   el script lo convierte a toneladas igual que nosotros.
# Dejar la lista vacía sólo crea la colección + el índice (sin cargar nada).
DATOS: list[dict] = [
    # {"periodo": "2026-05", "commodity": "SOJA", "toneladas": 1_500_000, "fuente": "MATBA-ROFEX"},
    # {"periodo": "2026-05", "commodity": "TRIGO", "contratos": 12_000, "unidad": "full", "fuente": "MATBA-ROFEX"},
]

_FACTOR = {"full": 100, "mini": 10}


def _toneladas(row: dict) -> float:
    """Toneladas de la fila: directas o convertidas desde contratos."""
    if row.get("toneladas") is not None:
        return float(row["toneladas"])
    contratos = row.get("contratos")
    unidad = (row.get("unidad") or "full").lower()
    if contratos is None or unidad not in _FACTOR:
        raise ValueError(f"fila sin toneladas ni contratos+unidad válidos: {row!r}")
    return float(contratos) * _FACTOR[unidad]


def _validar(row: dict) -> tuple[str, dict]:
    periodo = str(row.get("periodo") or "").strip()
    commodity = str(row.get("commodity") or "").strip().upper()
    if len(periodo) != 7 or periodo[4] != "-":
        raise ValueError(f"periodo inválido (esperaba YYYY-MM): {periodo!r}")
    if commodity not in _COMMS:
        raise ValueError(f"commodity inválido {commodity!r} (esperaba {_COMMS})")
    doc = {
        "periodo": periodo,
        "commodity": commodity,
        "toneladas": round(_toneladas(row), 0),
        "fuente": row.get("fuente"),
        "actualizado_en": datetime.now(UTC),
    }
    return f"{periodo}/{commodity}", doc


def main() -> int:
    dry = "--dry-run" in sys.argv
    db = get_mongo_client()["CashFlow"]

    # 1) Crear la colección (idempotente) — así existe aunque DATOS esté vacío.
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

    # 3) Upsert de DATOS (si hay).
    if not DATOS:
        print("DATOS vacío → no se carga nada (colección lista para llenar a mano).")
        return 0

    ops, etiquetas = [], []
    for row in DATOS:
        etiqueta, doc = _validar(row)
        etiquetas.append(f"{etiqueta} = {doc['toneladas']:,.0f} t")
        ops.append(UpdateOne({"periodo": doc["periodo"], "commodity": doc["commodity"]},
                             {"$set": doc}, upsert=True))

    if dry:
        print(f"\n[DRY-RUN] {len(ops)} filas a upsertear:")
        for e in etiquetas:
            print("  ", e)
        return 0

    res = col.bulk_write(ops, ordered=False)
    print(f"\nupsert: {res.upserted_count} nuevos · {res.modified_count} actualizados "
          f"· {len(ops)} filas")
    for e in etiquetas:
        print("  ", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
