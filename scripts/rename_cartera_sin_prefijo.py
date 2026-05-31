"""Saca el prefijo redundante 'CARTERA ' de los valores del campo CARTERA
en Valuaciones.Assets (el campo ya se llama CARTERA → 'CARTERA RENTA VARIABLE'
ocupa espacio al pedo). Ej: 'CARTERA RENTA VARIABLE' → 'RENTA VARIABLE',
'CARTERA FCI' → 'FCI'.

El código backend (portfolio/operaciones/aum_resumen_fci) y el color map del
frontend ya quedaron tolerantes a ambos valores (nuevo y legacy), así que el
orden de deploy no importa. Tras aplicar, re-sincronizar la copia derivada:
    python -m scripts.api_migrate assets

DRY-RUN por defecto (no escribe). Para aplicar:
    python -m scripts.rename_cartera_sin_prefijo            # dry-run
    python -m scripts.rename_cartera_sin_prefijo --apply    # escribe

Idempotente: re-correr no encuentra nada (ya no quedan valores con prefijo).
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

from core.mongo import get_mongo_client, get_mongo_client_read

_PREFIJO = "CARTERA "
_ACTOR = "rename_cartera_sin_prefijo"


def _nuevo_valor(valor: str) -> str | None:
    """Devuelve el valor sin prefijo, o None si no aplica (no tiene prefijo
    o quedaría vacío)."""
    if not isinstance(valor, str) or not valor.startswith(_PREFIJO):
        return None
    nuevo = valor[len(_PREFIJO):].strip()
    return nuevo or None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Escribe (default: dry-run)")
    args = ap.parse_args()

    read_db = get_mongo_client_read()
    col = read_db["Valuaciones"]["Assets"]

    print("=== sacar prefijo 'CARTERA ' de Valuaciones.Assets.CARTERA ===\n")

    valores = sorted(v for v in col.distinct("CARTERA") if isinstance(v, str) and v)
    print(f"valores distintos de CARTERA: {len(valores)}")

    cambios: list[tuple[str, str, int]] = []  # (viejo, nuevo, n_docs)
    for v in valores:
        nuevo = _nuevo_valor(v)
        n = col.count_documents({"CARTERA": v})
        marca = f"  →  {nuevo!r}" if nuevo else "   (sin cambio)"
        print(f"  {v!r:<32} ({n:>4} docs){marca}")
        if nuevo:
            cambios.append((v, nuevo, n))

    total = sum(n for _, _, n in cambios)
    print(f"\n{len(cambios)} valores a renombrar · {total} docs afectados")

    if not args.apply:
        print("\nDRY-RUN — no se escribió nada. Re-correr con --apply para aplicar.")
        return

    # --- APPLY ---
    write_col = get_mongo_client()["Valuaciones"]["Assets"]
    now = datetime.now(UTC)
    escritos = 0
    for viejo, nuevo, _n in cambios:
        res = write_col.update_many(
            {"CARTERA": viejo},
            {"$set": {"CARTERA": nuevo, "actualizado_por": _ACTOR, "actualizado_at": now}},
        )
        escritos += res.modified_count
        print(f"  {viejo!r} → {nuevo!r}: {res.modified_count} docs")

    print(f"\n✓ APLICADO: {escritos} docs renombrados")
    print("  Re-sincronizar la copia derivada con: python -m scripts.api_migrate assets")


if __name__ == "__main__":
    main()
