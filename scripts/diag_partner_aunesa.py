"""diag_partner_aunesa.py — qué devuelve Aunesa para las cuentas 175 y 463.

El backfill de prueba mostró: 175 → 'sin datos', 463 → '0 pos' (Aunesa tiene
datos pero procesar() los filtró). Este diag pega a Aunesa para esas cuentas
y muestra la respuesta cruda + qué regla de _aum_filters las deja afuera.

Pega a Aunesa (igual que jobs.aum_backfill). Uso:
    python -m scripts.diag_partner_aunesa [YYYY-MM-DD]   # default 2026-05-15
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from datetime import datetime, timedelta

from jobs._aum_filters import (
    _build_contrapartes_regex,
    is_excluded,
    load_contrapartes_id_cuentas,
    load_contrapartes_names,
)
from jobs.aum import autenticar, consultar_posicion

_CUENTAS = ["175", "463"]


def main() -> None:
    fecha = sys.argv[1] if len(sys.argv) > 1 else "2026-05-15"
    fecha_dt = datetime.strptime(fecha, "%Y-%m-%d")
    desde = (fecha_dt + timedelta(days=1)).strftime("%d/%m/%Y")
    print(f"fecha_snapshot={fecha}  desde={desde}")

    contrapartes_ids = load_contrapartes_id_cuentas()
    contrapartes_names = load_contrapartes_names()
    regex_contr = _build_contrapartes_regex(contrapartes_names)

    print("Autenticando con Aunesa...", flush=True)
    headers = autenticar()
    print("Auth OK")
    print("=" * 72)

    for cid in _CUENTAS:
        print(f"\n### Cuenta {cid}", flush=True)
        data, reauth = consultar_posicion(cid, dict(headers), desde, timeout=240)
        if reauth:
            headers = autenticar()
            data, _ = consultar_posicion(cid, dict(headers), desde, timeout=240)

        if data is None:
            print("  Aunesa devolvió None (status != 200) — cuenta inexistente o error.")
            continue
        if not isinstance(data, list):
            print(f"  Respuesta no es lista: {type(data).__name__} = {str(data)[:200]}")
            continue
        print(f"  filas crudas: {len(data)}")
        if not data:
            print("  → Aunesa devuelve lista vacía: la cuenta no tiene posiciones "
                  "esa fecha (o el número no existe en Aunesa).")
            continue

        infos = Counter(r.get("informacion") for r in data)
        print(f"  valores de 'informacion': {dict(infos)}")
        acum = [r for r in data if r.get("informacion") == "Acumulado"]
        print(f"  filas 'Acumulado' (las que toma el AuM): {len(acum)}")
        if not acum:
            print("  → No hay filas 'Acumulado' — por eso procesar() no toma nada.")
            continue

        nombres = sorted({r.get("cuenta") for r in acum if r.get("cuenta")})
        print(f"  nombre(s) de cuenta: {nombres}")
        unidades = Counter(r.get("unidad") for r in acum)
        print(f"  unidades (top 10): {unidades.most_common(10)}")

        for nombre in nombres:
            m = re.search(r"\[(\d+)\]", nombre or "")
            idc = m.group(1) if m else cid
            r3 = idc in contrapartes_ids
            hit = regex_contr and re.search(regex_contr, nombre, re.IGNORECASE)
            print(f"  nombre {nombre!r}:")
            print(f"     regla 3 (id {idc} en ContrapartesAPI): {r3}")
            print(f"     regla 4 (nombre matchea contraparte): {bool(hit)}"
                  + (f"  → matchea {hit.group(0)!r}" if hit else ""))

        idc0 = (re.search(r"\[(\d+)\]", nombres[0]) or [None, cid])[1] if nombres else cid
        sobreviven = sum(
            not is_excluded(r.get("cuenta"), r.get("unidad"), idc0,
                            contrapartes_ids, contrapartes_names)
            for r in acum
        )
        print(f"  filas que SOBREVIVEN a is_excluded(): {sobreviven} / {len(acum)}")
        if sobreviven and len(acum):
            print("  (si el backfill igual dio 0 pos, las posiciones netean a "
                  "cantidad 0 — procesar() descarta cantidad==0)")


if __name__ == "__main__":
    main()
