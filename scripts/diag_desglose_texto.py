"""READ-ONLY. ¿Por qué un texto que SE VE en la pantalla no lo agarra ningún balde?

Herramienta: diag · Cero escrituras. Corre sobre las fechas que hay en la base.

EL PUNTO
========

Caso que lo motivó (2026-09-01): el back office agregó al balde **IIBBPERCEP** la
grafía `NOTA DB` sobre el campo **DESCRIPCIÓN**, el movimiento YA estaba contado
como gasto… y el desglose lo sigue dejando en MOVIMIENTOS RESTANTES.

La sospecha, que este script confirma o descarta con datos reales, es que
**la columna DESCRIPCIÓN de la pantalla y el campo `descripcion_banco` de un
matcher NO son el mismo dato**:

    api/services/bancos.py::_movimiento_publico
        "descripcion": (descripcion_banco  OR  descripcion_ib  OR  "")
    api/services/bancos.py::CAMPOS_REGLA
        "descripcion_banco" -> columna descripcion_banco, a secas

O sea: cuando el banco NO manda `code_description_bank`, la pantalla **cae en el
concepto** y lo dibuja bajo DESCRIPCIÓN. El que mira la fila lee `NOTA DB` ahí,
escribe la grafía sobre DESCRIPCIÓN (que además es el campo que viene elegido por
defecto en el ABM) y el motor la compara contra una columna VACÍA. No falla nada:
el movimiento se queda en RESTO y el total sigue dando bien.

QUÉ IMPRIME
===========

  1. **CATÁLOGO** — los baldes y sus matchers con `repr()` del valor. El `repr`
     es el punto: un espacio de más, un `\\xa0` o un acento roto se ven acá y en
     ningún otro lado (`contiene` es substring literal, no regex).
  2. **EL TEXTO** — dónde vive de verdad, movimiento por movimiento: en qué campo
     está, qué muestra la pantalla, si es gasto y en qué balde cayó.
  3. **MATCHERS QUE NUNCA AGARRAN NADA** — grafías cargadas que no matchean un
     solo movimiento de la base. Es la lista de lo que alguien cargó creyendo que
     resolvía algo.
  4. **GASTOS EN RESTO** — todo lo que el desglose no supo clasificar, con el
     `repr` de los cuatro campos que un matcher puede mirar.
  5. **IMPACTO DEL ARREGLO PROPUESTO** — si un matcher de DESCRIPCIÓN leyera lo
     MISMO que muestra la pantalla (`descripcion_banco` y, si está vacío, el
     concepto), cuántos movimientos cambiarían de balde y cuántos cambiarían de
     ser/no ser gasto. Ese segundo número es el que decide si el arreglo toca
     plata o solo la presentación.

El catálogo, las reglas y la clasificación se IMPORTAN de `api/services/bancos.py`
— no se copian. Lo único propio es la simulación del punto 5, que está aparte a
propósito: es la propuesta, no lo que corre hoy.

Uso:
    python -m scripts.diag_desglose_texto
    python -m scripts.diag_desglose_texto --texto "NOTA DB"
    python -m scripts.diag_desglose_texto --texto "IIBB CABA" --todos
"""
from __future__ import annotations

import argparse

from api.services import bancos as B

# ── La PROPUESTA, simulada. No toca el módulo de producción. ─────────────────
#
# Un matcher de DESCRIPCIÓN pasaría a leer lo mismo que dibuja la pantalla: el
# texto del banco y, SOLO si viene vacío, el concepto. Es estrictamente aditivo —
# donde `descripcion_banco` tiene contenido, se comporta idéntico a hoy.
_FALLBACK = {"descripcion_banco": ("descripcion_banco", "descripcion_ib")}


def _valor_propuesto(mov: dict, campo: str) -> str:
    for col in _FALLBACK.get(campo, (B.CAMPOS_REGLA.get(campo),)):
        v = str(mov.get(col) or "").strip()
        if v:
            return v
    return ""


def _cumple(mov: dict, campo: str, operador: str, valor: str) -> bool:
    dato = _valor_propuesto(mov, campo).casefold()
    v = str(valor or "").strip().casefold()
    if not dato or not v:
        return False
    return dato == v if operador == "igual" else v in dato


def _desglosar_propuesto(mov: dict, baldes: list[dict]) -> str:
    for balde in baldes:
        for m in balde.get("matchers") or []:
            if m["campo"] in B.CAMPOS_REGLA and _cumple(mov, m["campo"], m["operador"], m["valor"]):
                return balde["clave"]
    return B.RESTO


def _es_gasto_propuesto(mov: dict, reglas: list[dict]) -> bool:
    return any(_cumple(mov, str(r.get("campo") or ""), r.get("operador"), r.get("valor"))
               for r in reglas if r.get("activa", True)
               and str(r.get("campo") or "") in B.CAMPOS_REGLA)


