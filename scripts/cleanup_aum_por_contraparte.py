"""Borra de Valuaciones.AuM los docs cuya `cuenta` contenga (case-insensitive,
palabra completa) algún valor de `CashFlow.Contrapartes.contraparte`.

Complementa `cleanup_aum_excluidos` cuando el sync ContrapartesAPI / AuM
no es perfecto (cuentas renombradas, IDs distintos, contrapartes nuevas
sin propagarse a la copia API). Acá el matching va por NOMBRE: si la
cuenta en AuM contiene la palabra "ADCAP", "LOMBARD", "BALANZ" (o lo que
sea que esté como `contraparte` en CashFlow), se borra.

Uso:
    python -m scripts.cleanup_aum_por_contraparte --dry-run
    python -m scripts.cleanup_aum_por_contraparte
    python -m scripts.cleanup_aum_por_contraparte --no-word-boundary
"""
from __future__ import annotations

import argparse
import re

from core.mongo import get_mongo_client

# Strings que aparecen en el campo `contraparte` pero no son nombres
# reales (placeholders o vacíos). No los usamos como criterio de match.
_PLACEHOLDERS: frozenset[str] = frozenset({
    "", "NO APLICA", "N/A", "NONE", "NULL", "-",
})

# Largo mínimo de un nombre para usarlo como criterio de match. Nombres
# de 1-2 caracteres ("AC", "AR") darían falsos positivos masivos.
_MIN_NAME_LEN = 3


def get_contrapartes_names() -> list[str]:
    """Lee `CashFlow.Contrapartes` y devuelve nombres únicos de `contraparte`,
    normalizados a mayúsculas, ordenados por longitud descendente (para que
    en el regex matcheen primero los más específicos)."""
    client = get_mongo_client()
    raw = client["CashFlow"]["Contrapartes"].distinct("contraparte")
    out: set[str] = set()
    for r in raw:
        if not isinstance(r, str):
            continue
        s = r.strip().upper()
        if not s or s in _PLACEHOLDERS or len(s) < _MIN_NAME_LEN:
            continue
        out.add(s)
    # Ordenado por longitud desc — Mongo regex es greedy left-to-right;
    # poner los más largos primero matchea "ALLARIA SECURITIES" antes que
    # "ALLARIA" si ambos están en la lista.
    return sorted(out, key=lambda x: (-len(x), x))


def build_regex(names: list[str], word_boundary: bool) -> str:
    """Arma el pattern Mongo que matchea cualquiera de los nombres."""
    sep = r"\b" if word_boundary else ""
    parts = [sep + re.escape(n) + sep for n in names]
    return "|".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="No borra, solo cuenta y muestra ejemplos.")
    ap.add_argument("--no-word-boundary", action="store_true",
                    help="Por default los nombres se matchean como palabra "
                         "completa (\\bNAME\\b). Si pasás esto, hace match "
                         "por substring (más agresivo, riesgo de falsos pos).")
    args = ap.parse_args()

    names = get_contrapartes_names()
    print(f"Nombres de contraparte únicos: {len(names)}")
    if not names:
        print("No hay nombres que usar como criterio. Nada que hacer.")
        return

    pattern = build_regex(names, word_boundary=not args.no_word_boundary)

    client = get_mongo_client()
    col = client["Valuaciones"]["AuM"]
    match = {"cuenta": {"$regex": pattern, "$options": "i"}}

    n = col.count_documents(match)
    print(f"Docs en Valuaciones.AuM cuya `cuenta` matchea: {n}")
    if n == 0:
        print("Nada que borrar.")
        return

    # Muestra de cuentas únicas afectadas (sin importes — solo nombres).
    cuentas_afectadas = sorted(set(col.distinct("cuenta", match)))
    print(f"\nCuentas únicas que matchean ({len(cuentas_afectadas)}):")
    show = cuentas_afectadas[:40]
    for c in show:
        print(f"  {c}")
    if len(cuentas_afectadas) > 40:
        print(f"  ... y {len(cuentas_afectadas) - 40} más")

    if args.dry_run:
        print("\n--dry-run: nada se borró.")
        return

    result = col.delete_many(match)
    print(f"\ndeleted_count: {result.deleted_count}")
    print("\nPróximos pasos:")
    print("  python -m scripts.api_migrate aum")
    print("  python -m jobs.aum_resumen_fci --backfill")
    print("  systemctl restart api.service")


if __name__ == "__main__":
    main()
