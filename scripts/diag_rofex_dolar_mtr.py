"""diag_rofex_dolar_mtr.py — READ-ONLY: encontrar el dólar Matba Rofex en pyRofex.

Busca en el catálogo de instrumentos de ROFEX (get_detailed_instruments) los que
matcheen DOLAR / MTR / MRI / MATBA e imprime su SÍMBOLO EXACTO + underlying +
cficode + segment. Con ese dato se cablea la suscripción del `dolar_matba` de AGRO
(hoy manual). No suscribe ni escribe nada — solo lista.

Correr donde haya sesión ROFEX (Droplet):  python -m scripts.diag_rofex_dolar_mtr
"""
from __future__ import annotations

import pyRofex

from core.rofex_session import inicializar_sesion

# Substrings a matchear (case-insensitive) en symbol / underlying / cficode.
CLAVES = ("DOLAR", "MTR", "MRI", "MATBA", "DLR")


def _symbol(inst: dict) -> str:
    iid = inst.get("instrumentId") or {}
    return inst.get("symbol") or iid.get("symbol") or ""


def main() -> None:
    inicializar_sesion()
    res = pyRofex.get_detailed_instruments()
    instrumentos = (res or {}).get("instruments") or []
    print(f"total instrumentos en el catálogo: {len(instrumentos)}\n")

    vistos = 0
    for inst in instrumentos:
        sym = _symbol(inst)
        under = str(inst.get("underlying") or "")
        cfi = str(inst.get("cficode") or "")
        blob = f"{sym} {under} {cfi}".upper()
        if not any(k in blob for k in CLAVES):
            continue
        vistos += 1
        seg = (inst.get("segment") or {})
        print(
            f"symbol   = {sym!r}\n"
            f"  underlying = {under!r}\n"
            f"  cficode    = {cfi!r}\n"
            f"  segment    = {seg.get('marketSegmentId') if isinstance(seg, dict) else seg!r}\n"
            f"  market     = {(inst.get('instrumentId') or {}).get('marketId')!r}\n"
            f"  maturity   = {inst.get('maturityDate')!r}  currency={inst.get('currency')!r}\n"
        )

    print(f"\n{vistos} instrumentos matchearon {CLAVES}.")
    print("Pasame el `symbol` EXACTO del dólar Matba (el que muestra $1.487) y lo suscribo.")


if __name__ == "__main__":
    main()
