"""Diag read-only: por qué la grilla BANCOS de Tesorería no cierra.

Responde, para UN día, las tres preguntas que no se pueden contestar desde el código:

  1. ¿Entran filas que NO son de ese día? — compara lo que devuelve Aunesa para el
     rango [día, día+1] contra lo que sobrevive al filtro `fecha == día`, y muestra
     cuántas filas fueron CREADAS otro día (el `id` empieza con YYYYMMDD) aunque
     su `fecha` sea la del día.
  2. ¿Se están sumando movimientos que nunca movieron plata? — desglosa por
     `estado` (Rechazado / Anulado / Pendiente… no tocaron la cuenta del banco).
  3. ¿Sobran bancos? — cuántas cuentas operativas operaron ese día vs cuántas
     trae el catálogo `operaciones.tesoreria_cuentas` (la grilla las muestra todas,
     las que no operaron salen en cero).

Además lista los campos crudos de Aunesa (para ver si hay alguna fecha de
liquidación además de `fecha`) y chequea `id` duplicados (sumarían doble).

NO imprime importes salvo que pases `--montos`: por defecto solo cuenta filas, así
la salida se puede pegar en el chat sin exponer saldos.

Uso (desde la raíz, en el Droplet):
    python -m scripts.diag_tesoreria_bancos                    # hoy
    python -m scripts.diag_tesoreria_bancos --fecha 2026-08-06
    python -m scripts.diag_tesoreria_bancos --fecha 2026-08-06 --montos
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date

from api.services.tesoreria import (
    _ENDPOINT,
    TODOS_ESTADOS,
    _dia,
    _fechas,
    _num,
    aplanar,
    catalogo,
)
from core import aunesa

SEP = "─" * 78


def _crudas_sin_filtrar(dia: date, estado: str) -> list[dict]:
    """Lo que Aunesa devuelve para [día, día+1], SIN el filtro de día de la vista."""
    ddmmyyyy, hasta, _ = _fechas(dia.isoformat())
    params: dict = {"liquidacionDesde": ddmmyyyy, "liquidacionHasta": hasta}
    if estado:
        params["estados"] = estado
    resp = aunesa.get(_ENDPOINT, params)
    if resp.status_code == 204:
        return []
    if resp.status_code != 200:
        print(f"! Aunesa HTTP {resp.status_code}: {resp.text[:300]}")
        return []
    body = resp.json()
    return body if isinstance(body, list) else []


def _fecha_del_id(id_: object) -> str:
    """'20260806143012' → '06/08/2026'. '' si el id no tiene forma de fecha."""
    s = str(id_ or "")
    return f"{s[6:8]}/{s[4:6]}/{s[:4]}" if len(s) >= 8 and s[:8].isdigit() else ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fecha", default=None, help="ISO YYYY-MM-DD; default = hoy ART")
    ap.add_argument("--montos", action="store_true",
                    help="además de contar filas, imprime importes (NO pegar en el chat)")
    a = ap.parse_args()

    dia = _dia(a.fecha)
    ddmmyyyy, hasta, _ = _fechas(dia.isoformat())
    print(SEP)
    print(f"TESORERÍA · BANCOS — diag del {ddmmyyyy}")
    print(f"Aunesa: {_ENDPOINT}  liquidacionDesde={ddmmyyyy}  liquidacionHasta={hasta}")
    print(f"Estados pedidos: TODOS ({TODOS_ESTADOS})")
    print(SEP)

    crudas = _crudas_sin_filtrar(dia, TODOS_ESTADOS)
    del_dia = [r for r in crudas if str(r.get("fecha") or "").strip() == ddmmyyyy]
    otros = len(crudas) - len(del_dia)

    # 1 — ¿entra algo que no es del día?
    print("\n[1] FILAS POR `fecha` (lo que Aunesa devolvió para el rango)")
    for f, n in sorted(Counter(str(r.get("fecha") or "?").strip() for r in crudas).items()):
        marca = "  <-- EL DÍA (es lo que usa la vista)" if f == ddmmyyyy else ""
        print(f"    {f:>12} : {n:>5} filas{marca}")
    print(f"    total devuelto por Aunesa: {len(crudas)}")
    print(f"    se descartan por no ser del día: {otros}")
    print(f"    quedan para la vista: {len(del_dia)}")

    print("\n[2] ¿ESAS FILAS SE CREARON ESE DÍA? (`id` = YYYYMMDDHHMMSS)")
    por_creacion = Counter(_fecha_del_id(r.get("id")) or "(id sin fecha)" for r in del_dia)
    for f, n in sorted(por_creacion.items()):
        marca = "  <-- creada ese día" if f == ddmmyyyy else "  <-- CREADA OTRO DÍA"
        print(f"    {f:>14} : {n:>5} filas{marca}")
    print("    Nota: `fecha` es la que la vista toma como 'el día'. Si hay filas creadas")
    print("    otro día, es porque Aunesa las liquida/imputa en este — decidí vos si")
    print("    esas TIENEN que contar para el saldo del día o no.")

    # 3 — campos crudos (¿hay otra fecha además de `fecha`?)
    print("\n[3] CAMPOS QUE MANDA AUNESA (nombre : en cuántas filas viene con valor)")
    campos: Counter = Counter()
    for r in del_dia:
        for k, v in r.items():
            if v not in (None, "", {}, []):
                campos[k] += 1
    for k, n in sorted(campos.items()):
        print(f"    {k:<24} : {n}")

    # 4 — estados: lo que no está Procesado NO movió plata en el banco
    print("\n[4] DESGLOSE POR ESTADO (filas del día)")
    por_estado: Counter = Counter()
    monto_estado: defaultdict = defaultdict(float)
    for r in del_dia:
        e = str(r.get("estado") or "?").strip()
        por_estado[e] += 1
        monto_estado[e] += _num(r.get("monto"))
    for e, n in sorted(por_estado.items(), key=lambda kv: -kv[1]):
        extra = f"   monto={monto_estado[e]:,.2f}" if a.montos else ""
        print(f"    {e:<26} : {n:>5} filas{extra}")
    no_proc = sum(n for e, n in por_estado.items() if e != "Procesado")
    print(f"    -> filas NO 'Procesado': {no_proc} de {len(del_dia)}")
    print("    Si la vista está en estado 'Todos', ESAS filas están entrando al")
    print("    saldo final aunque el banco nunca las movió.")

    # 5 — duplicados (sumarían doble)
    ids = [str(r.get("id") or "") for r in del_dia if r.get("id")]
    dup = {i: n for i, n in Counter(ids).items() if n > 1}
    print(f"\n[5] `id` DUPLICADOS: {len(dup)} (filas repetidas suman doble)")
    for i, n in list(dup.items())[:10]:
        print(f"    {i} x{n}")

    # 6 — bancos: los del día vs el catálogo completo
    print("\n[6] BANCOS")
    operaron = {(aplanar(r, "")["cuentaOperativa"], str(r.get("unidad") or "?").upper())
                for r in del_dia}
    try:
        cat = set(catalogo())
    except Exception as exc:  # sin DB accesible el diag igual sirve
        cat = set()
        print(f"    (no pude leer el catálogo: {exc})")
    print(f"    operaron ese día            : {len(operaron)}")
    print(f"    en el catálogo (todos)      : {len(cat)}")
    print(f"    en la grilla SIN movimientos: {len(cat - operaron)}  <-- salen en cero")
    for c, u in sorted(cat - operaron)[:15]:
        print(f"        · {c} [{u}]")
    if len(cat - operaron) > 15:
        print(f"        … y {len(cat - operaron) - 15} más")

    # 7 — la reconciliación que importa
    print("\n[7] LO QUE ALIMENTA EL SALDO FINAL")
    tot: defaultdict = defaultdict(lambda: {"n": 0, "ing": 0.0, "egr": 0.0})
    for r in del_dia:
        m = aplanar(r, "")
        if not m["_tipo"]:
            continue
        e = "Procesado" if str(r.get("estado") or "").strip() == "Procesado" else "otros estados"
        d = tot[e]
        d["n"] += 1
        d["ing" if m["_tipo"] == "ingreso" else "egr"] += _num(r.get("monto"))
    for e in ("Procesado", "otros estados"):
        d = tot.get(e) or {"n": 0, "ing": 0.0, "egr": 0.0}
        extra = (f"   ingresos={d['ing']:,.2f}  egresos={d['egr']:,.2f}  "
                 f"neto={d['ing'] - d['egr']:,.2f}") if a.montos else ""
        print(f"    {e:<16} : {d['n']:>5} movimientos{extra}")
    sin_tipo = sum(1 for r in del_dia if not aplanar(r, "")["_tipo"])
    print(f"    sin tipo (ni Depósito ni Extracción, NO suman): {sin_tipo}")
    print("\n" + SEP)
    print("Pegá en el chat las secciones [1] [2] [4] [5] [6] [7] (sin --montos no hay")
    print("importes, son solo conteos). Con eso te digo exactamente qué corregir.")
    print(SEP)


if __name__ == "__main__":
    main()
