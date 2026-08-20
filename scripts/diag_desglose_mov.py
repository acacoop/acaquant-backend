"""scripts/diag_desglose_mov.py — por qué un movimiento NO cae en la columna esperada.

READ-ONLY: solo `SELECT` sobre `bancos.*`. No escribe nada, no toca el catálogo.

## Por qué existe

El desglose de gastos falla **en silencio**: si un movimiento no cae donde se
espera, no salta ninguna excepción — la columna simplemente muestra otro número.
Y hay DOS capas que pueden estar fallando, con dos ABMs distintos:

  1. **REGLAS** (`bancos.gastos_reglas`, botón «Reglas de gastos») deciden si el
     movimiento **es gasto**.
  2. **BALDES** (`gastos_baldes` + `gastos_balde_matchers`, botón «Desglose»)
     deciden **en qué columna** cae un gasto que ya lo es.

⚠️ El que se confunde siempre es este: `_gastos_bancarios` **saltea** el
movimiento que no es gasto ANTES de llamar a `desglosar`. Agregarle un texto a la
columna no sirve de nada si ninguna regla lo marcó como gasto primero — el
matcher queda decorativo y nadie se entera.

El otro sospechoso es el TEXTO. `descripcion_banco` llega **truncada a ~25
caracteres y con los acentos rotos** (`Comisi.n`), así que un texto transcrito de
otra pantalla puede ser MÁS LARGO que lo que hay guardado, y `contiene` falla.
Por eso este script imprime el texto real con `repr()` y su longitud: se ve el
punto exacto donde diverge, en vez de adivinar.

Contesta, para los movimientos que uno le pida:

  1. ¿Existe el movimiento ese día en esa cuenta?
  2. ¿Es gasto? ¿Por qué regla, o por marca manual?
  3. ¿Está ignorado (NO CUENTA)?
  4. ¿En qué balde cayó, y por CUÁL matcher?
  5. Si cayó en RESTO: matcher por matcher del balde esperado, por qué no agarró.

Uso:
    python -m scripts.diag_desglose_mov --fecha 2026-08-18 --texto TRANSF
    python -m scripts.diag_desglose_mov --fecha 2026-08-18 --texto TRANSF \\
        --cuenta 000100010488 --balde comtransf
    python -m scripts.diag_desglose_mov --fecha 2026-08-18 --todas   # el día entero
"""
from __future__ import annotations

import argparse
from datetime import date, datetime

from api.services import bancos as svc

SEP = "=" * 100
SUB = "-" * 100


def _norm(v) -> str:
    """La MISMA normalización que usa `desglosar`. Se copia a propósito: si
    mañana cambia allá y no acá, el diagnóstico mentiría — y un diagnóstico que
    miente es peor que no tenerlo."""
    return str(v or "").strip().casefold()


def _agarra(dato: str, valor: str, operador: str) -> bool:
    if not dato or not valor:
        return False
    return dato == valor if operador == "igual" else valor in dato


# --------------------------------------------------------------------------- #
# 1. El catálogo, tal cual está en la base
# --------------------------------------------------------------------------- #
def mostrar_baldes(baldes: list[dict]) -> None:
    print(SEP)
    print("BALDES DEL DESGLOSE (orden = quién gana los empates; gana el PRIMERO)")
    print(SEP)
    for b in baldes:
        print(f"\n  [{b['orden']:>4}] {b['clave']:<20} {b['etiqueta']:<28} grupo={b['grupo']}")
        if not b["matchers"]:
            print("         (sin textos — esta columna no agarra nada)")
        for m in b["matchers"]:
            # `repr` y no el texto pelado: es la única forma de VER un espacio
            # doble, una coma invisible o un acento que llegó como otro byte.
            print(f"         · id={m['id']:<5} {m['campo']:<20} {m['operador']:<8} "
                  f"{m['valor']!r}  (len={len(m['valor'])})")


