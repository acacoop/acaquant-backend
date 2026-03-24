import sys
import os
import holidays
import requests
import pandas as pd
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


# ── Fecha T+2 ─────────────────────────────────────────────────────────────────
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
    data = resp.json()
    df = pd.DataFrame(data)
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


# ── Procesar respuesta → lista de dicts ──────────────────────────────────────
def procesar(data, cuenta_id, timestamp):
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

    # Precio solo para Fondos de Inversión
    df_g["precio"] = df_g.apply(
        lambda x: x["precio"] if x["tipoTitulo"] == "Fondos de Inversión" else None,
        axis=1,
    )

    df_g["ultimo_update"] = timestamp
    return df_g.to_dict(orient="records")


# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    print("🔑 Autenticando con Aunesa...", flush=True)
    headers = autenticar()
    print("✅ Auth OK\n", flush=True)

    print("📋 Obteniendo listado de cuentas activas...", flush=True)
    cuentas = obtener_cuentas(headers)
    total   = len(cuentas)
    print(f"   {total} cuentas activas encontradas\n", flush=True)

    client    = get_mongo_client()
    col       = client["Valuaciones"]["AuM"]
    col.create_index([("id_cuenta", 1), ("unidad", 1)], unique=True, background=True)

    desde     = fecha_t2()
    timestamp = datetime.utcnow()
    registros_total = 0

    for i, row in cuentas.iterrows():
        cuenta_id   = str(row["id"])
        denominacion = row["denominacion"]
        print(f"[{i+1:3d}/{total}] [{cuenta_id}] {denominacion[:50]:<50} ...", end="  ", flush=True)

        try:
            data, necesita_reauth = consultar_posicion(cuenta_id, headers, desde)

            if necesita_reauth:
                print("⚠️  Re-autenticando...", flush=True)
                headers = autenticar()
                data, _ = consultar_posicion(cuenta_id, headers, desde)

            if not data:
                print("sin datos")
                continue

            registros = procesar(data, cuenta_id, timestamp)

            if not registros:
                print("0 posiciones")
                continue

            # Upsert inmediato
            ops = [
                UpdateOne(
                    {"id_cuenta": r["id_cuenta"], "unidad": r["unidad"]},
                    {"$set": r},
                    upsert=True,
                )
                for r in registros
            ]
            col.bulk_write(ops, ordered=False)

            # Eliminar posiciones que ya no existen para esta cuenta
            unidades_actuales = [r["unidad"] for r in registros]
            col.delete_many({
                "id_cuenta": str(cuenta_id),
                "unidad":    {"$nin": unidades_actuales},
            })

            registros_total += len(registros)
            print(f"{len(registros)} posiciones guardadas")

        except Exception as e:
            print(f"❌ Error: {e}")

    print(f"\n🏁 Proceso finalizado. Total registros en AuM: {registros_total}")
    client.close()


if __name__ == "__main__":
    run()
