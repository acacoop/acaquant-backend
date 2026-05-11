"""Service puro — Pase Agro (Trigo / Maíz / Soja Rosario).

Dos capas:

1. **Pase Agro (PIZARRA)** — replica la planilla de la mesa: por cada
   commodity se rendea una fila PIZARRA (manual, editable), una fila
   DISPO (placeholder #N/A) y N filas de futuros (last live de
   Trading.AgroSnapshot).

   Cálculos puros (no se persisten):
   - ars      = us  × dolar_oficial_mid
   - pase     = us_pizarra − us_futuro
   - tnav_us  = (us_pizarra / us_futuro)^(365/dias_a_vto) − 1  (compuesta)

   La fórmula TNAV se validó contra la planilla:
   - TRI.ROS/DIC26 last=229.60, pizarra=202.79, dias≈236 → -17.41% (planilla -17.47%)
   - MAI.ROS/SEP26 last=191.90, pizarra=190.00, dias≈149 →  -2.41% (planilla -2.41%)

2. **Panel de Opciones + Simulador de Estrategias** — alimenta la vista
   ESTRATEGIAS. Lee Trading.AgroOpcionesSnapshot (motor_agro_opciones)
   y arma una cadena tipo planilla (calls a la izquierda, puts a la
   derecha, strikes en el medio) agrupada por vencimiento, con el
   precio del futuro embebido.

   Simulador de dos estrategias canónicas (PDF de cobertura agro):

   - **Put sintético** = venta de futuro F0 + compra de call K (prima C):
     - piso = F0 − C
     - zona expuesta = (F0, K)  → margin calls proporcionales
     - diferencia_max = K − F0  (constante una vez que el mercado supera K;
       la prima ya se pagó upfront, no entra en "diferencias")
     - precio_efectivo(F) = F0 − C + max(F − K, 0)
     - diferencia(F)      = (F0 − F) + max(F − K, 0)

   - **Long put** = compra de put K (prima P):
     - piso = K − P
     - sin diferencias (solo se pierde la prima)
     - precio_efectivo(F) = max(K, F) − P
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from core.dolar_oficial import mid_oficial_live
from core.mongo import get_mongo_client, get_mongo_client_read

COMMODITY_ORDER = ("TRIGO", "MAIZ", "SOJA")

PIZARRA_LABELS = {
    "TRIGO": "TRIGO PIZARRA",
    "MAIZ":  "MAIZ PIZARRA",
    "SOJA":  "SOJA PIZARRA",
}

DISPO_LABELS = {
    "TRIGO": "TRI.ROS.P/DISPO",
    "MAIZ":  "MAI.ROS.P/DISPO",
    "SOJA":  "SOJ.ROS.P/DISPO",
}


def _dias_entre(mat_str: str, hoy: date) -> int:
    """Días calendario hoy→maturity (formato YYYYMMDD del snapshot)."""
    try:
        vto = date(int(mat_str[:4]), int(mat_str[4:6]), int(mat_str[6:8]))
        return max(1, (vto - hoy).days)
    except Exception:
        return 1


def _tnav_us(pizarra_us: float | None, last_us: float | None, dias: int) -> float | None:
    """TNAV compuesta en US$: (pizarra/last)^(365/dias) − 1.

    None si falta data o magnitudes no positivas. Devuelve fracción (no %).
    """
    if not pizarra_us or not last_us or pizarra_us <= 0 or last_us <= 0 or dias <= 0:
        return None
    try:
        return round((pizarra_us / last_us) ** (365 / dias) - 1, 6)
    except Exception:
        return None


def _validate_commodity(commodity: str) -> str:
    if commodity not in COMMODITY_ORDER:
        raise ValueError(f"Commodity inválido: {commodity}. Válidos: {COMMODITY_ORDER}")
    return commodity


# ─────────────────────────────────────────────────────────────────────────────
# READS
# ─────────────────────────────────────────────────────────────────────────────


def get_pase_agro() -> dict[str, Any]:
    """Tabla PASE AGRO completa, lista para renderear.

    Output:
        {
          "oficial": {"value": float | None, "ts": dt | None, "source": str},
          "ts":      datetime,
          "bloques": [
            {
              "commodity": "TRIGO",
              "rows": [
                {"tipo":"pizarra", "vencimiento":"2026-04-29", "posicion":"TRIGO PIZARRA", "us":202.79, "pase":None, "ars":..., "tnav_us":None},
                {"tipo":"dispo",   "vencimiento":"2026-04-29", "posicion":"TRI.ROS.P/DISPO", "us":None, ...},
                {"tipo":"futuro",  "vencimiento":"20260724", "posicion":"TRI.ROS/JUL26", "us":223.10, "pase":-20.31, "ars":..., "tnav_us":-0.3331, "bid":..., "offer":..., "vol_efectivo":..., "updated_at":dt},
                ...
              ]
            }, ...
          ]
        }
    """
    db_read = get_mongo_client_read()
    pizarras_raw = list(db_read["Derivados"]["AgroPizarra"].find({}))
    pizarras = {p["_id"]: p for p in pizarras_raw}
    snapshots = list(db_read["Trading"]["AgroSnapshot"].find({}))

    oficial = mid_oficial_live("oficial")
    oficial_value = oficial.get("value")

    hoy = date.today()
    bloques = []
    for commodity in COMMODITY_ORDER:
        bloques.append(_build_bloque(
            commodity, pizarras.get(commodity, {}), snapshots, oficial_value, hoy,
        ))

    return {
        "oficial": {
            "value":  oficial_value,
            "ts":     oficial.get("ts"),
            "source": oficial.get("source"),
        },
        "ts":      datetime.now(UTC),
        "bloques": bloques,
    }


def _build_bloque(
    commodity: str,
    pizarra: dict,
    snapshots: list[dict],
    oficial_value: float | None,
    hoy: date,
) -> dict[str, Any]:
    vto_p = pizarra.get("vencimiento_pizarra")
    us_p = pizarra.get("us_pizarra")
    ars_p = (us_p * oficial_value) if (us_p and oficial_value) else None

    pizarra_row = {
        "tipo":        "pizarra",
        "vencimiento": vto_p,
        "posicion":    PIZARRA_LABELS[commodity],
        "us":          us_p,
        "pase":        None,
        "ars":         round(ars_p, 2) if ars_p is not None else None,
        "tnav_us":     None,
        "updated_by":  pizarra.get("updated_by"),
        "updated_at":  pizarra.get("updated_at"),
    }

    dispo_row = {
        "tipo":        "dispo",
        "vencimiento": vto_p,
        "posicion":    DISPO_LABELS[commodity],
        "us":          None,
        "pase":        None,
        "ars":         None,
        "tnav_us":     None,
    }

    snaps_commodity = sorted(
        (s for s in snapshots if s.get("commodity") == commodity),
        key=lambda s: s.get("vencimiento") or "9999",
    )

    futuros_rows = []
    for s in snaps_commodity:
        ticker = s.get("ticker", "")
        mat = s.get("vencimiento") or ""
        last = s.get("last_price")
        dias = s.get("dias_a_vto") or _dias_entre(mat, hoy)

        ars_f = round(last * oficial_value, 2) if (last and oficial_value) else None
        # Si no hay last, pase queda None (no replicamos el comportamiento de
        # Excel donde celda vacía = 0 → pase = pizarra).
        pase = round(us_p - last, 4) if (us_p and last) else None
        tnav = _tnav_us(us_p, last, dias)

        futuros_rows.append({
            "tipo":          "futuro",
            "ticker":        ticker,
            "vencimiento":   mat,
            "posicion":      ticker,
            "us":            last,
            "pase":          pase,
            "ars":           ars_f,
            "tnav_us":       tnav,
            "bid":           s.get("bid_price"),
            "offer":         s.get("offer_price"),
            "vol_efectivo":  s.get("vol_efectivo"),
            "dias_a_vto":    dias,
            "updated_at":    s.get("updated_at"),
        })

    return {
        "commodity": commodity,
        "rows":      [pizarra_row, dispo_row, *futuros_rows],
    }


# ─────────────────────────────────────────────────────────────────────────────
# WRITES — solo trader+admin (gate en el router)
# ─────────────────────────────────────────────────────────────────────────────


def set_pizarra(
    commodity: str,
    vencimiento_pizarra: str | None,
    us_pizarra: float | None,
    email: str,
) -> dict[str, Any]:
    """Upsert manual de la fila PIZARRA + audit en Derivados.AgroPizarraAudit.

    `vencimiento_pizarra` y `us_pizarra` se pueden actualizar de a uno —
    null/None = no tocar ese campo. Si ambos son None y no existe doc previo,
    crea uno vacío.
    """
    _validate_commodity(commodity)

    client = get_mongo_client()
    col = client["Derivados"]["AgroPizarra"]
    audit = client["Derivados"]["AgroPizarraAudit"]
    now = datetime.now(UTC)

    prev = col.find_one({"_id": commodity}) or {}
    new = {
        "_id":                 commodity,
        "vencimiento_pizarra": vencimiento_pizarra
                                if vencimiento_pizarra is not None
                                else prev.get("vencimiento_pizarra"),
        "us_pizarra":          float(us_pizarra)
                                if us_pizarra is not None
                                else prev.get("us_pizarra"),
        "updated_by":          email,
        "updated_at":          now,
    }
    if new["us_pizarra"] is not None and new["us_pizarra"] <= 0:
        raise ValueError("us_pizarra debe ser > 0")

    col.replace_one({"_id": commodity}, new, upsert=True)

    audit.insert_one({
        "commodity":  commodity,
        "prev": {
            "vencimiento_pizarra": prev.get("vencimiento_pizarra"),
            "us_pizarra":          prev.get("us_pizarra"),
        },
        "new": {
            "vencimiento_pizarra": new["vencimiento_pizarra"],
            "us_pizarra":          new["us_pizarra"],
        },
        "updated_by": email,
        "updated_at": now,
    })
    return new


# ─────────────────────────────────────────────────────────────────────────────
# PANEL DE OPCIONES + SIMULADOR (vista ESTRATEGIAS)
# ─────────────────────────────────────────────────────────────────────────────

TipoEstrategia = Literal["put_sintetico", "long_put"]


def _futuro_ticker_de_opcion(option_ticker: str) -> str:
    """`SOJ.ROS/JUL26 312 C` → `SOJ.ROS/JUL26`. Sirve para joinear con AgroSnapshot."""
    # El symbol del futuro es el prefijo hasta el primer espacio.
    return option_ticker.split(" ", 1)[0]


def get_panel_opciones(commodity: str) -> dict[str, Any]:
    """Cadena de opciones agro para un commodity, agrupada por **contrato futuro**.

    Importante: las opciones agro vencen ~1 mes antes que el futuro al que están
    escritas (convención estándar). `SOJ.ROS/JUL26 312 C` expira el 23/06/26
    pero es una opción sobre el futuro SOJ.ROS/JUL26 (vto ~24/07/26). Por eso
    el grupping y el join con `AgroSnapshot` se hace por **prefijo del ticker
    del futuro** (`SOJ.ROS/JUL26`), NO por la fecha de vencimiento.

    Output:
        {
          "commodity": "SOJA",
          "ts": datetime,
          "vencimientos": [
            {
              "vencimiento":     "20260623",         # expiry de las opciones del grupo
              "futuro_ticker":   "SOJ.ROS/JUL26" | None,
              "futuro_vto":      "20260724" | None,  # vto del futuro subyacente
              "futuro_last":     330.50 | None,
              "dias_a_vto":      43,                 # días hasta expiry de la opción
              "strikes": [
                {
                  "strike": 312,
                  "call":   {"ticker":..., "bid":..., "offer":..., "last":..., "vol":..., "updated_at":...} | None,
                  "put":    {"ticker":..., "bid":..., "offer":..., "last":..., "vol":..., "updated_at":...} | None,
                },
                ...
              ]
            },
            ...
          ]
        }

    Sin docs en AgroOpcionesSnapshot → vencimientos = [] (no error).
    """
    commodity = _validate_commodity(commodity.upper())

    db_read = get_mongo_client_read()
    opciones = list(db_read["Trading"]["AgroOpcionesSnapshot"].find(
        {"commodity": commodity}
    ))
    futuros = list(db_read["Trading"]["AgroSnapshot"].find(
        {"commodity": commodity}
    ))
    futuros_by_ticker = {f.get("ticker"): f for f in futuros if f.get("ticker")}

    # Group by future-ticker-prefix (no por vencimiento de la opción).
    by_future: dict[str, list[dict]] = {}
    for o in opciones:
        ticker = o.get("ticker")
        if not ticker:
            continue
        prefix = _futuro_ticker_de_opcion(ticker)
        by_future.setdefault(prefix, []).append(o)

    # Orden de los grupos: por vencimiento del FUTURO si existe, sino por
    # vencimiento de la opción. Así DIC26 viene después de NOV26 aunque
    # algún futuro falte temporalmente.
    def _sort_key(prefix: str) -> str:
        opts = by_future[prefix]
        fut = futuros_by_ticker.get(prefix)
        if fut and fut.get("vencimiento"):
            return fut["vencimiento"]
        return opts[0].get("vencimiento") or "99999999"

    vencimientos = []
    for prefix in sorted(by_future.keys(), key=_sort_key):
        opts = by_future[prefix]
        futuro = futuros_by_ticker.get(prefix)
        opt_vto = opts[0].get("vencimiento")
        dias_a_vto = opts[0].get("dias_a_vto")

        # Merge call+put por strike.
        by_strike: dict[float, dict[str, dict]] = {}
        for o in opts:
            strike = o.get("strike")
            tipo = o.get("tipo")
            if strike is None or tipo not in ("C", "P"):
                continue
            row = by_strike.setdefault(float(strike), {})
            slot = "call" if tipo == "C" else "put"
            row[slot] = {
                "ticker":     o.get("ticker"),
                "bid":        o.get("bid_price"),
                "offer":      o.get("offer_price"),
                "last":       o.get("last_price"),
                "vol":        o.get("vol_efectivo"),
                "updated_at": o.get("updated_at"),
            }

        strikes = [
            {
                "strike": k,
                "call":   v.get("call"),
                "put":    v.get("put"),
            }
            for k, v in sorted(by_strike.items())
        ]

        vencimientos.append({
            "vencimiento":   opt_vto,
            "futuro_ticker": prefix,
            "futuro_vto":    futuro.get("vencimiento") if futuro else None,
            "futuro_last":   futuro.get("last_price") if futuro else None,
            "dias_a_vto":    dias_a_vto,
            "strikes":       strikes,
        })

    return {
        "commodity":    commodity,
        "ts":           datetime.now(UTC),
        "vencimientos": vencimientos,
    }


def _curva_estrategia_y_diferencias(
    tipo: TipoEstrategia,
    futuro_F0: float,
    strike_K: float,
    prima: float,
    steps: int = 40,
) -> tuple[list[dict], list[dict]]:
    """Genera los puntos (precio_futuro_mkt, precio_efectivo_venta, diferencia) para los gráficos.

    Rango del eje X: del 30% al 180% del strike (cubre la zona interesante
    sin perderse en extremos). El front recorta visualmente lo que no usa.
    """
    x_low = max(0.0, strike_K * 0.3)
    x_high = strike_K * 1.8
    if x_high <= x_low:
        x_high = x_low + strike_K

    curva_e: list[dict] = []
    curva_d: list[dict] = []
    for i in range(steps + 1):
        f = x_low + (x_high - x_low) * (i / steps)
        if tipo == "put_sintetico":
            precio_efectivo = futuro_F0 - prima + max(f - strike_K, 0.0)
            diferencia = (futuro_F0 - f) + max(f - strike_K, 0.0)
        else:  # long_put
            precio_efectivo = max(strike_K, f) - prima
            diferencia = 0.0
        curva_e.append({
            "x":          round(f, 2),
            "estrategia": round(precio_efectivo, 2),
            "futuro":     round(f, 2),
        })
        curva_d.append({
            "x":          round(f, 2),
            "diferencia": round(diferencia, 2),
        })
    return curva_e, curva_d


def simular_estrategia(
    commodity: str,
    vencimiento: str,
    tipo: TipoEstrategia,
    strike: float,
    prima_override: float | None = None,
) -> dict[str, Any]:
    """Simula put sintético o long put sobre el contrato (commodity, vencimiento).

    Inputs:
        commodity:        TRIGO | MAIZ | SOJA
        vencimiento:      YYYYMMDD (del DB)
        tipo:             put_sintetico | long_put
        strike:           strike de la opción
        prima_override:   si se pasa, se usa en lugar del last_price del libro;
                          útil cuando el trader quiere usar otro nivel (bid/offer/mid)

    Errors:
        ValueError si commodity inválido, futuro inexistente, strike no listado,
        o prima no disponible.
    """
    commodity = _validate_commodity(commodity.upper())
    if tipo not in ("put_sintetico", "long_put"):
        raise ValueError(f"tipo inválido: {tipo!r}")
    if strike <= 0:
        raise ValueError("strike debe ser > 0")

    db_read = get_mongo_client_read()

    # Tipo de opción según estrategia: put sintético usa CALL, long put usa PUT.
    tipo_opcion = "C" if tipo == "put_sintetico" else "P"

    # Primero buscamos la opción — el ticker nos da el prefijo del futuro.
    # Las opciones agro vencen ~1 mes antes que el futuro al que están escritas
    # (convención estándar), así que NO se puede joinear futuro↔opción por
    # vencimiento — hay que joinear por ticker del futuro.
    opcion = db_read["Trading"]["AgroOpcionesSnapshot"].find_one({
        "commodity":   commodity,
        "vencimiento": vencimiento,
        "strike":      strike,
        "tipo":        tipo_opcion,
    })
    if not opcion:
        raise ValueError(
            f"opción {tipo_opcion} K={strike} vto {vencimiento} no listada"
        )
    future_ticker = _futuro_ticker_de_opcion(opcion.get("ticker") or "")
    if not future_ticker:
        raise ValueError(
            f"no pude extraer ticker del futuro de la opción {opcion.get('ticker')!r}"
        )

    futuro = db_read["Trading"]["AgroSnapshot"].find_one({"ticker": future_ticker})
    if not futuro or futuro.get("last_price") in (None, 0):
        raise ValueError(
            f"sin precio de futuro {future_ticker} (subyacente de la opción)"
        )
    futuro_F0 = float(futuro["last_price"])

    if prima_override is not None:
        if prima_override <= 0:
            raise ValueError("prima_override debe ser > 0")
        prima = float(prima_override)
    else:
        last = opcion.get("last_price")
        if last in (None, 0):
            raise ValueError(
                "sin last_price para la opción seleccionada — pasar prima_override"
            )
        prima = float(last)

    # Métricas del payoff.
    if tipo == "put_sintetico":
        piso = round(futuro_F0 - prima, 4)
        diferencia_max = round(strike - futuro_F0, 4)
        zona_expuesta = {
            "desde": round(min(futuro_F0, strike), 4),
            "hasta": round(max(futuro_F0, strike), 4),
        }
    else:  # long_put
        piso = round(strike - prima, 4)
        diferencia_max = 0.0
        zona_expuesta = None

    curva_e, curva_d = _curva_estrategia_y_diferencias(
        tipo=tipo, futuro_F0=futuro_F0, strike_K=strike, prima=prima,
    )

    return {
        "tipo":               tipo,
        "commodity":          commodity,
        "vencimiento":        vencimiento,
        "strike":             strike,
        "prima":              prima,
        "prima_override":     prima_override is not None,
        "futuro_ticker":      futuro.get("ticker"),
        "futuro_last":        futuro_F0,
        "opcion_ticker":      opcion.get("ticker"),
        "piso":               piso,
        "diferencia_max":     diferencia_max,
        "zona_expuesta":      zona_expuesta,
        "curva_estrategia":   curva_e,
        "curva_diferencias":  curva_d,
        "ts":                 datetime.now(UTC),
    }
