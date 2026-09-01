"""READ-ONLY. ¿Por qué una grafía del DESGLOSE de INTERBANKING no agarra nada?

Herramienta: diag · Cero escrituras. Corre sobre las fechas que hay en la base.

PARA QUÉ SIRVE
==============

El catálogo del desglose lo edita el back office desde la vista (botón DESGLOSE),
y una grafía que no agarra **no falla**: la columna sigue en cero, el total sigue
dando bien y el movimiento se queda en MOVIMIENTOS RESTANTES. No hay forma de
distinguir «cargué mal el texto» de «hoy no hubo ese impuesto» mirando la
pantalla. Esto lo contesta:

  1. **CATÁLOGO** — los baldes en el orden en que se evalúan (gana el primero) y
     sus grafías con `repr()`. El `repr` es el punto: un espacio de más, un
     `\\xa0` o un acento roto se ven ahí y en ningún otro lado — `contiene` es
     substring literal, no regex.
  2. **DÓNDE VIVE UN TEXTO** — movimiento por movimiento: en qué campo está de
     verdad, qué muestra la pantalla, si es gasto y en qué balde cayó.
  3. **GRAFÍAS QUE NO AGARRAN NI UN MOVIMIENTO** — lo que alguien cargó creyendo
     que resolvía algo. ⚠️ Con pocos días en la base, una grafía puede estar bien
     y no tener a quién agarrar: es una lista para MIRAR, no un veredicto.
  4. **GASTOS EN RESTO** — lo que el desglose no supo clasificar, con los cuatro
     campos que un matcher puede mirar y lo que dibuja la pantalla.

El catálogo, las reglas y la clasificación se IMPORTAN de `api/services/bancos.py`
— no se copian. Si mañana cambian, este diag cambia con ellos.

HISTORIA — el bug que lo motivó (2026-09-01, ya arreglado)
==========================================================

El back office cargó `NOTA DB` en el balde IIBBPERCEP sobre el campo DESCRIPCIÓN,
el movimiento ya estaba contado como gasto, y el desglose lo seguía dejando en
MOVIMIENTOS RESTANTES. La causa: **la columna DESCRIPCIÓN de la pantalla y el
campo `descripcion_banco` de un matcher no eran el mismo dato**. Cuando el banco
no manda su descripción, la vista cae al CONCEPTO y lo dibuja bajo DESCRIPCIÓN;
la regla comparaba contra la columna cruda, o sea contra un string vacío.

Se arregló con `bancos.valor_campo()`, la única puerta por la que las dos mitades
del criterio (¿es gasto? / ¿en qué balde cae?) leen un campo. Este diag quedó
porque la pregunta que contesta —«¿mi grafía agarró algo?»— no la contesta ninguna
pantalla, y se la hace el equipo cada vez que edita el catálogo.

Uso:
    python -m scripts.diag_desglose_texto
    python -m scripts.diag_desglose_texto --texto "NOTA DB"
    python -m scripts.diag_desglose_texto --texto "INTERESES" --todos
"""
from __future__ import annotations

import argparse

from api.services import bancos as B


def _fechas() -> list:
    return [r["fecha"] for r in B._q(
        "SELECT DISTINCT fecha FROM bancos.movimientos ORDER BY fecha")]


def _cuentas() -> dict[int, str]:
    return {r["id"]: f"{r['bank_name']} {r['account_number']} {r['currency']}"
            for r in B._q(
                "SELECT id, bank_name, account_number, currency FROM bancos.cuentas")}


def _pantalla(m: dict) -> str:
    """Lo que la vista dibuja en la columna DESCRIPCIÓN. Ver `_movimiento_publico`."""
    return (m.get("descripcion_banco") or m.get("descripcion_ib") or "").strip()


def _linea(m: dict, ctas: dict, marca: dict, balde: str) -> str:
    origen = marca.get("origen") or "—"
    return (f"    {m['fecha']}  {ctas.get(m['cuenta_id'], m['cuenta_id'])[:34]:<34} "
            f"{m['tipo']} {float(m['importe'] or 0):>13,.2f}  "
            f"gasto={'SI' if marca.get('es_gasto') else 'no'}({origen})  balde={balde}"
            + ("  [IGNORADO]" if m.get("ignorado") else ""))


