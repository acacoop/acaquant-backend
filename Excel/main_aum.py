import sys
import os
import holidays
import requests
import pandas as pd
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pymongo import UpdateOne

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import config
from mongo_manager import get_mongo_client

AUTH_URL     = "https://aca.aunesa.com/Irmo/api/login"
LISTADO_URL  = "https://aca.aunesa.com/Irmo/api/cuentas/listadoCuentas"
POSICION_URL = "https://aca.aunesa.com/Irmo/api/cuentas/{}/posicionValuada"


# ── Auth ──────────────────────────────────────────────────────────────────────
def autenticar():
    resp = requests.post(
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
    resp = requests.get(LISTADO_URL, headers=headers, timeout=15)
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
    resp = requests.get(
        POSICION_URL.format(cuenta_id),
        params=params,
        headers=headers,
        timeout=20,
    )
    if resp.status_code == 401:
        return None, True   # señal de re-auth
    if resp.status_code != 200:
        return None, False
    return resp.json(), False


# ── Reglas de valuación ──────────────────────────────────────────────────────
CAMPOS_ASSETS = ["CALIFICACION", "CARTERA", "CLASE_ACTIVO", "EMISOR", "TICKER", "VENCIMIENTO"]


def _sincronizar_assets(col_assets, unidades):
    """
    Asegura que cada unidad exista en Assets con los 6 campos requeridos.
    No pisa valores existentes — solo completa los que faltan.
    """
    for unidad in unidades:
        col_assets.update_one(
            {"unidad": unidad},
            [{"$set": {
                "unidad":       unidad,
                "CALIFICACION": {"$ifNull": ["$CALIFICACION", ""]},
                "CARTERA":      {"$ifNull": ["$CARTERA",      ""]},
                "CLASE_ACTIVO": {"$ifNull": ["$CLASE_ACTIVO", ""]},
                "EMISOR":       {"$ifNull": ["$EMISOR",       ""]},
                "TICKER":       {"$ifNull": ["$TICKER",       ""]},
                "VENCIMIENTO":  {"$ifNull": ["$VENCIMIENTO",  ""]},
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
    # 1. Eliminar cualquier registro que contenga "OTC" en cuenta o unidad
    otc_mask = (
        df_g["cuenta"].str.contains("OTC", case=False, na=False) |
        df_g["unidad"].str.contains("OTC", case=False, na=False)
    )
    df_g = df_g[~otc_mask].copy()

    # 2. Eliminar cash (ARS/USD) con cantidad negativa
    cash_neg = (
        df_g["unidad"].isin(["ARS", "USD"]) &
        (df_g["cantidad"] < 0)
    )
    df_g = df_g[~cash_neg].copy()

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

    # Sincronizar unidades nuevas hacia Assets
    unidades_snapshot = col.distinct("unidad", {"fecha_snapshot": fecha_snapshot})
    col_assets = client["Valuaciones"]["Assets"]
    _sincronizar_assets(col_assets, unidades_snapshot)
    print(f"✅ Assets sincronizado: {len(unidades_snapshot)} unidades revisadas.")

    client.close()


if __name__ == "__main__":
    run()
