"""scripts/test_mae.py — smoke test del endpoint /mercado/repo del MAE.

Requiere MAE_API_KEY en .env. Pega 3 variantes de rango (hoy-hoy, últimos
3 días, sin params) y muestra el status + primeros items + shape del
response. Sirve para:
- Validar que la API key funciona.
- Descubrir el formato de params exacto (probable: YYYY-MM-DD; la doc no
  es explícita).
- Ver qué campos trae cada operación de repo (clave upsert, plazo, TNA,
  volumen, etc).

Uso:
    /root/TradingAV/venv/bin/python -m scripts.test_mae
    /root/TradingAV/venv/bin/python -m scripts.test_mae --fecha 2026-04-18
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from core.mae import MaeError, MaeNotConfigured, get_repo


def _pretty(obj: Any, max_chars: int = 3000) -> str:
    s = json.dumps(obj, indent=2, default=str, ensure_ascii=False)
    return s[:max_chars] + (
        f"\n... ({len(s) - max_chars} chars más, truncado)"
        if len(s) > max_chars else ""
    )


def _header(title: str) -> None:
    print(f"\n── {title} ".ljust(78, "─"))


def _sample(data: Any, tag: str) -> None:
    """Resume lo que llegó sin inundar el log."""
    print(f"\n  tipo raíz: {type(data).__name__}")

    if isinstance(data, dict):
        print(f"  keys top-level: {list(data.keys())}")
        # Si hay una key que parece la lista de resultados, resumirla.
        for key in ("result", "data", "results", "items", "operations"):
            if key in data and isinstance(data[key], list):
                arr = data[key]
                print(f"  {tag}: key '{key}' tiene {len(arr)} items")
                if arr:
                    print(f"  primer item ({tag}):")
                    print(_pretty(arr[0], 800))
                return
        # Si es un dict sin envelope obvio, imprimir entero (truncado)
        print(f"\n  response completo ({tag}):")
        print(_pretty(data, 2500))
        return

    if isinstance(data, list):
        print(f"  {tag}: lista directa con {len(data)} items")
        if data:
            print(f"  primer item ({tag}):")
            print(_pretty(data[0], 800))
        if len(data) > 1:
            print(f"  segundo item ({tag}):")
            print(_pretty(data[1], 500))
        return

    print(f"  response atípico ({tag}):")
    print(_pretty(data, 1500))


def _intentar(label: str, **kwargs) -> Any:
    _header(label)
    print(f"  params: {kwargs}")
    try:
        out = get_repo(**kwargs)
        _sample(out, label)
        return out
    except MaeError as e:
        print(f"  ✗ {type(e).__name__}: {e}")
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fecha",
        default=None,
        help="YYYY-MM-DD de un día específico (default: probar varias variantes)",
    )
    args = parser.parse_args()

    print("=" * 78)
    print(" MAE MarketData — smoke test /mercado/repo")
    print("=" * 78)

    # Chequeo de config
    try:
        _ = get_repo.__module__  # tocar algo del módulo; _ensure_configured corre dentro
    except Exception as e:
        print(f"  error importando core.mae: {e}")
        return 1

    # Si el user pidió fecha explícita
    if args.fecha:
        _intentar(f"Fecha explícita {args.fecha}", desde=args.fecha, hasta=args.fecha)
        print("\n" + "=" * 78)
        print(" Fin. Pegame el output completo.")
        print("=" * 78)
        return 0

    # 3 variantes: sin params, rango hoy, rango últimos 3 días
    hoy = datetime.now(UTC).date()
    ayer = hoy - timedelta(days=1)
    hace_3 = hoy - timedelta(days=3)

    first: Any | None = None

    try:
        first = _intentar("Variante A: sin params (lo que sea que devuelva por default)")
    except MaeNotConfigured as e:
        print(f"\n✗ {e}")
        print("Agregá MAE_API_KEY a /root/TradingAV/.env y volvé a correr.")
        return 1

    _intentar(
        "Variante B: ayer completo",
        desde=ayer.isoformat(),
        hasta=ayer.isoformat(),
    )

    _intentar(
        "Variante C: últimos 3 días",
        desde=hace_3.isoformat(),
        hasta=hoy.isoformat(),
    )

    # Variante extra si la A no trajo nada o da formato distinto: probar
    # formato DD/MM/YYYY por las dudas
    _intentar(
        "Variante D: formato DD/MM/YYYY (por compatibilidad)",
        desde=ayer.strftime("%d/%m/%Y"),
        hasta=ayer.strftime("%d/%m/%Y"),
    )

    print("\n" + "=" * 78)
    print(" Fin. Pegame todo el output (sobre todo el shape de la variante")
    print(" que devolvió 200). Con eso diseño el schema del rollup diario.")
    print("=" * 78)
    _ = first  # evita warning si ninguna variante retornó
    return 0


if __name__ == "__main__":
    sys.exit(main())
