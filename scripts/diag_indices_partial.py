"""scripts/diag_indices_partial.py — busca OTROS índices partial que puedan causar
COLLSCAN como el de boleto (incidente 2026-06-04, ver project_incident_boleto_index).

Un índice PARTIAL no se usa salvo que la query incluya su `partialFilterExpression`.
Si hay un partial sobre un campo que se consulta por igualdad SIN ese filtro → COLLSCAN
silencioso (exactamente lo que pasó con uq_boleto). Este diag lista TODOS los índices
partial de las bases grandes con su filtro, para revisarlos antes de que exploten.

Los más peligrosos: UNIQUE partial sobre un campo de ID (boleto, comprobante, etc.)
que la app consulta/upsertea por igualdad.

Read-only. Correr:
    python -m scripts.diag_indices_partial
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read

_DBS = ("CashFlow", "Valuaciones", "Trading", "Clientes", "Manager", "CuentasAPI")


def main() -> int:
    client = get_mongo_client_read()
    print("Buscando índices PARTIAL (posibles COLLSCAN ocultos como el de hoy)…\n")
    total = 0
    sospechosos = 0
    for dbname in _DBS:
        try:
            colls = client[dbname].list_collection_names()
        except Exception as e:
            print(f"  {dbname}: no accesible ({e})")
            continue
        for cname in sorted(colls):
            try:
                indices = list(client[dbname][cname].list_indexes())
            except Exception:
                continue
            for ix in indices:
                d = dict(ix)
                pfe = d.get("partialFilterExpression")
                if not pfe:
                    continue
                total += 1
                es_uniq = bool(d.get("unique"))
                if es_uniq:
                    sospechosos += 1
                n = client[dbname][cname].estimated_document_count()
                marca = "🔴 UNIQUE partial (revisar SÍ o SÍ)" if es_uniq else "partial"
                print(f"  {dbname}.{cname}  ({n:,} docs)")
                print(f"    {d.get('name')}  key={list(d.get('key', {}).items())}  [{marca}]")
                print(f"    filtro: {pfe}\n")

    print(f"→ {total} índices partial · {sospechosos} UNIQUE partial (los riesgosos).")
    if sospechosos:
        print("  Para cada UNIQUE partial sobre un campo de ID: si la app lo consulta por "
              "igualdad sin el filtro → COLLSCAN. Mismo fix que hoy (recrear plano).")
    else:
        print("  ✓ No quedan UNIQUE partial — el de boleto era el único de ese tipo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
