"""scripts/diag_ap5_margenes_tabla.py — QUÉ hay en `ap5.margenes`, y por qué la
card da lo que da.

READ-ONLY sobre NUESTRA base (no pega a Postrade).

⚠️ **Por qué existe**: la card de ACTIVO INTEGRADO está dando lo mismo que la de
REQUERIMIENTO. Las dos suman conceptos distintos, así que si dan igual es porque
el concepto que las diferencia (`Inicial A3`) **no está entrando en el filtro** —
y la card no tiene forma de decirlo: dibuja un número correcto para las filas que
sí encontró.

Este diag muestra las TRES cosas que hacen falta para saber cuál de las dos
puntas falla, sin adivinar:

  1. la tabla ENTERA del día (son pocas filas), con su cuenta y su compensación;
  2. dónde vive cada concepto — de qué pares (cuenta, compensación) cuelga;
  3. las dos cards calculadas de CUATRO formas: con el filtro de cuentas y sin
     él. Si sin filtro dan distinto y con filtro dan igual, el problema es el
     filtro; si dan igual en los dos casos, el problema es el concepto.

Uso:
    python -m scripts.diag_ap5_margenes_tabla
    python -m scripts.diag_ap5_margenes_tabla --fecha 2026-08-24
"""
from __future__ import annotations

import argparse

import config
from core.postgres import get_pool


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        if cur.description is None:
            return []
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _total(filas: list[dict]) -> float:
    signo = -1.0 if config.AP5_MARGENES_INVERTIR_SIGNO else 1.0
    return round(sum(float(f["margen"] or 0) for f in filas) * signo, 2)


def main() -> None:
    ap = argparse.ArgumentParser(description="Qué hay en ap5.margenes (read-only).")
    ap.add_argument("--fecha", help="AAAA-MM-DD (default: la última que haya)")
    args = ap.parse_args()

    fecha = args.fecha
    if not fecha:
        r = _q("SELECT max(fecha) AS f FROM ap5.margenes")
        fecha = str(r[0]["f"]) if r and r[0]["f"] else ""
    if not fecha:
        print("ap5.margenes está VACÍA. Corré el job primero.")
        return

    filas = _q("SELECT cuenta, cuenta_compensacion, concepto, moneda, margen, "
               "       primas, inter_temporal, referencias, titular "
               "FROM ap5.margenes WHERE fecha = %(f)s "
               "ORDER BY concepto, cuenta", {"f": fecha})

    print("=" * 88)
    print(f"ap5.margenes — {fecha} — {len(filas)} filas")
    print("=" * 88)
    print(f"  {'concepto':<16} {'cuenta':<10} {'comp.':<10} {'moneda':<8} "
          f"{'margen':>18} {'inter':>14} {'refs':>5}  titular")
    for x in filas:
        print(f"  {x['concepto'][:16]:<16} {x['cuenta']:<10} "
              f"{x['cuenta_compensacion']:<10} {x['moneda']:<8} "
              f"{float(x['margen'] or 0):>18,.2f} "
              f"{float(x['inter_temporal'] or 0):>14,.2f} "
              f"{x['referencias']:>5}  {(x['titular'] or '')[:24]}")

    # ── 2) dónde vive cada concepto ───────────────────────────────────────
    print("\n" + "=" * 88)
    print("DE QUÉ PARES (cuenta, compensación) CUELGA CADA CONCEPTO")
    print("=" * 88)
    pedidos = {(c, k) for c, k in config.AP5_CUENTAS_REQUERIMIENTO}
    porcon: dict[str, set[tuple[str, str]]] = {}
    for x in filas:
        porcon.setdefault(x["concepto"], set()).add(
            (x["cuenta"], x["cuenta_compensacion"]))
    for con, pares in sorted(porcon.items()):
        coinciden = pares & pedidos
        marca = ("  ✔ entra en el filtro" if coinciden
                 else "  ✗ NINGUNO de sus pares está en AP5_CUENTAS_REQUERIMIENTO")
        print(f"\n  {con}  ({len(pares)} par(es)){marca}")
        for c, k in sorted(pares)[:12]:
            aca = " ← pedido" if (c, k) in pedidos else ""
            print(f"      cuenta={c:<10} compensación={k}{aca}")

    print(f"\n  Pares pedidos en config: {sorted(pedidos)}")

    # ── 3) las dos cards, con filtro y sin filtro ─────────────────────────
    print("\n" + "=" * 88)
    print("LAS DOS CARDS, CALCULADAS DE CUATRO FORMAS")
    print("=" * 88)
    combos = [
        ("REQUERIMIENTO", config.AP5_CONCEPTOS_REQUERIMIENTO,
         config.AP5_REQUERIMIENTO_FILTRA_CUENTAS),
        ("ACTIVO INTEGR.", config.AP5_CONCEPTOS_ACTIVO_INTEGRADO,
         config.AP5_ACTIVO_INTEGRADO_FILTRA_CUENTAS),
    ]
    print(f"  {'card':<16} {'conceptos':<26} {'filtra?':<8} "
          f"{'CON filtro':>18} {'SIN filtro':>18}   ← el que USA va marcado")
    for nombre, conceptos, filtra in combos:
        con = [x for x in filas if x["concepto"] in conceptos
               and (x["cuenta"], x["cuenta_compensacion"]) in pedidos]
        sin = [x for x in filas if x["concepto"] in conceptos]
        m_con = " *" if filtra else "  "
        m_sin = "  " if filtra else " *"
        print(f"  {nombre:<16} {'+'.join(conceptos)[:26]:<26} "
              f"{('sí' if filtra else 'NO'):<8} "
              f"{_total(con):>16,.2f}{m_con} {_total(sin):>16,.2f}{m_sin}")
    print("\n  (*) = el criterio que la card usa hoy, según config.py")

    print("\n  Cómo leerlo:")
    print("   · Si las DOS cards dan igual CON filtro y distinto SIN filtro →")
    print("     el concepto existe pero cuelga de otra cuenta: el filtro lo tira.")
    print("   · Si dan igual en las dos columnas → el concepto que las")
    print("     diferencia no está en la tabla con ese nombre exacto.")
    print("   · Los conceptos de la tabla que NO están en ninguna lista de")
    print("     config quedan guardados y no suman — es a propósito.")


if __name__ == "__main__":
    main()
