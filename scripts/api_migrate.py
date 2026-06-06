"""Script para crear y migrar colecciones API desde las colecciones existentes.

Herramienta: infra · Re-sincroniza las colecciones *API derivadas (drop+insert por contrato de API).

Uso:
    python -m scripts.api_migrate aum               → copia Valuaciones.AuM → PortfolioAPI.AumAPI
    python -m scripts.api_migrate assets            → copia Valuaciones.Assets → TitulosAPI.AssetsAPI
    python -m scripts.api_migrate flujos-titulos    → merge Trading.Curvas + Trading.BondsMaster → TitulosAPI.ValuacionesAPI

Nota: CuentasAPI y OperacionesAPI (MesaAPI/FlujosAPI) fueron ELIMINADAS — la API
lee directo de CashFlow.Accionistas/Contrapartes/Flujo/Movimientos.
"""
import sys
from datetime import datetime

from core.mongo import get_mongo_client


def _fecha_str_to_datetime(raw: str) -> datetime | None:
    """Convierte '2026-03-28' (YYYY-MM-DD) → datetime(2026, 3, 28)."""
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d")
    except (ValueError, AttributeError):
        return None


def migrate_aum():
    """Copia Valuaciones.AuM → PortfolioAPI.AumAPI con campos seleccionados.

    Origen:  {fecha_snapshot, id_cuenta, unidad, cantidad, cuenta, precio, valuacion, timestamp, tipoTitulo, ...}
    Destino: {fecha, id_cuenta, unidad, cantidad, cuenta, precio, valuacion}

    fecha_snapshot (string YYYY-MM-DD) se convierte a datetime.
    No borra el origen.
    """
    client = get_mongo_client()
    src = client["Valuaciones"]["AuM"]
    dst = client["PortfolioAPI"]["AumAPI"]

    projection = {
        "_id": 0, "fecha_snapshot": 1, "id_cuenta": 1, "unidad": 1,
        "cantidad": 1, "cuenta": 1, "precio": 1, "valuacion": 1,
    }
    docs = list(src.find({}, projection))
    if not docs:
        print("No hay docs en Valuaciones.AuM — nada que migrar.")
        return

    bulk = []
    for doc in docs:
        fecha = _fecha_str_to_datetime(doc.get("fecha_snapshot", ""))
        bulk.append({
            "fecha": fecha,
            "id_cuenta": doc.get("id_cuenta", ""),
            "unidad": doc.get("unidad", ""),
            "cantidad": doc.get("cantidad"),
            "cuenta": doc.get("cuenta", ""),
            "precio": doc.get("precio"),
            "valuacion": doc.get("valuacion"),
        })

    dst.drop()
    dst.insert_many(bulk)
    # Recrear índice tras el rebuild: drop() borra los índices, y AumAPI se
    # consulta por (fecha, id_cuenta) (último snapshot + filtro por cuenta).
    # Sin esto quedan 248k docs en COLLSCAN. (fecha desc cubre el find_one del
    # último; +id_cuenta cubre el filtro por cuenta del mismo snapshot.)
    dst.create_index([("fecha", -1), ("id_cuenta", 1)], name="fecha_idcuenta")
    print(f"OK: {len(bulk)} docs copiados a PortfolioAPI.AumAPI (+ índice fecha_idcuenta)")

    for d in bulk[:3]:
        print(f"  fecha={d['fecha']}  id_cuenta={d['id_cuenta']!r}  unidad={d['unidad']!r}  valuacion={d['valuacion']}")
    if len(bulk) > 3:
        print(f"  ... y {len(bulk) - 3} más")


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
    'NO APLICA' se convierte a null.

    No borra el origen.
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
        venc_raw = doc.get("VENCIMIENTO", "")
        venc = _parse_vencimiento(venc_raw)
        bulk.append({
            "unidad": doc.get("unidad", ""),
            "calificacion": doc.get("CALIFICACION", ""),
            "cartera": doc.get("CARTERA", ""),
            "clase_activo": doc.get("CLASE_ACTIVO", ""),
            "emisor": doc.get("EMISOR", ""),
            "ticker": doc.get("TICKER", ""),
            "vencimiento": venc,
            "instrumento": doc.get("INSTRUMENTO", ""),
        })

    dst.drop()
    dst.insert_many(bulk)
    print(f"OK: {len(bulk)} docs copiados a TitulosAPI.AssetsAPI")

    for d in bulk[:3]:
        print(f"  unidad={d['unidad']!r}  ticker={d['ticker']!r}  cartera={d['cartera']!r}  emisor={d['emisor']!r}")
    if len(bulk) > 3:
        print(f"  ... y {len(bulk) - 3} más")


def _fecha_to_datetime(raw) -> datetime | None:
    """Convierte string 'YYYY-MM-DD' o datetime a datetime solo fecha. None si falla."""
    if isinstance(raw, datetime):
        return datetime(raw.year, raw.month, raw.day)
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = datetime.strptime(raw.strip()[:10], "%Y-%m-%d")
        return datetime(dt.year, dt.month, dt.day)
    except (ValueError, AttributeError):
        return None