def mostrar_reglas(reglas: list[dict]) -> None:
    print()
    print(SEP)
    print("REGLAS DE GASTO — deciden si el movimiento ES gasto (capa 1, la que se olvida)")
    print(SEP)
    activas = [r for r in reglas if r.get("activa", True)]
    print(f"  activas: {len(activas)} de {len(reglas)}\n")
    for r in reglas:
        marca = " " if r.get("activa", True) else "×"
        print(f"  [{marca}] id={r['id']:<5} {r['campo']:<20} {r['operador']:<8} "
              f"{r['valor']!r}  (len={len(r['valor'] or '')})  {r.get('nota') or ''}")


# --------------------------------------------------------------------------- #
# 2. El movimiento, capa por capa
# --------------------------------------------------------------------------- #
def diagnosticar(mov: dict, marca: dict, baldes: list[dict], reglas: list[dict],
                 balde_esperado: str, cuentas: dict) -> None:
    cta = cuentas.get(mov["cuenta_id"], {})
    print(SUB)
    print(f"  MOV {mov['mov_hash'][:16]}…  cuenta_id={mov['cuenta_id']} "
          f"({cta.get('nombre', '?')} · {cta.get('numero', '?')})")
    print(f"     importe : {mov['tipo']} {mov['importe']}")
    print(f"     descripcion_banco : {mov['descripcion_banco']!r}  "
          f"(len={len(mov['descripcion_banco'] or '')})")
    print(f"     descripcion_ib    : {mov['descripcion_ib']!r}")
    print(f"     codigo_ib={mov['codigo_operacion_ib']!r}  "
          f"codigo_banco={mov['codigo_operacion_banco']!r}")

    # ── CAPA 0: ignorado ──────────────────────────────────────────────────
    if mov.get("ignorado"):
        print("     ⛔ IGNORADO (NO CUENTA) → no suma en ninguna columna. "
              "Se destildó desde la vista.")
        return

    # ── CAPA 1: ¿es gasto? ────────────────────────────────────────────────
    if not marca["es_gasto"]:
        print(f"     ⛔ NO ES GASTO (origen={marca['origen']}) → **el desglose NI "
              "SIQUIERA LO MIRA**.")
        print("        `_gastos_bancarios` saltea el movimiento ANTES de llamar a "
              "`desglosar`,")
        print("        así que agregarle un texto a la columna no cambia nada.")
        print("        Se arregla en «Reglas de gastos», no en «Desglose» — o "
              "marcándolo SÍ a mano.")
        print("        Reglas activas que NO lo agarraron:")
        for r in reglas:
            if not r.get("activa", True):
                continue
            col = svc.CAMPOS_REGLA.get(r["campo"])
            dato = _norm(mov.get(col)) if col else ""
            print(f"          · {r['campo']:<18} {r['operador']:<8} {r['valor']!r} "
                  f"vs {dato!r}")
        return
    print(f"     ✔ ES GASTO (origen={marca['origen']}, regla={marca.get('regla')})")

    # ── CAPA 2: ¿qué balde? ───────────────────────────────────────────────
    cayo = svc.desglosar(mov, baldes)
    if cayo != svc.RESTO:
        ganador = next(b for b in baldes if b["clave"] == cayo)
        print(f"     → cae en «{ganador['etiqueta']}» ({cayo}, orden {ganador['orden']})")
    else:
        print(f"     → cae en RESTO (MOVIMIENTOS RESTANTES): ningún balde lo agarró")

    if balde_esperado and cayo != balde_esperado:
        print(f"     ⚠️ SE ESPERABA «{balde_esperado}». Matcher por matcher:")
        esperado = next((b for b in baldes if b["clave"] == balde_esperado), None)
        if not esperado:
            print(f"        No existe ningún balde con clave «{balde_esperado}».")
            print(f"        Claves reales: {', '.join(b['clave'] for b in baldes)}")
            return
        for m in esperado["matchers"]:
            col = svc.CAMPOS_REGLA.get(m["campo"])
            if not col:
                print(f"        · campo {m['campo']!r} NO está en CAMPOS_REGLA → "
                      "no matchea NUNCA")
                continue
            dato, valor = _norm(mov.get(col)), _norm(m["valor"])
            ok = _agarra(dato, valor, m["operador"])
            print(f"        {'✔' if ok else '✗'} {m['campo']:<18} {m['operador']:<8} "
                  f"busca {valor!r} (len={len(valor)})")
            print(f"           en el movimiento hay {dato!r} (len={len(dato)})")
            if not ok and m["operador"] == "contiene" and len(valor) > len(dato):
                print("           ⚠️ EL TEXTO BUSCADO ES MÁS LARGO QUE EL GUARDADO: "
                      "el banco lo mandó truncado.")
                print("              Cargá una RAÍZ más corta (ej. las primeras "
                      "15 letras) en vez de la frase entera.")
        # Si cayó en otro balde, el culpable es el ORDEN y hay que decirlo.
        if cayo != svc.RESTO:
            print(f"        ⚠️ Se lo llevó «{cayo}» porque se evalúa ANTES "
                  f"(gana el primero). Subí «{balde_esperado}» con ▲.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fecha", help="YYYY-MM-DD (default: último día hábil)")
    p.add_argument("--texto", default="",
                   help="filtra por descripción o concepto que CONTENGA esto")
    p.add_argument("--cuenta", default="",
                   help="número de cuenta (o su terminación), ej. 000100010488")
    p.add_argument("--balde", default="",
                   help="clave del balde donde SE ESPERABA que caiga, ej. comtransf")
    p.add_argument("--todas", action="store_true",
                   help="lista TODAS las descripciones distintas del día")
    args = p.parse_args()

    fecha = (datetime.strptime(args.fecha, "%Y-%m-%d").date() if args.fecha
             else svc.fecha_default())

    baldes = svc._baldes()
    reglas = svc.listar_reglas()
    mostrar_baldes(baldes)
    mostrar_reglas(reglas)

    cuentas = {r["id"]: {"nombre": r["account_label"] or r["bank_name"],
                         "numero": r["account_number"],
                         "moneda": r["currency"], "tipo": r["account_type"]}
               for r in svc._q(
                   """SELECT id, bank_name, account_label, account_number,
                             account_type, currency FROM bancos.cuentas""")}

    movs = svc._movs_para_clasificar(fecha)
    print()
    print(SEP)
    print(f"MOVIMIENTOS DEL {fecha} — {len(movs)} en total")
    print(SEP)
    if not movs:
        print("  No hay movimientos ese día. La base retiene pocas fechas: "
              "probá con el último día hábil.")
        return

    if args.cuenta:
        ids = {cid for cid, c in cuentas.items()
               if args.cuenta.strip() in (c["numero"] or "")}
        if not ids:
            print(f"  Ninguna cuenta con «{args.cuenta}». Cuentas del día:")
            for cid in sorted({m["cuenta_id"] for m in movs}):
                c = cuentas.get(cid, {})
                print(f"    {cid:<6} {c.get('nombre', '?'):<32} {c.get('numero', '?')}")
            return
        movs_f = [m for m in movs if m["cuenta_id"] in ids]
    else:
        movs_f = movs

    # La clasificación se hace con TODOS los movimientos del día, como en
    # producción: filtrar antes podría cambiar algo el día de mañana.
    marcas = svc.clasificar(movs, reglas, svc._overrides(fecha))

    if args.todas:
        print("\n  DESCRIPCIONES DISTINTAS (lo que el banco manda de verdad):")
        vistas: dict[tuple, int] = {}
        for m in movs_f:
            k = (m["descripcion_banco"], m["descripcion_ib"])
            vistas[k] = vistas.get(k, 0) + 1
        for (db, dib), n in sorted(vistas.items(), key=lambda x: -x[1]):
            print(f"    ×{n:<4} {db!r:<50} (len={len(db or '')})  ib={dib!r}")

    if args.texto:
        t = _norm(args.texto)
        movs_f = [m for m in movs_f
                  if t in _norm(m["descripcion_banco"]) or t in _norm(m["descripcion_ib"])]

    print(f"\n  A DIAGNOSTICAR: {len(movs_f)}\n")
    for m in movs_f:
        diagnosticar(m, marcas[m["mov_hash"]], baldes, reglas, args.balde, cuentas)

    print(SUB)


if __name__ == "__main__":
    main()
