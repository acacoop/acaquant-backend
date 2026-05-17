"""diag_cuentas_faltantes.py — detecta cuentas que faltan en snapshots de AuM.

`cuentas_con_error.json` se sobreescribe en cada corrida y `Manager.AumBackfillLog`
solo lo escribe `aum_backfill_historico` — ninguno es confiable para saber qué
quedó afuera del backfill de 2026. La fuente de verdad es la data misma.

Lógica: por cada snapshot objetivo se compara su set de cuentas contra una
REFERENCIA = las cuentas que estaban en el snapshot inmediatamente anterior
Y en el inmediatamente posterior. Si una cuenta estaba antes y después pero
NO en el snapshot del medio, es un "hueco" — casi seguro un backfill que no
la levantó (una cuenta no cierra un mes y reabre al siguiente).

Uso:
  python -m scripts.diag_cuentas_faltantes
  python -m scripts.diag_cuentas_faltantes --meses 2026-01-31,2026-02-28,2026-03-31
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client_read

_DEFAULT_MESES = ["2026-01-31", "2026-02-28", "2026-03-31"]


def _cuentas_en(col, fecha: str) -> set:
    return {c for c in col.distinct("id_cuenta", {"fecha_snapshot": fecha})
            if c is not None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meses", default=",".join(_DEFAULT_MESES),
                    help="CSV de fecha_snapshot a chequear")
    args = ap.parse_args()
    meses = [m.strip() for m in args.meses.split(",") if m.strip()]

    col = get_mongo_client_read()["Valuaciones"]["AuM"]
    todas = sorted(str(f) for f in col.distinct("fecha_snapshot") if f)
    if not todas:
        print("Valuaciones.AuM vacío — nada que comparar.")
        return
    print(f"Snapshots en Valuaciones.AuM: {len(todas)}  "
          f"(rango {todas[0]} … {todas[-1]})\n")

    primero, ultimo = meses[0], meses[-1]
    antes   = [f for f in todas if f < primero]
    despues = [f for f in todas if f > ultimo]
    ref_antes = antes[-1] if antes else None
    ref_desp  = despues[0] if despues else None
    print(f"Referencia ANTES:   {ref_antes}")
    print(f"Referencia DESPUÉS: {ref_desp}\n")

    set_antes = _cuentas_en(col, ref_antes) if ref_antes else set()
    set_desp  = _cuentas_en(col, ref_desp) if ref_desp else set()
    if set_antes and set_desp:
        referencia = set_antes & set_desp
        print(f"Cuentas presentes ANTES y DESPUÉS (deberían estar en el medio): "
              f"{len(referencia)}\n")
    elif set_antes:
        referencia = set_antes
        print(f"⚠ Sin snapshot posterior — referencia = solo ANTES "
              f"({len(referencia)} cuentas), menos preciso (puede incluir "
              f"cuentas que cerraron de verdad).\n")
    else:
        print("⚠ Sin snapshot de referencia anterior — no se puede comparar.")
        return

    for mes in meses:
        presentes = _cuentas_en(col, mes)
        faltan = sorted(referencia - presentes, key=lambda x: str(x))
        print("=" * 66)
        print(f"{mes}: {len(presentes)} cuentas presentes  |  "
              f"{len(faltan)} FALTAN")
        if faltan:
            ids = ",".join(str(x) for x in faltan)
            print(f"  ids: {ids}")
            print(f"  reintento:\n    python -m jobs.aum_backfill {mes} "
                  f"--cuenta {ids} --workers 1 --timeout 360 --retries 5")
        print()


if __name__ == "__main__":
    main()