def _build_from_curvas(doc: dict) -> dict:
    """Construye un doc FlujosAPI desde un doc de Trading.Curvas."""
    flujos_raw = doc.get("flujos", []) or []
    flujos = []
    for f in flujos_raw:
        flujos.append({
            "fecha": _fecha_to_datetime(f.get("fecha")),
            "amortizacion": f.get("amortizacion_pct", f.get("amortizacion")),
            "interes": f.get("cupon_sobre_residual", f.get("interes")),
            "residual": f.get("residual_previo_pct", f.get("valor_residual")),
        })

    return {
        "ticker": doc.get("ticker_corto", ""),
        "instrumento": doc.get("ticker", ""),
        "curva": doc.get("curva", ""),
        "moneda_flujo": "",
        "fecha_emision": _fecha_to_datetime(doc.get("fecha_emision")),
        "fecha_vencimiento": _fecha_to_datetime(doc.get("fecha_vencimiento")),
        "valor_nominal": doc.get("valor_nominal"),
        "cupon_anual": doc.get("cupon_anual"),
        "cer_emision": doc.get("cer_emision"),
        "tasa_cupon": None,
        "flujo_vencimiento": doc.get("flujo_vencimiento"),
        "valor_residual_actual_pct": doc.get("valor_residual_actual_pct"),
        "flujos": flujos,
    }


def _calcular_residual_actual(flujos: list[dict]) -> float | None:
    """Calcula el residual actual: residual del último flujo cuya fecha ya pasó.

    Si no hay flujos pasados, devuelve 100 (no amortizó nada aún).
    Si no hay flujos, devuelve None.
    """
    if not flujos:
        return None
    hoy = datetime.now()
    ultimo_residual = 100.0
    for f in flujos:
        fecha = f.get("fecha")
        if fecha and fecha <= hoy:
            residual = f.get("residual")
            if residual is not None:
                ultimo_residual = residual
    return ultimo_residual


def _build_from_bondmaster(doc: dict) -> dict:
    """Construye un doc FlujosAPI desde un doc de Trading.BondsMaster."""
    flujos_raw = doc.get("flujos", []) or []
    flujos = []
    for f in flujos_raw:
        flujos.append({
            "fecha": _fecha_to_datetime(f.get("fecha")),
            "amortizacion": f.get("amortizacion"),
            "interes": f.get("interes"),
            "residual": f.get("valor_residual"),
        })

    tickers = doc.get("tickers", {}) or {}
    instrumento = tickers.get("ARS") or tickers.get("USD") or ""

    return {
        "ticker": doc.get("asset", ""),
        "instrumento": instrumento,
        "curva": "",
        "moneda_flujo": doc.get("moneda_flujo", ""),
        "fecha_emision": None,
        "fecha_vencimiento": _fecha_to_datetime(doc.get("vencimiento")),
        "valor_nominal": 100,
        "cupon_anual": None,
        "cer_emision": None,
        "tasa_cupon": doc.get("tasa_cupon"),
        "flujo_vencimiento": None,
        "valor_residual_actual_pct": _calcular_residual_actual(flujos),
        "flujos": flujos,
    }


def migrate_flujos_titulos():
    """Merge Trading.Curvas + Trading.BondsMaster → TitulosAPI.ValuacionesAPI.

    Un doc por instrumento con flujos normalizados.
    Join key con AssetsAPI: ticker.

    No borra los orígenes.
    """
    client = get_mongo_client()
    dst = client["TitulosAPI"]["ValuacionesAPI"]

    # --- Trading.Curvas ---
    curvas_docs = list(client["Trading"]["Curvas"].find({}, {"_id": 0}))
    bulk = [_build_from_curvas(d) for d in curvas_docs]
    n_curvas = len(bulk)

    # --- Trading.BondsMaster ---
    bond_docs = list(client["Trading"]["BondsMaster"].find({}, {"_id": 0}))
    # Evitar duplicados: si un ticker ya vino de Curvas, no lo pisamos
    tickers_curvas = {d["ticker"] for d in bulk}
    for d in bond_docs:
        doc = _build_from_bondmaster(d)
        if doc["ticker"] not in tickers_curvas:
            bulk.append(doc)
    n_bonds = len(bulk) - n_curvas

    if not bulk:
        print("No hay docs en Trading.Curvas ni Trading.BondsMaster — nada que migrar.")
        return

    dst.drop()
    dst.insert_many(bulk)
    print(f"OK: {len(bulk)} docs copiados a TitulosAPI.ValuacionesAPI ({n_curvas} de Curvas, {n_bonds} de BondsMaster)")

    for d in bulk[:3]:
        n_flujos = len(d["flujos"])
        print(f"  ticker={d['ticker']!r}  curva={d['curva']!r}  venc={d['fecha_vencimiento']}  flujos={n_flujos}")
    if len(bulk) > 3:
        print(f"  ... y {len(bulk) - 3} más")


COMMANDS = {
    "aum": migrate_aum,
    "assets": migrate_assets,
    "flujos-titulos": migrate_flujos_titulos,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Uso: python -m scripts.api_migrate <{'|'.join(COMMANDS)}>")
        sys.exit(1)
    COMMANDS[sys.argv[1]]()
