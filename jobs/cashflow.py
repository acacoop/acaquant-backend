import argparse
import os
import sys
import unicodedata
from datetime import UTC, date, datetime, timedelta

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import config
from core import proveedores

AUTH_URL = "https://aca.aunesa.com/Irmo/api/login"
OPS_URL  = "https://aca.aunesa.com/Irmo/api/operaciones/consolidadosGenerales"

PALABRAS_CLAVE = ["deposito", "transferencia", "extraccion"]


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
    # El rastro para el detector de caídas (§0.an): este módulo NO pasa por
    # `core/aunesa`, así que sin esto su fallo es invisible para el agente.
    proveedores.mirar(resp)
    resp.raise_for_status()
    token = resp.json().get("token")
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}


def normalizar(s):
    return unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode("utf-8").lower()


def es_movimiento(informacion):
    norm = normalizar(informacion)
    return any(p in norm for p in PALABRAS_CLAVE)


def dias_habiles(desde, hasta):
    from core.calendario import habiles_entre
    return habiles_entre(desde, hasta)


def fetch_dia(dia_str, headers):
    params = {
        "tiposCuenta": "Comitente",
        "concertacionDesde": dia_str,
        "concertacionHasta": dia_str,
    }
    resp = requests.get(OPS_URL, params=params, headers=headers, timeout=30)
    if resp.status_code == 401:
        print("⚠️  Token expirado, re-autenticando...", flush=True)
        headers = autenticar()
        resp = requests.get(OPS_URL, params=params, headers=headers, timeout=30)
    proveedores.mirar(resp)
    if resp.status_code == 400:
        print(f"\n🔍 Respuesta 400 para {dia_str}: {resp.text[:500]}", flush=True)
    resp.raise_for_status()
    return resp.json(), headers


def run(desde, hasta):
    print("🔑 Autenticando con Aunesa...", flush=True)
    headers = autenticar()
    print("✅ Auth OK\n", flush=True)

    # SQL-NATIVE: CashFlow.Movimientos (Mongo) fue migrada → dropeada. Escribe directo a
    # operaciones.movimientos (upsert por comprobante, write_native). La PK comprobante hace
    # el upsert idempotente — la API re-envía el mismo valor por boleto → mismo efecto.
    from core.pg_mirror import doc_iso, write_native

    dias  = dias_habiles(desde, hasta)
    total = len(dias)
    print(f"📅 Días hábiles a procesar: {total}  ({desde} → {hasta})\n", flush=True)

    insertados_total = 0

    for i, dia in enumerate(dias, 1):
        dia_str = dia.strftime("%d/%m/%Y")
        print(f"[{i:3d}/{total}] {dia_str} ...", end="  ", flush=True)

        try:
            data, headers = fetch_dia(dia_str, headers)

            if not data:
                print("sin datos")
                continue

            movimientos = [r for r in data if es_movimiento(r.get("informacion", ""))]

            if not movimientos:
                print("0 movimientos")
                continue

            # La API devuelve el signo invertido: depósitos como negativos.
            # Invertimos para que entradas sean positivas y salidas negativas.
            for r in movimientos:
                if "total" in r:
                    r["total"] = float(r["total"]) * -1

            # Filtrar movimientos sin `comprobante`: es la PK del upsert SQL → sin esa
            # clave la fila no puede materializarse. Mismo patrón defensivo que
            # negocio_movimientos.py.
            con_comp = [r for r in movimientos if r.get("comprobante")]
            sin_comp = len(movimientos) - len(con_comp)
            if sin_comp:
                print(f"⚠ {sin_comp} movimientos sin comprobante — salteados")
            if not con_comp:
                print("0 con comprobante")
                continue

            # Escritura SQL-NATIVE a operaciones.movimientos (upsert por comprobante).
            # `fecha` se guarda CRUDA dd/mm/yyyy (el read service la parsea a ISO).
            # data = doc completo (datetimes→ISO).
            sql_rows = [{
                "comprobante": r["comprobante"], "cuenta": r.get("cuenta"),
                "fecha": r.get("fecha"), "informacion": r.get("informacion"),
                "total": r.get("total"), "unidad": r.get("unidad"),
                "data": doc_iso(r),
            } for r in con_comp]
            write_native("operaciones.movimientos", ["comprobante"], sql_rows)
            insertados_total += len(con_comp)
            print(f"{len(con_comp)} con comprobante  →  upsert SQL")

        except Exception as e:
            print(f"❌ Error: {e}")

    print(f"\n🏁 Proceso finalizado. Total insertados: {insertados_total}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--today", action="store_true",
        help="Procesar solo el día de hoy en horario Argentina (para cron diario)"
    )
    args = parser.parse_args()

    from core.job_runs import JobRunLogger
    with JobRunLogger("cashflow"):
        if args.today:
            # El cron corre a las 02:00 UTC = 23:00 ART del día anterior
            hoy_art = (datetime.now(UTC) - timedelta(hours=3)).date()
            run(desde=hoy_art, hasta=hoy_art)
        else:
            run(desde=date(2025, 7, 1), hasta=date.today())
