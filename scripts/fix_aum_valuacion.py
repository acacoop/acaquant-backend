"""
fix_aum_valuacion.py — recalcular `valuacion` para unidades cuyo divisor
/100 no se aplicó (típicamente porque vino con tipoTitulo desconocido o
null y `_calcular_valuacion` cae al default `precio * cantidad`).

Para una unidad dada (match por substring por default, exacto con
--exact), recorre TODOS los docs de Valuaciones.AuM y setea
    valuacion = precio * cantidad / 100

NO toca precio, cantidad ni tipoTitulo. Solo `valuacion`. Idempotente
si ya estaba dividido — aún así lo dividiría de nuevo, así que NO
correr dos veces sobre la misma unidad. El dry-run obliga a confirmar
visual.

Uso:
    # Ver qué tocaría (dry-run, default)
    python -m scripts.fix_aum_valuacion --unidad D16E6

    # Match exacto en lugar de substring
    python -m scripts.fix_aum_valuacion --unidad "D16E6" --exact

    # Solo de un mes en particular (acotar por fecha_snapshot)
    python -m scripts.fix_aum_valuacion --unidad D16E6 --desde 2025-07-01 --hasta 2026-02-28

    # Aplicar (después de ver el dry-run y estar seguro)
    python -m scripts.fix_aum_valuacion --unidad D16E6 --yes

    # AUDIT: listar unidades únicas en AuM cuyo tipoTitulo NO es conocido
    # (potencialmente otros casos como D16E6 que el cron deja sin /100)
    python -m scripts.fix_aum_valuacion --audit
"""
from __future__ import annotations

import argparse
import re

from pymongo import UpdateOne

from core.mongo import get_mongo_client

# Mismos sets que jobs/aum.py::_calcular_valuacion. Si esto se desincroniza
# con el cron, este script reportará falsos positivos en --audit.
_TIPOS_DIVISOR_100 = {
    "Títulos Públicos",
    "Letras del Tesoro Capitalizables en Pesos",
    "Letras del Tesoro Ajustables por CER en Pesos",
    "Títulos de Deuda",
    "Obligaciones Negociables",
    "Fideicomisos Financieros",
    "Cheques de Pago Diferido",
}
_TIPOS_FUTUROS = {"Futuros", "Forwards", "Derivados"}


def _build_match(unidad: str, exact: bool, desde: str | None, hasta: str | None) -> dict:
    match: dict = {}
    if exact:
        match["unidad"] = unidad
    else:
        match["unidad"] = {"$regex": re.escape(unidad), "$options": "i"}
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        match["fecha_snapshot"] = rango
    return match


def _fix(unidad: str, exact: bool, desde: str | None, hasta: str | None,
         apply: bool) -> None:
    client = get_mongo_client()
    coll = client["Valuaciones"]["AuM"]

    match = _build_match(unidad, exact, desde, hasta)
    docs = list(coll.find(
        match,
        {"_id": 1, "unidad": 1, "id_cuenta": 1, "cuenta": 1,
         "fecha_snapshot": 1, "cantidad": 1, "precio": 1,
         "valuacion": 1, "tipoTitulo": 1},
    ))
    if not docs:
        print("(sin docs que matcheen)")
        return

    # Resumen de unidades únicas afectadas + suma total previa/posterior.
    unidades_unicas: dict[str, int] = {}
    sum_prev = 0.0
    sum_new = 0.0
    ops = []
    for d in docs:
        u = d.get("unidad", "")
        unidades_unicas[u] = unidades_unicas.get(u, 0) + 1
        try:
            cant = float(d.get("cantidad") or 0)
            precio = float(d.get("precio") or 0)
            val_prev = float(d.get("valuacion") or 0)
        except (TypeError, ValueError):
            continue
        val_new = round((precio * cant) / 100, 6)
        sum_prev += val_prev
        sum_new += val_new
        ops.append(UpdateOne({"_id": d["_id"]}, {"$set": {"valuacion": val_new}}))

    print(f"\n  Docs matched: {len(docs)}")
    print(f"  Unidades únicas afectadas ({len(unidades_unicas)}):")
    for u, n in sorted(unidades_unicas.items(), key=lambda x: -x[1])[:20]:
        print(f"    - {u}  ×{n}")
    if len(unidades_unicas) > 20:
        print(f"    ... +{len(unidades_unicas) - 20} más")
    print(f"\n  Suma valuacion ANTES:    {sum_prev:>20,.2f}")
    print(f"  Suma valuacion DESPUÉS:  {sum_new:>20,.2f}")
    print(f"  Diferencia:              {sum_prev - sum_new:>20,.2f}")

    # Mostrar 5 ejemplos para inspección.
    print("\n  Primeros 5 docs (ANTES):")
    for d in docs[:5]:
        print(f"    [{d.get('id_cuenta')}] {d.get('unidad','')[:60]} "
              f"({d.get('fecha_snapshot')})  "
              f"qty={d.get('cantidad')} px={d.get('precio')} "
              f"val={d.get('valuacion')}  tipo={d.get('tipoTitulo')!r}")

    if not apply:
        print("\n  (DRY-RUN — pasar --yes para ejecutar)")
        return

    print(f"\n  Aplicando {len(ops)} updates...", flush=True)
    res = coll.bulk_write(ops, ordered=False)
    print(f"  ✅ matched={res.matched_count}  modified={res.modified_count}")


