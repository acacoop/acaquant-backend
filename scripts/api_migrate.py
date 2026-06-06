"""Script para crear y migrar colecciones API desde las colecciones existentes.

Herramienta: infra · Re-sincroniza las colecciones *API derivadas (drop+insert por contrato de API).

Uso:
    python -m scripts.api_migrate assets            → copia Valuaciones.Assets → TitulosAPI.AssetsAPI

Nota: CuentasAPI, OperacionesAPI, PortfolioAPI.AumAPI y TitulosAPI.ValuacionesAPI
fueron ELIMINADAS — la API lee directo de las fuentes (CashFlow.*, Valuaciones.AuM
y Trading.Curvas+BondsMaster vía el servicio api/services/titulos_flujos.py).
Queda solo AssetsAPI (TitulosAPI), un rename UPPER→lower de Valuaciones.Assets.
"""
import sys
from datetime import datetime

from core.mongo import get_mongo_client


def _parse_vencimiento(raw: str) -> datetime | None:
    """Convierte '2026-10-30 00:00:00' o 'NO APLICA' → datetime(2026,10,30) o None."""
    if not raw or raw.strip().upper() == "NO APLICA":
        return None
    try:
        dt = datetime.strptime(raw.strip()[:10], "%Y-%m-%d")
        return datetime(dt.year, dt.month, dt.day)
    except (ValueError, AttributeError):
        return None


def migrate_assets():
    """Copia Valuaciones.Assets → TitulosAPI.AssetsAPI con campos en minúscula.

    Origen:  {unidad, CALIFICACION, CARTERA, CLASE_ACTIVO, EMISOR, TICKER, VENCIMIENTO, INSTRUMENTO}
    Destino: {unidad, calificacion, cartera, clase_activo, emisor, ticker, vencimiento (datetime), instrumento}

    VENCIMIENTO se convierte de string 'YYYY-MM-DD HH:MM:SS' a datetime (solo fecha).
    'NO APLICA' se convierte a null. No borra el origen.
    """
    client = get_mongo_client()
    src = client["Valuaciones"]["Assets"]
    dst = client["TitulosAPI"]["AssetsAPI"]

    docs = list(src.find({}, {"_id": 0}))
    if not docs:
        print("No hay docs en Valuaciones.Assets — nada que migrar.")
        return

    bulk = []
    for doc in docs:
        bulk.append({
            "unidad": doc.get("unidad", ""),
            "calificacion": doc.get("CALIFICACION", ""),
            "cartera": doc.get("CARTERA", ""),
            "clase_activo": doc.get("CLASE_ACTIVO", ""),
            "emisor": doc.get("EMISOR", ""),
            "ticker": doc.get("TICKER", ""),
            "vencimiento": _parse_vencimiento(doc.get("VENCIMIENTO", "")),
            "instrumento": doc.get("INSTRUMENTO", ""),
        })

    dst.drop()
    dst.insert_many(bulk)
    print(f"OK: {len(bulk)} docs copiados a TitulosAPI.AssetsAPI")

    for d in bulk[:3]:
        print(f"  unidad={d['unidad']!r}  ticker={d['ticker']!r}  cartera={d['cartera']!r}  emisor={d['emisor']!r}")
    if len(bulk) > 3:
        print(f"  ... y {len(bulk) - 3} más")


COMMANDS = {
    "assets": migrate_assets,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Uso: python -m scripts.api_migrate <{'|'.join(COMMANDS)}>")
        sys.exit(1)
    COMMANDS[sys.argv[1]]()
