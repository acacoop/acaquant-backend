"""diag_flujo_contraparte.py — por qué una contraparte con operaciones no
aparece en la vista Contrapartes.

La vista Contrapartes se arma con los FLUJOS de /api/operaciones/flujo, que lee
OperacionesAPI.MesaAPI (copia de CashFlow.Flujo) y AGRUPA por el campo
`contraparte`. Una contraparte aparece SOLO si hay flujos cuyo `contraparte` ==
su nombre, dentro del rango (la vista pide los últimos ~2 años). La lista de
ContrapartesAPI solo aporta el `grupo` (Fondos/ALYC/…), NO trae la contraparte
al listado.

Este diag busca los flujos que matchean un término en MesaAPI (lo que LEE la
vista) y en la fuente CashFlow.Flujo, y muestra qué `contraparte` tienen — así
ves si el problema es sync (no está en MesaAPI) o linkeo (el `contraparte` del
flujo no coincide con el nombre).

Read-only. Uso (desde la raíz del repo en el Droplet):
    venv/bin/python -m scripts.diag_flujo_contraparte            # default: DRACMA
    venv/bin/python -m scripts.diag_flujo_contraparte --q 1927
"""
from __future__ import annotations

import argparse
from collections import Counter

from core.mongo import get_mongo_client_read


def _scan(coll, q: str, campos: list[str]) -> list[dict]:
    out = []
    for d in coll.find({}, {"_id": 0}):
        if any(q in str(d.get(cm) or "").lower() for cm in campos):
            out.append(d)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", default="DRACMA",
                    help="término a buscar (contraparte / cuenta / denominacion)")
    args = ap.parse_args()
    q = args.q.lower()

    c = get_mongo_client_read()
    mesa = c["OperacionesAPI"]["MesaAPI"]   # lo que LEE la vista Contrapartes
    src = c["CashFlow"]["Flujo"]            # la fuente

    for label, coll, campos in [
        ("OperacionesAPI.MesaAPI  (la que LEE la vista)", mesa,
         ["contraparte", "cuenta", "denominacion"]),
        ("CashFlow.Flujo  (FUENTE)", src,
         ["contraparte", "cuenta", "denominacion", "instrumento"]),
    ]:
        hits = _scan(coll, q, campos)
        print("=" * 100)
        print(f"{label} — {len(hits)} flujos matchean '{args.q}'")
        print("=" * 100)
        if not hits:
            print("  (ninguno)\n")
            continue
        cps = Counter(str(d.get("contraparte") or "(vacío)") for d in hits)
        print("  campo `contraparte` (valor → cuántos flujos):")
        for v, n in cps.most_common():
            print(f"      {v!r}: {n}")
        fechas = sorted(str(d.get("concertacion") or "") for d in hits if d.get("concertacion"))
        if fechas:
            print(f"  rango de concertación: {fechas[0]} → {fechas[-1]}")
        print("  ejemplos:")
        for d in hits[:5]:
            print(f"      contraparte={d.get('contraparte')!r}  cuenta={d.get('cuenta')!r}  "
                  f"concertacion={d.get('concertacion')!r}  bruto={d.get('bruto')}")
        print()

    print("=" * 100)
    print("CÓMO LEERLO")
    print("=" * 100)
    print("La vista agrupa por `contraparte` y solo muestra contrapartes con flujos.")
    print("• MesaAPI con 0 pero CashFlow.Flujo con datos → falta sync:")
    print("      python -m scripts.api_migrate flujo")
    print(f"• La `contraparte` de los flujos NO es '{args.q}' (vacío u otro nombre) →")
    print("  el flujo no linkea con la contraparte. Hay que corregir el campo")
    print("  `contraparte` en CashFlow.Flujo (que coincida EXACTO con el nombre de la")
    print("  contraparte) y re-sync. Ese campo es el que une operación ↔ contraparte.")


if __name__ == "__main__":
    main()
