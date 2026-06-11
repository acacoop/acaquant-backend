"""diag_grupos_scope.py — READ-ONLY. Por qué un usuario ve MENOS carteras de las esperadas.

Regla (core/grupos.py::cuentas_visibles): el acceso a carteras NO depende del ROL,
solo de los GRUPOS. admin → todo; sin grupo → todo; en ≥1 grupo → SOLO la unión de
las id_cuentas de sus grupos. Si alguien ve menos, es porque está en un grupo acotado.

Muestra:
  1) Todos los grupos (Manager.Grupos): nombre, nº usuarios, nº cuentas.
  2) Por usuario agrupado: rol + cuántas carteras ve (unión) vs el universo total.
  3) (opcional --email) el detalle de un usuario: sus grupos y qué cuentas le faltan.

Lee el MASTER en Mongo (Manager.Grupos). Si la app lee de SQL (AUTH_SQL=1) y el sync
viene atrasado, lo que ve el usuario puede diferir de esto → se avisa al final.

Uso:
    python -m scripts.diag_grupos_scope
    python -m scripts.diag_grupos_scope --email asistente@acaquant.com
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client_read


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", type=str, default=None, help="detalle de un usuario puntual")
    args = ap.parse_args()

    cli = get_mongo_client_read()
    grupos = list(cli["Manager"]["Grupos"].find({}, {"_id": 0, "nombre": 1, "emails": 1, "id_cuentas": 1}))

    # Universo de carteras = id_cuenta distintos en el último snapshot de AuM.
    aum = cli["Valuaciones"]["AuM"]
    snap = aum.find_one({}, {"_id": 0, "fecha_snapshot": 1}, sort=[("fecha_snapshot", -1)])
    universo: set[str] = set()
    if snap:
        universo = {str(x) for x in aum.distinct("id_cuenta", {"fecha_snapshot": snap["fecha_snapshot"]})}
    n_uni = len(universo) or 0

    print("=== Diag grupos / scope de carteras ===")
    print(f"    universo de carteras (AuM último snapshot {snap.get('fecha_snapshot') if snap else '—'}): {n_uni}\n")

    # ── 1) Grupos ──────────────────────────────────────────────────────────────
    print("── 1) GRUPOS (Manager.Grupos) ──\n")
    if not grupos:
        print("    ⚠️ NO hay grupos cargados → TODOS los usuarios ven TODAS las carteras.")
        print("       (Si igual alguien ve menos, no es por grupos — avisar para revisar otra cosa.)\n")
    else:
        print(f"    {'grupo':<28}{'usuarios':>9}{'cuentas':>9}")
        for g in sorted(grupos, key=lambda x: x.get("nombre") or ""):
            print(f"    {str(g.get('nombre') or '—'):<28}"
                  f"{len(g.get('emails') or []):>9}{len(g.get('id_cuentas') or []):>9}")
        print()

    # ── 2) Por usuario agrupado: cuántas carteras ve ───────────────────────────
    # email → unión de id_cuentas de TODOS sus grupos.
    por_email: dict[str, set[str]] = {}
    for g in grupos:
        cuentas = {str(c) for c in (g.get("id_cuentas") or [])}
        for e in g.get("emails") or []:
            por_email.setdefault(str(e).lower().strip(), set()).update(cuentas)

    roles = {str(u.get("email", "")).lower().strip(): u.get("role")
             for u in cli["Manager"]["Users"].find({}, {"_id": 0, "email": 1, "role": 1})}

    if por_email:
        print("── 2) USUARIOS EN GRUPOS (ven SOLO estas carteras) ──\n")
        print(f"    {'email':<38}{'rol':<10}{'ve':>8}{'de':>6}")
        for e, cuentas in sorted(por_email.items(), key=lambda kv: len(kv[1])):
            rol = roles.get(e) or "?"
            nota = "  ← admin: el grupo NO lo limita, ve todo" if rol == "admin" else ""
            print(f"    {e:<38}{rol:<10}{len(cuentas):>8}{n_uni:>6}{nota}")
        print()
        print("    Los que NO aparecen acá (y no son admin) NO están en ningún grupo → ven TODO.\n")

    # ── 3) Detalle de un usuario ───────────────────────────────────────────────
    if args.email:
        e = args.email.lower().strip()
        print(f"── 3) DETALLE: {e} ──\n")
        rol = roles.get(e) or "?"
        sus_grupos = [g for g in grupos if e in {str(x).lower().strip() for x in (g.get("emails") or [])}]
        if rol == "admin":
            print("    Es ADMIN → ve TODAS las carteras, sin importar grupos.")
        elif not sus_grupos:
            print("    NO está en ningún grupo → ve TODAS las carteras.")
            print("    Si dice que ve menos, el problema NO son los grupos (revisar rol/módulo o front).")
        else:
            ve = por_email.get(e, set())
            faltan = universo - ve
            print(f"    rol={rol} · en {len(sus_grupos)} grupo(s): {[g.get('nombre') for g in sus_grupos]}")
            print(f"    ve {len(ve)} de {n_uni} carteras → le faltan {len(faltan)}")
            if faltan:
                print(f"    ejemplos de carteras que NO ve: {sorted(faltan)[:15]}")
        print()

    print("=== Lectura ===")
    print("  - El rol (sales/trader/asistente) NO limita carteras; SOLO el grupo.")
    print("  - Si los asistentes ven menos, es porque están en grupos acotados (paso 2).")
    print("  - Para que vean todo: sacarlos del grupo, o ampliar id_cuentas del grupo en /manager → GRUPOS.")
    print("  - Nota: la app puede leer grupos de SQL (AUTH_SQL=1); si el sync viene atrasado, lo que")
    print("    ve el usuario puede ser aún más viejo que este master Mongo.")


if __name__ == "__main__":
    main()