def _campos(m: dict) -> str:
    return ("      DESCRIPCIÓN (descripcion_banco) = " + repr(m.get("descripcion_banco"))
            + "\n      CONCEPTO    (descripcion_ib)    = " + repr(m.get("descripcion_ib"))
            + "\n      COD OP      (codigo_operacion_ib)    = " + repr(m.get("codigo_operacion_ib"))
            + "\n      COD OP BCO  (codigo_operacion_banco) = " + repr(m.get("codigo_operacion_banco"))
            + "\n      LO QUE MUESTRA LA PANTALLA           = " + repr(_pantalla(m))
            + "\n      LO QUE MATCHEA UNA REGLA DE DESCRIPCIÓN = "
            + repr(B.valor_campo(m, "descripcion_banco")))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--texto", default="NOTA DB", help="el texto a rastrear (default: NOTA DB)")
    ap.add_argument("--todos", action="store_true",
                    help="lista TODOS los gastos en RESTO, no solo los primeros 40")
    args = ap.parse_args()
    aguja = args.texto.strip().casefold()

    baldes = B._baldes()
    reglas = B.listar_reglas()
    ctas = _cuentas()
    fechas = _fechas()

    print("=" * 100)
    print(f"DESGLOSE — rastreo de {args.texto!r}")
    print(f"fechas en la base: {', '.join(str(f) for f in fechas) or '(ninguna)'}")
    print("=" * 100)

    # ── 1. CATÁLOGO ──────────────────────────────────────────────────────────
    print("\n1) CATÁLOGO DE BALDES (en el ORDEN en que se evalúan — gana el primero)\n")
    for b in baldes:
        print(f"  [{b['orden']:>4}] {b['etiqueta']:<24} clave={b['clave']:<14} grupo={b['grupo']}")
        for m in b["matchers"]:
            print(f"           · {B.CAMPOS_REGLA.get(m['campo'], '?!'):<20} "
                  f"{m['operador']:<9} {m['valor']!r}")
        if not b["matchers"]:
            print("           · (SIN MATCHERS — este balde no puede agarrar nada)")

    print(f"\n  reglas de GASTO activas: {sum(1 for r in reglas if r.get('activa', True))}"
          f" de {len(reglas)}")

    # ── recorrido único: clasifica todo una vez ──────────────────────────────
    todos: list[tuple[dict, dict, str]] = []
    for f in fechas:
        movs = B._movs_para_clasificar(f)
        if not movs:
            continue
        marcas = B.clasificar(movs, reglas, B._overrides(f))
        for m in movs:
            m["fecha"] = f
            todos.append((m, marcas[m["mov_hash"]], B.desglosar(m, baldes)))

    print(f"\n  movimientos leídos: {len(todos)}")

    # ── 2. DÓNDE VIVE EL TEXTO ───────────────────────────────────────────────
    print(f"\n\n2) DÓNDE VIVE {args.texto!r} DE VERDAD\n")
    hits = [t for t in todos
            if aguja in " ".join(str(t[0].get(c) or "") for c in (
                "descripcion_banco", "descripcion_ib",
                "codigo_operacion_ib", "codigo_operacion_banco")).casefold()]
    if not hits:
        print(f"  Ningún movimiento de la base contiene {args.texto!r} en ninguno de los")
        print("  cuatro campos que un matcher puede mirar. Con las fechas que hay hoy,")
        print("  la grafía no tiene a quién agarrar (la base retiene pocos días).")
    for m, marca, balde in hits:
        print(_linea(m, ctas, marca, balde))
        print(_campos(m))
        print()

    # ── 3. GRAFÍAS QUE NO AGARRAN NADA ───────────────────────────────────────
    print("\n3) GRAFÍAS QUE NO AGARRAN NI UN MOVIMIENTO (con las fechas de la base)\n")
    muertas = 0
    for b in baldes:
        for mm in b["matchers"]:
            if mm["campo"] not in B.CAMPOS_REGLA:
                print(f"  · {b['etiqueta']:<22} campo INVÁLIDO {mm['campo']!r} — no matchea nunca")
                muertas += 1
                continue
            if not any(B.desglosar(m, [{"clave": "x", "matchers": [mm]}]) == "x"
                       for m, *_ in todos):
                print(f"  · {b['etiqueta']:<22} {mm['campo']:<20} {mm['operador']:<9} "
                      f"{mm['valor']!r}")
                muertas += 1
    if not muertas:
        print("  (ninguna: todas las grafías cargadas agarran algo)")
    else:
        print("\n  ⚠️ Que una grafía no agarre NO prueba que esté mal: puede ser de un banco")
        print("     o de un impuesto que no aparece en estas fechas. Es para mirarla.")

    # ── 4. GASTOS EN RESTO ───────────────────────────────────────────────────
    resto = [t for t in todos
             if t[1]["es_gasto"] and not t[0].get("ignorado") and t[2] == B.RESTO]
    print(f"\n\n4) GASTOS QUE CAEN EN «MOVIMIENTOS RESTANTES»: {len(resto)}\n")
    for m, marca, balde in (resto if args.todos else resto[:40]):
        print(_linea(m, ctas, marca, balde))
        print(_campos(m))
        print()
    if not args.todos and len(resto) > 40:
        print(f"  … y {len(resto) - 40} más (--todos para verlos)")
    print("=" * 100)


if __name__ == "__main__":
    main()
