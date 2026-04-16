"""Script para crear y migrar colecciones API desde las colecciones existentes.

Uso:
    python -m scripts.api_migrate accionistas       → migra CashFlow.Accionistas → CashFlow.AccionistasAPI
"""
import re
import sys

from core.mongo import get_mongo_client

_CUENTA_RE = re.compile(r"^\[(\d+)\]\s+(.+)$")


def _parse_cuenta(raw: str) -> tuple[int | None, str]:
    """Extrae (id_cuenta, nombre) de '[139] LA SEGUNDA SEGUROS DE RETIRO SA RVP'."""
    m = _CUENTA_RE.match(raw.strip())
    if m:
        return int(m.group(1)), m.group(2).strip()
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


COMMANDS = {
    "accionistas": migrate_accionistas,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Uso: python -m scripts.api_migrate <{'|'.join(COMMANDS)}>")
        sys.exit(1)
    COMMANDS[sys.argv[1]]()
