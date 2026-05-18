"""diag_partner_cuentas.py — por qué faltan cuentas en el export al proveedor.

`jobs/partner_export.py` exporta a `ACAPortfolio.Cartera` las cuentas de
`config.PARTNER_EXPORT_CUENTAS` leyendo `Valuaciones.AuM`. Se reportó que
101 aparece pero 175 y 463 no.

Para cada id_cuenta de PARTNER_EXPORT_CUENTAS este diag informa:
  - cuántos docs hay en Valuaciones.AuM (matcheando id_cuenta str Y int).
  - el/los tipo(s) en que está guardado id_cuenta (str vs int — relevante
    porque el export filtra por string).
  - en cuántas fechas de snapshot aparece y el rango.
  - el/los nombre(s) de `cuenta`.
  - si la excluye un filtro del AuM (_aum_filters):
      · regla 3 — id_cuenta en CuentasAPI.ContrapartesAPI.
      · regla 4 — nombre matchea una contraparte de CashFlow.Contrapartes.

Con eso se decide: backfill (nunca capturada) vs. exclusión por filtro.

Solo lectura. Uso:  python -m scripts.diag_partner_cuentas
"""
from __future__ import annotations

from config import PARTNER_EXPORT_CUENTAS
from core.mongo import get_mongo_client_read
from jobs._aum_filters import (
    is_excluded,
    load_contrapartes_id_cuentas,
    load_contrapartes_names,
)


def _id_variantes(cid: str) -> list:
    """id_cuenta como string y, si es numérico, también como int."""
    out: list = [cid]
    if cid.lstrip("-").isdigit():
        out.append(int(cid))
    return out


def main() -> None:
    db = get_mongo_client_read()
    aum = db["Valuaciones"]["AuM"]

    contrapartes_ids = load_contrapartes_id_cuentas()
    contrapartes_names = load_contrapartes_names()

    cuentas = [str(c).strip() for c in PARTNER_EXPORT_CUENTAS if str(c).strip()]
    print(f"PARTNER_EXPORT_CUENTAS = {cuentas}")
    print(f"CuentasAPI.ContrapartesAPI: {len(contrapartes_ids)} id_cuenta")
    print("=" * 72)

    for cid in cuentas:
        print(f"\n### Cuenta {cid}")
        q = {"id_cuenta": {"$in": _id_variantes(cid)}}
        total = aum.count_documents(q)
        print(f"  docs en Valuaciones.AuM (id str|int): {total}")

        nombres: list[str] = []
        if total:
            sample = list(aum.find(q, {"_id": 0, "id_cuenta": 1, "cuenta": 1}).limit(200))
            tipos = sorted({type(d["id_cuenta"]).__name__ for d in sample})
            nombres = sorted({d["cuenta"] for d in sample if d.get("cuenta")})
            fechas = sorted(str(f)[:10] for f in aum.distinct("fecha_snapshot", q) if f)
            print(f"  tipo(s) de id_cuenta guardado(s): {tipos}")
            print(f"  fechas de snapshot: {len(fechas)}"
                  + (f"  ({fechas[0]} … {fechas[-1]})" if fechas else ""))
            print(f"  nombre(s) de cuenta: {nombres}")
            if "str" not in tipos:
                print("  ⚠ id_cuenta NO está guardado como string — el export "
                      "filtra por str y por eso no la levanta. Es un bug del export.")

        # Reglas de exclusión.
        en_contrapartes = cid in contrapartes_ids
        print(f"  regla 3 — id en ContrapartesAPI: {en_contrapartes}")
        if nombres:
            excluida = any(
                is_excluded(n, "XXX", cid, contrapartes_ids, contrapartes_names)
                for n in nombres
            )
            print(f"  regla 3+4 — is_excluded() por nombre/id: {excluida}")

        # Veredicto.
        if total == 0 and en_contrapartes:
            print("  → VEREDICTO: la excluye la regla 3 (es contraparte/fondo). "
                  "El backfill NO la traería.")
        elif total == 0:
            print("  → VEREDICTO: no está en AuM y no es contraparte. "
                  "Candidata a backfill (o la excluye la regla 4 por nombre).")
        else:
            print("  → VEREDICTO: SÍ está en AuM. Revisar tipo de id / fechas arriba.")


if __name__ == "__main__":
    main()
