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
from datetime import date
from typing import Any

from fastapi.encoders import jsonable_encoder

from api.services import derivados as der
from api.services import mercado_hist_sql as sql
from api.services import repo

_ROUND = 9
HOY = date.today().isoformat()  # la fila de hoy es live → se tolera (ver _case)


def _norm(v: Any) -> Any:
    """Redondea números (float/Decimal) y recursa. NO reordena listas: las
    anidadas (ej. `pares`) preservan el orden del doc fuente (jsonb == Mongo);
    el orden de la lista TOP se resuelve por identidad en _case."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return round(float(v), _ROUND)
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_norm(x) for x in v]
    return v


def _ident(d: Any) -> tuple:
    """Clave de identidad de un doc histórico (fecha + subclave) para alinear las
    dos listas por fila lógica, no por posición."""
    if not isinstance(d, dict):
        return (json.dumps(d, sort_keys=True, default=str),)
    return tuple(str(d.get(k, "")) for k in ("fecha", "curva", "ticker", "moneda"))


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


def _case(fm, fs) -> tuple[bool, list[str], list[str], int]:
    """Devuelve (ok, hist_difs, live_warns, n). La HISTORIA (fecha < hoy) es
    inmutable → debe matchear exacto (drift = BUG, falla el gate). La fila de HOY
    es live (breakevens/forwards reviven con precios) → mutará entre el write a
    Mongo y el espejo aunque haya dual-write → se tolera y se reporta."""
    m = _norm(jsonable_encoder(fm()))
    s = _norm(jsonable_encoder(fs()))
    if not isinstance(m, list) or not isinstance(s, list):
        return (m == s, [] if m == s else ["<root>: difiere"], [], -1)

    # Alinear por identidad (fecha+subclave), no por posición.
    mi: dict[tuple, Any] = {_ident(d): d for d in m}
    si: dict[tuple, Any] = {_ident(d): d for d in s}
    hist: list[str] = []
    live: list[str] = []

    def _bucket(fecha: str) -> list[str]:
        return live if fecha >= HOY else hist

    for k in sorted(mi.keys() - si.keys()):
        _bucket(k[0]).append(f"fila solo en Mongo: {k}")
    for k in sorted(si.keys() - mi.keys()):
        _bucket(k[0]).append(f"fila solo en SQL: {k}")
    drift_hist: set = set()
    drift_live: set = set()
    for k in sorted(mi.keys() & si.keys()):
        d = _diff(si[k], mi[k], f"[{k[0]}]")
        if not d:
            continue
        if k[0] >= HOY:
            drift_live.add(k[0]); live += d
        else:
            drift_hist.add(k[0]); hist += d
    if drift_hist:
        hist.insert(0, f">>> HISTORIA con drift (BUG): {sorted(drift_hist)}")
    if drift_live:
        live.insert(0, f">>> hoy/live con drift (tolerado; requiere SNAPSHOT_SQL=1): "
                       f"{sorted(drift_live)}")
    return (not hist, hist, live, len(m))


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

    print("GATE históricos de mercado — SQL (mercado.mercado_hist) ↔ Mongo (Trading.*)")
    print(f"Historia (fecha < {HOY}) = exacta (gate). Fila de hoy = live, se tolera.\n")
    ok_n = 0
    live_total = 0
    for nombre, fm, fs in grid:
        ok, hist, live, n = _case(fm, fs)
        live_total += 1 if live else 0
        mark = "✓" if ok else "✗"
        warn = "  ⚠ hoy/live difiere (tolerado)" if live else ""
        print(f"  {mark} {nombre}  ({n} docs){warn}")
        for d in (hist if not ok else [])[:8]:
            print(f"      {d}")
        if not ok and len(hist) > 8:
            print(f"      … (+{len(hist) - 8} difs más)")
        if ok:
            ok_n += 1
            for d in live[:1]:  # mostrar solo el resumen de fechas live
                print(f"      {d}")

    total = len(grid)
    print(f"\nRESULTADO: {ok_n}/{total} casos con HISTORIA en paridad exacta.")
    if live_total:
        print(f"({live_total} casos con drift SOLO en la fila de hoy — esperable hasta SNAPSHOT_SQL=1 "
              "+ restart de motores breakevens/forwards.)")
    if ok_n == total:
        print("✅ GATE VERDE (historia exacta) — MERCADO_HIST_SQL=1 OK; prender SNAPSHOT_SQL=1 "
              "para la fila de hoy de breakevens/forwards.")
        return 0
    print("❌ GATE ROJO — hay drift en HISTORIA (no es la fila de hoy). Revisar arriba.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
