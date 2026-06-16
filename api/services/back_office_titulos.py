"""Service — Títulos / Mercado (Back Office).

Calcula qué títulos hay que **enviar al mercado** hoy y cuáles hay que
**recibir**, a partir de las operaciones de clientes que ya tenemos en
`CashFlow.NegocioMovimientos`. La lógica es plata-y-papeles del lado del
back office:

    settlement HOY = ops de HOY  con plazo "CI" / "Inm"   (T+0)
                   + ops de AYER hábil con plazo "24hs"   (T+1)

Para cada match: `op == "Venta"` → se ENVÍA al mercado; `op == "Compra"`
→ se RECIBE. Quantity = `abs(cantidad)` (robusto al signo del feed).

Cache 10s: durante la rueda el chequeo se hace muchas veces y el cómputo
es barato pero la consulta a Mongo suma.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

import holidays
from psycopg.rows import dict_row

from api.cache import cached
from core.postgres import get_pool

# Plazos que liquidan en el mismo día de la operación (T+0).
PLAZOS_T0 = ("CI", "Inm")
# Plazos que liquidan al día hábil siguiente (T+1).
PLAZOS_T1 = ("24hs",)

# Categorías que generan obligación de envío/recepción con el mercado.
# Las demás (comision, acreencia, suscripcion_fci, rescate_fci, etc.) no
# pasan por el broker hacia BYMA/MAE — quedan afuera.
CATEGORIAS_MERCADO = ("compra", "venta")

# Calendar de feriados de Argentina, cacheado al import (la lib genera el
# año actual on-demand).
_AR_HOLIDAYS = holidays.Argentina()


def _is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _AR_HOLIDAYS


def _previous_business_day(d: date) -> date:
    prev = d - timedelta(days=1)
    while not _is_business_day(prev):
        prev -= timedelta(days=1)
    return prev


_CUENTA_ID_RE = re.compile(r"^\[(\d+)\]")


def _id_cuenta_from_label(cuenta: str | None) -> str | None:
    """`[916] MORDEGLIA BAUTISTA` → `916`. None si no matchea."""
    if not cuenta:
        return None
    m = _CUENTA_ID_RE.match(cuenta)
    return m.group(1) if m else None


@cached(ttl=10)
def get_titulos_mercado(fecha: str | None = None) -> dict[str, Any]:
    """Devuelve los títulos a enviar/recibir al mercado para `fecha` (ISO,
    default hoy). Si la fecha no es día hábil, devuelve estructura vacía
    con flag `mercado_cerrado=True`.

    Output:
        {
          "fecha":            "2026-05-19",
          "dia_anterior":     "2026-05-18",
          "mercado_cerrado":  bool,
          "ts":               datetime,
          "tickers": [
            {
              "ticker": "S29Y6",
              "enviar_qty":  10000,   "enviar_importe":  12940000,
              "recibir_qty":  8000,   "recibir_importe": 10353000,
              "neto_qty":     2000,
              "n_ops": 5,
              "cuentas": [
                {
                  "id_cuenta": "916", "cuenta": "[916] MORDEGLIA BAUTISTA",
                  "op": "Venta", "plazo": "24hs", "cantidad": 8000,
                  "importe": 1034312.23, "precio": 129.28, "comprobante": "BOL...",
                  "fecha": "2026-05-18",
                },
                ...
              ]
            }, ...
          ],
          "totales": {
            "n_ops":        15,
            "enviar_qty":   42000,   "enviar_importe":  54320000,
            "recibir_qty":  28000,   "recibir_importe": 36210000,
          },
          "plazos_desconocidos": [...]  # strings de plazo que no son CI/Inm/24hs
        }
    """
    hoy = (
        datetime.fromisoformat(fecha).date()
        if fecha
        else date.today()
    )
    if not _is_business_day(hoy):
        return {
            "fecha":           hoy.isoformat(),
            "dia_anterior":    None,
            "mercado_cerrado": True,
            "ts":              datetime.now(UTC),
            "tickers":         [],
            "totales": {
                "n_ops": 0, "enviar_qty": 0, "enviar_importe": 0,
                "recibir_qty": 0, "recibir_importe": 0,
            },
            "plazos_desconocidos": [],
        }

    ayer = _previous_business_day(hoy)
    hoy_str = hoy.isoformat()
    ayer_str = ayer.isoformat()

    # SQL operaciones.negocio_movimientos. `fecha::text` para comparar con los ISO strings.
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT fecha::text AS fecha, plazo, op, categoria, ticker, cantidad, precio, "
            "importe, cuenta, comprobante, moneda FROM negocio_movimientos "
            "WHERE categoria = ANY(%s) AND "
            "((fecha = %s AND plazo = ANY(%s)) OR (fecha = %s AND plazo = ANY(%s)))",
            (list(CATEGORIAS_MERCADO), hoy_str, list(PLAZOS_T0), ayer_str, list(PLAZOS_T1)))
        docs = cur.fetchall()

    # Defensive: pueden caer "Inm" (=CI) hoy O 24hs ayer, pero también
    # podríamos ver otros plazos raros que no encuadran (48hs, Contado,
    # vacíos). Los logueamos en `plazos_desconocidos` para que el front
    # pueda mostrarlos si quiere — no los descartamos sin avisar.
    desconocidos: set[str] = set()
    relevantes: list[dict] = []
    for d in docs:
        plazo = (d.get("plazo") or "").strip()
        fecha_d = d.get("fecha")
        if (
            (fecha_d == hoy_str and plazo in PLAZOS_T0)
            or (fecha_d == ayer_str and plazo in PLAZOS_T1)
        ):
            relevantes.append(d)
        else:
            desconocidos.add(plazo or "(vacío)")

    # Agregación por ticker.
    by_ticker: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "ticker":          "",
        "enviar_qty":      0.0,
        "enviar_importe":  0.0,
        "recibir_qty":     0.0,
        "recibir_importe": 0.0,
        "n_ops":           0,
        "cuentas":         [],
    })

    tot_envq = tot_envi = tot_recq = tot_reci = 0.0
    tot_ops = 0

    for d in relevantes:
        ticker = (d.get("ticker") or "").strip() or "?"
        op = d.get("op") or ""
        # Usamos abs(cantidad) para no depender del signo. La dirección sale
        # de `op` (Compra/Venta).
        qty = abs(float(d.get("cantidad") or 0))
        importe = abs(float(d.get("importe") or 0))
        cuenta_lbl = d.get("cuenta") or ""
        id_cuenta = _id_cuenta_from_label(cuenta_lbl)

        b = by_ticker[ticker]
        b["ticker"] = ticker
        b["n_ops"] += 1
        is_venta = op.lower().startswith("v")
        if is_venta:
            b["enviar_qty"]     += qty
            b["enviar_importe"] += importe
            tot_envq += qty
            tot_envi += importe
        else:
            b["recibir_qty"]     += qty
            b["recibir_importe"] += importe
            tot_recq += qty
            tot_reci += importe
        tot_ops += 1

        b["cuentas"].append({
            "id_cuenta":   id_cuenta,
            "cuenta":      cuenta_lbl,
            "op":          op,
            "plazo":       d.get("plazo"),
            "cantidad":    qty,
            "importe":     importe,
            "precio":      d.get("precio"),
            "comprobante": d.get("comprobante"),
            "fecha":       d.get("fecha"),
            "moneda":      d.get("moneda"),
        })

    # Orden: primero los tickers con MAYOR cantidad a enviar (lo que pidió la
    # mesa — eso es lo crítico). Empate desempata por recibir.
    tickers_out = sorted(
        by_ticker.values(),
        key=lambda b: (-b["enviar_qty"], -b["recibir_qty"]),
    )
    for b in tickers_out:
        b["neto_qty"] = b["enviar_qty"] - b["recibir_qty"]
        # Cuentas dentro de cada ticker: ventas primero (lo a enviar), luego
        # por cantidad desc.
        b["cuentas"].sort(
            key=lambda c: (
                0 if (c["op"] or "").lower().startswith("v") else 1,
                -c["cantidad"],
            )
        )

    return {
        "fecha":           hoy_str,
        "dia_anterior":    ayer_str,
        "mercado_cerrado": False,
        "ts":              datetime.now(UTC),
        "tickers":         tickers_out,
        "totales": {
            "n_ops":           tot_ops,
            "enviar_qty":      tot_envq,
            "enviar_importe":  tot_envi,
            "recibir_qty":     tot_recq,
            "recibir_importe": tot_reci,
        },
        "plazos_desconocidos": sorted(desconocidos),
    }
