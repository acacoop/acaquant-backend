"""Seed de un bono soberano (globales / bonares) desde su CSV de prospecto.

Parsea un CSV con el layout del prospecto BCBA (2 filas de header) y hace
upsert en `Trading.Curvas` con shape compatible con engines/curvas.py.

Columnas esperadas del CSV (índices 0-based):
    0  Cupón #
    1  Fecha de pago       (ISO datetime)
    2  Amortización (vn)   (porcentaje del VN, ej. "8.00%")
    3  Valor residual      (porcentaje AFTER-pago, ej. "96.00%")
    4  Tasa de interés     (tasa anual, ignorado)
    5  Interés (vn)        (cupón expresado en %VN, ej. "0.27%")
    6+ Flujos derivados    (ignorado — calculados por nosotros)

Uso:
    python -m scripts.seed_soberano_csv \\
        --csv docs/soberanos/GD35.csv \\
        --ticker "MERV - XMEV - GD35D - 24hs" \\
        --ticker-corto GD35D \\
        --fecha-emision 2020-09-04

    # --dry solo imprime el doc sin escribir en Mongo
    python -m scripts.seed_soberano_csv --csv ... --ticker ... --ticker-corto ... --dry

La fecha_vencimiento se toma del último flujo del CSV. Los campos
`tipo='globales'`, `curva='soberanos'`, `valor_nominal=100`,
`cupon_anual=0`, `cer_emision=None` son fijos.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from core.mongo import get_mongo_client


def _validar_ticker(ticker: str) -> str:
    """Normaliza (collapse whitespace + trim) y valida el formato ROFEX.

    Formato esperado: 'MERV - XMEV - <SYMBOL> - 24hs' o similar. Si trae
    doble espacio en algún lado (típico de copy-paste mal), lo normaliza.
    Si igual no matchea el formato, aborta — el motor_rofex fallaría al
    suscribir un ticker con formato raro.
    """
    original = ticker
    ticker = re.sub(r"\s+", " ", ticker).strip()
    if ticker != original:
        print(f"  ⚠ ticker normalizado: {original!r} -> {ticker!r}")

    partes = ticker.split(" - ")
    if len(partes) != 4:
        raise SystemExit(
            f"Ticker {ticker!r} no tiene 4 segmentos 'A - B - C - D'. "
            f"Formato esperado: 'MERV - XMEV - <SYMBOL> - 24hs'."
        )
    simbolo = partes[2]
    if not re.fullmatch(r"[A-Z0-9]+", simbolo):
        raise SystemExit(
            f"Símbolo {simbolo!r} contiene caracteres inválidos. "
            f"Solo letras mayúsculas y dígitos."
        )
    return ticker


def _pct(s: str) -> float:
    """'8.00%' → 8.0.  '0.27%' → 0.27.  Tolera coma decimal y espacios."""
    s = (s or "").strip().replace(",", ".").rstrip("%").strip()
    return float(s) if s else 0.0


def _iso_fecha(s: str) -> str:
    """'2024-07-10T00:00:00.000Z' → '2024-07-10'."""
    return (s or "").split("T", 1)[0].strip()


def parse_csv(path: Path) -> list[dict]:
    """Lee el CSV del prospecto y devuelve la lista de flujos con el shape
    usado por engines/curvas.py."""
    with path.open(encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        filas = [r for r in reader if r and any(c.strip() for c in r)]

    if len(filas) < 3:
        raise SystemExit(f"CSV con < 3 filas (2 headers + al menos 1 flujo): {path}")

    # Saltar las 2 filas de header
    datos = filas[2:]

    flujos: list[dict] = []
    residual_previo = 100.0
    for i, row in enumerate(datos, start=1):
        if len(row) < 6:
            raise SystemExit(f"Fila {i} del CSV tiene solo {len(row)} columnas (esperadas ≥6): {row}")
        try:
            fecha = _iso_fecha(row[1])
            amort = _pct(row[2])
            residual_after = _pct(row[3])
            cupon = _pct(row[5])
        except ValueError as e:
            raise SystemExit(f"Error parseando fila {i}: {e} · raw={row}") from e

        flujos.append({
            "fecha": fecha,
            "amortizacion_pct": amort,
            "cupon_sobre_residual": cupon,
            "residual_previo_pct": residual_previo,
        })
        residual_previo = residual_after

    # Sanity: el último residual_after debería ser ~0 (bono amortizado completo)
    if residual_previo > 0.5:
        print(
            f"⚠ Residual final {residual_previo}% ≠ 0%. ¿CSV incompleto?"
            " (se ignora, pero revisalo)",
        )
    return flujos


def build_doc(
    ticker: str,
    ticker_corto: str,
    fecha_emision: str,
    flujos: list[dict],
    tipo: str = "globales",
    curva: str = "soberanos",
) -> dict:
    if not flujos:
        raise SystemExit("Sin flujos parseados — abortando.")
    fecha_vto = flujos[-1]["fecha"]
    return {
        "ticker":            ticker,
        "ticker_corto":      ticker_corto,
        "tipo":              tipo,
        "curva":             curva,
        "fecha_emision":     fecha_emision,
        "fecha_vencimiento": fecha_vto,
        "cer_emision":       None,
        "valor_nominal":     100,
        "cupon_anual":       0,
        "flujos":            flujos,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Path del CSV del prospecto")
    parser.add_argument("--ticker", required=True,
                        help='Ticker completo ROFEX, ej. "MERV - XMEV - GD35D - 24hs"')
    parser.add_argument("--ticker-corto", required=True,
                        help="Label corto, ej. GD35D")
    parser.add_argument("--fecha-emision", required=True,
                        help="YYYY-MM-DD")
    parser.add_argument("--tipo", default="globales",
                        help="Tipo (default: globales)")
    parser.add_argument("--curva", default="soberanos",
                        help="Curva (default: soberanos)")
    parser.add_argument("--dry", action="store_true",
                        help="Imprime el doc sin escribir en Mongo")
    args = parser.parse_args()

    path = Path(args.csv)
    if not path.exists():
        raise SystemExit(f"CSV no existe: {path}")

    # Normaliza el ticker (collapse whitespace) y valida formato antes de
    # escribir en Mongo. Evita que un doble espacio accidental al pegar el
    # comando genere un ticker ROFEX inválido que rompa la suscripción WS.
    ticker_limpio = _validar_ticker(args.ticker)

    flujos = parse_csv(path)
    doc = build_doc(
        ticker=ticker_limpio,
        ticker_corto=args.ticker_corto,
        fecha_emision=args.fecha_emision,
        flujos=flujos,
        tipo=args.tipo,
        curva=args.curva,
    )

    print(f"\n--- Parseado OK: {args.ticker_corto} ---")
    print(f"  ticker:            {doc['ticker']}")
    print(f"  ticker_corto:      {doc['ticker_corto']}")
    print(f"  fecha_emision:     {doc['fecha_emision']}")
    print(f"  fecha_vencimiento: {doc['fecha_vencimiento']}")
    print(f"  flujos:            {len(doc['flujos'])}")
    print(f"  primer flujo:      {doc['flujos'][0]}")
    print(f"  ultimo flujo:      {doc['flujos'][-1]}")

    if args.dry:
        print("\n--dry: NO se escribe en Mongo. Doc completo:")
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0

    client = get_mongo_client()
    res = client["Trading"]["Curvas"].update_one(
        {"ticker_corto": args.ticker_corto},
        {"$set": doc},
        upsert=True,
    )
    if res.upserted_id:
        print(f"\n✓ Upsert OK (insertado)  _id={res.upserted_id}")
    else:
        print(f"\n✓ Upsert OK (actualizado)  matched={res.matched_count}")
    print("  Reiniciá motor_rofex + motor_curvas para que tome el ticker nuevo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