def _audit() -> None:
    """Listar unidades únicas con tipoTitulo desconocido (potenciales bugs)."""
    client = get_mongo_client()
    coll = client["Valuaciones"]["AuM"]

    pipeline = [
        {"$group": {
            "_id": {"unidad": "$unidad", "tipoTitulo": "$tipoTitulo"},
            "n":   {"$sum": 1},
        }},
        {"$sort": {"_id.unidad": 1}},
    ]
    rows = list(coll.aggregate(pipeline, allowDiskUse=True))

    desconocidas: dict[str, dict] = {}
    for r in rows:
        u = r["_id"].get("unidad") or ""
        t = r["_id"].get("tipoTitulo")
        if t in _TIPOS_DIVISOR_100:
            continue
        if t and any(f.lower() in str(t).lower() for f in _TIPOS_FUTUROS):
            continue
        # Skip cash explícito (ARS, USD, USDC, USDL no llevan /100)
        if u in {"ARS", "USD", "USDC", "USDL"}:
            continue
        # Skip FCI conocidos (cuotapartes — `precio * qty` directo).
        if "CAFCI" in u:
            continue
        info = desconocidas.setdefault(u, {"tipos": set(), "n": 0})
        info["tipos"].add(str(t) if t is not None else "<null>")
        info["n"] += r["n"]

    if not desconocidas:
        print("(sin unidades con tipoTitulo desconocido — todo OK)")
        return

    print("\n  Unidades con tipoTitulo no contemplado por _TIPOS_DIVISOR_100/FUTUROS")
    print(f"  ({len(desconocidas)} unidades únicas, ordenadas por n_docs):\n")
    print(f"    {'unidad':<60} {'n_docs':>7}  tipoTitulo")
    print(f"    {'-' * 60} {'-' * 7}  {'-' * 40}")
    for u, info in sorted(desconocidas.items(), key=lambda x: -x[1]["n"])[:50]:
        tipos = ", ".join(sorted(info["tipos"]))
        print(f"    {u[:60]:<60} {info['n']:>7}  {tipos[:40]}")
    if len(desconocidas) > 50:
        print(f"    ... +{len(desconocidas) - 50} más")
    print(f"\n  Total unidades sospechosas: {len(desconocidas)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--unidad", help="String a matchear en `unidad` (substring por default)")
    ap.add_argument("--exact",  action="store_true",
                    help="Match exacto (igualdad) en vez de substring")
    ap.add_argument("--desde",  help="Acotar por fecha_snapshot >= YYYY-MM-DD")
    ap.add_argument("--hasta",  help="Acotar por fecha_snapshot <= YYYY-MM-DD")
    ap.add_argument("--yes",    action="store_true",
                    help="Aplicar updates (default: dry-run)")
    ap.add_argument("--audit",  action="store_true",
                    help="Listar unidades con tipoTitulo desconocido (no aplica nada)")
    args = ap.parse_args()

    if args.audit:
        _audit()
        return

    if not args.unidad:
        ap.error("--unidad requerido (o usar --audit)")

    _fix(
        unidad=args.unidad,
        exact=args.exact,
        desde=args.desde,
        hasta=args.hasta,
        apply=args.yes,
    )


if __name__ == "__main__":
    main()
