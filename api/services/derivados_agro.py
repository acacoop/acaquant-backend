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

# Frescura. El motor escribe `updated_at` cada 5s mientras corre (haya tick
# o no). Si el último snapshot tiene más de STALE_SNAPSHOT_S, el motor está
# caído o fuera de horario → la data NO es live y hay que avisarlo en la UI.
STALE_SNAPSHOT_S = 30
# El dólar oficial (feed MAE, script en la PC de la oficina) refresca más
# lento y de forma menos predecible — umbral más holgado.
STALE_OFICIAL_S = 600


def _age_s(dt: datetime | None, now: datetime) -> float | None:
    """Segundos transcurridos desde `dt` hasta `now`. None si falta `dt`.

    pymongo puede devolver datetimes naive (asumidos UTC) — los normalizamos.
    """
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return max(0.0, (now - dt).total_seconds())


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
# HELPERS PUROS DE LECTURA — sin Mongo. Las lecturas AGRO (pase/opciones/
# simulador) viven SQL-native en `agro_sql.py` (decomiso Mongo); ese módulo
# reusa estos helpers puros (`_build_bloque`, `_curva_estrategia_y_diferencias`,
# `_futuro_ticker_de_opcion`, `_age_s`, `_validate_commodity`). NO borrarlos.
# ─────────────────────────────────────────────────────────────────────────────


def _build_bloque(
    commodity: str,
    pizarra: dict,
    camara: dict,
    snapshots: list[dict],
    oficial_value: float | None,
    hoy: date,
) -> dict[str, Any]:
    # Vto pizarra = HOY siempre. No se lee de Mongo (campo legacy editable
    # quedó sin uso). Formato ISO YYYY-MM-DD para que el front lo formatee
    # con su helper de fecha como cualquier otro vencimiento.
    vto_p = hoy.isoformat()
    # US$ pizarra = Cámara.precio_usd (única fuente de verdad). Fallback al
    # `us_pizarra` viejo solo si Cámara no tiene cargado el cereal todavía
    # (transición: hasta que el trader cargue Cámara, mostramos lo legacy).
    us_p = camara.get("precio_usd") or pizarra.get("us_pizarra")
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
            # open / closing: para que la tabla de futuros (vista detalle)
            # calcule la variación intradía y vs. cierre del día anterior.
            "open":          s.get("open"),
            "closing":       s.get("closing"),
            "vol_efectivo":  s.get("vol_efectivo"),
            "dias_a_vto":    dias,
            "updated_at":    s.get("updated_at"),
        })

    return {
        "commodity": commodity,
        "rows":      [pizarra_row, dispo_row, *futuros_rows],
    }


# ─────────────────────────────────────────────────────────────────────────────
# WRITES — gate en el router (admin-only mientras la vista está en beta;
# cuando la mesa valide pasa a trader+admin)
# ─────────────────────────────────────────────────────────────────────────────


def _leer_pizarra_sql(commodity: str) -> dict:
    """Doc actual de la pizarra (jsonb `data`) desde SQL. {} si no existe.

    Fuente del `prev` para el update parcial de set_pizarra (cutover SQL-native).
    Si PG está caído devuelve {} → un update parcial podría nullear el otro campo;
    es el tradeoff inherente de tener SQL como única fuente."""
    from psycopg.rows import dict_row

    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT data FROM mercado.agro_pizarra WHERE commodity = %s", (commodity,))
        row = cur.fetchone()
    return (row["data"] if row else None) or {}


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

    from core import pg_mirror
    now = datetime.now(UTC)

    # SQL-native: la tabla `mercado.agro_pizarra` es la fuente. `prev` (para el
    # update parcial: None = no tocar) sale de SQL, no de Mongo.
    prev = _leer_pizarra_sql(commodity)
    new = {
        "commodity":           commodity,
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

    # Write SQL-native incondicional (carga manual de la mesa). `commodity` queda
    # también dentro de `data` para que el read SQL reconstruya el mismo dict que
    # indexa el service por commodity. write_native nunca levanta (best-effort).
    pg_mirror.write_native(
        "mercado.agro_pizarra", ["commodity"],
        [{"commodity": commodity, "data": pg_mirror.doc_iso(new)}],
    )

    # Audit SQL-native (mercado.agro_pizarra_audit, self-create). Best-effort: un fallo del
    # audit NO rompe la carga (ya escrita arriba). Antes iba a Mongo Derivados.AgroPizarraAudit
    # (no migrado) → la recreaba al dropearla.
    _audit_pizarra_sql(commodity, prev, new, email, now)
    return new


def _audit_pizarra_sql(commodity: str, prev: dict, new: dict, email: str, ts: datetime) -> None:
    """Append del cambio a mercado.agro_pizarra_audit (SQL). Reemplaza el audit Mongo."""
    try:
        from psycopg.types.json import Jsonb

        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS mercado.agro_pizarra_audit ("
                "id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, commodity text, "
                "prev jsonb, new jsonb, updated_by text, updated_at timestamptz)")
            cur.execute(
                "INSERT INTO mercado.agro_pizarra_audit (commodity, prev, new, updated_by, updated_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (commodity,
                 Jsonb({"vencimiento_pizarra": prev.get("vencimiento_pizarra"),
                        "us_pizarra": prev.get("us_pizarra")}),
                 Jsonb({"vencimiento_pizarra": new["vencimiento_pizarra"],
                        "us_pizarra": new["us_pizarra"]}),
                 email, ts))
            conn.commit()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# PANEL DE OPCIONES + SIMULADOR (vista ESTRATEGIAS)
# ─────────────────────────────────────────────────────────────────────────────

TipoEstrategia = Literal["put_sintetico", "long_put"]


def _futuro_ticker_de_opcion(option_ticker: str) -> str:
    """`SOJ.ROS/JUL26 312 C` → `SOJ.ROS/JUL26`. Sirve para joinear con AgroSnapshot."""
    # El symbol del futuro es el prefijo hasta el primer espacio.
    return option_ticker.split(" ", 1)[0]


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
