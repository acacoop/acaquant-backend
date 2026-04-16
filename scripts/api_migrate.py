"""Script para crear y migrar colecciones API desde las colecciones existentes.

Uso:
    python -m scripts.api_migrate accionistas       → migra CashFlow.Accionistas → CashFlow.AccionistasAPI
    python -m scripts.api_migrate contrapartes      → migra CashFlow.Contrapartes → CashFlow.ContrapartesAPI
    python -m scripts.api_migrate flujo             → copia CashFlow.Flujo → OperacionesAPI.MesaAPI
    python -m scripts.api_migrate movimientos       → copia CashFlow.Movimientos → OperacionesAPI.FlujosAPI
    python -m scripts.api_migrate carteras          → copia Valuaciones.Carteras → PortfolioAPI.CarterasAPI
    python -m scripts.api_migrate aum               → copia Valuaciones.AuM → PortfolioAPI.AumAPI
    python -m scripts.api_migrate assets            → copia Valuaciones.Assets → TitulosAPI.AssetsAPI
"""
import re
import sys
from datetime import datetime

from core.mongo import get_mongo_client

_CUENTA_RE = re.compile(r"^\[(\d+)\]\s+(.+)$")


def _parse_cuenta(raw: str) -> tuple[str | None, str]:
    """Extrae (id_cuenta, nombre) de '[139] LA SEGUNDA SEGUROS DE RETIRO SA RVP'."""
    m = _CUENTA_RE.match(raw.strip())
    if m:
        return m.group(1), m.group(2).strip()
    return None, raw.strip()


def migrate_accionistas():
    """Lee CashFlow.Accionistas y crea CashFlow.AccionistasAPI con campos normalizados.

    Origen:  {cuenta, accionista}
    Destino: {cuenta, id_cuenta, nombre, grupo}
    """
    client = get_mongo_client()
    src = client["CashFlow"]["Accionistas"]
    dst = client["CashFlow"]["AccionistasAPI"]

    docs = list(src.find({}, {"_id": 0}))
    if not docs:
        print("No hay docs en CashFlow.Accionistas — nada que migrar.")
        return

    bulk = []
    errores = []
    for doc in docs:
        cuenta_raw = doc.get("cuenta", "")
        grupo = doc.get("accionista", "")

        id_cuenta, nombre = _parse_cuenta(cuenta_raw)
        if id_cuenta is None:
            errores.append(cuenta_raw)

        bulk.append({
            "cuenta": cuenta_raw,
            "id_cuenta": id_cuenta,
            "nombre": nombre,
            "grupo": grupo,
        })

    if errores:
        print(f"WARN: {len(errores)} docs no matchearon el patrón [N] NOMBRE:")
        for e in errores:
            print(f"  → {e!r}")

    dst.drop()
    dst.insert_many(bulk)
    print(f"OK: {len(bulk)} docs migrados a CashFlow.AccionistasAPI")

    for d in bulk[:3]:
        print(f"  cuenta={d['cuenta']!r}  id_cuenta={d['id_cuenta']}  nombre={d['nombre']!r}  grupo={d['grupo']!r}")
    if len(bulk) > 3:
        print(f"  ... y {len(bulk) - 3} más")


def migrate_contrapartes():
    """Lee CashFlow.Contrapartes y crea CashFlow.ContrapartesAPI con campos normalizados.

    Origen:  {denominacion, cuenta, contraparte, segmento}
    Destino: {cuenta, id_cuenta, nombre, grupo}
    """
    client = get_mongo_client()
    src = client["CashFlow"]["Contrapartes"]
    dst = client["CashFlow"]["ContrapartesAPI"]

    docs = list(src.find({}, {"_id": 0}))
    if not docs:
        print("No hay docs en CashFlow.Contrapartes — nada que migrar.")
        return

    bulk = []
    for doc in docs:
        bulk.append({
            "cuenta": doc.get("denominacion", ""),
            "id_cuenta": doc.get("cuenta"),
            "nombre": doc.get("contraparte", ""),
            "grupo": doc.get("segmento", ""),
        })

    dst.drop()
    dst.insert_many(bulk)
    print(f"OK: {len(bulk)} docs migrados a CashFlow.ContrapartesAPI")

    for d in bulk[:3]:
        print(f"  cuenta={d['cuenta']!r}  id_cuenta={d['id_cuenta']}  nombre={d['nombre']!r}  grupo={d['grupo']!r}")
    if len(bulk) > 3:
        print(f"  ... y {len(bulk) - 3} más")


def mover_a_cuentasapi():
    """Mueve AccionistasAPI y ContrapartesAPI de CashFlow → CuentasAPI, y borra las de CashFlow."""
    client = get_mongo_client()
    src_db = client["CashFlow"]
    dst_db = client["CuentasAPI"]

    colecciones = ["AccionistasAPI", "ContrapartesAPI"]
    for col_name in colecciones:
        src_col = src_db[col_name]
        dst_col = dst_db[col_name]

        docs = list(src_col.find({}, {"_id": 0}))
        if not docs:
            print(f"SKIP: CashFlow.{col_name} está vacía o no existe.")
            continue

        dst_col.drop()
        dst_col.insert_many(docs)
        print(f"OK: {len(docs)} docs copiados a CuentasAPI.{col_name}")

        src_col.drop()
        print(f"OK: CashFlow.{col_name} eliminada")


