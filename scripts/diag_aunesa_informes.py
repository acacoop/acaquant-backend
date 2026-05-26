"""Diag read-only de Aunesa GET /api/operaciones/informes ("Informe de operaciones").

DISTINTO de /operaciones/consolidadosGenerales (el que ingesta hoy
negocio_movimientos): este endpoint es boleto-level y trae el desglose de
ARANCELES / gastos / impuestos que el otro no tiene. Sirve para evaluar si lo
integramos para capturar aranceles.

NO escribe en Mongo. Forza una respuesta de una cuenta puntual (ej. 805) en un
rango y dumpea la unión de todas las claves + items completos, con foco en los
campos de costo.

Validaciones del endpoint (del doc): hay que consultar por al menos uno de
`cuenta` / `boleto` / `grupo` / `gruposCuentas`. Si es por `cuenta`,
`fechaDesde`+`fechaHasta` (Fecha Liquidación) son OBLIGATORIAS.

Uso (en el Droplet, desde la raíz):
    python -m scripts.diag_aunesa_informes --cuenta 805
    python -m scripts.diag_aunesa_informes --cuenta 805 --desde 2026-01-01 --hasta 2026-05-26
    python -m scripts.diag_aunesa_informes --boleto BOL2026069919
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, timedelta

import requests

import config

AUTH_URL = "https://aca.aunesa.com/Irmo/api/login"
INFORMES_URL = "https://aca.aunesa.com/Irmo/api/operaciones/informes"

# Campos de costo que son el motivo de evaluar este endpoint.
_COSTO_FIELDS = ("aranceles", "arancelesA", "arancelesP", "gastos",
                 "impuestos", "otros", "otrosA", "otrosP", "neto", "bruto", "nocional")

# Subconjunto que nos importa de verdad (lo que el usuario necesita capturar).
# Excluye neto/bruto/nocional, que siempre vienen poblados.
_FEE_FIELDS = ("aranceles", "arancelesA", "arancelesP", "gastos",
               "impuestos", "otros", "otrosA", "otrosP")


def _tiene_fee(item: dict) -> bool:
    """True si el boleto trae ALGÚN arancel/gasto/impuesto con monto real."""
    for k in _FEE_FIELDS:
        for row in (item.get(k) or []):
            monto = str((row or {}).get("Monto") or "").strip()
            if monto not in ("", "0", "0.0"):
                return True
    return False


def _auth() -> dict[str, str]:
    r = requests.post(
        AUTH_URL,
        json={
            "clientId": config.AUNESA_CLIENT_ID,
            "username": config.AUNESA_USERNAME,
            "password": config.AUNESA_PASSWORD,
        },
        headers={"Content-Type": "application/json"},
        timeout=15,
    )
    r.raise_for_status()
    return {"Content-Type": "application/json", "Authorization": f"Bearer {r.json().get('token')}"}


def _to_ddmmyyyy(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def main() -> int:
    ap = argparse.ArgumentParser(description="Dump crudo de /operaciones/informes (read-only).")
    ap.add_argument("--cuenta", default=None, help="ID de cuenta (ej. 805).")
    ap.add_argument("--boleto", default=None, help="ID de boleto (consulta sola, sin fechas).")
    ap.add_argument("--desde", default=None, help="Fecha Liquidación desde YYYY-MM-DD (default -90d).")
    ap.add_argument("--hasta", default=None, help="Fecha Liquidación hasta YYYY-MM-DD (default hoy).")
    ap.add_argument("--samples", type=int, default=3, help="items completos a dumpear.")
    args = ap.parse_args()

    if not args.cuenta and not args.boleto:
        print("❌ Indicá --cuenta o --boleto (el endpoint exige al menos uno).")
        return 1

    params: dict[str, str] = {}
    if args.boleto:
        params["boleto"] = args.boleto
    else:
        hasta = args.hasta or date.today().isoformat()
        desde = args.desde or (date.fromisoformat(hasta) - timedelta(days=90)).isoformat()
        params["cuenta"] = args.cuenta
        params["fechaDesde"] = _to_ddmmyyyy(desde)   # Fecha Liquidación (obligatorias por cuenta)
        params["fechaHasta"] = _to_ddmmyyyy(hasta)

    headers = _auth()
    print(f"GET /operaciones/informes {params}")
    r = requests.get(INFORMES_URL, params=params, headers=headers, timeout=180)
    print(f"→ HTTP {r.status_code}")
    if r.status_code == 204:
        print("204: sin operaciones para esos parámetros. Probá otro rango/cuenta.")
        return 0
    if r.status_code != 200:
        print(r.text[:1500])
        return 1

    body = (r.text or "").strip()
    data = r.json() if body else []
    if isinstance(data, dict):
        data = data.get("data") or [data]
    if not isinstance(data, list):
        print(f"shape inesperada: {type(data).__name__}\n{str(data)[:1000]}")
        return 1
    print(f"Total boletos: {len(data)}\n")
    if not data:
        print("Sin datos.")
        return 0

    # Unión de todas las claves (top-level) con tipo.
    claves: dict[str, str] = {}
    for it in data:
        if isinstance(it, dict):
            for k, v in it.items():
                claves.setdefault(k, type(v).__name__)
    print("═" * 72)
    print(f"TODAS LAS CLAVES ({len(claves)}):")
    for k in sorted(claves):
        flag = "  ⬅️ COSTO" if k in _COSTO_FIELDS else ""
        print(f"   {k:<20} ({claves[k]}){flag}")

    # Distribución por tipo de operación — para ver que NO son solo cauciones
    # (las cauciones no pagan arancel; necesitamos ver compras/ventas).
    tipos: Counter = Counter(str(it.get("tipoOperacion") or "—") for it in data)
    print("\n" + "═" * 72)
    print("tipoOperacion (top 15):")
    for t, n in tipos.most_common(15):
        print(f"   {n:>5}  {t}")

    # LA PREGUNTA CLAVE: ¿algún boleto de los 506 trae arancel/gasto/impuesto?
    con_fee = [it for it in data if _tiene_fee(it)]
    print("\n" + "═" * 72)
    print(f"BOLETOS CON ARANCEL/GASTO/IMPUESTO POBLADO: {len(con_fee)} de {len(data)}")
    if not con_fee:
        print("❌ NINGUNO trae costos con monto en este rango/cuenta.")
        print("   → O esta cuenta no paga aranceles (ej. DMA/propia), o el endpoint")
        print("     no los expone para este cliente. Probá otra cuenta con compras/ventas.")
    else:
        print(f"✅ SÍ vienen poblados. Dumpeo los primeros {args.samples}:")
        for it in con_fee[: args.samples]:
            print("-" * 72)
            print(json.dumps(it, ensure_ascii=False, indent=2))

    # Además, una muestra general (cualquier boleto) por si querés ver el shape.
    print("\n" + "═" * 72)
    print(f"MUESTRA GENERAL (primeros {args.samples}, sin filtrar):")
    for it in data[: args.samples]:
        print("-" * 72)
        print(json.dumps(it, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
