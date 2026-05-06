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
from jobs._aum_filters import is_excluded

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
def obtener_cuentas(headers):
    resp = _SESSION.get(LISTADO_URL, headers=headers, timeout=60)
    resp.raise_for_status()
    df = pd.DataFrame(resp.json())
    activas = df[
        df["tipo"].isin(["Comitente", "Propia"]) &
        (df["estado"] == "Activa")
    ][["id", "denominacion"]].copy()
    return activas.reset_index(drop=True)


# ── Consulta posición por cuenta ──────────────────────────────────────────────
def consultar_posicion(cuenta_id, headers, desde):
    params = {
        "desde":           desde,
        "hasta":           "",
        "tipoCuenta":      "Comitentes y propias",
        "nivel":           "Especie x cuenta",
        "ocultarCerradas": "true",
    }
    # Timeout=120 (subido de 60 el 2026-05-06): cuentas con muchas
    # posiciones tardan más de 60s del lado de Aunesa al computar la
    # valuación. Con 60s se observaba ~30% de timeouts en backfills.
    resp = _SESSION.get(
        POSICION_URL.format(cuenta_id),
        params=params,
        headers=headers,
        timeout=120,
    )
    if resp.status_code == 401:
        return None, True   # señal de re-auth
    if resp.status_code != 200:
        return None, False
    return resp.json(), False


# ── Reglas de valuación ──────────────────────────────────────────────────────
def _sincronizar_assets(col_assets, unidades):
    """
    Asegura que cada unidad exista en TitulosAPI.AssetsAPI con los campos requeridos.
    No pisa valores existentes — solo completa los que faltan ($ifNull).
    """
    for unidad in unidades:
        col_assets.update_one(
            {"unidad": unidad},
            [{"$set": {
                "unidad":       unidad,
                "calificacion": {"$ifNull": ["$calificacion", ""]},
                "cartera":      {"$ifNull": ["$cartera",      ""]},
                "clase_activo": {"$ifNull": ["$clase_activo", ""]},
                "emisor":       {"$ifNull": ["$emisor",       ""]},
                "ticker":       {"$ifNull": ["$ticker",       ""]},
                "vencimiento":  {"$ifNull": ["$vencimiento",  None]},
                "instrumento":  {"$ifNull": ["$instrumento",  ""]},
            }}],
            upsert=True,
        )


TIPOS_DIVISOR_100 = {
    "Títulos Públicos",
    "Letras del Tesoro Capitalizables en Pesos",
    "Letras del Tesoro Ajustables por CER en Pesos",
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
    # criterio. Hoy cubre: OTC en cuenta/unidad, unidad USDL, cuentas con
    # SCHRODER/TORONTO/ALLARIA, y la tenencia ARS de [100]/[101].
    df_g = df_g[~df_g.apply(
        lambda row: is_excluded(row.get("cuenta"), row.get("unidad")),
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


# ── CarterasII: snapshot fijo del primer día hábil del mes anterior ──────────
def _primer_dia_habil_mes_anterior(hoy_str):
    """
    Devuelve la fecha (YYYY-MM-DD) del primer día hábil del mes anterior
    relativo a hoy_str. Consulta Trading.DiasHabiles.
    """
    hoy = datetime.strptime(hoy_str, "%Y-%m-%d").date()
    primer_dia_mes_actual = hoy.replace(day=1)
    ultimo_dia_mes_anterior = primer_dia_mes_actual - timedelta(days=1)
    primer_dia_mes_anterior = ultimo_dia_mes_anterior.replace(day=1)

    client = get_mongo_client()
    col_dh = client["Trading"]["DiasHabiles"]
    doc = col_dh.find_one(
        {
            "fecha": {
                "$gte": primer_dia_mes_anterior.isoformat(),
                "$lte": ultimo_dia_mes_anterior.isoformat(),
            }
        },
        sort=[("fecha", 1)],
    )
    return doc["fecha"] if doc else None


def sync_carteras_ii(hoy_str=None):
    """
    Reconstruye Valuaciones.CarterasII con los docs de Valuaciones.AuM
    cuyo fecha_snapshot = primer día hábil del mes anterior a hoy.
    Overwrite completo (upsert + delete de docs huérfanos).
    """
    if hoy_str is None:
        hoy_str = datetime.utcnow().strftime("%Y-%m-%d")

    fecha_target = _primer_dia_habil_mes_anterior(hoy_str)
    if not fecha_target:
        print("⚠️ CarterasII: no se encontró primer día hábil del mes anterior en DiasHabiles.")
        return

    client = get_mongo_client()
    col_aum = client["Valuaciones"]["AuM"]
    col_cii = client["Valuaciones"]["CarterasII"]

    docs = list(col_aum.find({"fecha_snapshot": fecha_target}, {"_id": 0}))
    if not docs:
        print(f"⚠️ CarterasII: AuM vacío para {fecha_target}. No se actualiza.")
        return

    ops = [
        UpdateOne(
            {"id_cuenta": d["id_cuenta"], "unidad": d["unidad"]},
            {"$set": d},
            upsert=True,
        )
        for d in docs
    ]
    col_cii.bulk_write(ops, ordered=False)

    col_cii.delete_many({"fecha_snapshot": {"$ne": fecha_target}})

    print(f"✅ CarterasII sincronizado: {len(docs)} docs para fecha_snapshot={fecha_target}")


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
            registros = f.result()
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

    # Sincronizar unidades nuevas hacia TitulosAPI.AssetsAPI (fuente de verdad de la API)
    unidades_snapshot = col.distinct("unidad", {"fecha_snapshot": fecha_snapshot})
    col_assets = client["TitulosAPI"]["AssetsAPI"]
    _sincronizar_assets(col_assets, unidades_snapshot)
    print(f"✅ AssetsAPI sincronizado: {len(unidades_snapshot)} unidades revisadas.")

    # Sincronizar CarterasII (primer día hábil del mes anterior a hoy)
    sync_carteras_ii(fecha_snapshot)

    # Pre-materializar resumen FCI por (fecha_snapshot, unidad)
    from jobs.aum_resumen_fci import sync_fecha as sync_resumen_fci
    sync_resumen_fci(client, fecha_snapshot)



if __name__ == "__main__":
    import time
    MAX_INTENTOS = 3
    ESPERA_SEGUNDOS = 60

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
