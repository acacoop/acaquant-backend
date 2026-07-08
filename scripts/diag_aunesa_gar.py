"""diag READ-ONLY — ¿Aunesa nos dice el estado (GAR/garantía) de una tenencia?

Consulta la posición de las cuentas propias (100/255/256) a Aunesa con los MISMOS
params que el writer diario (jobs.portafolio_backfill) y vuelca la ESTRUCTURA CRUDA:
todas las claves que trae cada fila, los valores de `informacion`, y cualquier campo
que huela a situación/garantía/plazo. Con eso decidimos si hay un dato GAR que hoy
estamos ignorando (el parser solo mira filas 'Acumulado').

Uso:
    python -m scripts.diag_aunesa_gar
    python -m scripts.diag_aunesa_gar --cuentas 255 --desde 2026-07-07

Solo hace GET (no escribe nada). Borrar tras cerrar el tema (REGLA #5).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, timedelta

import holidays

from jobs.aum import _SESSION, POSICION_URL, autenticar

_PARAMS_BASE = {
    "hasta": "", "tipoCuenta": "Comitentes y propias",
    "nivel": "Especie x cuenta", "ocultarCerradas": "true",
}
_FERIADOS = holidays.Argentina()
# Claves candidatas a "estado del título" (garantía, situación, plazo, caución, etc.).
_CAND = ("estad", "situac", "garant", "gar", "plazo", "dispon", "afect", "caucion",
         "prenda", "bloq", "embarg", "concepto", "detalle", "tipo")


def _prox_habil(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5 or d in _FERIADOS:
        d += timedelta(days=1)
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuentas", default="100,255,256")
    ap.add_argument("--desde", help="ISO YYYY-MM-DD (default hoy). Aunesa: desde=X devuelve X-1")
    args = ap.parse_args()

    base = date.fromisoformat(args.desde) if args.desde else date.today()
    # Aunesa exige dd/mm/yyyy (igual que jobs.portafolio_backfill). ISO da HTTP 400.
    desde = _prox_habil(base).strftime("%d/%m/%Y")
    hdr = autenticar()
    cuentas = [c.strip() for c in args.cuentas.split(",") if c.strip()]

    for idc in cuentas:
        print("=" * 72)
        print(f"CUENTA {idc}   (desde={desde})")
        params = {"desde": desde, **_PARAMS_BASE}
        r = _SESSION.get(POSICION_URL.format(idc), params=params, headers=hdr, timeout=120)
        print("HTTP", r.status_code)
        if r.status_code != 200:
            print("  body:", r.text[:300])
            continue
        data = r.json()
        if not isinstance(data, list):
            print("  respuesta no-lista:", type(data).__name__)
            print("  ", json.dumps(data, ensure_ascii=False, default=str)[:600])
            continue

        rows = [x for x in data if isinstance(x, dict)]
        print("filas:", len(rows))
        keys: set[str] = set()
        for row in rows:
            keys |= set(row.keys())
        print("CLAVES:", sorted(keys))
        print("informacion:", dict(Counter(str(row.get("informacion")) for row in rows)))

        cand = sorted(k for k in keys if any(t in k.lower() for t in _CAND))
        print("CLAVES CANDIDATAS (estado/garantía/plazo/…):", cand)
        for k in cand:
            vals = Counter(str(row.get(k)) for row in rows)
            # recorto a los 15 valores más comunes para no spamear
            print(f"   {k} -> {dict(vals.most_common(15))}")

        acum = next((row for row in rows if row.get("informacion") == "Acumulado"), None)
        no_acum = [row for row in rows if row.get("informacion") != "Acumulado"][:4]
        if acum:
            print("MUESTRA 'Acumulado':", json.dumps(acum, ensure_ascii=False, default=str)[:900])
        for i, row in enumerate(no_acum, 1):
            print(f"MUESTRA no-Acum #{i}:", json.dumps(row, ensure_ascii=False, default=str)[:900])


if __name__ == "__main__":
    main()
