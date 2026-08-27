"""Service — Mejoras Precio Disponible (Agro).

Replica la planilla que arma la mesa para proponerle al productor:
*"En vez de pagarte hoy en pesos, te colocás en una LECAP a X días → al
vto cobrás capital + interés, y si encima cubrís con un futuro DLR del
mismo mes, te llevás esos pesos a USD locked."*

Tres bloques (SOJA / MAIZ / TRIGO). Para cada uno, una fila por cada
LECAP / BONCAP vigente:

    tna            = TEM × 12,  TEM = (1 + TEA)^(1/12) − 1   (quant.tasas)
    tasa_directa   = TNA × días / 365             (lineal)
    tasa_diaria    = (1 + tasa_directa)^(1/días) − 1
    interes_ganado = precio_ars × tasa_directa
    valor_final    = precio_ars + interes_ganado
    valor_usd      = valor_final / Px_Futuro_DLR_del_mismo_mes
                      (None si descalce > MAX_DESCALCE_DIAS)

`precio_ars` (Cámara) y la **TEA** (MarketSnapshot) las resuelve el lector
SQL-native `agro_sql.get_mejoras_dispo`. Match LECAP ↔ futuro DLR por (año, mes),
igual que en `sinteticos`.

⚠️ **La columna TNA es TNA — se DERIVA, no se toma cruda (corregido 2026-08-26).**
Hasta acá la fila publicaba en `tna` el valor tal cual salía de
`mercado.market_snapshot`, que es la **TEA** (la TIR efectiva anual del XIRR:
`engines/curvas.py` escribe TEA y nada más). O sea: encabezado TNA, número TEA.
Nada fallaba y nada quedaba en `--` — la columna simplemente decía otra cosa de
la que dice el mismo bono en RENTA FIJA, donde la TNA sí se deriva (TEM×12) desde
siempre. Medido en pantalla: una Lecap con TEA 29,34% mostraba 29,34% acá y 26,01%
allá. La conversión vive UNA sola vez, en `quant.tasas.tna_desde_tea`, para que no
pueda volver a haber dos TNAs para el mismo papel.

Y arrastraba: `tasa_directa` prorrateaba LINEALMENTE una tasa EFECTIVA
(`TEA × días/365`), que no es ninguna de las dos convenciones — sobreestimaba el
interés (TEA 30% a 180 días: 14,79% contra 13,08% de la convención lineal). Ahora
lo lineal se le aplica a una tasa nominal, que es para lo que existe una TNA.
Además viaja `rendimiento_efectivo` = (1+TEA)^(días/365) − 1, que es lo que la
Lecap rinde EXACTO al vencimiento (13,81% en el mismo ejemplo): no se muestra como
columna, pero está en el payload para que la mesa pueda contrastar la convención
contra la plata real sin recalcular a mano.

Decomiso Mongo: la lectura `get_mejoras_dispo` (que leía Derivados.CamaraCereales
+ Trading.FuturosDLRSnapshot) se borró — vivía muerta vía router (el router usa
`agro_sql`). Este módulo queda como helpers PUROS (`_build_filas`, `_parse_yyyymmdd`,
`_to_date`, `COMMODITIES`, `MAX_DESCALCE_DIAS`) que reusa `agro_sql`. NO borrarlos.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from quant.tasas import rendimiento_al_plazo, tna_desde_tea

# Solo estos 3 commodities matchean con la cosecha local + futuros DLR.
# GIRASOL y SORGO existen en la Cámara pero por ahora no se proponen.
COMMODITIES = ("SOJA", "MAIZ", "TRIGO")

# Descalce máximo (días entre vto LECAP y vto futuro DLR) para mostrar el
# valor en US$. Por encima de eso la cobertura tiene gap operativo y la
# planilla pone N/A.
MAX_DESCALCE_DIAS = 10


def _parse_yyyymmdd(s: str | None) -> date | None:
    if not s or len(s) != 8:
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except (TypeError, ValueError):
        return None


def _to_date(d: Any) -> date | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        try:
            return datetime.fromisoformat(d[:10]).date()
        except ValueError:
            return None
    return None


def _build_filas(
    lecaps: list[dict],
    tea_map: dict[str, float],
    fut_by_ym: dict[tuple[int, int], dict],
    precio_ars: float | None,
    today: date,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in lecaps:
        vto = _to_date(c.get("fecha_vencimiento"))
        if not vto or vto <= today:
            continue
        dias = (vto - today).days
        if dias <= 0:
            continue
        # `tea_map` trae la TEA del snapshot (es lo único que publica el motor).
        # La TNA se DERIVA con la convención de la casa — misma fórmula que
        # RENTA FIJA, así el mismo papel no muestra dos tasas según la pantalla.
        tea = tea_map.get(c.get("ticker", ""))
        tna = tna_desde_tea(tea)

        # Tasa lineal al vto (sobre los días), prorrateando la TNA — que es para
        # lo que existe una tasa NOMINAL. Si falta la tasa, las derivadas quedan
        # en None, pero la fila igual aparece: el trader ve el universo completo.
        tasa_directa = (tna * dias / 365) if tna is not None else None
        tasa_diaria = (
            (1 + tasa_directa) ** (1 / dias) - 1
            if tasa_directa is not None and dias > 0
            else None
        )

        interes = (
            precio_ars * tasa_directa
            if (precio_ars is not None and tasa_directa is not None)
            else None
        )
        valor_final = (
            precio_ars + interes
            if (precio_ars is not None and interes is not None)
            else None
        )

        # Match con futuro DLR del mismo mes — descalce > MAX → None.
        fut = fut_by_ym.get((vto.year, vto.month))
        fvto = _parse_yyyymmdd(fut.get("vencimiento")) if fut else None
        descalce = abs((fvto - vto).days) if fvto else None
        px_fut = fut.get("last_price") if fut else None
        if (
            valor_final is not None
            and px_fut
            and descalce is not None
            and descalce <= MAX_DESCALCE_DIAS
        ):
            valor_usd = valor_final / px_fut
        else:
            valor_usd = None

        out.append({
            "ticker":         c.get("ticker_corto"),
            "ticker_largo":   c.get("ticker"),
            "vencimiento":    vto.isoformat(),
            "dias":           dias,
            "tea":            tea,
            "tna":            tna,
            # Lo que rinde EXACTO la Lecap al vto (capitalizado). No es la
            # convención de la planilla; viaja para poder contrastarla.
            "rendimiento_efectivo": rendimiento_al_plazo(tea, dias),
            "tasa_diaria":    tasa_diaria,
            "tasa_directa":   tasa_directa,
            "interes_ganado": interes,
            "valor_final":    valor_final,
            "futuro_ticker":  fut.get("ticker") if fut else None,
            "futuro_px":      px_fut,
            "descalce":       descalce,
            "valor_usd":      valor_usd,
        })
    out.sort(key=lambda r: r["vencimiento"])
    return out
