"""scripts/compare_rem_sql_vs_mongo.py — GATE de paridad REM (SQL ↔ Mongo).

Read-only. Corre los DOS motores de REM (api/services/rem.py Mongo vs
api/services/rem_sql.py Postgres) sobre la misma grilla de inputs y compara la
salida. Es el gate ANTES de prender REM_SQL=1: exigir 100% PASS.

Compara por VALOR (no byte-a-byte): los números se normalizan a float y se
redondean (las columnas SQL son numeric→Decimal, Mongo float; ambos alimentan un
chart). Las listas se ordenan por clave estable antes de comparar para no
fallar por orden de empate.

    python -m scripts.compare_rem_sql_vs_mongo
"""
from __future__ import annotations

from typing import Any

from api.services import rem as mongo
from api.services import rem_sql as sql

_ROUND = 9  # tolerancia numérica: difs < 1e-9 son ruido de float/Decimal


def _norm(v: Any) -> Any:
    """Normaliza para comparar por valor: números→float redondeado, dicts/listas
    recursivo. Las listas de items REM se ordenan por (periodo, periodo_tipo)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return round(float(v), _ROUND)
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    if isinstance(v, list):
        normed = [_norm(x) for x in v]
        if normed and isinstance(normed[0], dict):
            def _key(d):
                return (str(d.get("periodo", "")), str(d.get("periodo_tipo", "")))
            try:
                normed = sorted(normed, key=_key)
            except Exception:
                pass
        return normed
    return v


def _diff(a: Any, b: Any, path: str = "") -> list[str]:
    """Devuelve la lista de diferencias entre dos estructuras ya normalizadas."""
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


def _case(nombre: str, fn_mongo, fn_sql) -> tuple[bool, list[str]]:
    m = _norm(fn_mongo())
    s = _norm(fn_sql())
    difs = _diff(s, m, "")  # s=SQL, m=Mongo
    return (not difs, difs)


def main() -> int:
    # Informes a probar: el último + algunos históricos reales (los lee de Mongo).
    informes = [r["informe"] for r in mongo.listar_informes()[:4]]
    grid: list[tuple[str, Any, Any]] = [
        ("listar_informes", mongo.listar_informes, sql.listar_informes),
        ("debug_info", mongo.debug_info, sql.debug_info),
        ("expectativas(ultimo)",
         lambda: mongo.expectativas(), lambda: sql.expectativas()),
        ("expectativas(mensual)",
         lambda: mongo.expectativas(periodo_tipo="mensual"),
         lambda: sql.expectativas(periodo_tipo="mensual")),
        ("breakeven_acumulado(ultimo)",
         mongo.breakeven_acumulado, sql.breakeven_acumulado),
    ]
    for inf in informes:
        grid.append((f"expectativas({inf})",
                     lambda inf=inf: mongo.expectativas(informe=inf),
                     lambda inf=inf: sql.expectativas(informe=inf)))
        grid.append((f"breakeven_acumulado({inf})",
                     lambda inf=inf: mongo.breakeven_acumulado(informe=inf),
                     lambda inf=inf: sql.breakeven_acumulado(informe=inf)))

    print("GATE paridad REM — SQL (macro.rem) ↔ Mongo (Trading.REM)\n")
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
        print("✅ GATE VERDE — se puede prender REM_SQL=1 en el .env del Droplet.")
        return 0
    print("❌ GATE ROJO — NO prender REM_SQL. Revisar las difs de arriba.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
