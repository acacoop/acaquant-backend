"""scripts/diag_a3_custodia_fci.py — ¿Dónde están las cuotapartes de FCI en A3?

READ-ONLY. `diag_a3_fci_posicion` ya midió lo importante y negativo (REGLA #2):
`PositionReport` devuelve SOLO derivados de cámara (NDF/Futuro/Opciones), CERO
FCI. La base `api.anywhereportfolio.com.ar` es el clearing de A3 Mercados /
ACyRSA, no la custodia de fondos. La tenencia de cuotapartes que el bot viejo
sacaba del export "gridOne" (con columna **Depositaria** y **Activo Cód.**) tiene
que salir de OTRO método del mismo catálogo.

Este diag prueba, en una sola corrida, los métodos de LECTURA candidatos a tener
custodia/tenencia de FCI, y para cada uno informa: si responde, qué forma tiene,
una muestra, y —lo que importa— si adentro aparece algo de **FCI / CAFCI / Fondo**
y con qué campo vendría el **código de fondo** para emparejar con AUNESA por
ficha (REGLA #9).

SEGURIDAD: solo toca métodos marcados LECTURA en `core/postrade_catalogo.py`. Cada
nombre se valida contra el catálogo antes de llamarlo; si alguno no fuera lectura,
se saltea. Los métodos de escritura (suscribir/rescatar FCI, etc.) no aparecen en
la lista y no hay forma de que se cuelen.

Uso (desde la raíz del repo):
    python -m scripts.diag_a3_custodia_fci
    python -m scripts.diag_a3_custodia_fci --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from core import postrade
from core.postrade_catalogo import LECTURA, POR_NOMBRE, metodo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEP = "=" * 78

# Candidatos a tener la tenencia/custodia de FCI. TODOS son de LECTURA (se
# revalida abajo). El orden es el de "más probable" según lo que trae el
# gridOne: primero los de custodia/depositaria, después saldos.
CANDIDATOS = (
    "DepositaryAccountList",   # el gridOne tiene columna "Depositaria"
    "CustodyRegistration",     # "activos en custodia"
    "AccountBalance",          # "balance de saldos"
    "CorporateActionCredits",  # "acreencias" (por descarte)
    "DigitalWalletStock",      # "stock de billetera digital"
)

# Tokens que delatarían que un método trae fondos.
PISTA_FCI = re.compile(r"FCI|CAFCI|Fondo|Cuotaparte|Cuota\s*parte", re.IGNORECASE)


def _muestra_items(valor, n: int) -> list:
    if isinstance(valor, list):
        return valor[:n]
    if isinstance(valor, dict):
        # A veces el payload viene envuelto: {"Something":[...]}. Devolvemos el
        # primer array que encontremos para poder mirarlo.
        for v in valor.values():
            if isinstance(v, list) and v:
                return v[:n]
        return [valor]
    return []


def _claves(items: list) -> dict:
    claves: dict[str, int] = {}
    for it in items:
        if isinstance(it, dict):
            for k in it:
                claves[k] = claves.get(k, 0) + 1
    return claves


def _forma(valor) -> str:
    if valor is None:
        return "null"
    if isinstance(valor, list):
        return f"lista de {len(valor)}"
    if isinstance(valor, dict):
        return f"dict({', '.join(list(valor)[:6])})"
    return type(valor).__name__


def probar(nombre: str, muestras: int) -> dict:
    m = POR_NOMBRE.get(nombre)
    if m is None:
        return {"metodo": nombre, "estado": "desconocido", "detalle": "no está en el catálogo"}
    if metodo(nombre).verbo != LECTURA:
        return {"metodo": nombre, "estado": "no_lectura", "detalle": "NO es lectura — salteado"}

    try:
        valor = postrade.leer(nombre, None)
    except postrade.PostradeNoHabilitado as e:
        return {"metodo": nombre, "estado": "no_habilitado", "detalle": str(e)[:180]}
    except (postrade.PostradeError, ValueError) as e:
        return {"metodo": nombre, "estado": "error", "detalle": str(e)[:180]}

    items = _muestra_items(valor, muestras)
    crudo_txt = json.dumps(valor, ensure_ascii=False)
    tiene_fci = bool(PISTA_FCI.search(crudo_txt))
    vacio = valor is None or (isinstance(valor, (list, dict, str)) and len(valor) == 0)
    return {
        "metodo": nombre,
        "path": m.path,
        "que_trae": m.que_trae,
        "estado": "vacio" if vacio else "con_datos",
        "forma": _forma(valor),
        "tiene_pista_fci": tiene_fci,
        "claves_item": _claves(items),
        "muestra": items,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Ubica la tenencia de FCI en los métodos de custodia de A3 (READ-ONLY)."
    )
    ap.add_argument("--muestras", type=int, default=3, help="items de ejemplo por método")
    ap.add_argument("--json", action="store_true", help="salida JSON en vez de texto")
    args = ap.parse_args()

    try:
        postrade.token()
    except postrade.PostradeError as e:
        print(f"✗ No se pudo obtener token: {e}")
        raise SystemExit(2) from None

    resultados = [probar(n, max(1, args.muestras)) for n in CANDIDATOS]

    if args.json:
        print(json.dumps(resultados, ensure_ascii=False, indent=2))
        return

    print(SEP)
    print("DIAG A3 — ¿dónde está la tenencia de FCI?  (READ-ONLY)")
    print(SEP)
    print(f"  Base URL : {postrade._base()}")
    print(f"  Candidatos (solo LECTURA): {', '.join(CANDIDATOS)}\n")

    for r in resultados:
        marca = "✓" if r.get("estado") == "con_datos" else ("○" if r.get("estado") == "vacio" else "✗")
        fci = "  ⭐ PISTA FCI" if r.get("tiene_pista_fci") else ""
        print(SEP)
        print(f"{marca} {r['metodo']}  ({r.get('path', '')}){fci}")
        print(f"    qué trae: {r.get('que_trae', '')}")
        print(f"    estado  : {r.get('estado')}  |  forma: {r.get('forma', r.get('detalle', ''))}")
        if r.get("claves_item"):
            print(f"    claves del item: {', '.join(r['claves_item'])}")
        for i, it in enumerate(r.get("muestra", []), 1):
            print(f"    [{i}] {json.dumps(it, ensure_ascii=False)[:600]}")

    print("\n" + SEP)
    print("RESUMEN")
    print(SEP)
    con_fci = [r["metodo"] for r in resultados if r.get("tiene_pista_fci")]
    con_datos = [r["metodo"] for r in resultados if r.get("estado") == "con_datos"]
    print(f"  Responden con datos : {', '.join(con_datos) or '(ninguno)'}")
    print(f"  Con pista de FCI    : {', '.join(con_fci) or '(ninguno)'}")
    print("\n  Si NINGUNO trae FCI, la tenencia de cuotapartes no está en esta API")
    print("  (el clearing de cámara) y el lado A3 se resuelve subiendo el Excel")
    print("  del gridOne — patrón import_tenencia.")


if __name__ == "__main__":
    main()
