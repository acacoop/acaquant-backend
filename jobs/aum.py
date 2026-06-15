import os
import sys
from datetime import datetime, timedelta

import holidays
import pandas as pd
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import config
from jobs._aum_filters import (
    is_excluded,
    load_contrapartes_id_cuentas,
    load_contrapartes_names,
)

# Lazy singletons — se cargan al primer pedido y se reusan por los workers
# del ThreadPoolExecutor. Las listas cambian con poca frecuencia (cuando
# alguien edita Contrapartes), así que no vale la pena refrescar cada call.
_CONTRAPARTES_IDS_CACHE: frozenset[str] | None = None
_CONTRAPARTES_NAMES_CACHE: frozenset[str] | None = None


def _contrapartes_ids() -> frozenset[str]:
    global _CONTRAPARTES_IDS_CACHE
    if _CONTRAPARTES_IDS_CACHE is None:
        _CONTRAPARTES_IDS_CACHE = load_contrapartes_id_cuentas()
    return _CONTRAPARTES_IDS_CACHE


def _contrapartes_names() -> frozenset[str]:
    global _CONTRAPARTES_NAMES_CACHE
    if _CONTRAPARTES_NAMES_CACHE is None:
        _CONTRAPARTES_NAMES_CACHE = load_contrapartes_names()
    return _CONTRAPARTES_NAMES_CACHE

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


# ── Calendario hábil ──────────────────────────────────────────────────────────
# Regla H1 CONFIRMADA (bitácora docs/AUM_PROBLEMAS_Y_SOLUCIONES.md, medida contra
# el contable con la cuenta 805): Aunesa con `desde=X` devuelve la posición del
# día hábil ANTERIOR a X. Para tener la posición AL día D hay que pedir
# `desde = próximo_hábil(D)` y etiquetar `fecha_snapshot = D`.
_ARG_HOLIDAYS = holidays.Argentina()


def _proximo_habil(d):
    d += timedelta(days=1)
    while d.weekday() >= 5 or d in _ARG_HOLIDAYS:
        d += timedelta(days=1)
    return d


def _habil_anterior(d):
    d -= timedelta(days=1)
    while d.weekday() >= 5 or d in _ARG_HOLIDAYS:
        d -= timedelta(days=1)
    return d


def fecha_objetivo_diario():
    """`desde` = la fecha de HOY (el día que corre el job). Por la regla H1
    (desde=X → posición del día hábil ANTERIOR a X), eso trae los datos del
    último día hábil, y el snapshot se etiqueta con ESE día.

    Ej.: corre hoy 16/06 → desde=16/06 → Aunesa devuelve el 13/06 →
    fecha_snapshot = 2026-06-13.

    Devuelve (desde_ddmmyyyy, fecha_snapshot_iso).
    """
    hoy = datetime.now().date()
    desde = hoy.strftime("%d/%m/%Y")                          # la fecha del día que corre
    fecha_snapshot = _habil_anterior(hoy).strftime("%Y-%m-%d")  # datos del día hábil anterior
    return desde, fecha_snapshot


# ── Fecha T+2 (igual que main_carteras) — usado por api/routers/manager/aunesa ─
# OJO: este sigue la regla VIEJA (T+2). NO lo usa el job diario (ver
# fecha_objetivo_diario). Se conserva por el import del router; revisar aparte.
def fecha_t2():
    hoy     = datetime.now()
    t_mas_1 = _proximo_habil(hoy)
    t_mas_2 = _proximo_habil(t_mas_1)
    return t_mas_2.strftime("%d/%m/%Y")


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


# ── Procesar respuesta → lista de dicts ──────────────────────────────────────
def procesar(data, fecha_snapshot, timestamp):
    items = [r for r in data if r.get("informacion") == "Acumulado"]
    if not items:
        return []

    df = pd.DataFrame(items)
    df["id_cuenta"] = df["cuenta"].str.extract(r"\[(\d+)\]")
    df["cantidad"]  = pd.to_numeric(df["cantidad"], errors="coerce") * -1
    df["precio"]    = pd.to_numeric(df["precio"],   errors="coerce")

    df_g = df.groupby(
        ["id_cuenta", "unidad", "tipoTitulo", "cuenta"],
        as_index=False, dropna=False
    ).agg({"cantidad": "sum", "precio": "max"})

    df_g = df_g[df_g["cantidad"] != 0].copy()

    # ── Filtros de limpieza ──────────────────────────────────────────────────
    # Reglas centralizadas en `jobs._aum_filters` para que el backfill
    # one-shot (`scripts/cleanup_aum_excluidos`) use exactamente el mismo
    # criterio. Hoy cubre: OTC/CDC patterns, USDL, contrapartes por
    # id_cuenta (CuentasAPI.ContrapartesAPI), contrapartes por nombre
    # (CashFlow.Contrapartes.contraparte) y la tenencia ARS de [100]/[101].
    contrapartes_ids = _contrapartes_ids()
    contrapartes_names = _contrapartes_names()
    df_g = df_g[~df_g.apply(
        lambda row: is_excluded(
            row.get("cuenta"), row.get("unidad"),
            id_cuenta=row.get("id_cuenta"),
            contrapartes_ids=contrapartes_ids,
            contrapartes_names=contrapartes_names,
        ),
        axis=1,
    )].copy()

    # NOTA (removido 2026-05-05): antes filtrábamos cash con cantidad
    # negativa (ARS/USD < 0). Eso ocultaba posiciones short de cash —
    # legítimas cuando la cuenta compró más bonos que el saldo en
    # efectivo (margen / debt position). La vista /valuaciones necesita
    # ver esas posiciones para que el cuadre AuM ↔ posiciones sea fiel.

    if df_g.empty:
        return []

    # ── Valuación ────────────────────────────────────────────────────────────
    df_g["valuacion"] = df_g.apply(_calcular_valuacion, axis=1)

    df_g["fecha_snapshot"] = fecha_snapshot
    df_g["timestamp"]      = timestamp

    return df_g.to_dict(orient="records")