# ── Lectura ─────────────────────────────────────────────────────────────────

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
            + "\n      LO QUE MUESTRA LA PANTALLA           = " + repr(_pantalla(m)))


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
    todos: list[tuple[dict, dict, str, str, bool]] = []   # mov, marca, balde_hoy, balde_prop, gasto_prop
    for f in fechas:
        movs = B._movs_para_clasificar(f)
        if not movs:
            continue
        marcas = B.clasificar(movs, reglas, B._overrides(f))
        for m in movs:
            m["fecha"] = f
            marca = marcas[m["mov_hash"]]
            todos.append((m, marca, B.desglosar(m, baldes),
                          _desglosar_propuesto(m, baldes), _es_gasto_propuesto(m, reglas)))

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
    for m, marca, balde, _bp, _gp in hits:
        print(_linea(m, ctas, marca, balde))
        print(_campos(m))
        en_banco = aguja in str(m.get("descripcion_banco") or "").casefold()
        en_ib = aguja in str(m.get("descripcion_ib") or "").casefold()
        if not en_banco and en_ib:
            print("      ⚠️  EL TEXTO ESTÁ SOLO EN EL CONCEPTO. La pantalla lo muestra bajo")
            print("          DESCRIPCIÓN porque cae al concepto cuando el banco no manda el")
            print("          suyo → un matcher de DESCRIPCIÓN compara contra un campo VACÍO")
            print("          y NUNCA agarra. Es el bug.")
        elif en_banco:
            print("      · el texto SÍ está en descripcion_banco: un matcher de DESCRIPCIÓN")
            print("        debería agarrarlo. Si no lo hace, mirá el repr de arriba (espacios,")
            print("        acentos rotos) y el ORDEN — gana el primer balde que matchea.")
        print()

    # ── 3. MATCHERS QUE NO AGARRAN NADA ──────────────────────────────────────
    print("\n3) MATCHERS QUE NO AGARRAN NI UN MOVIMIENTO (con las fechas de la base)\n")
    muertos = 0
    for b in baldes:
        for mm in b["matchers"]:
            col = B.CAMPOS_REGLA.get(mm["campo"])
            if not col:
                print(f"  · {b['etiqueta']:<22} campo INVÁLIDO {mm['campo']!r} — no matchea nunca")
                muertos += 1
                continue
            n = sum(1 for m, *_ in todos
                    if B.desglosar(m, [{"clave": "x", "matchers": [mm]}]) == "x")
            if n == 0:
                # ¿Agarraría con el fallback de la pantalla? Eso lo separa todo.
                con_fb = sum(1 for m, *_ in todos
                             if _cumple(m, mm["campo"], mm["operador"], mm["valor"]))
                extra = ("  ← ¡pero agarraría "
                         f"{con_fb} con el arreglo propuesto!" if con_fb else "")
                print(f"  · {b['etiqueta']:<22} {mm['campo']:<20} {mm['operador']:<9} "
                      f"{mm['valor']!r}{extra}")
                muertos += 1
    if not muertos:
        print("  (ninguno: todas las grafías cargadas agarran algo)")

    # ── 4. GASTOS EN RESTO ───────────────────────────────────────────────────
    resto = [t for t in todos
             if t[1]["es_gasto"] and not t[0].get("ignorado") and t[2] == B.RESTO]
    print(f"\n\n4) GASTOS QUE CAEN EN «MOVIMIENTOS RESTANTES»: {len(resto)}\n")
    for m, marca, balde, _bp, _gp in (resto if args.todos else resto[:40]):
        print(_linea(m, ctas, marca, balde))
        print(_campos(m))
        print()
    if not args.todos and len(resto) > 40:
        print(f"  … y {len(resto) - 40} más (--todos para verlos)")

    # ── 5. IMPACTO DEL ARREGLO ───────────────────────────────────────────────
    print("\n\n5) IMPACTO DEL ARREGLO PROPUESTO")
    print("   (un matcher de DESCRIPCIÓN leería lo MISMO que muestra la pantalla:")
    print("    descripcion_banco y, solo si viene vacío, el concepto)\n")
    cambian_balde = [t for t in todos if t[1]["es_gasto"] and t[2] != t[3]]
    cambian_gasto = [t for t in todos
                     if t[1]["origen"] != "manual" and t[1]["es_gasto"] != t[4]]
    print(f"  · cambian de BALDE (presentación, NO mueve el total): {len(cambian_balde)}")
    for m, _marca, balde, prop, _gp in cambian_balde[:30]:
        print(f"      {m['fecha']}  {float(m['importe'] or 0):>13,.2f}  "
              f"{balde}  →  {prop}   {_pantalla(m)!r}")
    print(f"\n  · cambian de SER O NO GASTO (esto SÍ mueve el total): {len(cambian_gasto)}")
    for m, marca, _b, _p, gp in cambian_gasto[:30]:
        print(f"      {m['fecha']}  {float(m['importe'] or 0):>13,.2f}  "
              f"{'no' if marca['es_gasto'] else 'SI'}  →  {'SI' if gp else 'no'}   "
              f"{_pantalla(m)!r}")
    if not cambian_gasto:
        print("      (ninguno: con las reglas de hoy el arreglo NO mueve un solo peso,")
        print("       solo reparte mejor lo que ya estaba contado)")
    print("\n" + "=" * 100)


if __name__ == "__main__":
    main()
