"""jobs/mercado_1816_discovery.py — arma el universo de la vista RESEARCH desde TUS
bonos (no hardcodeado). Doc madre: docs/VISTA_RESEARCH.md.

En vez de una lista fija, cruza los bonos que YA tenés en `mercado.curvas` (los
no-ON: soberanos, CER, tasa fija, dollar-linked, duales) contra el catálogo REAL
de 1816 (`/instrumentos` por curva soberana). Solo entran los que 1816 tiene, con
su ticker canónico. Popula `research.mkt_1816_watch` (universo de series) y
`research.mkt_1816_instrumentos` (catálogo). Idempotente.

Cómo se matchea: tu `ticker_corto` puede traer la especie (AL30D/GD30C) y 1816 usa
el base (AL30/GD30) → se normaliza sacando la D/C final antes de comparar.

Uso:
    python -m jobs.mercado_1816_discovery --dry-run   # qué matchea, qué falta (sin escribir)
    python -m jobs.mercado_1816_discovery --apply      # popula watch + instrumentos
"""
from __future__ import annotations

import argparse
import logging
import re

from core import mercado_1816
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# IDs de CURVA de 1816 a cruzar (docs/VISTA_RESEARCH.md §4.3). Son IDs de curva
# (estables), no bonos hardcodeados: los bonos salen del cruce con los tuyos.
# Soberanas + BCRA (BOPREALes como BPOC7 viven en la curva BCRA USD, id 24).
_CURVAS_CRUCE = {
    1: "Soberanos ARS Badlar", 7: "Soberanos ARS CER", 8: "Soberanos USD Bonares",
    9: "Soberanos ARS tasa fija", 10: "Soberanos ARS Botes", 11: "Soberanos USD Globales",
    12: "Soberanos USD Linked", 13: "Soberanos ARS Letras CER", 14: "Soberanos Duales",
    17: "Soberanos USD Linked Lelink", 28: "Soberanos ARS Tamar", 31: "Soberanos EUR Globales",
    24: "BCRA USD",
}

# EXTRAS curados a mano (el "watch a demanda" del día a día — docs/VISTA_RESEARCH
# §4.4b): tickers que NO salen del cruce con mercado.curvas pero se quieren en el
# watch de series. Editar esta lista + correr `--apply` = agregar uno.
# Nivel 1 (2026-07-18, frescos verificados en el sondeo del 17-jul):
#   - Nuestras ONs con operación en 1816 (serie histórica de NUESTROS papeles).
#   - BOPREALes frescos (completan la curva BCRA; solo BPOC7 estaba).
#   - GD46 (opera y no está ni en mercado.curvas — quedaba como "otros").
_EXTRA_WATCH = {
    "AER9O", "AERBO", "AFCIO", "BACGO", "BYCWO", "ZPC3O", "ZZC1O",   # ONs nuestras
    "BPOA7", "BPOA8", "BPOB7", "BPOB8", "BPOD7",                     # BOPREALes
    "GD46",                                                          # global suelto
}
# Curvas extra SOLO para conseguir el metadata de los _EXTRA_WATCH corporativos
# (no se cruzan contra mis bonos): 16 = Corporativos USD, 3 = Corp. USD Linked.
_CURVAS_METADATA_EXTRA = {16: "Corporativos USD", 3: "Corporativos USD Linked"}

_RE_ESPECIE = re.compile(r"^([A-Z]+\d+)[DC]$")   # AL30D/GD30C → AL30/GD30


def _norm(t: str | None) -> str:
    """Normaliza a la forma de 1816: saca la especie (D/C) final si la hay."""
    t = (t or "").strip().upper()
    m = _RE_ESPECIE.match(t)
    return m.group(1) if m else t


def _mis_tickers() -> dict[str, str]:
    """{ticker_1816_normalizado: mi_ticker_corto} de mis bonos NO-ON."""
    from core import curvas_sql

    out: dict[str, str] = {}
    for d in curvas_sql.por_curva_not_like("on%"):
        tc = (d.get("ticker_corto") or "").strip().upper()
        if tc:
            out[_norm(tc)] = tc
    return out


