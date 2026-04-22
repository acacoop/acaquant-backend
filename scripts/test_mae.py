"""scripts/test_mae.py — smoke test del endpoint /api/v1/mercado/cotizaciones/repo.

Requiere MAE_API_KEY en .env. Pega páginas 1-3 del endpoint REPO y muestra
el shape del response.

Uso:
    /root/TradingAV/venv/bin/python -m scripts.test_mae
    /root/TradingAV/venv/bin/python -m scripts.test_mae --page 2
    /root/TradingAV/venv/bin/python -m scripts.test_mae --all   # itera todas
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Any

from core.mae import MaeError, MaeNotConfigured, get_repo, iter_repo_pages


def _pretty(obj: Any, max_chars: int = 3000) -> str:
    s = json.dumps(obj, indent=2, default=str, ensure_ascii=False)
    return s[:max_chars] + (
        f"\n... ({len(s) - max_chars} chars más, truncado)"
        if len(s) > max_chars else ""
    )


def _header(title: str) -> None:
    print(f"\n── {title} ".ljust(78, "─"))


def _resumir_pagina(data: Any, tag: str) -> int:
    """Imprime resumen + primeros items. Devuelve cantidad."""
    print(f"\n  tipo raíz ({tag}): {type(data).__name__}")
    if isinstance(data, dict):
        print(f"  keys top-level: {list(data.keys())}")
        print(_pretty(data, 2500))
        return 0
    if not isinstance(data, list):
        print(f"  response atípico: {_pretty(data, 1500)}")
        return 0

    n = len(data)
    print(f"  {tag}: lista con {n} items")
    if not data:
        return 0

    # Muestro primeros 2 items completos
    print(f"\n  primer item ({tag}):")
    print(_pretty(data[0], 1200))
    if n > 1:
        print(f"\n  segundo item ({tag}):")
        print(_pretty(data[1], 1200))

    # Stats rápidas
    monedas = Counter(d.get("moneda") for d in data if isinstance(d, dict))
    plazos = Counter(d.get("plazo") for d in data if isinstance(d, dict))
    ruedas = Counter(d.get("rueda") for d in data if isinstance(d, dict))
    fechas = sorted({(d.get("fecha") or "")[:10] for d in data if isinstance(d, dict)})
    print(f"\n  stats {tag}:")
    print(f"    monedas: {dict(monedas)}")
    print(f"    plazos:  {dict(plazos)}")
    print(f"    ruedas:  {dict(ruedas)}")
    print(f"    fechas distintas: {len(fechas)}  (ej: {fechas[:5]}...)")
    return n


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", type=int, default=None, help="Página específica")
    parser.add_argument(
        "--all", action="store_true",
        help="Iterar todas las páginas hasta que venga vacía (máx 50)",
    )
    args = parser.parse_args()

    print("=" * 78)
    print(" MAE MarketData — smoke test /api/v1/mercado/cotizaciones/repo")
    print("=" * 78)

    try:
        if args.page is not None:
            _header(f"Página {args.page}")
            data = get_repo(page=args.page)
            _resumir_pagina(data, f"p{args.page}")
            return 0

        if args.all:
            _header("Iterando TODAS las páginas (máx 50)")
            total = 0
            for i, page in enumerate(iter_repo_pages(max_pages=50), start=1):
                n = _resumir_pagina(page, f"p{i}") if i <= 2 else len(page)
                if i > 2 and isinstance(page, list):
                    print(f"  página {i}: {n} items (skip detalle)")
                total += n
            print(f"\n  TOTAL items across pages: {total}")
            return 0

        # Default: páginas 1 y 2
        _header("Página 1 (default)")
        data1 = get_repo(page=1)
        n1 = _resumir_pagina(data1, "p1")

        if n1:
            _header("Página 2")
            try:
                data2 = get_repo(page=2)
                _resumir_pagina(data2, "p2")
            except MaeError as e:
                print(f"  ✗ p2 falló: {type(e).__name__}: {e}")

    except MaeNotConfigured as e:
        print(f"\n✗ {e}")
        print("Agregá MAE_API_KEY a /root/TradingAV/.env y volvé a correr.")
        return 1
    except MaeError as e:
        print(f"\n✗ {type(e).__name__}: {e}")
        return 2

    print("\n" + "=" * 78)
    print(" Fin. Pegame el output — con el shape real + volumen típico")
    print(" diseño schema Trading.RepoMAE + job con cron diario.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
