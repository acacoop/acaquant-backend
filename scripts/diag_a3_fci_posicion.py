"""scripts/diag_a3_fci_posicion.py — ¿PositionReport trae los FCI, y con qué llave?

READ-ONLY. Llama UN método de LECTURA (`PosTrade/PositionReport`) del catálogo
Postrade y NO toca nada más — ni base, ni escritura. Sirve para UNA decisión de
diseño del futuro conciliador de cuotapartes (A3 vs AUNESA) que no se puede
adivinar del manual (REGLA #2): **el ejemplo oficial de PositionReport solo
muestra futuros**, y el parser de producción (`core/postrade_posicion.aplanar`)
descarta todo lo que no sea `SecurityType == 'Futuro'`, contándolo como
`descartadas_no_futuro`. La pregunta es si adentro de ese descarte están las
cuotapartes de FCI, y con qué campo viene el código de fondo (el `Activo Cód.`
/ CAFCI) para poder emparejar por FICHA con AUNESA y no por nombre (REGLA #9).

Qué contesta, medido contra producción y no supuesto:

  1. Qué valores de `SecurityType` devuelve PositionReport a t-1, y cuántas
     filas de cada uno. (¿Aparece FCI / Fondo? ¿En qué volumen?)
  2. Para las filas que NO son futuro: la ESTRUCTURA completa del `Instrument`
     y de la fila, para ver qué campo lleva el código de fondo (CAFCI / código),
     el `Account`, y cómo viene la cantidad (¿`PositionQty[].LongQty` como los
     futuros, u otro campo?).
  3. El inventario de claves distintas del `Instrument` en las filas no-futuro,
     para saber si hay una llave estable de fondo.

Con eso se decide si el lado A3 del conciliador sale por API (ideal) o hay que
subir Excel, y cuál es la llave de fondo.

La fecha por defecto es **t-1 hábil** (el mismo criterio con el que se consulta
la posición: t0 no sirve). Se puede forzar con `--fecha AAAAMMDD`.

Uso (desde la raíz del repo):
    python -m scripts.diag_a3_fci_posicion
    python -m scripts.diag_a3_fci_posicion --fecha 20260904
    python -m scripts.diag_a3_fci_posicion --muestras 5
    python -m scripts.diag_a3_fci_posicion --json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date

from core import postrade
from core.calendario import restar_habiles

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEP = "=" * 78
METODO = "PositionReport"
FUTURO = "Futuro"


def _security_type(fila: dict) -> str:
    inst = fila.get("Instrument") or {}
    if not isinstance(inst, dict):
        return "(sin Instrument)"
    return str(inst.get("SecurityType") or "(sin SecurityType)").strip()


def _cantidad_resumen(fila: dict) -> str:
    """Cómo viene la cantidad de esta fila, en una línea (sin interpretar)."""
    pq = fila.get("PositionQty")
    if isinstance(pq, list) and pq:
        partes = []
        for q in pq:
            if isinstance(q, dict):
                trozo = {k: v for k, v in q.items() if v not in (None, "")}
                partes.append(json.dumps(trozo, ensure_ascii=False))
        return "PositionQty=[" + ", ".join(partes) + "]"
    return "PositionQty=" + json.dumps(pq, ensure_ascii=False)


def analizar(crudo) -> dict:
    """Clasifica la respuesta cruda de PositionReport. PURA: no toca red ni base."""
    resultado: dict = {
        "es_lista": isinstance(crudo, list),
        "total_filas": 0,
        "por_security_type": {},
        "muestras_no_futuro": [],
        "claves_instrument_no_futuro": {},
        "muestras_futuro": [],
    }
    if not isinstance(crudo, list):
        resultado["crudo_tipo"] = type(crudo).__name__
        return resultado

    resultado["total_filas"] = len(crudo)
    tipos: Counter = Counter()
    claves_inst: Counter = Counter()

    for fila in crudo:
        if not isinstance(fila, dict):
            tipos["(fila no-dict)"] += 1
            continue
        st = _security_type(fila)
        tipos[st] += 1
        es_futuro = st == FUTURO
        inst = fila.get("Instrument") or {}
        if not es_futuro and isinstance(inst, dict):
            for k in inst:
                claves_inst[k] += 1

    resultado["por_security_type"] = dict(tipos.most_common())
    resultado["claves_instrument_no_futuro"] = dict(claves_inst.most_common())
    return resultado


def _recolectar_muestras(crudo, n: int) -> tuple[list, list]:
    """Hasta `n` filas de ejemplo por lado (no-futuro y futuro), crudas."""
    no_fut, fut = [], []
    if not isinstance(crudo, list):
        return no_fut, fut
    for fila in crudo:
        if not isinstance(fila, dict):
            continue
        if _security_type(fila) == FUTURO:
            if len(fut) < n:
                fut.append(fila)
        elif len(no_fut) < n:
            no_fut.append(fila)
        if len(no_fut) >= n and len(fut) >= n:
            break
    return no_fut, fut


def main() -> None:
    ap = argparse.ArgumentParser(
        description="¿PositionReport de A3 trae FCI y con qué llave de fondo? (READ-ONLY)"
    )
    ap.add_argument("--fecha", help="AAAAMMDD; por defecto t-1 hábil")
    ap.add_argument("--muestras", type=int, default=3, help="filas de ejemplo por lado")
    ap.add_argument("--json", action="store_true", help="salida JSON en vez de texto")
    args = ap.parse_args()

    fecha = args.fecha or postrade.fecha_api(restar_habiles(date.today(), 1))

    try:
        crudo = postrade.leer(METODO, {"clearingBusinessDate": fecha, "viewDetails": "false"})
    except postrade.PostradeError as e:
        print(f"✗ PositionReport falló: {e}")
        raise SystemExit(2) from None

    resumen = analizar(crudo)
    no_fut, fut = _recolectar_muestras(crudo, max(1, args.muestras))

    if args.json:
        print(json.dumps(
            {"fecha": fecha, "resumen": resumen,
             "muestras_no_futuro": no_fut, "muestras_futuro": fut},
            ensure_ascii=False, indent=2,
        ))
        return

    print(SEP)
    print("DIAG A3 — ¿PositionReport trae cuotapartes de FCI?  (READ-ONLY)")
    print(SEP)
    print(f"  Base URL : {postrade._base()}")
    print(f"  Fecha    : {fecha}  (t-1 hábil salvo --fecha)")
    print(f"  Método   : {METODO} (viewDetails=false, consolidado)\n")

    if not resumen["es_lista"]:
        print(f"  ⚠️ La respuesta NO es una lista (tipo {resumen.get('crudo_tipo')}). "
              "Sin filas para analizar.")
        return

    print(f"  Filas recibidas: {resumen['total_filas']}\n")
    print("  SecurityType → filas:")
    for st, n in resumen["por_security_type"].items():
        marca = "  (← futuros, lo que hoy guardamos)" if st == FUTURO else ""
        print(f"    {st:<28} {n}{marca}")

    print("\n  Claves del Instrument en filas NO-futuro (candidatas a llave de fondo):")
    if resumen["claves_instrument_no_futuro"]:
        for k, n in resumen["claves_instrument_no_futuro"].items():
            print(f"    {k:<28} en {n} filas")
    else:
        print("    (ninguna — no hay filas no-futuro)")

    print("\n" + SEP)
    print("MUESTRAS NO-FUTURO (fila cruda + cómo viene la cantidad)")
    print(SEP)
    if not no_fut:
        print("  (no hay filas no-futuro en esta fecha)")
    for i, fila in enumerate(no_fut, 1):
        print(f"\n  [{i}] SecurityType = {_security_type(fila)}")
        print(f"      Account = {fila.get('Account')!r}")
        print(f"      Instrument = {json.dumps(fila.get('Instrument'), ensure_ascii=False)}")
        print(f"      {_cantidad_resumen(fila)}")
        print(f"      fila completa: {json.dumps(fila, ensure_ascii=False)}")

    print("\n" + SEP)
    print("MUESTRA FUTURO (para contraste)")
    print(SEP)
    if not fut:
        print("  (no hay futuros en esta fecha)")
    for i, fila in enumerate(fut, 1):
        print(f"\n  [{i}] Instrument = {json.dumps(fila.get('Instrument'), ensure_ascii=False)}")
        print(f"      {_cantidad_resumen(fila)}")

    print("\n  Qué mirar:")
    print("   1. ¿Aparece un SecurityType de FCI/Fondo con volumen razonable?")
    print("   2. En el Instrument no-futuro, ¿hay un campo con el código de fondo")
    print("      (CAFCI / 'Activo Cód.') para emparejar con AUNESA por ficha?")
    print("   3. ¿La cantidad de FCI viene en PositionQty[].LongQty, o en otro campo?")


if __name__ == "__main__":
    main()
