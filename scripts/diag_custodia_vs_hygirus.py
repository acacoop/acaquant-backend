"""¿CIERRA EL CRUCE CVSA vs HYGIRUS (T0)? — read-only, no escribe nada.

Antes de dibujar la conciliación en pantalla hay que saber si el cruce cierra al
90% o al 30%: son dos pantallas distintas. Y hay SIETE cosas que no se pueden
asumir; este script las mide todas.

  1. GRANULARIDAD — CVSA tiene una fila por (cuenta, papel, ESTADO); Hygirus una
     por (cuenta, papel). Hay que sumar CVSA antes de comparar.
  2. EL FILTRO `aum='si'` — la vista de Carteras lo aplica. Con él se pierden
     filas que CVSA sí trae; sin él, el total no coincide con lo que el user ve
     en Carteras. Se cuentan las dos.
  3. LAS FILAS SIN `unidad` — no se pueden parear. Van aparte: contarlas como
     diferencia seria mentir.
  4. LAS FECHAS — si las dos fotos no son del mismo día, todo el cruce es ruido.
  5. EL UNIVERSO DE CUENTAS — CVSA trajo 488 y `tenencia_live` tiene ~1.800.
  6. EL SIGNO — el repo documenta que Aunesa manda tenencias invertidas en otro
     endpoint (`control_saldos`). Si TODAS las diferencias son exactamente ×-1,
     es eso y no un descalce.
  7. QUÉ ESTADOS SUMAN — ¿un EMBARGO está adentro de la tenencia de Hygirus?
     Se mide comparando el cruce con y sin lo trabado.

Uso:
    python -m scripts.diag_custodia_vs_hygirus
    python -m scripts.diag_custodia_vs_hygirus --cuenta 805   # una sola
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from core.postgres import get_pool

DISPONIBLE = "AVAILABLE"
TOLERANCIA = 0.01          # dos nominales que difieren en centavos no son un descalce


def _q(sql: str, p: tuple = ()) -> list[tuple]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, p)
        return cur.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(description="Cruce CVSA vs Hygirus T0 (read-only).")
    ap.add_argument("--cuenta", help="limitar a una cuenta (ej. 805)")
    args = ap.parse_args()

    print("=" * 78)
    print("CVSA (Caja de Valores) vs HYGIRUS T0 (tenencia_live)")
    print("=" * 78)

    # ── 4. LAS FECHAS. Si no son el mismo día, el resto no significa nada. ────
    f_cvsa = _q("SELECT max(fecha) FROM portafolio.custodia_cvsa")[0][0]
    f_hyg = _q("SELECT max(fecha) FROM portafolio.tenencia_live")[0][0]
    print(f"\n[4] FECHAS      CVSA: {f_cvsa}   ·   Hygirus: {f_hyg}")
    if f_cvsa is None or f_hyg is None:
        print("    Falta una de las dos fotos. No hay nada que cruzar.")
        return 1
    if f_cvsa != f_hyg:
        print("    ⚠️ SON DE DÍAS DISTINTOS. Todo lo de abajo mezcla dos momentos:")
        print("       leelo como una referencia, no como un descalce.")

    filtro_cta = " AND id_cuenta = %s" if args.cuenta else ""
    p_cta: tuple = (args.cuenta,) if args.cuenta else ()

    # ── 1. CVSA sumado por (cuenta, unidad). Y aparte, solo lo disponible. ────
    cvsa_total: dict[tuple[str, str], float] = defaultdict(float)
    cvsa_disp: dict[tuple[str, str], float] = defaultdict(float)
    sin_unidad = 0
    for cta, uni, est, cant in _q(
            "SELECT id_cuenta, unidad, sub_balance_type, cantidad "
            "FROM portafolio.custodia_cvsa WHERE fecha = %s" + filtro_cta,
            (f_cvsa, *p_cta)):
        if uni is None:
            sin_unidad += 1          # 3. no se puede parear
            continue
        cvsa_total[(cta, uni)] += float(cant or 0)
        if est == DISPONIBLE:
            cvsa_disp[(cta, uni)] += float(cant or 0)

    # ── 2. Hygirus T0, con y sin el filtro de AuM. ───────────────────────────
    hyg_todo: dict[tuple[str, str], float] = {}
    hyg_aum: dict[tuple[str, str], float] = {}
    for cta, uni, cant, aum in _q(
            "SELECT id_cuenta, unidad, cantidad, aum FROM portafolio.tenencia_live "
            "WHERE fecha = %s AND horizonte = 't0'" + filtro_cta, (f_hyg, *p_cta)):
        hyg_todo[(cta, uni)] = float(cant or 0)
        if aum == "si":
            hyg_aum[(cta, uni)] = float(cant or 0)

    print(f"\n[1] FILAS       CVSA agrupado: {len(cvsa_total):>6}   "
          f"(de {len(cvsa_total) + sin_unidad} pares; {sin_unidad} sin instrumento)")
    print(f"[3] SIN UNIDAD  {sin_unidad} filas de CVSA no se pueden parear (quedan afuera)")
    print(f"[2] HYGIRUS T0  todo: {len(hyg_todo):>6}   ·   solo aum='si': {len(hyg_aum):>6}")

    # ── 5. EL UNIVERSO DE CUENTAS ────────────────────────────────────────────
    ctas_cvsa = {k[0] for k in cvsa_total}
    ctas_hyg = {k[0] for k in hyg_todo}
    print(f"\n[5] CUENTAS     CVSA: {len(ctas_cvsa)}   ·   Hygirus T0: {len(ctas_hyg)}   ·   "
          f"en ambas: {len(ctas_cvsa & ctas_hyg)}")
    print(f"    solo en CVSA: {len(ctas_cvsa - ctas_hyg)}   ·   "
          f"solo en Hygirus: {len(ctas_hyg - ctas_cvsa)}")

    # ── EL CRUCE, en sus cuatro variantes ────────────────────────────────────
    def cruzar(cvsa: dict, hyg: dict, etiqueta: str) -> dict:
        claves = set(cvsa) | set(hyg)
        iguales = distintas = solo_c = solo_h = 0
        invertidas = 0
        ejemplos: list[tuple] = []
        for k in claves:
            a, b = cvsa.get(k), hyg.get(k)
            if a is None:
                solo_h += 1
            elif b is None:
                solo_c += 1
            elif abs(a - b) <= TOLERANCIA:
                iguales += 1
            else:
                distintas += 1
                # 6. EL SIGNO: si a == -b, no es un descalce, es la convención.
                if abs(a + b) <= TOLERANCIA:
                    invertidas += 1
                elif len(ejemplos) < 8:
                    ejemplos.append((k[0], k[1], a, b, a - b))
        total = len(claves) or 1
        print(f"\n--- {etiqueta} ---")
        print(f"    coinciden      {iguales:>6}  ({100 * iguales / total:.1f}%)")
        print(f"    difieren       {distintas:>6}  ({100 * distintas / total:.1f}%)")
        print(f"    solo en CVSA   {solo_c:>6}")
        print(f"    solo en Hygirus{solo_h:>6}   <-- el caso mas grave")
        if invertidas:
            print(f"    ⚠️ [6] de las que difieren, {invertidas} son EXACTAMENTE el mismo")
            print("       numero con el signo cambiado: eso es la convencion de Aunesa,")
            print("       no un descalce. Hay que corregir el signo antes de comparar.")
        for cta, uni, a, b, d in ejemplos:
            print(f"       {cta:>6} {uni[:38]:<38} cvsa={a:>14,.2f} hyg={b:>14,.2f} dif={d:>12,.2f}")
        return {"iguales": iguales, "distintas": distintas, "total": total}

    r_todo = cruzar(cvsa_total, hyg_todo, "[2] CVSA total (todos los estados) vs HYGIRUS todo")
    cruzar(cvsa_total, hyg_aum, "[2] CVSA total vs HYGIRUS solo aum='si'")
    r_disp = cruzar(cvsa_disp, hyg_todo, "[7] CVSA solo AVAILABLE vs HYGIRUS todo")

    print("\n" + "=" * 78)
    print("QUE MIRAR")
    print("=" * 78)
    print("· El % de 'coinciden' mas alto dice CUAL es la comparacion correcta.")
    print("  Si gana 'solo AVAILABLE', Hygirus NO cuenta lo trabado y hay que")
    print("  excluirlo; si gana 'CVSA total', si lo cuenta.")
    print("· Si aparece el aviso del SIGNO, hay que corregirlo antes de dibujar nada.")
    print("· 'solo en Hygirus' es el caso grave: nosotros decimos tener algo que la")
    print("  Caja no registra.")
    if r_todo["iguales"] < r_disp["iguales"]:
        print("\n>> Por ahora gana comparar SOLO AVAILABLE.")
    else:
        print("\n>> Por ahora gana comparar el TOTAL de CVSA.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
