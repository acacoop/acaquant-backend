import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import holidays
import pandas as pd
import requests
from pymongo import UpdateOne

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import config
from core.mongo import get_mongo_client
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


# ── Fecha T+2 (igual que main_carteras) ──────────────────────────────────────
def fecha_t2():
    arg_holidays = holidays.Argentina()

    def proximo_habil(d):
        d += timedelta(days=1)
        while d.weekday() >= 5 or d in arg_holidays:
            d += timedelta(days=1)
        return d

    hoy     = datetime.now()
    t_mas_1 = proximo_habil(hoy)
    t_mas_2 = proximo_habil(t_mas_1)
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
def _sincronizar_assets_valuaciones(col_assets, unidades):
    """Garantiza que cada unidad del snapshot exista en Valuaciones.Assets
    — la fuente de verdad UPPERCASE que edita el panel Manager → Assets.

    No pisa metadata ya cargada: usa `$ifNull` sobre los 7 campos
    UPPERCASE. Una unidad nueva queda con todos vacíos ("") y aparece en
    Manager lista para categorizar. CAFCI se deriva de la unidad.

    Es el ÚNICO destino de assets: la API lee Valuaciones.Assets directo
    (servicio api/services/titulos_flujos), ya no hay copia derivada AssetsAPI.
    """
    from core.cafci import extract_cafci
    for unidad in unidades:
        cafci = extract_cafci(unidad)
        col_assets.update_one(
            {"unidad": unidad},
            [{"$set": {
                "unidad":       unidad,
                "CARTERA":      {"$ifNull": ["$CARTERA",      ""]},
                "EMISOR":       {"$ifNull": ["$EMISOR",       ""]},
                "CLASE_ACTIVO": {"$ifNull": ["$CLASE_ACTIVO", ""]},
                "CALIFICACION": {"$ifNull": ["$CALIFICACION", ""]},
                "TICKER":       {"$ifNull": ["$TICKER",       ""]},
                "VENCIMIENTO":  {"$ifNull": ["$VENCIMIENTO",  ""]},
                "INSTRUMENTO":  {"$ifNull": ["$INSTRUMENTO",  ""]},
                "CAFCI":        cafci,
            }}],
            upsert=True,
        )


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


# ── Worker por cuenta (ejecutado en threads) ──────────────────────────────────
def _consultar_cuenta(cuenta_id, denominacion, idx, total, desde, fecha_snapshot, timestamp,
                      headers_ref, headers_lock):
    """Consulta y procesa una cuenta. Retorna lista de registros o []."""
    try:
        with headers_lock:
            h = dict(headers_ref)

        data, necesita_reauth = consultar_posicion(cuenta_id, h, desde)

        if necesita_reauth:
            with headers_lock:
                nuevos = autenticar()
                headers_ref.clear()
                headers_ref.update(nuevos)
                h = dict(headers_ref)
            data, _ = consultar_posicion(cuenta_id, h, desde)

        if not data:
            print(f"[{idx:3d}/{total}] [{cuenta_id}] {denominacion[:40]} → sin datos", flush=True)
            return []

        registros = procesar(data, fecha_snapshot, timestamp)
        print(f"[{idx:3d}/{total}] [{cuenta_id}] {denominacion[:40]} → {len(registros)} posiciones", flush=True)
        return registros

    except Exception as e:
        print(f"[{idx:3d}/{total}] [{cuenta_id}] ❌ Error: {e}", flush=True)
        return []


# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    print("🔑 Autenticando con Aunesa...", flush=True)
    headers_ref  = autenticar()   # dict mutable compartido entre threads
    headers_lock = threading.Lock()
    print("✅ Auth OK\n", flush=True)

    print("📋 Obteniendo listado de cuentas activas...", flush=True)
    cuentas = obtener_cuentas(headers_ref)
    total   = len(cuentas)
    print(f"   {total} cuentas activas encontradas\n", flush=True)

    client = get_mongo_client()
    col    = client["Valuaciones"]["AuM"]

    desde          = fecha_t2()
    timestamp      = datetime.utcnow()
    fecha_snapshot = timestamp.strftime("%Y-%m-%d")
    registros_total = 0

    futures_map = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        for i, row in cuentas.iterrows():
            cuenta_id    = str(row["id"])
            denominacion = row["denominacion"]
            f = executor.submit(
                _consultar_cuenta,
                cuenta_id, denominacion, i + 1, total,
                desde, fecha_snapshot, timestamp,
                headers_ref, headers_lock,
            )
            futures_map[f] = cuenta_id

        for f in as_completed(futures_map):
            cuenta_id = futures_map[f]
            registros = f.result()

            # Idempotencia cuando el cron corre N veces por día (intra-day):
            # antes de reinsertar las posiciones de esta cuenta, borrar los
            # docs viejos de la cuenta para HOY. Sin esto, posiciones que
            # se cerraron entre dos corridas del cron quedan como
            # fantasmas (la upsert key (id_cuenta, unidad, fecha_snapshot)
            # no detecta filas "que ya no vienen").
            # Gap de <1s entre delete y bulk_write — aceptable.
            col.delete_many({
                "id_cuenta":      cuenta_id,
                "fecha_snapshot": fecha_snapshot,
            })

            if not registros:
                continue

            ops = [
                UpdateOne(
                    {
                        "id_cuenta":      r["id_cuenta"],
                        "unidad":         r["unidad"],
                        "fecha_snapshot": r["fecha_snapshot"],
                    },
                    {"$set": r},
                    upsert=True,
                )
                for r in registros
            ]
            col.bulk_write(ops, ordered=False)
            registros_total += len(registros)

    print(f"\n🏁 Proceso finalizado. Total registros insertados: {registros_total}")

    # Sincronizar unidades del snapshot hacia los dos lados de Assets.
    unidades_snapshot = col.distinct("unidad", {"fecha_snapshot": fecha_snapshot})
    # Origen de verdad: Valuaciones.Assets (lo que edita Manager → Assets). La API
    # lee de acá directo (servicio titulos_flujos), ya no hay copia derivada AssetsAPI.
    _sincronizar_assets_valuaciones(client["Valuaciones"]["Assets"], unidades_snapshot)
    print(f"✅ Valuaciones.Assets sincronizado: {len(unidades_snapshot)} unidades.")

    # Pre-materializar resumen FCI por (fecha_snapshot, unidad)
    from jobs.aum_resumen_fci import sync_fecha as sync_resumen_fci
    sync_resumen_fci(client, fecha_snapshot)



if __name__ == "__main__":
    import time

    from core.job_runs import JobRunLogger
    MAX_INTENTOS = 3
    ESPERA_SEGUNDOS = 60

    # JobRunLogger envuelve la corrida: persiste el run en Manager.JobRuns y
    # alerta por Telegram si falla (antes el job moría en silencio).
    with JobRunLogger("aum"):
        for intento in range(1, MAX_INTENTOS + 1):
            try:
                run()
                break
            except requests.exceptions.Timeout as e:
                print(f"⚠️  Timeout en intento {intento}/{MAX_INTENTOS}: {e}", flush=True)
                if intento < MAX_INTENTOS:
                    print(f"   Reintentando en {ESPERA_SEGUNDOS}s...", flush=True)
                    time.sleep(ESPERA_SEGUNDOS)
                else:
                    print("❌ Se agotaron los reintentos.", flush=True)
                    sys.exit(1)
            except Exception as e:
                print(f"❌ Error inesperado: {e}", flush=True)
                sys.exit(1)
