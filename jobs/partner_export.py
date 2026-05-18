"""partner_export.py — exporta el AuM de cuentas puntuales a ACAPortfolio.Cartera.

La API externa del proveedor (servicio `partner-api`, aparte) NO lee
`Valuaciones.AuM` directo: lee esta colección dedicada, que contiene SOLO
las cuentas de `config.PARTNER_EXPORT_CUENTAS` y SOLO los campos que el
proveedor necesita. Así el export controla exactamente qué sale y la API
del proveedor nunca toca la base real.

Schema de `ACAPortfolio.Cartera` (1 doc por (fecha, id_cuenta, unidad)):
  {
    fecha:       "YYYY-MM-DD",   # = fecha_snapshot del AuM
    id_cuenta:   "805",
    cuenta:      "[805] NOMBRE",
    unidad:      "...",
    cantidad:    float,
    precio:      float,
    valuacion:   float,
    exported_at: datetime UTC,
  }

Mantiene histórico (no borra fechas viejas). Idempotente: re-correr una
fecha reemplaza solo los docs de esa fecha.

Uso:
  python -m jobs.partner_export          # exporta solo la última fecha (uso del cron diario)
  python -m jobs.partner_export --all    # backfill: exporta TODAS las fechas de Valuaciones.AuM

Corre como cron 1×/día sin flag, encadenado después del AuM final (jobs.aum, 23 UTC).
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

from config import PARTNER_EXPORT_CUENTAS
from core.mongo import get_mongo_client

_DB_NAME = "ACAPortfolio"
_COL_NAME = "Cartera"

# Campos del doc AuM que se exponen al proveedor. Cualquier campo fuera de
# esta lista NO sale — el export es la frontera de qué ve el proveedor.
_PROJ_AUM = {
    "_id": 0, "id_cuenta": 1, "cuenta": 1, "unidad": 1,
    "cantidad": 1, "precio": 1, "valuacion": 1,
}


def _export_fecha(col, aum, cuentas: list[str], fecha_snapshot) -> int:
    """Exporta una `fecha_snapshot` puntual. Idempotente: borra y reinserta
    los docs de esa fecha. Devuelve cuántas posiciones insertó."""
    fecha = str(fecha_snapshot)[:10]
    docs = list(aum.find(
        {"fecha_snapshot": fecha_snapshot, "id_cuenta": {"$in": cuentas}},
        _PROJ_AUM,
    ))
    if not docs:
        print(f"⚠ {fecha}: sin posiciones para {cuentas} — se saltea.")
        return 0

    ahora = datetime.now(UTC)
    export_docs = [
        {
            "fecha":       fecha,
            "id_cuenta":   d.get("id_cuenta"),
            "cuenta":      d.get("cuenta"),
            "unidad":      d.get("unidad"),
            "cantidad":    d.get("cantidad"),
            "precio":      d.get("precio"),
            "valuacion":   d.get("valuacion"),
            "exported_at": ahora,
        }
        for d in docs
    ]
    # Idempotente: reemplaza solo los docs de ESTA fecha.
    col.delete_many({"fecha": fecha})
    col.insert_many(export_docs)

    n_ctas = len({d["id_cuenta"] for d in export_docs})
    print(f"✅ {fecha}: {len(export_docs)} posiciones de {n_ctas} cuenta(s).")
    return len(export_docs)


def main(todas: bool = False) -> None:
    cuentas = [str(c).strip() for c in PARTNER_EXPORT_CUENTAS if str(c).strip()]
    if not cuentas:
        # Fail-safe: sin cuentas configuradas NO exportamos nada. Evita que
        # un config vacío termine volcando todo el AuM al proveedor.
        print("⚠ config.PARTNER_EXPORT_CUENTAS está vacío — abortando, "
              "no se exportó nada.")
        return

    client = get_mongo_client()
    aum = client["Valuaciones"]["AuM"]
    col = client[_DB_NAME][_COL_NAME]
    # Índice para el patrón de query de la API del proveedor (por cuenta /
    # por fecha). create_index es idempotente.
    col.create_index([("id_cuenta", 1), ("fecha", -1)])

    if todas:
        fechas = sorted(f for f in aum.distinct("fecha_snapshot") if f)
        if not fechas:
            print("⚠ Valuaciones.AuM no tiene snapshots — abortando.")
            return
        print(f"Backfill — {len(fechas)} fecha(s) de snapshot en Valuaciones.AuM.")
        total = sum(_export_fecha(col, aum, cuentas, f) for f in fechas)
        print(f"✅ Backfill completo — {total} posiciones exportadas a "
              f"{_DB_NAME}.{_COL_NAME} ({datetime.now(UTC).isoformat()}).")
        return

    # Modo cron: solo la última fecha de snapshot.
    last = aum.find_one(
        {}, {"_id": 0, "fecha_snapshot": 1}, sort=[("fecha_snapshot", -1)],
    )
    if not last or not last.get("fecha_snapshot"):
        print("⚠ Valuaciones.AuM no tiene snapshots — abortando.")
        return
    total = _export_fecha(col, aum, cuentas, last["fecha_snapshot"])
    if total:
        print(f"✅ Export de la última fecha completo — {total} posiciones a "
              f"{_DB_NAME}.{_COL_NAME} ({datetime.now(UTC).isoformat()}).")


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Exporta el AuM de cuentas puntuales a ACAPortfolio.Cartera.",
    )
    p.add_argument(
        "--all", action="store_true", dest="todas",
        help="Backfill: exporta TODAS las fechas de snapshot, no solo la última.",
    )
    args = p.parse_args()
    main(todas=args.todas)
