"""scripts/diag_briefing_render.py — ¿cómo se VERÍA el Briefing de apertura AHORA?

Diagnóstico READ-ONLY que llama al service REAL (`briefing.briefing_hoy()`, la
misma fuente de verdad que consume el modal de HOME) y lo renderiza como una
maqueta de texto del cartel. A diferencia de `diag_briefing_datos.py` (que
vuelca las fuentes crudas para diseñar), esto muestra el PAYLOAD YA COMPUESTO:
variaciones calculadas, flags de stale, "aún sin operaciones", etc. — lo que la
mesa vería si abriera el briefing en este instante.

Correrlo idealmente ~10:00 ART:
    python -m scripts.diag_briefing_render

Al final imprime el JSON crudo del payload por si hace falta el detalle exacto.
"""
from __future__ import annotations

import json
from datetime import date, datetime

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
        return "  s/var"
    signo = "+" if float(v) >= 0 else ""
    return f"{signo}{_num(v)}%"


def _cuando(ts) -> str:
    if ts is None:
        return ""
    if isinstance(ts, datetime):
        from datetime import UTC
        m = int((datetime.now(UTC) - ts).total_seconds() // 60)
        hace = f"hace {m} min" if m < 120 else f"hace {m // 60} h"
        return f"({hace})"
    if isinstance(ts, date):
        return f"({ts.isoformat()})"
    return f"({ts})"


def _linea(etiqueta: str, valor: str, extra: str = "") -> str:
    return f"    {etiqueta:<22} {valor:>16}   {extra}".rstrip()


def main() -> None:
    p = briefing_hoy()

    print("\n" + "═" * 60)
    print(f"  ☀  BRIEFING DE APERTURA — {p['fecha']}")
    gen = p.get("generado")
    if isinstance(gen, datetime):
        print(f"     generado {gen:%H:%M}Z")
    print("═" * 60)

    # ── FUTUROS US ──────────────────────────────────────────────
    print("\n  FUTUROS US")
    futuros = p.get("futuros") or []
    if not futuros:
        print("    (sin datos de futuros)")
    for f in futuros:
        flag = "  ⚠ STALE" if f.get("stale") else ""
        print(_linea(
            f.get("label") or "?",
            _num(f.get("last")),
            f"{_pct(f.get('pct_day'))}   {_cuando(f.get('updated_at'))}{flag}",
        ))

    # ── DÓLAR OFICIAL ───────────────────────────────────────────
    print("\n  DÓLAR OFICIAL")
    ol = p.get("oficial_live")
    if ol is None:
        print(_linea("Mayorista MAE (live)", "aún sin operaciones hoy"))
    else:
        extra = (
            f"{_pct(ol.get('variacion_pct'))}   "
            f"máx {_num(ol.get('maximo'))} / mín {_num(ol.get('minimo'))}   "
            f"{_cuando(ol.get('ts'))}"
        )
        print(_linea("Mayorista MAE (live)", _num(ol.get("valor")), extra))
    a3500 = p.get("a3500")
    if a3500:
        print(_linea(
            "A3500 BCRA (fixing)",
            _num(a3500.get("valor")),
            f"{_pct(a3500.get('variacion_pct'))}   {_cuando(a3500.get('fecha'))}",
        ))
    else:
        print(_linea("A3500 BCRA (fixing)", "—"))

    # ── FINANCIEROS ─────────────────────────────────────────────
    mep, ccl = p.get("mep"), p.get("ccl")
    fecha_cierre = (mep or ccl or {}).get("fecha")
    print(f"\n  DÓLARES FINANCIEROS (cierre {fecha_cierre or '—'})")
    for nombre, blk in (("MEP", mep), ("CCL", ccl)):
        if blk:
            print(_linea(nombre, _num(blk.get("cierre")), _pct(blk.get("variacion_pct"))))
        else:
            print(_linea(nombre, "—"))

    print("\n" + "═" * 60)
    print("  JSON crudo del payload (para el detalle exacto):")
    print("═" * 60)
    print(json.dumps(p, ensure_ascii=False, indent=2, default=str))
    print("\nListo. Pegale este output a Claude.")


if __name__ == "__main__":
    main()
