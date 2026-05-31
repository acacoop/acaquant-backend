"""Saca la hora ' 00:00:00' de VENCIMIENTO en Valuaciones.Assets → deja 'YYYY-MM-DD'.

El campo venía guardado como 'YYYY-MM-DD 00:00:00' (la hora siempre es medianoche
y no aporta nada). Esto normaliza TODOS los assets cuyo VENCIMIENTO tenga una hora
detrás de la fecha, dejándolos en 'YYYY-MM-DD'. No toca placeholders ('-', '',
'NO APLICA') ni valores que ya están sin hora.

Seguro: el downstream (scripts/api_migrate.py) ya parsea solo los primeros 10
caracteres ([:10]), así que funciona igual con o sin hora.

DRY-RUN por defecto. Para aplicar:
    python -m scripts.normalizar_vencimiento_sin_hora            # dry-run
    python -m scripts.normalizar_vencimiento_sin_hora --apply    # escribe

Tras aplicar, re-sincronizar la copia derivada:
    python -m scripts.api_migrate assets

Idempotente: re-correr no encuentra nada (ya no quedan VENCIMIENTO con hora).
"""
from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime

from core.mongo import get_mongo_client, get_mongo_client_read

_ACTOR = "normalizar_vencimiento_sin_hora"
# Fecha ISO seguida de cualquier cosa (espacio + hora) → la queremos recortar.
_RE_CON_HORA = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T].*$")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Escribe (default: dry-run)")
    args = ap.parse_args()

    read_db = get_mongo_client_read()
    col = read_db["Valuaciones"]["Assets"]

    # Solo los que tienen 'YYYY-MM-DD ' + algo detrás.
    filtro = {"VENCIMIENTO": {"$regex": r"^\d{4}-\d{2}-\d{2}[ T]"}}
    docs = list(col.find(filtro, {"_id": 0, "unidad": 1, "VENCIMIENTO": 1}))

    print("=== normalizar VENCIMIENTO sin hora en Valuaciones.Assets ===")
    print(f"docs con hora a recortar: {len(docs)}\n")

    propuestas: list[tuple[str, str]] = []  # (unidad, nuevo)
    for d in docs:
        v = d.get("VENCIMIENTO", "")
        m = _RE_CON_HORA.match(v)
        if m:
            propuestas.append((d.get("unidad", ""), m.group(1)))

    for unidad, nuevo in propuestas[:15]:
        print(f"  {unidad!r}  →  VENCIMIENTO={nuevo!r}")
    if len(propuestas) > 15:
        print(f"  ... y {len(propuestas) - 15} más")

    if not args.apply:
        print("\nDRY-RUN — no se escribió nada. Re-correr con --apply para aplicar.")
        return

    # --- APPLY ---
    write_col = get_mongo_client()["Valuaciones"]["Assets"]
    now = datetime.now(UTC)
    escritos = 0
    for unidad, nuevo in propuestas:
        res = write_col.update_one(
            {"unidad": unidad},
            {"$set": {"VENCIMIENTO": nuevo, "actualizado_por": _ACTOR, "actualizado_at": now}},
        )
        escritos += res.modified_count

    print(f"\n✓ APLICADO: {escritos}/{len(propuestas)} VENCIMIENTO normalizados")
    print("  Re-sincronizar la copia derivada con: python -m scripts.api_migrate assets")


if __name__ == "__main__":
    main()
