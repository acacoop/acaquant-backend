"""exportar_boletos_diff.py — exporta a CSV las diferencias de boletos entre
CashFlow.Flujo y CashFlow.NegocioMovimientos, en ambas direcciones.

Linkeo por NÚMERO de boleto normalizado (solo dígitos): los prefijos difieren
('BOL 2026071504' en Flujo, 'CL ...'/'BOL ...' en Negocio) pero el número es el
mismo identificador. La cuenta linkea así: CashFlow.Contrapartes.cuenta ==
NegocioMovimientos.id_cuenta.

Genera dos documentos (CSV):

  1. boletos_faltan_en_negocio.csv
     Boletos que están en CashFlow.Flujo y NO en NegocioMovimientos.
     (los 652 del diag — lo que la vista nueva NO tendría). Detalle completo
     del lado Flujo para entender cada uno.

  2. boletos_solo_en_negocio_contrapartes.csv
     El inverso: boletos en NegocioMovimientos de cuentas que SON contrapartes
     (id_cuenta ∈ Contrapartes.cuenta) y que NO están en Flujo. Detalle del lado
     Negocio + la contraparte mapeada por id_cuenta.

Read-only sobre Mongo. Solo escribe los CSV a disco.

Uso (desde la raíz del repo en el Droplet):
    venv/bin/python -m scripts.exportar_boletos_diff
    venv/bin/python -m scripts.exportar_boletos_diff --out /root/exports
"""
from __future__ import annotations

import argparse
import csv
import os
import re

from core.mongo import get_mongo_client_read

_DIGITS = re.compile(r"\d+")


def _norm(v) -> str | None:
    if v is None:
        return None
    grupos = _DIGITS.findall(str(v))
    return "".join(grupos) if grupos else None


def _to_iso(raw) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    if "/" in s:
        try:
            d, m, y = s[:10].split("/")
            return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        except (ValueError, IndexError):
            return None
    return None


# Columnas de cada CSV (orden explícito).
COLS_FLUJO = [
    "boleto", "numero", "concertacion", "contraparte", "id_cuenta",
    "tipoOperacion", "instrumento", "bruto", "moneda", "segmento", "denominacion",
]
COLS_NEGOCIO = [
    "comprobante", "numero", "fecha", "contraparte", "id_cuenta", "cuenta",
    "categoria", "op", "ticker", "cantidad", "precio", "importe", "moneda",
    "unidad", "plazo", "estado", "informacion",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".", help="carpeta de salida (default: cwd)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    c = get_mongo_client_read()
    contrapartes = c["CashFlow"]["Contrapartes"]
    flujo = c["CashFlow"]["Flujo"]
    nego = c["CashFlow"]["NegocioMovimientos"]

    # ── Contrapartes: id_cuenta → contraparte ───────────────────────────────
    cuenta_to_cp: dict[str, str] = {}
    for d in contrapartes.find({}, {"_id": 0, "contraparte": 1, "cuenta": 1}):
        cp = (d.get("contraparte") or "").strip()
        idc = str(d.get("cuenta") or "").strip()
        if cp and idc:
            cuenta_to_cp[idc] = cp
    cuentas_cp = list(cuenta_to_cp)
    print(f"Contrapartes con cuenta: {len(cuentas_cp)}")

    # ── Flujo: docs + set de números normalizados ───────────────────────────
    flujo_docs = list(flujo.find({}, {"_id": 0}))
    flujo_nums: set[str] = set()
    for d in flujo_docs:
        k = _norm(d.get("boleto"))
        if k:
            flujo_nums.add(k)
    print(f"Flujo: {len(flujo_docs)} docs · {len(flujo_nums)} números únicos")

    # ── Negocio: set de números normalizados (todos) ────────────────────────
    nego_nums: set[str] = set()
    for d in nego.find({}, {"_id": 0, "comprobante": 1}):
        k = _norm(d.get("comprobante"))
        if k:
            nego_nums.add(k)
    print(f"Negocio: {len(nego_nums)} números únicos")

    # ── DOC 1: boletos de Flujo que NO están en Negocio ─────────────────────
    path1 = os.path.join(args.out, "boletos_faltan_en_negocio.csv")
    n1 = 0
    with open(path1, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS_FLUJO, extrasaction="ignore")
        w.writeheader()
        for d in flujo_docs:
            num = _norm(d.get("boleto"))
            if not num or num in nego_nums:
                continue
            w.writerow({
                "boleto":        d.get("boleto"),
                "numero":        num,
                "concertacion":  _to_iso(d.get("concertacion")) or d.get("concertacion"),
                "contraparte":   d.get("contraparte"),
                "id_cuenta":     d.get("cuenta"),
                "tipoOperacion": d.get("tipoOperacion"),
                "instrumento":   d.get("instrumento"),
                "bruto":         d.get("bruto"),
                "moneda":        d.get("moneda"),
                "segmento":      d.get("segmento"),
                "denominacion":  d.get("denominacion"),
            })
            n1 += 1
    print(f"\n[1] {n1} boletos en Flujo y NO en Negocio → {os.path.abspath(path1)}")

    # ── DOC 2: boletos de Negocio (cuentas contraparte) que NO están en Flujo ─
    path2 = os.path.join(args.out, "boletos_solo_en_negocio_contrapartes.csv")
    n2 = 0
    with open(path2, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS_NEGOCIO, extrasaction="ignore")
        w.writeheader()
        cur = nego.find(
            {"id_cuenta": {"$in": cuentas_cp}},
            {"_id": 0, "comprobante": 1, "fecha": 1, "id_cuenta": 1, "cuenta": 1,
             "categoria": 1, "op": 1, "ticker": 1, "cantidad": 1, "precio": 1,
             "importe": 1, "moneda": 1, "unidad": 1, "plazo": 1, "estado": 1,
             "informacion": 1},
        )
        for d in cur:
            num = _norm(d.get("comprobante"))
            if not num or num in flujo_nums:
                continue
            idc = str(d.get("id_cuenta") or "")
            w.writerow({
                "comprobante": d.get("comprobante"),
                "numero":      num,
                "fecha":       d.get("fecha"),
                "contraparte": cuenta_to_cp.get(idc, ""),
                "id_cuenta":   idc,
                "cuenta":      d.get("cuenta"),
                "categoria":   d.get("categoria"),
                "op":          d.get("op"),
                "ticker":      d.get("ticker"),
                "cantidad":    d.get("cantidad"),
                "precio":      d.get("precio"),
                "importe":     d.get("importe"),
                "moneda":      d.get("moneda"),
                "unidad":      d.get("unidad"),
                "plazo":       d.get("plazo"),
                "estado":      d.get("estado"),
                "informacion": d.get("informacion"),
            })
            n2 += 1
    print(f"[2] {n2} boletos en Negocio (cuentas contraparte) y NO en Flujo → {os.path.abspath(path2)}")

    print("\nListo. Abrí los dos CSV (o bajalos con scp) para inspeccionarlos.")


if __name__ == "__main__":
    main()
