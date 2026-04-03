import argparse
import requests
import urllib3
from datetime import date
from mongo_manager import get_mongo_client

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

VARIABLES_BCRA = {
    "CER":    30,
    "TAMAR":  44,
    "DOLAR":   5,
    "BADLAR":  7,
}


def fetch_y_guardar(nombre, id_variable, desde, hasta):
    url = f"https://api.bcra.gob.ar/estadisticas/v4.0/Monetarias/{id_variable}"
    params = {"desde": desde, "hasta": hasta}

    try:
        res = requests.get(url, params=params, verify=False)
        res.raise_for_status()

        resultados = res.json().get('results', [])
        if not resultados:
            print(f"[{nombre}] BCRA devolvió vacío.")
            return

        historial = resultados[0].get('detalle', [])
        if not historial:
            print(f"[{nombre}] Sin datos para el rango {desde} → {hasta}.")
            return

        client = get_mongo_client()
        coleccion = client["Trading"][nombre]

        for d in historial:
            fecha = d.get('fecha')
            valor = d.get('valor')
            coleccion.update_one(
                {"fecha": fecha},
                {"$set": {"fecha": fecha, "valor": valor}},
                upsert=True
            )

        print(f"[{nombre}] {len(historial)} registros guardados en Trading.{nombre}.")

    except requests.exceptions.HTTPError as err:
        print(f"[{nombre}] Error HTTP: {err.response.status_code}")
    except Exception as e:
        print(f"[{nombre}] Error: {e}")


def run(today=False):
    if today:
        desde = hasta = date.today().isoformat()
    else:
        desde = "2023-01-01"
        hasta = date.today().isoformat()

    for nombre, id_variable in VARIABLES_BCRA.items():
        fetch_y_guardar(nombre, id_variable, desde, hasta)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--today", action="store_true", help="Fetch solo el dia de hoy para todas las variables")
    args = parser.parse_args()
    run(today=args.today)
