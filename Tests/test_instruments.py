import json
import pandas as pd
import logging

# Configuración básica de logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Exportador")


def exportar_a_excel():
    json_file = "../lista_instrumentos_completa.json"
    output_file = "instrumentos_mercado_completo.xlsx"

    try:
        # 1. Leer el archivo JSON
        logger.info(f"Leyendo {json_file}...")
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if data.get('status') != 'OK':
            logger.error("El JSON no tiene un estado OK.")
            return

        # 2. Procesar la lista de instrumentos
        # Extraemos y aplanamos la estructura de 'instrumentId'
        instrumentos_raw = data.get('instruments', [])
        lista_aplanada = []

        for inst in instrumentos_raw:
            # Extraemos los datos clave
            info = {
                "Symbol": inst.get("instrumentId", {}).get("symbol"),
                "Market": inst.get("instrumentId", {}).get("marketId"),
                "CFICode": inst.get("cficode"),
                "Currency": inst.get("currency")  # Algunos lo traen, otros no
            }
            lista_aplanada.append(info)

        # 3. Crear DataFrame y exportar
        df = pd.DataFrame(lista_aplanada)

        # Ordenamos por Símbolo para que sea fácil de navegar
        df = df.sort_values(by="Symbol")

        logger.info(f"Exportando {len(df)} registros a Excel...")
        df.to_excel(output_file, index=False)

        print("\n" + "✅" * 10)
        print(f"ARCHIVO CREADO: {output_file}")
        print(f"Ya podés abrirlo para buscar tus activos.")
        print("✅" * 10)

    except FileNotFoundError:
        logger.error(f"No se encontró el archivo {json_file}. Corré primero el test anterior.")
    except Exception as e:
        logger.error(f"Error inesperado: {e}")


if __name__ == "__main__":
    exportar_a_excel()