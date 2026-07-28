"""jobs/aum.py — CLIENTE Aunesa (librería, no es un job).

El cron de AuM que vivía acá se eliminó (2026-06-15): la fuente única de
tenencias es SQL `portafolio.tenencia`, que escribe `jobs/portafolio_backfill.py
--diario`. Lo que sobrevive es el cliente HTTP de Aunesa + la regla de valuación,
reusados por:
  - jobs/portafolio_backfill.py        (_SESSION, POSICION_URL, autenticar,
                                        obtener_cuentas, _calcular_valuacion)
  - jobs/portafolio_reparar_timeouts.py (_SESSION, POSICION_URL, autenticar)
  - api/routers/manager/aunesa.py       (autenticar, consultar_posicion)

No tiene __main__: no se ejecuta, se importa.
"""
import os
import sys

import pandas as pd
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import config

AUTH_URL     = "https://aca.aunesa.com/Irmo/api/login"
LISTADO_URL  = "https://aca.aunesa.com/Irmo/api/cuentas/listadoCuentas"
POSICION_URL = "https://aca.aunesa.com/Irmo/api/cuentas/{}/posicionValuada"

# Session compartida por TODAS las llamadas a Aunesa: TLS handshake +
# TCP connect se hacen una sola vez, después se reusa la conexión via
# HTTP keep-alive. Quita ~200-400ms de latencia por call después del
# primer request. requests.Session es thread-safe (docs). Compartido
# entre el ThreadPoolExecutor de 4 workers sin issues.
_SESSION = requests.Session()


# ── Auth ──────────────────────────────────────────────────────────────────────
def autenticar():
    resp = _SESSION.post(
        AUTH_URL,
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


# ── Listado de cuentas activas ────────────────────────────────────────────────
def obtener_cuentas(headers, timeout=120, retries=3):
    """Trae el listado completo de cuentas (1 sola request, ~1800 docs).
    Con retry+backoff: si Aunesa anda lenta, no abortar el backfill al primer
    timeout — el listado es chico, vale la pena reintentar."""
    import time as _time
    last_err: Exception | None = None
    for intento in range(1, retries + 1):
        try:
            resp = _SESSION.get(LISTADO_URL, headers=headers, timeout=timeout)
            resp.raise_for_status()
            break
        except Exception as e:
            last_err = e
            if intento < retries:
                backoff = 2 ** intento
                print(f"⚠ obtener_cuentas intento {intento} falló ({type(e).__name__}); "
                      f"reintentando en {backoff}s", flush=True)
                _time.sleep(backoff)
    else:
        raise last_err  # type: ignore[misc]
    df = pd.DataFrame(resp.json())
    activas = df[
        df["tipo"].isin(["Comitente", "Propia"]) &
        (df["estado"] == "Activa")
    ][["id", "denominacion"]].copy()
    return activas.reset_index(drop=True)


# ── Consulta posición por cuenta ──────────────────────────────────────────────
def consultar_posicion(cuenta_id, headers, desde, timeout=120):
    params = {
        "desde":           desde,
        "hasta":           "",
        "tipoCuenta":      "Comitentes y propias",
        "nivel":           "Especie x cuenta",
        "ocultarCerradas": "true",
    }
    # Timeout configurable: cuentas con muchas posiciones tardan más del
    # lado de Aunesa al computar la valuación. Default 120s, subible para
    # backfills donde Aunesa puede ser todavía más lenta (datos históricos).
    resp = _SESSION.get(
        POSICION_URL.format(cuenta_id),
        params=params,
        headers=headers,
        timeout=timeout,
    )
    if resp.status_code == 401:
        return None, True   # señal de re-auth
    if resp.status_code != 200:
        return None, False
    return resp.json(), False


# ── Reglas de valuación ──────────────────────────────────────────────────────
TIPOS_DIVISOR_100 = {
    "Títulos Públicos",
    "Letras del Tesoro Capitalizables en Pesos",
    "Letras del Tesoro Ajustables por CER en Pesos",
    "Letras de Liquidez del Banco Central",   # LELIQ/LEFI — cotizan en paridad
    "LETES",                      # Letras del Tesoro en USD (cotizan paridad)
    "LEDE",                       # Letras a descuento (ej. S13N6) — Aunesa las manda
                                  # con este tipo corto; cotizan en paridad. Medido
                                  # 2026-07-24: sin esto el AuM las guardaba ×100.
    "Títulos de Deuda",
    "Obligaciones Negociables",
    "Fideicomisos Financieros",
    "Cheques de Pago Diferido",
}
TIPOS_FUTUROS = {"Futuros", "Forwards", "Derivados"}


def _calcular_valuacion(row):
    precio   = row["precio"]
    cantidad = row["cantidad"]
    tipo     = str(row.get("tipoTitulo") or "")

    # Si precio es NaN asumir 1
    if pd.isna(precio):
        precio = 1.0

    # Futuros / Forwards / Derivados: precio + 1
    if any(f.lower() in tipo.lower() for f in TIPOS_FUTUROS):
        precio = precio + 1.0

    # Divisor según tipo
    if tipo in TIPOS_DIVISOR_100:
        return round((precio * cantidad) / 100, 6)
    return round(precio * cantidad, 6)
