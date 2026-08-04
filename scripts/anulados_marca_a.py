"""scripts/anulados_marca_a.py — barrido de boletos anulados por la marca `(A)`.

Aunesa marca el boleto anulado agregándole ` (A)` al número. Como la ingesta
upsertea por número, el boleto marcado entró como fila NUEVA y convive con el
original → la operación se cuenta dos veces.

    python -m scripts.anulados_marca_a          # SOLO reporta (no toca nada)
    python -m scripts.anulados_marca_a --fix    # marca anulado_en en las 2 tablas

Lista cada boleto con fecha, cuenta, cliente e importe para poder auditarlo
contra Aunesa. Idempotente: correrlo dos veces no cambia nada.
"""
from __future__ import annotations

import sys

from api.services.anulados import detectar_marca_a

_COLS = "{:<12} {:<6} {:<20} {:<24} {:<26} {:>18} {:<4} {}"


def _linea(f: dict) -> str:
    return _COLS.format(
        (f["fecha"] or "")[:12],
        str(f["id_cuenta"] or "")[:6],
        str(f["boleto"] or "")[:20],
        str(f["cliente"] or "")[:24],
        str(f["operacion"] or "")[:26],
        f"{f['importe']:,.2f}" if f["importe"] is not None else "-",
        str(f["moneda"] or "")[:4],
        "(ya anulado)" if f["ya_anulado"] else "",
    )


def _bloque(titulo: str, filas: list[dict]) -> None:
    print(f"\n{titulo} — {len(filas)}")
    if not filas:
        return
    print("-" * 130)
    print(_COLS.format("FECHA", "CTA", "BOLETO", "CLIENTE", "OPERACIÓN",
                       "IMPORTE", "MON", ""))
    print("-" * 130)
    for f in filas:
        print(_linea(f))


def main() -> None:
    commit = "--fix" in sys.argv
    print("=" * 130)
    print("BOLETOS CON MARCA (A) — " + ("APLICANDO" if commit else "SOLO REPORTE"))
    print("=" * 130)

    res = detectar_marca_a(commit=commit)
    for tabla, d in res["tablas"].items():
        print(f"\n\n### {tabla.upper()}")
        _bloque("CON LA MARCA (A) — anulados por Aunesa", d["con_marca_a"])
        _bloque("GEMELOS SIN MARCA — mismo número, ingestados antes de la anulación",
                d["gemelos_sin_marca"])
        print(f"\n  con (A): {d['n_con_marca']}   gemelos: {d['n_gemelos']}   "
              f"ya anuladas: {d['n_ya_anuladas']}   marcadas ahora: {d['marcadas']}")

    print("\n" + "=" * 130)
    if commit:
        print(f"✅ Marcadas {res['marcadas']} fila(s) como anuladas.")
    else:
        total = sum(d["n_con_marca"] + d["n_gemelos"] for d in res["tablas"].values())
        ya = sum(d["n_ya_anuladas"] for d in res["tablas"].values())
        print(f"Candidatas: {total} · ya anuladas: {ya} · "
              f"quedarían por marcar: {total - ya}")
        print("Para aplicarlo:  python -m scripts.anulados_marca_a --fix")


if __name__ == "__main__":
    main()