def migrate_flujo():
    """Copia CashFlow.Flujo → OperacionesAPI.MesaAPI con campos renombrados.

    Origen:  {instrumento, bruto, contraparte, concertacion, boleto, cuenta, segmento, moneda, ...}
    Destino: {unidad, bruto, contraparte, concertacion, boleto, id_cuenta, segmento, moneda}

    No borra el origen.
    """
    client = get_mongo_client()
    src = client["CashFlow"]["Flujo"]
    dst = client["OperacionesAPI"]["MesaAPI"]

    projection = {
        "_id": 0, "instrumento": 1, "bruto": 1, "contraparte": 1,
        "concertacion": 1, "boleto": 1, "cuenta": 1, "segmento": 1, "moneda": 1,
    }
    docs = list(src.find({}, projection))
    if not docs:
        print("No hay docs en CashFlow.Flujo — nada que migrar.")
        return

    bulk = []
    for doc in docs:
        bulk.append({
            "unidad": doc.get("instrumento", ""),
            "bruto": doc.get("bruto"),
            "contraparte": doc.get("contraparte", ""),
            "concertacion": doc.get("concertacion", ""),
            "boleto": doc.get("boleto"),
            "id_cuenta": doc.get("cuenta"),
            "segmento": doc.get("segmento", ""),
            "moneda": doc.get("moneda", ""),
        })

    dst.drop()
    dst.insert_many(bulk)
    print(f"OK: {len(bulk)} docs copiados a OperacionesAPI.MesaAPI")

    for d in bulk[:3]:
        print(f"  unidad={d['unidad']!r}  contraparte={d['contraparte']!r}  bruto={d['bruto']}  concertacion={d['concertacion']!r}")
    if len(bulk) > 3:
        print(f"  ... y {len(bulk) - 3} más")


def _fecha_ddmmyyyy_to_iso(raw: str) -> str:
    """Convierte '02/07/2025' (dd/mm/yyyy) → '2025-07-02' (YYYY-MM-DD)."""
    try:
        return datetime.strptime(raw.strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return raw


def migrate_movimientos():
    """Copia CashFlow.Movimientos → OperacionesAPI.FlujosAPI con campos renombrados.

    Origen:  {comprobante, cuenta, fecha, informacion, total, unidad, ...}
    Destino: {boleto, cuenta, concertacion, informacion, bruto, unidad}

    fecha se convierte de dd/mm/yyyy a YYYY-MM-DD.
    No borra el origen.
    """
    client = get_mongo_client()
    src = client["CashFlow"]["Movimientos"]
    dst = client["OperacionesAPI"]["FlujosAPI"]

    projection = {
        "_id": 0, "comprobante": 1, "cuenta": 1, "fecha": 1,
        "informacion": 1, "total": 1, "unidad": 1,
    }
    docs = list(src.find({}, projection))
    if not docs:
        print("No hay docs en CashFlow.Movimientos — nada que migrar.")
        return

    bulk = []
    for doc in docs:
        bulk.append({
            "boleto": doc.get("comprobante", ""),
            "cuenta": doc.get("cuenta", ""),
            "concertacion": _fecha_ddmmyyyy_to_iso(doc.get("fecha", "")),
            "informacion": doc.get("informacion", ""),
            "bruto": doc.get("total"),
            "unidad": doc.get("unidad", ""),
        })

    dst.drop()
    dst.insert_many(bulk)
    print(f"OK: {len(bulk)} docs copiados a OperacionesAPI.FlujosAPI")

    for d in bulk[:3]:
        print(f"  boleto={d['boleto']!r}  cuenta={d['cuenta']!r}  concertacion={d['concertacion']!r}  bruto={d['bruto']}  unidad={d['unidad']!r}")
    if len(bulk) > 3:
        print(f"  ... y {len(bulk) - 3} más")


def migrate_carteras():
    """Copia Valuaciones.Carteras → PortfolioAPI.CarterasAPI con timestamp truncado a fecha.

    Origen:  {id_cuenta, unidad, cantidad, precio, actualizado, timestamp}
    Destino: {id_cuenta, unidad, cantidad, precio, timestamp (datetime solo fecha)}

    No borra el origen.
    """
    client = get_mongo_client()
    src = client["Valuaciones"]["Carteras"]
    dst = client["PortfolioAPI"]["CarterasAPI"]

    projection = {
        "_id": 0, "id_cuenta": 1, "unidad": 1, "cantidad": 1,
        "precio": 1, "timestamp": 1,
    }
    docs = list(src.find({}, projection))
    if not docs:
        print("No hay docs en Valuaciones.Carteras — nada que migrar.")
        return

    bulk = []
    for doc in docs:
        ts = doc.get("timestamp")
        if isinstance(ts, datetime):
            ts = datetime(ts.year, ts.month, ts.day)
        bulk.append({
            "id_cuenta": doc.get("id_cuenta", ""),
            "unidad": doc.get("unidad", ""),
            "cantidad": doc.get("cantidad"),
            "precio": doc.get("precio"),
            "timestamp": ts,
        })

    dst.drop()
    dst.insert_many(bulk)
    print(f"OK: {len(bulk)} docs copiados a PortfolioAPI.CarterasAPI")

    for d in bulk[:3]:
        print(f"  id_cuenta={d['id_cuenta']!r}  unidad={d['unidad']!r}  cantidad={d['cantidad']}  precio={d['precio']}  timestamp={d['timestamp']}")
    if len(bulk) > 3:
        print(f"  ... y {len(bulk) - 3} más")


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
    print(f"OK: {len(bulk)} docs copiados a PortfolioAPI.AumAPI")

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


COMMANDS = {
    "accionistas": migrate_accionistas,
    "contrapartes": migrate_contrapartes,
    "mover": mover_a_cuentasapi,
    "flujo": migrate_flujo,
    "movimientos": migrate_movimientos,
    "carteras": migrate_carteras,
    "aum": migrate_aum,
    "assets": migrate_assets,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Uso: python -m scripts.api_migrate <{'|'.join(COMMANDS)}>")
        sys.exit(1)
    COMMANDS[sys.argv[1]]()