def _instrumentos_1816() -> list[dict]:
    """Todos los instrumentos de las curvas a cruzar en 1816 (dedup por ticker),
    más las curvas de metadata de los _EXTRA_WATCH (no se cruzan, solo aportan
    denominación/vencimiento de los extras corporativos)."""
    vistos: dict[str, dict] = {}
    for cid, nombre in {**_CURVAS_CRUCE, **_CURVAS_METADATA_EXTRA}.items():
        try:
            insts = mercado_1816.instrumentos(curva_id=cid) or []
        except Exception as e:
            logger.warning("discovery: curva %s (%s) falló (%s)", cid, nombre, e)
            continue
        for inst in insts:
            tk = (inst.get("ticker") or "").strip().upper()
            if tk:
                inst["_curva_id"] = cid
                vistos.setdefault(tk, inst)
    return list(vistos.values())


def _upsert(matches: list[dict]) -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        for inst in matches:
            tk = inst["ticker"].strip().upper()
            cur.execute(
                "INSERT INTO research.mkt_1816_instrumentos "
                "(ticker,denominacion,curva,curva_id,isin,fecha_emision,fecha_vencimiento,"
                " moneda_denom,moneda_pago,emisor,activo) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (ticker) DO UPDATE SET denominacion=EXCLUDED.denominacion, "
                "curva=EXCLUDED.curva, curva_id=EXCLUDED.curva_id, isin=EXCLUDED.isin, "
                "fecha_vencimiento=EXCLUDED.fecha_vencimiento, actualizado_en=now()",
                (tk, inst.get("denominacion"), inst.get("curva"), inst.get("_curva_id"),
                 inst.get("isinCode"), inst.get("fechaEmision") or None,
                 inst.get("fechaVencimiento") or None, inst.get("monedaDenom"),
                 inst.get("monedaPago"), inst.get("emisorNombre"), True),
            )
            cur.execute(
                "INSERT INTO research.mkt_1816_watch (ticker,curva,curva_id,activo) "
                "VALUES (%s,%s,%s,true) ON CONFLICT (ticker) DO UPDATE SET "
                "curva=EXCLUDED.curva, curva_id=EXCLUDED.curva_id, activo=true",
                (tk, inst.get("curva"), inst.get("_curva_id")),
            )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="escribe watch + instrumentos")
    ap.add_argument("--dry-run", action="store_true", help="solo muestra el cruce")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not mercado_1816.disponible():
        print("✗ falta MERCADO_1816_API_KEY en el .env")
        return

    mis = _mis_tickers()
    insts = _instrumentos_1816()
    # el cruce automático es SOLO contra las curvas de cruce (las de metadata
    # extra no cruzan — aportan info de los _EXTRA_WATCH corporativos)
    matches = [i for i in insts
               if (i.get("ticker") or "").upper() in mis
               and i.get("_curva_id") in _CURVAS_CRUCE]
    match_tks = {(i["ticker"] or "").upper() for i in matches}
    sin_match = sorted(norm for norm, orig in mis.items() if norm not in match_tks)

    # extras curados (lista _EXTRA_WATCH, editable en el repo)
    extras = [i for i in insts
              if (i.get("ticker") or "").upper() in _EXTRA_WATCH
              and (i.get("ticker") or "").upper() not in match_tks]
    extras_hallados = {(i["ticker"] or "").upper() for i in extras}
    extras_perdidos = sorted(_EXTRA_WATCH - extras_hallados - match_tks)

    print(f"Mis bonos no-ON: {len(mis)} · instrumentos relevados en 1816: "
          f"{len(insts)} · MATCH: {len(matches)} · EXTRAS curados: {len(extras)}")
    print("\nMATCH (van al watch):")
    for i in sorted(matches, key=lambda x: x.get("ticker") or ""):
        print(f"  {i['ticker']:8} {i.get('curva','')}")
    if extras:
        print("\nEXTRAS curados (lista _EXTRA_WATCH — van al watch):")
        for i in sorted(extras, key=lambda x: x.get("ticker") or ""):
            print(f"  {i['ticker']:8} {i.get('curva','')}")
    if extras_perdidos:
        print(f"\n⚠ EXTRAS no hallados en las curvas relevadas: {', '.join(extras_perdidos)} "
              "(¿faltó su curva en _CURVAS_METADATA_EXTRA?)")
    if sin_match:
        print(f"\nTuyos SIN match en 1816 ({len(sin_match)}) — quedan afuera "
              f"(pueden ser ONs, o ticker distinto):\n  " + ", ".join(sin_match))

    if not args.apply or args.dry_run:
        print("\n(DRY-RUN — no se escribió. Corré con --apply para popular el universo.)")
        return

    _upsert(matches + extras)
    print(f"\n✅ {len(matches) + len(extras)} bonos en research.mkt_1816_watch. "
          f"Ahora corré: python -m jobs.mercado_1816_series --backfill")


if __name__ == "__main__":
    main()
