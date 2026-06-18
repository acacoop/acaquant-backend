"""scripts/compare_mercado_hist_sql_vs_mongo.py — GATE históricos de mercado (SQL ↔ Mongo).

Read-only. Corre los get_historico_* Mongo (derivados.py / repo.py) vs el espejo
SQL (mercado_hist_sql.py) sobre la misma grilla y compara el CONTRATO REAL: lo
que la API/MCP emiten sobre JSON. Por eso ambos lados pasan primero por
`jsonable_encoder` (lo que usa FastAPI) — así un `datetime` nativo de Mongo y el
string ISO que el jsonb guardó (sync._jsonb→isoformat) se comparan en su forma
serializada, que es idéntica. Sin esto el gate falla por tipo (datetime vs str)
una diferencia que NO existe sobre HTTP.

El orden de empate dentro de una fecha NO se exige idéntico (la lectura Mongo de
forwards/breakevens no estaba ordenada) → ambas listas se ordenan por clave
canónica antes de comparar. Números a float redondeado. Gate de MERCADO_HIST_SQL.

    python -m scripts.compare_mercado_hist_sql_vs_mongo
"""
from __future__ import annotations

import json
from typing import Any

from fastapi.encoders import jsonable_encoder

from api.services import derivados as der
from api.services import mercado_hist_sql as sql
from api.services import repo

_ROUND = 9


def _norm(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return round(float(v), _ROUND)
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    if isinstance(v, list):
        normed = [_norm(x) for x in v]
        # multiset: ordenar por repr canónico (el orden de empate no es contractual)
        try:
            normed = sorted(normed, key=lambda x: json.dumps(x, sort_keys=True, default=str))
        except Exception:
            pass
        return normed
    return v


def _diff(a: Any, b: Any, path: str = "") -> list[str]:
    if type(a) is not type(b):
        return [f"{path or '<root>'}: tipo {type(a).__name__} ≠ {type(b).__name__}"]
    if isinstance(a, dict):
        out: list[str] = []
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: falta en SQL")
            elif k not in b:
                out.append(f"{path}.{k}: falta en Mongo")
            else:
                out += _diff(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}: len {len(a)} ≠ {len(b)}"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += _diff(x, y, f"{path}[{i}]")
        return out
    return [] if a == b else [f"{path}: {a!r} ≠ {b!r}"]


def _case(fm, fs) -> tuple[bool, list[str], int]:
    # jsonable_encoder = el contrato real (lo que FastAPI emite). Iguala el
    # datetime nativo de Mongo con el string ISO del jsonb SQL.
    m = jsonable_encoder(fm())
    s = jsonable_encoder(fs())
    difs = _diff(_norm(s), _norm(m), "")  # SQL vs Mongo
    return (not difs, difs, len(m) if isinstance(m, list) else -1)


def main() -> int:
    grid: list[tuple[str, Any, Any]] = [
        ("breakevens (todo)", der.get_historico_breakevens, sql.get_historico_breakevens),
        ("forwards (todo)", der.get_historico_forwards, sql.get_historico_forwards),
        ("forwards(cer)",
         lambda: der.get_historico_forwards(curva="cer"),
         lambda: sql.get_historico_forwards(curva="cer")),
        ("forwards(tasa_fija)",
         lambda: der.get_historico_forwards(curva="tasa_fija"),
         lambda: sql.get_historico_forwards(curva="tasa_fija")),
        ("futuros_dlr (todo)", der.get_historico_futuros_dlr, sql.get_historico_futuros_dlr),
        ("caucion (todo)", repo.get_historico_caucion, sql.get_historico_caucion),
        ("caucion(ARS)",
         lambda: repo.get_historico_caucion(moneda="ARS"),
         lambda: sql.get_historico_caucion(moneda="ARS")),
        ("caucion(USD)",
         lambda: repo.get_historico_caucion(moneda="USD"),
         lambda: sql.get_historico_caucion(moneda="USD")),
        ("caucion(rango)",
         lambda: repo.get_historico_caucion(desde="2026-01-01", hasta="2026-03-31"),
         lambda: sql.get_historico_caucion(desde="2026-01-01", hasta="2026-03-31")),
        ("futuros_dlr(rango)",
         lambda: der.get_historico_futuros_dlr(desde="2026-01-01", hasta="2026-03-31"),
         lambda: sql.get_historico_futuros_dlr(desde="2026-01-01", hasta="2026-03-31")),
    ]

    print("GATE históricos de mercado — SQL (mercado.mercado_hist) ↔ Mongo (Trading.*)\n")
    ok_n = 0
    for nombre, fm, fs in grid:
        ok, difs, n = _case(fm, fs)
        if ok:
            ok_n += 1
            print(f"  ✓ {nombre}  ({n} docs)")
        else:
            print(f"  ✗ {nombre}")
            for d in difs[:8]:
                print(f"      {d}")
            if len(difs) > 8:
                print(f"      … (+{len(difs) - 8} difs más)")

    total = len(grid)
    print(f"\nRESULTADO: {ok_n}/{total} casos en paridad.")
    if ok_n == total:
        print("✅ GATE VERDE — se puede prender MERCADO_HIST_SQL=1 en el .env del Droplet.")
        return 0
    print("❌ GATE ROJO — NO prender MERCADO_HIST_SQL. Revisar las difs de arriba.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
