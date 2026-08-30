"""Capa de servicio — portfolio / AuM / FCI: helpers PUROS compartidos.

Los lectores de AuM (`listar_aum`/`fci_*`/`total_*`/`listar_cuentas`) eran la rama
MONGO sobre `Valuaciones.AuM` (DROPEADA 2026-06-15) y se ELIMINARON: esas vistas son
SQL-native (`api/services/portfolio_sql.py`, flag `PORTFOLIO_SQL` ya en prod). Quedan
acá los helpers que todavía consumen otros services/tests — todos leen SQL
`portafolio.assets` vía `assets_rows`, NO Mongo:
  - `_fci_assets_map`: unidad → {emisor, ticker, fee} de assets FCI. Lo usa `comercial.py`
    (REFERIDOS).
  - `_assets_enrich_map`: unidad → {cartera, clase_activo}.
  - `_valuacion_api`: regla de valuación P×Q vs P×Q/100 (lo usa un test + el motor PnL).
"""
from __future__ import annotations

from api.cache import cached
from api.services.assets_sql import assets_rows


@cached(ttl=600)
def _fci_assets_map() -> dict[str, dict]:
    """Mapea unidad → {emisor, ticker, fee} para unidades con CARTERA=FCI, desde SQL
    `portafolio.assets` (vía `assets_rows`). Cacheado 10 min — los assets FCI cambian
    como mucho mensualmente. `fee` = honorario anual (fracción), para REFERIDOS."""
    return {
        a["unidad"]: {"emisor": a["EMISOR"], "ticker": a["TICKER"], "fee": a["FEE_ADMIN"]}
        # tolera el rename de cartera: 'FCI' (nuevo) y 'CARTERA FCI' (legacy).
        for a in assets_rows(["CARTERA", "EMISOR", "TICKER", "FEE_ADMIN"])
        if a["unidad"] and a["CARTERA"] in ("FCI", "CARTERA FCI")
    }


def _valuacion_api(cant: float, px: float, cartera: str, clase_activo: str) -> float:
    """Regla de valuación: FCI o clase OTROS → P×Q directo, resto → P×Q/100."""
    if clase_activo == "OTROS" or "FCI" in cartera:
        return cant * px
    return cant * px / 100
