"""scripts/diag_ap5_margenes.py — ¿de dónde salen "Márgenes" y "Activo Integrado"?

READ-ONLY. Solo GET, nada de escritura (`postrade.leer`, nunca `escribir`).

**Qué contesta.** El reporte de la mesa tiene tres números en la cabecera:

    Diferencias ACA HOY  ·  Requerimiento de Márgenes  ·  Activo Integrado

El primero ya lo tenemos (`ap5.portfolio.daily_settlement`). Los otros dos NO
están en ninguna tabla nuestra: la API los publica, pero **nunca los pedimos**.

Y no se puede elegir el método leyendo el manual: el manual documenta ~40
lecturas y **qué contesta NUESTRO usuario es otra cosa** (REGLA #2). Por eso
esto sondea los candidatos y muestra qué devuelve cada uno, para decidir con el
dato en la mano en vez de con una hipótesis.

**Los candidatos, y por qué cada uno.** Del índice del manual (sección
Garantías, pág. 103-121) y del catálogo `core/postrade_catalogo.py`:

    MarginRequirementReport          márgenes requeridos          ← el candidato
    DeliveryMarginRequirementReport  márgenes por entrega
    MarginBalance (Risk/)            saldos por finalidad
    AccountBalance                   balance de saldos            ← el candidato
    MT506                            garantías
    CollateralList                   activos aceptados en garantía
    CollateralAssignment             distribución de activos

⚠️ Cada llamada cuesta. La API de Postrade tiene throttle de **1 petición por
segundo** (lo aplica `core/postrade.py` global entre procesos), así que sondear
los siete tarda unos segundos — no es gratis pero es una sola vez.

Uso:
    python -m scripts.diag_ap5_margenes
    python -m scripts.diag_ap5_margenes --fecha 20260824
    python -m scripts.diag_ap5_margenes --cuenta 155235
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from core import postrade
from jobs.ap5_portfolio import ultimo_dia_habil

# Qué sondear y con qué. `None` = probar sin parámetros primero.
CANDIDATOS: list[tuple[str, str]] = [
    ("MarginRequirementReport", "márgenes requeridos — el candidato para REQUERIMIENTO DE MÁRGENES"),
    ("DeliveryMarginRequirementReport", "márgenes por entrega de mercadería"),
    ("MarginBalance", "saldos por finalidad (Risk/)"),
    ("AccountBalance", "balance de saldos — el candidato para ACTIVO INTEGRADO"),
    ("MT506", "garantías"),
    ("CollateralList", "activos aceptados en garantía"),
    ("CollateralAssignment", "distribución de activos"),
]


def _forma(v: Any, prof: int = 0) -> str:
    """Describe la FORMA de la respuesta sin volcarla entera.

    Lo que hace falta para decidir es el grano (¿una fila? ¿una por cuenta?) y
    los nombres de los campos — no 400 filas de contenido.
    """
    if isinstance(v, list):
        if not v:
            return "lista VACÍA"
        return f"lista de {len(v)} → cada elemento: {_forma(v[0], prof + 1)}"
    if isinstance(v, dict):
        if not v:
            return "dict vacío"
        claves = list(v)[:14]
        cola = "" if len(v) <= 14 else f" … (+{len(v) - 14})"
        return "{" + ", ".join(claves) + cola + "}"
    return type(v).__name__


def _numericos(v: Any) -> list[str]:
    """Los campos NUMÉRICOS del primer elemento: son los candidatos a ser el
    número de la card. Un campo de texto no es un importe."""
    d = v[0] if isinstance(v, list) and v else v
    if not isinstance(d, dict):
        return []
    return [f"{k}={d[k]}" for k in d
            if isinstance(d[k], (int, float)) and not isinstance(d[k], bool)]


def _sondear(nombre: str, para_que: str, params: dict) -> None:
    print(f"\n─── {nombre} ─────────────────────────────────────────")
    print(f"    {para_que}")
    print(f"    params: {params or '(ninguno)'}")
    try:
        r = postrade.leer(nombre, params or None)
    except Exception as e:  # el objetivo ES ver qué falla: un ✗ acá es el dato
        # Un 404/403 acá NO es un incidente: es el dato que vinimos a buscar
        # (qué nos habilitaron de verdad).
        print(f"    ✗ {type(e).__name__}: {str(e)[:220]}")
        return

    print(f"    ✓ forma: {_forma(r)}")
    nums = _numericos(r)
    if nums:
        print(f"    números: {', '.join(nums[:10])}")
    muestra = (r[0] if isinstance(r, list) and r else r)
    if isinstance(muestra, dict):
        print("    muestra:")
        print("      " + json.dumps(muestra, ensure_ascii=False, default=str)[:600])


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Sondea los métodos de Garantías/Saldos de Postrade (read-only).")
    ap.add_argument("--fecha", help="AAAAMMDD (default: último día hábil)")
    ap.add_argument("--cuenta", help="probar además con accountCode")
    args = ap.parse_args()

    f = postrade.fecha_api(args.fecha) if args.fecha else ultimo_dia_habil()
    print("=" * 74)
    print("AP5 · de dónde salen REQUERIMIENTO DE MÁRGENES y ACTIVO INTEGRADO")
    print(f"fecha de prueba: {f}" + (f" · cuenta: {args.cuenta}" if args.cuenta else ""))
    print("=" * 74)
    print("\nSondeo READ-ONLY. Un ✗ NO es un problema: es el dato que buscamos")
    print("(qué métodos nos habilitaron de verdad). Throttle: 1 req/s.\n")

    for nombre, para_que in CANDIDATOS:
        # Sin parámetros primero: si el método los necesita, el error lo dice y
        # eso también es información — mejor que adivinar qué pedirle.
        _sondear(nombre, para_que, {})
        if args.cuenta:
            _sondear(nombre, para_que + "  [con cuenta]", {"accountCode": args.cuenta})
        _sondear(nombre, para_que + "  [con fecha]", {"clearingBusinessDate": postrade.fecha_api(f)})

    print("\n" + "=" * 74)
    print("Qué mirar en la salida:")
    print("  · Cuál responde con datos (✓ y una lista NO vacía).")
    print("  · El GRANO: ¿una fila para todo, o una por cuenta/moneda?")
    print("  · Los campos NUMÉRICOS: ahí está el importe de la card.")
    print("  · La MONEDA de cada importe — el reporte no suma Pesos con Dólar MtR.")
    print("\nCon eso se decide qué persistir y se arma el job, igual que ap5_portfolio.")


if __name__ == "__main__":
    main()
