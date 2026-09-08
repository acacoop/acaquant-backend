"""scripts/diag_a3_stock_fci.py — CONFIRMA la fuente A3 de tenencia de cuotapartes.

READ-ONLY. Cierra la búsqueda: el manual de la API (Anexo "Registro de
Cuotapartes", pág. 157-158) dice que el stock de cuotapartes de FCI por cuenta
se consulta con **GET /PosTrade/DigitalWalletStock**, y su respuesta trae, por
fila: `NettingAccount` / `ExternalAccountCode` (la cuenta), `InstrumentCode`
(el **código CAFCI** del FCI — pág. 156: "AssetCode = Código CAFCI del FCI"),
`Qty` (cuotapartes) y `ReferenceCode` (7 = Tenencia).

Los diags anteriores lo llamaron SIN parámetros y dieron 400 — eso era llamarlo
mal, no ausencia de FCI (REGLA #2). Acá se llama BIEN: `date` (t-1 hábil por
defecto), `referenceCode=7` (Tenencia) y paginación, recorriendo todas las
páginas.

Qué contesta:
  1. ¿Cuántas filas de stock de cuotapartes hay a t-1, y en cuántas cuentas?
  2. Los `InstrumentCode` distintos — ¿son los códigos CAFCI (el `1133` que ya
     verifiqué que matchea con el segundo número del CAFCI de AUNESA)?
  3. Muestra de filas crudas, para ver el shape real antes de diseñar el job.

Es LECTURA pura (`DigitalWalletStock` está marcado LECTURA en el catálogo). No
persiste nada.

Uso (desde la raíz del repo):
    python -m scripts.diag_a3_stock_fci
    python -m scripts.diag_a3_stock_fci --fecha 20260904
    python -m scripts.diag_a3_stock_fci --json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date

from core import postrade
from core.calendario import restar_habiles
from core.postrade_catalogo import LECTURA, metodo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEP = "=" * 78
METODO = "DigitalWalletStock"
REFERENCE_TENENCIA = 7
PAGE_SIZE = 500
MAX_PAGINAS = 200  # cortafuegos: 200 × 500 = 100k filas, de sobra


def _valor_lista(crudo) -> list:
    """La API envuelve en {Status, Code, Value:[...]}. `postrade.leer` ya
    desempaqueta el sobre, así que acá `crudo` debería ser la lista `Value`.
    Se tolera igual el caso envuelto por las dudas."""
    if isinstance(crudo, list):
        return crudo
    if isinstance(crudo, dict):
        v = crudo.get("Value")
        if isinstance(v, list):
            return v
    return []


def traer_stock(fecha: str) -> tuple[list, list[dict]]:
    """Recorre todas las páginas de DigitalWalletStock (Tenencia). Devuelve
    (filas, traza_de_paginas). PURA salvo la llamada a la API (LECTURA)."""
    filas: list = []
    traza: list[dict] = []
    for pagina in range(1, MAX_PAGINAS + 1):
        crudo = postrade.leer(METODO, {
            "date": fecha,
            "referenceCode": REFERENCE_TENENCIA,
            "viewDetails": "false",
            "pageNumber": pagina,
            "pageSize": PAGE_SIZE,
        })
        lote = _valor_lista(crudo)
        traza.append({"pagina": pagina, "filas": len(lote)})
        filas.extend(lote)
        if len(lote) < PAGE_SIZE:
            break
    return filas, traza


def resumir(filas: list) -> dict:
    instrumentos: Counter = Counter()
    cuentas: set = set()
    referencias: Counter = Counter()
    for f in filas:
        if not isinstance(f, dict):
            continue
        instrumentos[str(f.get("InstrumentCode"))] += 1
        cuentas.add(str(f.get("NettingAccount") or f.get("ExternalAccountCode")))
        referencias[str(f.get("ReferenceCode"))] += 1
    return {
        "total_filas": len(filas),
        "cuentas_distintas": len(cuentas),
        "instrumentos_distintos": len(instrumentos),
        "por_reference_code": dict(referencias.most_common()),
        "instrumentos_top": instrumentos.most_common(15),
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Confirma la tenencia de cuotapartes de FCI en A3 (READ-ONLY)."
    )
    ap.add_argument("--fecha", help="AAAAMMDD; por defecto t-1 hábil")
    ap.add_argument("--muestras", type=int, default=5, help="filas de ejemplo")
    ap.add_argument("--json", action="store_true", help="salida JSON")
    args = ap.parse_args()

    # Guarda de seguridad: no llamar nada que no sea LECTURA.
    if metodo(METODO).verbo != LECTURA:
        print(f"✗ {METODO} no es LECTURA — abortando por seguridad.")
        raise SystemExit(2)

    fecha = args.fecha or postrade.fecha_api(restar_habiles(date.today(), 1))

    try:
        filas, traza = traer_stock(fecha)
    except postrade.PostradeError as e:
        print(f"✗ DigitalWalletStock falló: {e}")
        raise SystemExit(2) from None

    resumen = resumir(filas)
    muestra = [f for f in filas[: max(1, args.muestras)] if isinstance(f, dict)]

    if args.json:
        print(json.dumps(
            {"fecha": fecha, "paginas": traza, "resumen": resumen, "muestra": muestra},
            ensure_ascii=False, indent=2,
        ))
        return

    print(SEP)
    print("DIAG A3 — tenencia de cuotapartes de FCI (DigitalWalletStock)  READ-ONLY")
    print(SEP)
    print(f"  Base URL : {postrade._base()}")
    print(f"  Fecha    : {fecha}  (t-1 hábil salvo --fecha)")
    print(f"  Método   : GET PosTrade/{METODO}  (referenceCode=7 Tenencia)\n")

    print(f"  Páginas leídas: {len(traza)}  (última con {traza[-1]['filas'] if traza else 0} filas)")
    print(f"  Filas totales      : {resumen['total_filas']}")
    print(f"  Cuentas distintas  : {resumen['cuentas_distintas']}")
    print(f"  Instrumentos (FCI) : {resumen['instrumentos_distintos']}")
    print(f"  ReferenceCode      : {resumen['por_reference_code']}")

    print("\n  InstrumentCode más frecuentes (¿son códigos CAFCI?):")
    for cod, n in resumen["instrumentos_top"]:
        print(f"    {cod:<12} en {n} cuentas")

    print("\n  Muestra de filas crudas:")
    for i, f in enumerate(muestra, 1):
        print(f"    [{i}] {json.dumps(f, ensure_ascii=False)}")

    print("\n  Qué confirmar:")
    print("   - InstrumentCode debe ser el código CAFCI (segundo número del")
    print("     'CAFCIxxxx-YYYY' de AUNESA). Con eso el pareo por ficha es directo.")
    print("   - Qty = cuotapartes; NettingAccount/ExternalAccountCode = la cuenta.")


if __name__ == "__main__":
    main()
