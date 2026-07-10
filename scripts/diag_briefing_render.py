"""scripts/diag_briefing_render.py — ¿cómo se VERÍA el Briefing de apertura AHORA?

Diagnóstico READ-ONLY que llama al service REAL (`briefing.briefing_hoy()`, la
misma fuente de verdad que consume el modal de HOME) y lo renderiza como una
maqueta de texto del cartel, con el modelo uniforme de columnas HOY·1D·WTD·MTD
para las tres secciones (futuros, dólar oficial, financieros).

Correrlo idealmente ~10:00 ART:
    python -m scripts.diag_briefing_render

Al final imprime el JSON crudo del payload por si hace falta el detalle exacto.
"""
from __future__ import annotations

import json
from datetime import datetime

from api.services.briefing import briefing_hoy


def _num(v, dec: int = 2) -> str:
    """Número con separador de miles ARG (1.234,50). '—' si es None."""
    if v is None:
        return "—"
    try:
        s = f"{float(v):,.{dec}f}"
    except (TypeError, ValueError):
        return str(v)
    return s.replace(",", "@").replace(".", ",").replace("@", ".")


def _pct(v) -> str:
    if v is None:
        return "—"
    signo = "+" if float(v) >= 0 else ""
    return f"{signo}{_num(v)}%"


def _fila(label: str, r: dict) -> str:
    hoy = "Sin Ops" if r.get("hoy") is None else _num(r.get("hoy"))
    return (f"      {label:<14} {hoy:>12} "
            f"{_pct(r.get('ret_1d')):>9} {_pct(r.get('ret_wtd')):>9} "
            f"{_pct(r.get('ret_mtd')):>9}")


def _header() -> str:
    return f"      {'':<14} {'HOY':>12} {'1D':>9} {'WTD':>9} {'MTD':>9}"


def main() -> None:
    p = briefing_hoy()

    print("\n" + "═" * 62)
    print(f"  ☀  BRIEFING DE APERTURA — {p['fecha']}")
    gen = p.get("generado")
    if isinstance(gen, datetime):
        print(f"     generado {gen:%H:%M}Z")
    print("═" * 62)

    # ── FUTUROS ─────────────────────────────────────────────────
    print("\n  FUTUROS")
    print(_header())
    futuros = p.get("futuros") or []
    if not futuros:
        print("      (sin datos de futuros)")
    grupo_actual = None
    for f in futuros:
        g = f.get("grupo")
        if g != grupo_actual:
            print(f"    · {g}")
            grupo_actual = g
        flag = "  ⚠STALE" if f.get("stale") else ""
        print(_fila(f.get("label") or "?", f) + flag)

    # ── DÓLAR OFICIAL ───────────────────────────────────────────
    print("\n  DÓLAR OFICIAL")
    print(_header())
    for r in (p.get("oficial") or []):
        print(_fila(r.get("label") or "?", r))

    # ── FINANCIEROS ─────────────────────────────────────────────
    print("\n  DÓLARES FINANCIEROS")
    print(_header())
    for r in (p.get("financieros") or []):
        print(_fila(r.get("label") or "?", r))

    print("\n" + "═" * 62)
    print("  JSON crudo del payload:")
    print("═" * 62)
    print(json.dumps(p, ensure_ascii=False, indent=2, default=str))
    print("\nListo. Pegale este output a Claude.")


if __name__ == "__main__":
    main()
