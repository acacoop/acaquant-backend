"""scripts/seed_volumen_agro.py — SEED one-shot: volumen del MERCADO agro de Jul/2026.

Carga el denominador del share (tab SHARE DE MERCADO de NEGOCIO → OPERACIONES → AGRO)
con los datos que pasó la mesa. Idempotente (upsert por (periodo, commodity)).

BORRAR después de correrlo (REGLA #5). Para los meses que vengan:
`python -m scripts.cargar_volumen_agro --periodo YYYY-MM --soja N --trigo N --maiz N`.

Uso:
    python -m scripts.cargar_volumen_agro --listar   # antes: ver qué había
    python -m scripts.seed_volumen_agro
    python -m scripts.cargar_volumen_agro --listar   # después: verificar
"""
from __future__ import annotations

from scripts.cargar_volumen_agro import _upsert

DATOS: list[tuple[str, str, float]] = [
    ("2026-07", "TRIGO", 2_095_260),
    ("2026-07", "MAIZ", 3_578_700),
    ("2026-07", "SOJA", 3_689_090),
]


def main() -> int:
    n = _upsert(DATOS)
    for periodo, commodity, toneladas in DATOS:
        print(f"OK {periodo} {commodity:<5} {toneladas:,.0f} t")
    print(f"\n{n} filas upserteadas en mercado.volumen_mercado_agro.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
