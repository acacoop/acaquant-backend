"""Clasificación de eventos corporativos como `acreencia`.

Fija la regla de negocio del 2026-08-12: si `informacion` dice "dividend" ES un
dividendo, sin importar el subtipo. Antes la lista era de 3 nombres exactos y
"Stock dividend (DVSE)" caía en `categoria='otro'` con `op=NULL`.

El caso que NO puede romperse en la dirección contraria: un boleto de compra/
venta cuyo texto mencione la palabra sigue siendo compra/venta — los patrones
estructurales mandan sobre el match de palabra suelta.
"""
from __future__ import annotations

import pytest

from api.services.aunesa_negocio import categorizar, op_acreencia, parse_informacion


def _clasificar(info: str) -> tuple[str, str | None]:
    """(categoria, op) — el mismo par que persiste `jobs.negocio_movimientos`."""
    parsed = parse_informacion(info)
    return categorizar(info, parsed), (parsed or {}).get("op")


@pytest.mark.parametrize(("info", "op_esperada"), [
    # El caso que motivó el cambio.
    ("Liquidación 779664 - Stock dividend (DVSE) - s/YPFD", "Stock dividend"),
    # Los tres que ya funcionaban — no se rompen.
    ("Liquidación 123 - Cash dividend (DVCA) - s/GGAL", "Cash dividend"),
    ("Liquidación 124 - Interest payment (INTR) - s/AL30", "Interest payment"),
    ("Liquidación 125 - Partial redemption (PRED) - s/AL30", "Partial redemption"),
    # Variantes NUEVAS que antes caían en 'otro': las atrapa el genérico de
    # familia sin tener que agregar el subtipo exacto.
    ("Liquidación 126 - Optional dividend (DVOP) - s/PAMP", "Dividend"),
    ("Liquidación 127 - Final redemption (REDM) - s/TX26", "Redemption"),
    # Sin tildes / mayúsculas: `_normalizar` las aplana.
    ("LIQUIDACION 128 - STOCK DIVIDEND (DVSE) - s/YPFD", "Stock dividend"),
])
def test_evento_corporativo_es_acreencia(info: str, op_esperada: str) -> None:
    categoria, op = _clasificar(info)
    assert categoria == "acreencia"
    assert op == op_esperada


def test_ticker_se_extrae_del_sufijo() -> None:
    parsed = parse_informacion("Liquidación 779664 - Stock dividend (DVSE) - s/YPFD")
    assert parsed is not None
    assert parsed["ticker"] == "YPFD"


@pytest.mark.parametrize(("info", "categoria_esperada"), [
    # Un boleto real gana sobre el match de palabra suelta: el instrumento
    # podría llamarse "...Dividend..." y no por eso es un evento corporativo.
    ("Compra [DIVIDEND] 255,00@7835,00 (ARS 24hs)", "compra"),
    ("Venta [SDY Dividend ETF] 10,00@100,00 (USD 24hs)", "venta"),
    # Caución: se resuelve antes y no la toca el criterio de acreencia.
    ("Caución colocadora ARS 1.000,00@30,00% (ARS 7 días) (Apertura)", "caucion_col_ap"),
])
def test_boleto_no_se_convierte_en_acreencia(info: str, categoria_esperada: str) -> None:
    categoria, _ = _clasificar(info)
    assert categoria == categoria_esperada


def test_texto_sin_evento_no_es_acreencia() -> None:
    assert op_acreencia("Depósito en efectivo") is None
    assert _clasificar("Depósito en efectivo")[0] == "deposito"


def test_breakdown_del_pnl_pasivo_cubre_todas_las_ops() -> None:
    """`pnl._OPS_PASIVOS` sale de la misma tabla que el categorizador: ninguna
    op de acreencia puede caer en `breakdown_otros` por desincronización."""
    from api.services.pnl import _OPS_PASIVOS

    for info in ("Cash dividend (DVCA)", "Stock dividend (DVSE)",
                 "Interest payment (INTR)", "Partial redemption (PRED)",
                 "Optional dividend (DVOP)", "Final redemption (REDM)"):
        op = op_acreencia(info)
        assert op in _OPS_PASIVOS, f"{info} → op {op!r} fuera del breakdown"
