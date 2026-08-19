"""scripts/diag_comercial_llamadas.py — ¿el Tablero Comercial pide de más?

POR QUÉ ESTE DIAG
=================
`scripts/diag_peso_operaciones` mostró que el Tablero Comercial consume **10×
más tiempo que toda la vista OPERACIONES junta**: `/comercial/operador` (1.469s
en 7 días) + `/comercial/serie` (1.439s) = ~2.900s, contra ~142s de los siete
tabs de OPERACIONES. Y no es que sean lentos —183ms y 93ms están bien— es que
se llaman **23.500 veces por semana**.

Leyendo `comercial-operaciones-view.tsx` aparece un defecto REAL: el efecto que
pide `/comercial/operador` hace `setSelCuenta(null)`, y `selCuenta` es una
dependencia del efecto que pide `/comercial/serie`. Con un cliente
seleccionado, cambiar de operador dispara la serie DOS veces — una con el
cliente viejo, que se descarta.

Eso se lee en el código. Lo que NO se sabe es cuánto pesa, y el ratio global
(1,95 series por cada operador) **no lo prueba**: `metric` y `selCuenta` disparan
la serie por su cuenta, así que un ratio >1 es esperable sin ningún bug —
alcanza con que el usuario clickee clientes o cambie de métrica.

QUÉ MIDE ESTE SCRIPT
====================
El ratio serie/operador **hora por hora**, que es lo que distingue las dos
explicaciones:

  · Si el ratio es ERRÁTICO (1,2 en una hora, 4 en otra) → es comportamiento de
    usuario: clickea clientes y cambia métricas. No hay nada que arreglar.
  · Si se PEGA a ~2,0 hora tras hora → eso no lo hace una persona. Es el doble
    disparo, y cada hora de uso tira la mitad de las llamadas a la serie.

Es la diferencia entre un bug de 700s/semana y una lectura equivocada de un
promedio. Sin esto, "arreglarlo" sería adivinar.

Read-only. Uso (en el Droplet):
    python -m scripts.diag_comercial_llamadas [dias]     # default 7
"""
from __future__ import annotations

import sys

from api.services._sql import _q

_A = "/api/operaciones/comercial/operador"
_B = "/api/operaciones/comercial/serie"


def main() -> int:
    dias = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 7
    rows = _q(
        "SELECT hora, "
        " SUM(n) FILTER (WHERE endpoint = %(a)s) AS n_op, "
        " SUM(n) FILTER (WHERE endpoint = %(b)s) AS n_serie, "
        " SUM(total_ms) FILTER (WHERE endpoint = %(b)s) AS ms_serie "
        "FROM manager.latencia_endpoints "
        "WHERE hora >= now() - make_interval(days => %(d)s) AND endpoint IN (%(a)s, %(b)s) "
        "GROUP BY hora HAVING SUM(n) FILTER (WHERE endpoint = %(a)s) > 0 "
        "ORDER BY hora DESC",
        {"a": _A, "b": _B, "d": dias},
    )
    if not rows:
        print("Sin telemetría en la ventana.")
        return 1

    print(f"\nRATIO serie/operador POR HORA — últimos {dias} días")
    print("(solo horas con al menos 5 llamadas a /operador: con menos, el ratio es ruido)\n")
    print(f"  {'hora (UTC)':<18} {'operador':>9} {'serie':>7} {'ratio':>7}")
    ratios: list[float] = []
    for r in rows:
        n_op, n_serie = int(r["n_op"] or 0), int(r["n_serie"] or 0)
        if n_op < 5:
            continue
        ratio = n_serie / n_op
        ratios.append(ratio)
        marca = "  ← ~2,0" if 1.85 <= ratio <= 2.15 else ""
        print(f"  {str(r['hora'])[:16]:<18} {n_op:>9} {n_serie:>7} {ratio:>7.2f}{marca}")

    if not ratios:
        print("  (ninguna hora superó las 5 llamadas — sin señal)")
        return 0

    ratios.sort()
    n = len(ratios)
    p50 = ratios[n // 2]
    cerca_de_2 = sum(1 for x in ratios if 1.85 <= x <= 2.15)
    disp = ratios[-1] - ratios[0]

    print(f"\n{'='*62}\nVEREDICTO ({n} horas con uso real)\n{'='*62}")
    print(f"  mediana del ratio      {p50:.2f}")
    print(f"  mínimo / máximo        {ratios[0]:.2f} / {ratios[-1]:.2f}   (dispersión {disp:.2f})")
    print(f"  horas pegadas a ~2,0   {cerca_de_2}/{n}  ({cerca_de_2/n*100:.0f}%)")
    print()
    if cerca_de_2 / n >= 0.6:
        # El desperdicio es lo que sobra por encima de 1 llamada por operador,
        # acotado a lo que el doble disparo puede explicar (nunca más de la mitad).
        ms_total = sum(float(r["ms_serie"] or 0) for r in rows)
        n_serie_total = sum(int(r["n_serie"] or 0) for r in rows)
        avg = ms_total / n_serie_total if n_serie_total else 0
        print("  → EL DOBLE DISPARO ES REAL. Un usuario no produce el mismo ratio")
        print("    hora tras hora; un efecto que se dispara dos veces, sí.")
        print(f"    Techo del desperdicio: ~{n_serie_total/2:.0f} llamadas = "
              f"{n_serie_total/2*avg/1000:.0f}s en {dias} días.")
    elif disp > 1.5:
        print("  → ES COMPORTAMIENTO DE USUARIO. El ratio varía demasiado para ser")
        print("    un efecto que se dispara solo. El doble disparo existe en el")
        print("    código pero no es lo que mueve la aguja: mirar otra cosa.")
    else:
        print("  → NO CONCLUYENTE. El ratio no se pega a 2,0 ni varía como el uso")
        print("    humano. Hace falta instrumentar el front para separarlo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
