"""partner_export.py — exporta posiciones de cuentas puntuales a ACAPortfolio.Cartera.

Job AUTÓNOMO para la API externa del proveedor. Pega DIRECTO a Aunesa por
las cuentas de `config.PARTNER_EXPORT_CUENTAS` y vuelca su posición a
`ACAPortfolio.Cartera`.

NO depende de `Valuaciones.AuM` ni de `jobs/aum.py`, y NO aplica los filtros
de exclusión del AuM (`jobs/_aum_filters.py`) — el proveedor ve todas las
posiciones de sus cuentas. Replica de `jobs/aum.py` solo las manipulaciones
de datos legítimas: quedarse con las filas "Acumulado", el signo de
`cantidad`, el agrupado por especie, el descarte de posiciones netas en 0
y el cálculo de valuación.

Schema de `ACAPortfolio.Cartera` (1 doc por (fecha, id_cuenta, unidad)):
  {
    fecha:       "YYYY-MM-DD",
    id_cuenta:   "463",
    cuenta:      "[463] NOMBRE",
    unidad:      "...",
    cantidad:    float,
    precio:      float,
    valuacion:   float,
    exported_at: datetime UTC,
  }

Idempotente por (fecha, id_cuenta): re-correr el mismo día reemplaza los
docs de esa cuenta para esa fecha; el histórico de otras fechas queda
intacto. Una cuenta sin posiciones en Aunesa se saltea sin error — es un
caso normal, no una falla.

`fecha` es el día hábil de Argentina, NO el día UTC — así las dos corridas
diarias (18:30 y 23:00 hora Argentina) caen en la misma `fecha`, aunque la
de las 23:00 ART corra ya en el día UTC siguiente.

Corre como cron 2×/día (18:30 y 23:00 hora Argentina, L-V).
Uso:  python -m jobs.partner_export
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import holidays
import pandas as pd
import requests

import config
from core.mongo import get_mongo_client

_DB_NAME = "ACAPortfolio"
_COL_NAME = "Cartera"

# Zona horaria de Argentina — define el día de `fecha` del snapshot.
_AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

# ── Aunesa — endpoints y sesión propia (independiente de jobs/aum.py) ────────
_AUTH_URL = "https://aca.aunesa.com/Irmo/api/login"
_POSICION_URL = "https://aca.aunesa.com/Irmo/api/cuentas/{}/posicionValuada"
_SESSION = requests.Session()

# Tipos de título para el cálculo de valuación — idéntico a jobs/aum.py.
_TIPOS_DIVISOR_100 = {
    "Títulos Públicos",
    "Letras del Tesoro Capitalizables en Pesos",
    "Letras del Tesoro Ajustables por CER en Pesos",
    "Letras de Liquidez del Banco Central",
    "LETES",
    "Títulos de Deuda",
    "Obligaciones Negociables",
    "Fideicomisos Financieros",
    "Cheques de Pago Diferido",
}
_TIPOS_FUTUROS = {"Futuros", "Forwards", "Derivados"}


def _autenticar() -> dict:
    """Login contra Aunesa → headers con el Bearer token."""
    resp = _SESSION.post(
        _AUTH_URL,
        json={
            "clientId": config.AUNESA_CLIENT_ID,
            "username": config.AUNESA_USERNAME,
            "password": config.AUNESA_PASSWORD,
        },
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json().get("token")
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}


def _fecha_desde(hoy: date) -> str:
    """`desde` para Aunesa = T+2 hábil desde `hoy` (mismo criterio que jobs/aum.py)."""
    feriados = holidays.Argentina()

    def proximo_habil(d: date) -> date:
        d += timedelta(days=1)
        while d.weekday() >= 5 or d in feriados:
            d += timedelta(days=1)
        return d

    t1 = proximo_habil(hoy)
    t2 = proximo_habil(t1)
    return t2.strftime("%d/%m/%Y")


def _consultar_posicion(
    cuenta_id: str, headers: dict, desde: str, timeout: int = 240,
) -> tuple[list | None, bool]:
    """Posición valuada de una cuenta. Devuelve (data, necesita_reauth)."""
    resp = _SESSION.get(
        _POSICION_URL.format(cuenta_id),
        params={
            "desde":           desde,
            "hasta":           "",
            "tipoCuenta":      "Comitentes y propias",
            "nivel":           "Especie x cuenta",
            "ocultarCerradas": "true",
        },
        headers=headers,
        timeout=timeout,
    )
    if resp.status_code == 401:
        return None, True
    if resp.status_code != 200:
        return None, False
    return resp.json(), False


def _calcular_valuacion(row) -> float:
    """Valuación de una posición — idéntico a jobs/aum.py._calcular_valuacion."""
    precio = row["precio"]
    cantidad = row["cantidad"]
    tipo = str(row.get("tipoTitulo") or "")
    if pd.isna(precio):
        precio = 1.0
    if any(f.lower() in tipo.lower() for f in _TIPOS_FUTUROS):
        precio = precio + 1.0
    if tipo in _TIPOS_DIVISOR_100:
        return round((precio * cantidad) / 100, 6)
    return round(precio * cantidad, 6)


def _posiciones(data: list, cuenta_id: str) -> list[dict]:
    """Misma lógica que jobs/aum.py.procesar PERO sin los filtros de
    exclusión. Mantiene: solo filas 'Acumulado', signo de cantidad,
    agrupado por especie, descarte de cantidad neta 0 y valuación."""
    items = [r for r in data if r.get("informacion") == "Acumulado"]
    if not items:
        return []

    df = pd.DataFrame(items)
    df["id_cuenta"] = cuenta_id
    df["cantidad"] = pd.to_numeric(df["cantidad"], errors="coerce") * -1
    df["precio"] = pd.to_numeric(df["precio"], errors="coerce")

    df_g = df.groupby(
        ["id_cuenta", "unidad", "tipoTitulo", "cuenta"],
        as_index=False, dropna=False,
    ).agg({"cantidad": "sum", "precio": "max"})

    df_g = df_g[df_g["cantidad"] != 0].copy()
    if df_g.empty:
        return []

    df_g["valuacion"] = df_g.apply(_calcular_valuacion, axis=1)
    return df_g.to_dict(orient="records")


def main() -> None:
    cuentas = [str(c).strip() for c in config.PARTNER_EXPORT_CUENTAS if str(c).strip()]
    if not cuentas:
        # Fail-safe: sin cuentas configuradas NO exportamos nada.
        print("⚠ config.PARTNER_EXPORT_CUENTAS está vacío — abortando.")
        return

    # `fecha` = día hábil de Argentina (no UTC) para que las dos corridas
    # diarias (18:30 y 23:00 ART) caigan en el mismo snapshot.
    hoy_ar = datetime.now(_AR_TZ).date()
    fecha = hoy_ar.isoformat()
    desde = _fecha_desde(hoy_ar)
    print(f"fecha={fecha}  desde(Aunesa)={desde}  cuentas={cuentas}", flush=True)

    print("Autenticando con Aunesa...", flush=True)
    headers = _autenticar()
    print("Auth OK", flush=True)

    client = get_mongo_client()
    col = client[_DB_NAME][_COL_NAME]
    col.create_index([("id_cuenta", 1), ("fecha", -1)])

    ahora = datetime.now(UTC)
    total_pos = 0
    con_datos = 0
    for cid in cuentas:
        data, reauth = _consultar_posicion(cid, dict(headers), desde)
        if reauth:
            headers = _autenticar()
            data, _ = _consultar_posicion(cid, dict(headers), desde)

        registros = _posiciones(data, cid) if isinstance(data, list) and data else []

        # Idempotente: borra los docs de ESTA cuenta para ESTA fecha antes de
        # reinsertar. El histórico de otras fechas queda intacto.
        col.delete_many({"id_cuenta": cid, "fecha": fecha})

        if not registros:
            print(f"  [{cid}] sin posiciones — se saltea "
                  f"(cuenta vacía o sin datos en Aunesa).", flush=True)
            continue

        docs = [
            {
                "fecha":       fecha,
                "id_cuenta":   cid,
                "cuenta":      r.get("cuenta"),
                "unidad":      r.get("unidad"),
                "cantidad":    r.get("cantidad"),
                "precio":      r.get("precio"),
                "valuacion":   r.get("valuacion"),
                "exported_at": ahora,
            }
            for r in registros
        ]
        col.insert_many(docs)
        total_pos += len(docs)
        con_datos += 1
        print(f"  [{cid}] {len(docs)} posiciones exportadas.", flush=True)

    print(f"✅ {total_pos} posiciones de {con_datos}/{len(cuentas)} cuenta(s) "
          f"exportadas a {_DB_NAME}.{_COL_NAME} para {fecha} "
          f"({ahora.isoformat()}).")


if __name__ == "__main__":
    main()
