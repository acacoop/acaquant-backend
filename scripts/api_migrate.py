"""Script para crear y migrar colecciones API desde las colecciones existentes.

Uso:
    python -m scripts.api_migrate accionistas       → migra CashFlow.Accionistas → CashFlow.AccionistasAPI
    python -m scripts.api_migrate contrapartes      → migra CashFlow.Contrapartes → CashFlow.ContrapartesAPI
    python -m scripts.api_migrate flujo             → copia CashFlow.Flujo → OperacionesAPI.MesaAPI
    python -m scripts.api_migrate movimientos       → copia CashFlow.Movimientos → OperacionesAPI.FlujosAPI
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
    Destino: {unidad, bruto, contraparte, concertacion, boleto, cuenta, segmento, moneda}

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
            "cuenta": doc.get("cuenta"),
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


COMMANDS = {
    "accionistas": migrate_accionistas,
    "contrapartes": migrate_contrapartes,
    "mover": mover_a_cuentasapi,
    "flujo": migrate_flujo,
    "movimientos": migrate_movimientos,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Uso: python -m scripts.api_migrate <{'|'.join(COMMANDS)}>")
        sys.exit(1)
    COMMANDS[sys.argv[1]]()
