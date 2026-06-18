"""scripts/compare_macro_sql_vs_mongo.py — GATE de paridad series macro (SQL ↔ Mongo).

Read-only. Corre los DOS motores (api/services/macro.py Mongo vs macro_sql.py
Postgres) sobre las series SQL-backed y verifica:
  1. get_badlar/get_cer/get_dolar (series crudas, con y sin rango de fechas).
  2. obtener_serie_macro para las 7 series + variables de delegación (mep/ccl/
     canje/ticker/bloqueada) — estas últimas deben dar IGUAL porque el path SQL
     delega a Mongo.

Compara por valor (números→float redondeado). Gate antes de prender MACRO_SQL=1.

    python -m scripts.compare_macro_sql_vs_mongo
"""
from __future__ import annotations

from typing import Any

from api.services import macro as mongo
from api.services import macro_sql as sql

_ROUND = 9


def _norm(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return round(float(v), _ROUND)
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_norm(x) for x in v]
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


def _case(nombre: str, fm, fs) -> tuple[bool, list[str]]:
    difs = _diff(_norm(fs()), _norm(fm()), "")  # SQL vs Mongo
    return (not difs, difs)


def main() -> int:
    # Series SQL-backed (deben salir de macro.series_macro, idénticas).
    sql_vars = ["tamar", "cer", "dolar", "badlar", "riesgo_pais", "ipc",
                "ipc_interanual", "dolar_oficial", "dolar_mayorista"]
    # Variables de DELEGACIÓN (el path SQL llama al Mongo → idénticas trivialmente).
    deleg_vars = ["mep", "ccl", "canje", "caucion_ars", "ipim", "GD30.paridad", "desconocida_xyz"]

    grid: list[tuple[str, Any, Any]] = [
        ("get_badlar()", mongo.get_badlar, sql.get_badlar),
        ("get_cer()", mongo.get_cer, sql.get_cer),
        ("get_dolar()", mongo.get_dolar, sql.get_dolar),
        ("get_cer(rango)",
         lambda: mongo.get_cer(desde="2026-01-01", hasta="2026-03-31"),
         lambda: sql.get_cer(desde="2026-01-01", hasta="2026-03-31")),
    ]
    for v in sql_vars:
        grid.append((f"serie_macro({v})",
                     lambda v=v: mongo.obtener_serie_macro(variable=v, ventana_dias=120),
                     lambda v=v: sql.obtener_serie_macro(variable=v, ventana_dias=120)))
        grid.append((f"clasificar({v})",
                     lambda v=v: mongo.clasificar_nivel(variable=v, ventana_dias=120),
                     lambda v=v: sql.clasificar_nivel(variable=v, ventana_dias=120)))
    for v in deleg_vars:
        grid.append((f"serie_macro({v}) [delega]",
                     lambda v=v: mongo.obtener_serie_macro(variable=v, ventana_dias=90),
                     lambda v=v: sql.obtener_serie_macro(variable=v, ventana_dias=90)))

    print("GATE paridad series macro — SQL (macro.series_macro) ↔ Mongo (Trading.*)\n")
    ok_n = 0
    for nombre, fm, fs in grid:
        ok, difs = _case(nombre, fm, fs)
        if ok:
            ok_n += 1
            print(f"  ✓ {nombre}")
        else:
            print(f"  ✗ {nombre}")
            for d in difs[:8]:
                print(f"      {d}")
            if len(difs) > 8:
                print(f"      … (+{len(difs) - 8} difs más)")

    total = len(grid)
    print(f"\nRESULTADO: {ok_n}/{total} casos en paridad.")
    if ok_n == total:
        print("✅ GATE VERDE — se puede prender MACRO_SQL=1 en el .env del Droplet.")
        return 0
    print("❌ GATE ROJO — NO prender MACRO_SQL. Revisar las difs de arriba.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
