"""diag_aranceles_sin_match.py — inspecciona boletos de Aunesa /informes que NO
están en CashFlow.NegocioMovimientos (read-only, no escribe).

Doble propósito:
  1) Entender los boletos "sin match" del backfill de aranceles (la hipótesis es
     que son operaciones agro / mercados que todavía no ingestamos por
     consolidadosGenerales).
  2) Servir de inventario para inferir el campo `mercado`: tabula la distribución
     de `tipoOperacion` y `naturaleza` de esos boletos.

Toma cuentas al azar de NM (o las que pases), pide informes por cada una, cruza
por `boleto == comprobante` y reporta los que faltan en NM.

Uso (en el Droplet, desde la raíz):
    python -m scripts.diag_aranceles_sin_match
    python -m scripts.diag_aranceles_sin_match --n-cuentas 40 --dump 15
    python -m scripts.diag_aranceles_sin_match --cuenta 1346 --cuenta 805
    python -m scripts.diag_aranceles_sin_match --desde 2025-01-01 --seed 7
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from datetime import date, timedelta

from core import aunesa
from core.mongo import get_mongo_client_read

DB = "CashFlow"
COL = "NegocioMovimientos"

# Campos útiles para entender qué es cada boleto sin match.
_CAMPOS = ("boleto", "tipoOperacion", "naturaleza", "segmento", "sesion",
           "instrumento", "cantidadTotal", "condiciones", "informacion")


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspecciona boletos de informes sin match en NM.")
    ap.add_argument("--cuenta", action="append", default=None, help="cuenta(s) puntuales. Repetible.")
    ap.add_argument("--n-cuentas", type=int, default=25, help="cuentas al azar si no pasás --cuenta.")
    ap.add_argument("--desde", default=None, help="liquidación desde YYYY-MM-DD (default -180d).")
    ap.add_argument("--hasta", default=None, help="liquidación hasta YYYY-MM-DD (default hoy).")
    ap.add_argument("--dump", type=int, default=12, help="boletos sin match a dumpear (al azar).")
    ap.add_argument("--seed", type=int, default=None, help="semilla para reproducir el muestreo.")
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    hasta = date.fromisoformat(args.hasta) if args.hasta else date.today()
    desde = date.fromisoformat(args.desde) if args.desde else (hasta - timedelta(days=180))
    liq_desde, liq_hasta = _ddmmyyyy(desde), _ddmmyyyy(hasta + timedelta(days=15))

    col = get_mongo_client_read()[DB][COL]
    if args.cuenta:
        cuentas = [str(c) for c in args.cuenta]
    else:
        todas = [str(c) for c in col.distinct("id_cuenta") if c]
        cuentas = random.sample(todas, min(args.n_cuentas, len(todas)))
    print(f"Rango liquidación {liq_desde}..{liq_hasta} | cuentas: {len(cuentas)}\n")

    sin_match: list[dict] = []
    tot_inf = tot_match = 0
    for i, cuenta in enumerate(cuentas, 1):
        try:
            resp = aunesa.get("operaciones/informes", {
                "cuenta": cuenta, "fechaDesde": liq_desde, "fechaHasta": liq_hasta,
            })
        except Exception as e:
            print(f"   [{i}/{len(cuentas)}] cuenta {cuenta}: ERROR {e}")
            continue
        if resp.status_code != 200:
            continue
        body = (resp.text or "").strip()
        data = resp.json() if body else []
        if not isinstance(data, list):
            continue
        # dedupe por boleto (informes es multi-fila)
        por_boleto: dict[str, dict] = {}
        for it in data:
            if isinstance(it, dict) and it.get("boleto"):
                por_boleto.setdefault(it["boleto"], it)
        nm_boletos = {str(b) for b in col.distinct("comprobante", {"id_cuenta": cuenta})}
        tot_inf += len(por_boleto)
        for bol, it in por_boleto.items():
            if bol in nm_boletos:
                tot_match += 1
            else:
                it["_cuenta"] = cuenta
                sin_match.append(it)

    print(f"Boletos en informes: {tot_inf} | matchean en NM: {tot_match} | "
          f"SIN match: {len(sin_match)}\n")
    if not sin_match:
        print("No se encontraron boletos sin match en la muestra.")
        return 0

    print("═" * 72)
    print("DISTRIBUCIÓN de los SIN MATCH — tipoOperacion:")
    for t, n in Counter(s.get("tipoOperacion") or "—" for s in sin_match).most_common(20):
        print(f"   {n:>5}  {t}")
    print("\nDISTRIBUCIÓN de los SIN MATCH — naturaleza:")
    for t, n in Counter(s.get("naturaleza") or "—" for s in sin_match).most_common(20):
        print(f"   {n:>5}  {t}")

    print("\n" + "═" * 72)
    muestra = random.sample(sin_match, min(args.dump, len(sin_match)))
    print(f"MUESTRA ALEATORIA de {len(muestra)} boletos SIN match:")
    for it in muestra:
        compact = {k: it.get(k) for k in _CAMPOS}
        compact["_cuenta"] = it.get("_cuenta")
        print("-" * 72)
        print(json.dumps(compact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
