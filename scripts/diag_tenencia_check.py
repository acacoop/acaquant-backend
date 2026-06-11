"""diag_tenencia_check.py — READ-ONLY. Por qué la vista Tenencia Valorizada sigue en 0.

El backfill escribió 51 docs (cliente rw / primario). La API lee con
get_mongo_client_read (SECONDARY_PREFERRED). Este diag aísla dónde está el 0:

  - cuenta docs en TenenciaHD por el cliente rw (primario) Y por el read (secundario)
    → si rw=51 y read=0 es REPLICACIÓN; si ambos 51 el dato está y se lee bien.
  - llama a la MISMA función del endpoint (tenencia_dias) → si devuelve 51, el
    backend está 100% ok y el 0 es del proceso API viejo (no recargó) o del cache;
    si devuelve 0 con read=51, es bug del service.

Uso:
    python -m scripts.diag_tenencia_check
"""
from __future__ import annotations

from core.mongo import get_mongo_client, get_mongo_client_read


def main() -> None:
    print("=== Diag Tenencia Valorizada — por que sigue en 0 ===\n")

    rw = get_mongo_client()["Valuaciones"]["TenenciaHD"]
    ro = get_mongo_client_read()["Valuaciones"]["TenenciaHD"]
    n_rw = rw.count_documents({})
    n_ro = ro.count_documents({})
    print(f"  TenenciaHD docs  — rw/primario: {n_rw}   read/secundario: {n_ro}")
    d = ro.find_one({}, sort=[("fecha_snapshot", -1)])
    if d:
        print(f"  ultimo doc (read): fecha={d.get('fecha_snapshot')} aum={d.get('aum')} total={d.get('total')}")
    print()

    # Misma funcion que sirve el endpoint /api/back-office/tenencia-hd
    from api.services.tenencia_hd import tenencia_dias
    res = tenencia_dias()
    dias = res.get("dias", [])
    print(f"  tenencia_dias(): {len(dias)} dias · ultima_fecha={res.get('ultima_fecha')}")
    if dias:
        print(f"  primera fila: {dias[0]}")
    print()

    print("=== Lectura ===")
    if n_rw and not n_ro:
        print("  -> rw tiene docs pero el secundario NO: REPLICACION. Esperar unos seg y reintentar.")
    elif n_ro and not dias:
        print("  -> el secundario tiene docs pero tenencia_dias() devuelve 0: BUG del service.")
    elif dias:
        print("  -> backend OK (data + lectura + service). El 0 en la web es del PROCESO API:")
        print("     no recargo el codigo nuevo. Reintentar: systemctl restart api.service")
        print("     y confirmar con: systemctl status api.service (ver 'Active: since' reciente).")
    else:
        print("  -> no hay docs por ningun lado: el backfill no escribio donde se lee.")


if __name__ == "__main__":
    main()
