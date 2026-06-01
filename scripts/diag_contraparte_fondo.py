"""diag_contraparte_fondo.py — por qué una contraparte (Fondo) aparece o no en
/operaciones (Contrapartes Fondo).

La lista de Fondos del endpoint `/fondos` (api/routers/operaciones.py::
_fondos_emisores) es una INTERSECCIÓN por NOMBRE EXACTO de:
  (a) CuentasAPI.ContrapartesAPI con grupo == 'Fondos'   ← lo que LEE la API
  (b) TitulosAPI.AssetsAPI con cartera ∈ {FCI, CARTERA FCI} → emisor

Y el dato pasa por una cadena de copias de DOS pasos:
  CashFlow.Contrapartes
    →[api_migrate contrapartes]→ CashFlow.ContrapartesAPI   (intermedia)
    →[api_migrate mover]→        CuentasAPI.ContrapartesAPI  (FINAL, la que lee la API)

Si corriste 'contrapartes' pero NO 'mover', el dato queda en la intermedia y la
API no lo ve. Este diag rastrea por las 4 etapas y te dice dónde se cae.

Read-only. Uso (desde la raíz del repo en el Droplet):
    venv/bin/python -m scripts.diag_contraparte_fondo                 # panorama
    venv/bin/python -m scripts.diag_contraparte_fondo --nombre GALILEO
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client_read


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nombre", default=None,
                    help="substring (case-insensitive) del nombre a rastrear")
    args = ap.parse_args()

    c = get_mongo_client_read()
    src = c["CashFlow"]["Contrapartes"]
    inter = c["CashFlow"]["ContrapartesAPI"]
    final = c["CuentasAPI"]["ContrapartesAPI"]
    assets = c["TitulosAPI"]["AssetsAPI"]

    # Lado (b): emisores de assets FCI.
    fci_emisores = sorted({
        d["emisor"]
        for d in assets.find(
            {"cartera": {"$in": ["FCI", "CARTERA FCI"]}}, {"_id": 0, "emisor": 1}
        )
        if d.get("emisor")
    })
    fci_lower = {e.lower(): e for e in fci_emisores}

    # Lado (a): nombres marcados Fondos en la copia FINAL.
    fondos_final = sorted({
        d["nombre"]
        for d in final.find({"grupo": "Fondos"}, {"_id": 0, "nombre": 1})
        if d.get("nombre")
    })
    interseccion = sorted(set(fondos_final) & set(fci_emisores))

    if not args.nombre:
        print("=" * 90)
        print("PANORAMA — Contrapartes Fondo = intersección por nombre exacto")
        print("=" * 90)
        print(f"(a) Contrapartes con grupo='Fondos' en CuentasAPI.ContrapartesAPI: {len(fondos_final)}")
        print(f"(b) Emisores de assets FCI en TitulosAPI.AssetsAPI:               {len(fci_emisores)}")
        print(f"==> APARECEN en /fondos (intersección): {len(interseccion)}")
        for n in interseccion:
            print(f"      ✓ {n}")
        drop_a = sorted(set(fondos_final) - set(fci_emisores))
        print(f"\nMarcadas 'Fondos' pero SIN asset FCI con ese emisor exacto ({len(drop_a)}) — NO aparecen:")
        for n in drop_a:
            casi = fci_lower.get(n.lower())
            hint = f"  (¡hay emisor FCI '{casi}' — mismatch de may/espacios!)" if casi else ""
            print(f"      ✗ {n}{hint}")
        print("\nTip: corré con --nombre <texto> para rastrear una puntual por las 4 etapas.")
        return

    q = args.nombre.lower()

    def hits(coll, campos):
        out = []
        for d in coll.find({}, {"_id": 0}):
            if any(q in str(d.get(cm) or "").lower() for cm in campos):
                out.append(d)
        return out

    print("=" * 90)
    print(f"RASTREO de contrapartes que matchean '{args.nombre}'")
    print("=" * 90)

    s = hits(src, ["contraparte", "denominacion"])
    print(f"\n[1] CashFlow.Contrapartes (FUENTE) — {len(s)} match:")
    for d in s:
        print(f"    contraparte={d.get('contraparte')!r}  segmento={d.get('segmento')!r}  "
              f"cuenta={d.get('cuenta')!r}  denominacion={d.get('denominacion')!r}")

    i = hits(inter, ["nombre"])
    print(f"\n[2] CashFlow.ContrapartesAPI (intermedia — escribe 'api_migrate contrapartes') — {len(i)} match:")
    for d in i:
        print(f"    nombre={d.get('nombre')!r}  grupo={d.get('grupo')!r}")

    f = hits(final, ["nombre"])
    print(f"\n[3] CuentasAPI.ContrapartesAPI (FINAL — la que LEE la API; escribe 'api_migrate mover') — {len(f)} match:")
    for d in f:
        print(f"    nombre={d.get('nombre')!r}  grupo={d.get('grupo')!r}")

    af = [e for e in fci_emisores if q in e.lower()]
    print(f"\n[4] TitulosAPI.AssetsAPI — emisor FCI que matchea — {len(af)}:")
    for e in af:
        print(f"    emisor FCI: {e!r}")

    print("\n=== VEREDICTO ===")
    if not s:
        print("• No está ni en la FUENTE (CashFlow.Contrapartes). ¿Lo agregaste en otra colección?")
    if i and not f:
        print("• Está en la intermedia pero NO en la final → FALTA correr: "
              "python -m scripts.api_migrate mover")
    fondos_f = [d for d in f if d.get("grupo") == "Fondos"]
    if f and not fondos_f:
        print("• Está en la copia final pero su grupo NO es 'Fondos' "
              "(en la fuente, el campo `segmento` debe ser exactamente 'Fondos').")
    for d in fondos_f:
        nom = d.get("nombre") or ""
        if nom in fci_emisores:
            print(f"• ✓ '{nom}' está OK: grupo Fondos + emisor FCI exacto → DEBERÍA aparecer.")
        elif nom.lower() in fci_lower:
            print(f"• ✗ '{nom}': grupo Fondos OK, pero el emisor FCI es '{fci_lower[nom.lower()]}' "
                  "→ MISMATCH de mayúsculas/espacios. Igualá los nombres.")
        else:
            print(f"• ✗ '{nom}': grupo Fondos OK, pero NO hay asset FCI con ese emisor "
                  "→ no entra a la intersección (¿falta cargar el asset FCI o el nombre no coincide?).")


if __name__ == "__main__":
    main()
