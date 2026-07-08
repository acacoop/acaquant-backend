"""Service — Pase con Cobertura (AGRO).

Hogar de los cálculos del "Pase con Cobertura": una card por commodity × pase,
valuada SIEMPRE a hoy. Se construye por pasos junto con la mesa. Hoy solo vive
el primer derivado (Descuento a Tasa de Caución 7D → "Monto Pesos Cau 7D"); el
resto de la cadena (interés ON, tipo de cambio ON, compra USD/Tn, compra futuro,
ganancia; y la columna Pagaré/Sintético) se agrega en las siguientes iteraciones.

Fuentes de los inputs (todos manuales de la tab DATOS por ahora):
  - precio disponible por commodity → `camara_cereales.get_camara_cereales` (precio_ars)
  - tasa de caución a 7 días (TNA %)  → `camara_cereales.get_tasas_cobertura` (tasa_caucion_7d)
  - tipo de cambio Matba Rofex        → `camara_cereales.get_dolares_referencia` (dolar_matba)
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# Universo de commodities del Pase (TRIGO · MAÍZ · SOJA). La Cámara tiene 5
# cereales, pero el Pase con Cobertura se arma solo para estos 3.
PASE_COMMODITIES = ("TRIGO", "MAIZ", "SOJA")

# Convención de caución: la tasa es TNA en %, y el descuento es proporcional a
# 7 días sobre base 365.
_CAUCION_DIAS = 7
_ANIO_BASE = 365


def descuento_caucion_7d(precio_ars: float | None, tasa_caucion_7d: float | None) -> float | None:
    """Precio disponible descontado a la tasa de caución por 7 días.

        descuento = precio_ars × (1 − (tasa_caucion_7d/100) × 7/365)

    `tasa_caucion_7d` viene en % (ej. 21.5). Devuelve None si falta algún input.
    """
    if precio_ars is None or tasa_caucion_7d is None:
        return None
    factor = 1.0 - (tasa_caucion_7d / 100.0) * (_CAUCION_DIAS / _ANIO_BASE)
    return precio_ars * factor


def get_descuento_caucion() -> dict[str, Any]:
    """Tabla "Descuento a Tasa de Caución de 7D" (TRIGO/MAIZ/SOJA).

    Cruza el precio disponible manual (Cámara) con la tasa de caución 7D manual.

    Output:
        {
          "ts": datetime,
          "tasa_caucion_7d": float | None,   # TNA % usada
          "commodities": [
             {"commodity": "TRIGO", "precio_ars": float|None, "descuento_cau_7d": float|None},
             ...
          ]
        }
    """
    from api.services.camara_cereales import get_camara_cereales, get_tasas_cobertura

    tasa = get_tasas_cobertura().get("tasa_caucion_7d")
    by_cereal = {r["cereal"]: r for r in get_camara_cereales()["cereales"]}

    commodities: list[dict[str, Any]] = []
    for c in PASE_COMMODITIES:
        precio = (by_cereal.get(c) or {}).get("precio_ars")
        commodities.append({
            "commodity":        c,
            "precio_ars":       precio,
            "descuento_cau_7d": descuento_caucion_7d(precio, tasa),
        })

    return {
        "ts":              datetime.now(UTC),
        "tasa_caucion_7d": tasa,
        "commodities":     commodities,
    }
