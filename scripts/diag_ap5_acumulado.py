"""scripts/diag_ap5_acumulado.py — ¿el acumulado que va al PDF está completo?

READ-ONLY. No escribe nada.

**El problema que mide.** La cámara manda la diferencia de CADA DÍA, no el
arrastre. El acumulado que muestra la vista es::

    arrastre cargado + Σ diferencias posteriores a `fecha`

y si nadie cargó el arrastre, esa parte vale CERO — o sea que el número no es el
acumulado real, es *"lo acumulado desde que nosotros empezamos a guardar"*. En una pantalla operativa eso se avisa con un cartel; en un PDF para
gerencia el cartel no va, así que **hay que saber ANTES si el número cierra**.

Y en un RANKING importa el doble: una cuenta con mucho arrastre viejo y poco
movimiento reciente aparece más abajo de lo que corresponde. El top puede estar
en otro orden y no hay forma de notarlo mirando la pantalla.

Uso:
    python -m scripts.diag_ap5_acumulado
    python -m scripts.diag_ap5_acumulado --fecha 2026-08-21
"""
from __future__ import annotations

import argparse

from api.services.ap5_posiciones import _fecha_valida, acumulado, fechas


def main() -> None:
    ap = argparse.ArgumentParser(description="Cuánto le falta al acumulado de AP5.")
    ap.add_argument("--fecha", help="YYYY-MM-DD (default: el último día con posición)")
    args = ap.parse_args()

    dias = fechas()
    if not dias:
        print("No hay posición guardada todavía (ap5.portfolio vacía).")
        return

    hoy, _ = _fecha_valida(args.fecha)
    filas = acumulado(hoy)
    primer_dia = dias[-1]["fecha"]

    print(f"AP5 · acumulado al {hoy}")
    print(f"Nuestra serie arranca el {primer_dia} ({len(dias)} días guardados).")
    print("Sin arrastre cargado, el acumulado cuenta SOLO desde esa fecha.\n")

    # `cargado` = alguien selló `actualizado`. La fila puede existir con los dos
    # importes en 0 (la sembró el script) y eso NO cuenta como cargado: un cero
    # que nadie escribió se lee igual que uno verificado, y esto se imprime.
    sin = [f for f in filas if not f["cargado"]]
    con = [f for f in filas if f["cargado"]]
    print(f"  con arrastre cargado : {len(con):>4}")
    print(f"  PENDIENTES           : {len(sin):>4}   ← estas van incompletas al PDF")
    print("     (sin fila en ap5.acumulado, o con `actualizado` en NULL)")
    print(f"  total       : {len(filas):>4} (cuenta × moneda)\n")

    if not sin:
        print("✅ Todas tienen el arrastre cargado: el acumulado del PDF es el completo.")
        return

    # Las que más pesan primero: son las que pueden mover el top 10.
    sin.sort(key=lambda f: -abs(f["acumulado"]))
    print("Las 20 sin cargar que más pesan (son las que pueden mover el ranking):\n")
    print(f"  {'cuenta':<10} {'grupo':<16} {'moneda':<12} {'familia':<7} {'acum. parcial':>16}  nombre")
    for f in sin[:20]:
        print(f"  {f['cuenta']:<10} {f['grupo'][:16]:<16} {f['moneda'][:12]:<12} "
              f"{f['familia']:<7} {f['acumulado']:>16,.2f}  {f['nombre'][:44]}")

    print("\nQué hacer con esto:")
    print("  · Crear la grilla con `python -m scripts.sembrar_ap5_acumulado` y cargar")
    print("    `acumulado_pesos` / `acumulado_mtr` en Supabase, o con click en la fila")
    print("    desde la vista. El arrastre sale de la planilla de la mesa.")
    print("  · O, si el informe es 'desde que medimos', decirlo en el título del PDF")
    print(f"    ('Acumulado desde {primer_dia}') — así el número no afirma de más.")


if __name__ == "__main__":
    main()
