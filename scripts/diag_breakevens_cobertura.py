"""scripts/diag_breakevens_cobertura.py — ¿por qué un bono NO aparece en BREAKEVENS?

READ-ONLY (no escribe nada). Es la versión de consola de lo que muestra el panel
Manager → TÍTULOS → BREAKEVENS (mitad derecha): las dos leen la MISMA función
(`api.services.breakevens_admin.diagnostico`), así que no se pueden contradecir.

Responde tres preguntas que la vista sola no contesta:

  1. ¿Está COMPLETO?    → por cada bono `tasa_fija` del master, si entra a la
                          matriz y, si no, el MOTIVO exacto: no tiene CER dentro
                          de ±20 días, se lo robó otro par en el dedup, o lo
                          filtró el plazo mínimo / el IPC ya publicado.
  2. ¿Está FRESCO?      → última fila de `BreakevensHistorico` (fecha + updated_at)
                          y cuántos pares quedaron sin número.
  3. ¿Está AL DÍA el master? → vto y emisión más recientes por curva. Si la última
                          Lecap venció hace meses, el problema no es el motor: es
                          que nadie dio de alta las nuevas (el alta es MANUAL).

También lista los pares apagados a mano y los agregados a mano — un par "que no
aparece" puede estar simplemente excluido en Manager.

Uso:
    python -m scripts.diag_breakevens_cobertura
    python -m scripts.diag_breakevens_cobertura --solo-fuera   # solo lo que NO entra
"""
from __future__ import annotations

import argparse


def main() -> None:
    ap = argparse.ArgumentParser(description="Cobertura de la matriz de breakevens")
    ap.add_argument("--solo-fuera", action="store_true",
                    help="lista solo los bonos que NO entran a la matriz")
    args = ap.parse_args()

    from api.services.breakevens_admin import diagnostico
    d = diagnostico()

    print(f"\nHOY: {d['hoy']}   ·   último IPC publicado: {d['ultimo_ipc'] or '--'}")
    print(f"tolerancia ±{d['max_diff_dias']}d entre vtos · plazo mínimo "
          f"{d['min_dias_plazo']}d\n")

    print("=" * 78)
    print("1. MASTER `mercado.curvas` — de acá salen los candidatos")
    print("=" * 78)
    print(f"{'curva':<16} {'n':>4}  {'vto más lejano':<16} {'emisión más nueva':<18}")
    print("-" * 78)
    for curva, m in d["master"].items():
        print(f"{curva:<16} {m['n']:>4}  {(m['vto_max'] or '--'):<16} "
              f"{(m['emision_max'] or '--'):<18}")

    print()
    print("=" * 78)
    print("2. EMPAREJAMIENTO — un Lecap/Boncap por fila, con el motivo si no entra")
    print("=" * 78)
    print(f"{'lecap':<10} {'vto':<12} {'CER':<10} {'diff':>5} {'estado':<10} motivo")
    print("-" * 78)
    for f in d["filas"]:
        if args.solo_fuera and f["estado"] == "PAR":
            continue
        print(f"{f['lecap'] or '?':<10} {(f['vto'] or '--'):<12} "
              f"{(f['cer'] or '--'):<10} "
              f"{(f['diff'] if f['diff'] is not None else '--'):>5} "
              f"{f['estado']:<10} {f['motivo']}")

    print(f"\n  → {d['n_par']} pares vivos de {d['n_lecaps']} bonos tasa_fija en el master.")
    if d["cer_sin_par"]:
        print(f"  → CER sin par: {', '.join(d['cer_sin_par'])}")

    print()
    print("=" * 78)
    print("3. LO QUE VE LA VISTA — última fila de BreakevensHistorico")
    print("=" * 78)
    p = d["publicado"]
    if not p["fecha"]:
        print("  ⚠ No hay ninguna fila. El motor nunca escribió.")
    else:
        print(f"  fecha del doc : {p['fecha']}")
        print(f"  updated_at    : {p['updated_at']}")
        print(f"  pares         : {p['n_pares']}  (con BE calculado: {p['n_con_be']})")
        for s in p["sin_be"]:
            print(f"    {s['lecap']:<10} ↔ {s['cer']:<10} "
                  f"falta: {', '.join(s['falta']) or 'revisar rango del BE'}")

    print(f"\n  Excluidos a mano: {len(d['excluidos'])}")
    for lecap, cer in d["excluidos"]:
        print(f"    {lecap} ↔ {cer}")
    print(f"  Agregados a mano: {len(d['manuales'])}")
    for lecap, cer in d["manuales"]:
        print(f"    {lecap} ↔ {cer}")
    print()


if __name__ == "__main__":
    main()
