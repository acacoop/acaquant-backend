import requests
import urllib3
from datetime import date
from mongo_manager import get_mongo_client

# Apagamos alertas SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def ejecutar_backfill_cer(desde="2023-01-01", hasta=None):
    if hasta is None:
        hasta = date.today().isoformat()

    id_cer = 30
    url_datos = f"https://api.bcra.gob.ar/estadisticas/v4.0/Monetarias/{id_cer}"

    params = {
        "desde": desde,
        "hasta": hasta
    }

    try:
        res_datos = requests.get(url_datos, params=params, verify=False)
        res_datos.raise_for_status()

        resultados_crudos = res_datos.json().get('results', [])

        if not resultados_crudos:
            print("El BCRA devolvió vacío.")
            return

        historial = resultados_crudos[0].get('detalle', [])

        if not historial:
            print("No hay datos de historial para ese rango.")
            return

        print(f"Se encontraron {len(historial)} registros del CER. Insertando en MongoDB...")

        client = get_mongo_client()
        coleccion = client["Trading"]["CER"]

        # Upsert por fecha para evitar duplicados en re-ejecuciones
        insertados = 0
        for d in historial:
            fecha = d.get('fecha')
            valor = d.get('valor')

            coleccion.update_one(
                {"fecha": fecha},
                {"$set": {"fecha": fecha, "valor": valor}},
                upsert=True
            )
            insertados += 1

        print(f"Listo. {insertados} registros guardados en Trading.CER.")

    except requests.exceptions.HTTPError as err:
        print(f"Error HTTP: {err.response.status_code}")
    except Exception as e:
        print(f"Error general: {e}")


if __name__ == "__main__":
    ejecutar_backfill_cer()
