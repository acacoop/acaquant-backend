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


def _columna_vs_moneda(fecha: str) -> None:
    """¿El arrastre está en la COLUMNA que la cuenta usa?

    ⚠️ **El modo de falla más silencioso de esta carga.** `ap5.acumulado` tiene
    DOS columnas (`acumulado_pesos` / `acumulado_mtr`) y la vista elige una
    según la moneda de liquidación de cada posición::

        settlement_currency = 'Pesos'      → acumulado_pesos
        settlement_currency = 'Dólar MtR'  → acumulado_mtr

    Si el archivo traía el número en `mtr` y las posiciones de esa cuenta
    liquidan en `Pesos`, el arrastre que se lee es `acumulado_pesos` = 0. La
    carga funcionó, la fila existe, `actualizado` está sellado — **y la pantalla
    no cambia**. No hay error en ningún lado; simplemente se miró la otra
    columna.

    Esto lo lista: por cada cuenta, qué monedas opera de verdad y en qué columna
    quedó la plata.
    """
    from core.postgres import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.account,
                   COALESCE(NULLIF(c.name,''), c.denominacion, a.account) AS nombre,
                   a.acumulado_pesos, a.acumulado_mtr,
                   array_agg(DISTINCT p.settlement_currency) AS monedas
            FROM ap5.acumulado a
            LEFT JOIN ap5.cuentas c  ON c.account = a.account
            LEFT JOIN ap5.portfolio p ON p.account = a.account
                                     AND p.business_date <= %(f)s
            WHERE a.actualizado IS NOT NULL
            GROUP BY 1, 2, 3, 4
            ORDER BY 2
            """,
            {"f": fecha},
        )
        filas = cur.fetchall()

    print("\n" + "=" * 86)
    print("¿EL ARRASTRE ESTÁ EN LA COLUMNA QUE LA CUENTA USA?")
    print("=" * 86)
    if not filas:
        print("  Ninguna cuenta tiene arrastre cargado todavía.")
        return

    perdidas, sin_posicion, ok = [], [], 0
    for account, nombre, pesos, mtr, monedas in filas:
        mon = {m for m in (monedas or []) if m}
        pesos, mtr = float(pesos or 0), float(mtr or 0)
        if not mon:
            sin_posicion.append((account, nombre, pesos, mtr))
            continue
        # La plata que quedó en una columna que esta cuenta NO lee.
        huerfano = 0.0
        if pesos and "Pesos" not in mon:
            huerfano += pesos
        if mtr and "Dólar MtR" not in mon:
            huerfano += mtr
        if huerfano:
            perdidas.append((account, nombre, pesos, mtr, sorted(mon)))
        else:
            ok += 1

    print(f"  {ok} cuenta(s) con el arrastre en la columna correcta")
    print(f"  {len(perdidas)} con plata en una columna que NO leen  ← ACÁ ESTÁ EL PROBLEMA")
    print(f"  {len(sin_posicion)} con arrastre y SIN posición hasta {fecha}")

    if perdidas:
        print(f"\n  {'cuenta':<10} {'pesos':>16} {'MtR':>14}  opera en          nombre")
        for a, n, pe, mt, mon in perdidas[:30]:
            print(f"  {a:<10} {pe:>16,.2f} {mt:>14,.2f}  {','.join(mon)[:16]:<16}  "
                  f"{str(n)[:28]}")
        if len(perdidas) > 30:
            print(f"  … y {len(perdidas)-30} más")
        print("\n  → El archivo puso el número en la otra columna. Se arregla")
        print("    dando vuelta `mtr` y `pesos` en el Excel para ESAS filas y")
        print("    re-importando: el UPSERT pisa el valor viejo, no acumula.")

    if sin_posicion:
        print(f"\n  Con arrastre y sin posición hasta {fecha} (no se ven en la vista,")
        print("  no es un error — es una cuenta que dejó de operar):")
        for a, n, pe, mt in sin_posicion[:15]:
            print(f"    {a:<10} {pe:>16,.2f} {mt:>14,.2f}  {str(n)[:34]}")
        if len(sin_posicion) > 15:
            print(f"    … y {len(sin_posicion)-15} más")


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
        _columna_vs_moneda(hoy)
        return

    # Las que más pesan primero: son las que pueden mover el top 10.
    sin.sort(key=lambda f: -abs(f["acumulado"]))
    print("Las 20 sin cargar que más pesan (son las que pueden mover el ranking):\n")
    print(f"  {'cuenta':<10} {'grupo':<16} {'moneda':<12} {'familia':<7} {'acum. parcial':>16}  nombre")
    for f in sin[:20]:
        print(f"  {f['cuenta']:<10} {f['grupo'][:16]:<16} {f['moneda'][:12]:<12} "
              f"{f['familia']:<7} {f['acumulado']:>16,.2f}  {f['nombre'][:44]}")

    _columna_vs_moneda(hoy)

    print("\nQué hacer con esto:")
    print("  · Crear la grilla con `python -m scripts.sembrar_ap5_acumulado` y cargar")
    print("    `acumulado_pesos` / `acumulado_mtr` en Supabase, o con click en la fila")
    print("    desde la vista. El arrastre sale de la planilla de la mesa.")
    print("  · O, si el informe es 'desde que medimos', decirlo en el título del PDF")
    print(f"    ('Acumulado desde {primer_dia}') — así el número no afirma de más.")


if __name__ == "__main__":
    main()
