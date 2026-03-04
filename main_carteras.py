import sys
import holidays
from datetime import datetime, timedelta
from aunesa_api_manager import AunesaApiManager
from excel_carteras_manager import ExcelCarterasManager

# Definición de las cuentas
CUENTAS_OBJETIVO = ["100", "255", "101", "163"]


def obtener_fechas_habiles():
    """
    Calcula el T+2 (próximo próximo día hábil) para el 'Desde'.
    Salta fines de semana y feriados.
    """
    arg_holidays = holidays.Argentina()

    def proximo_habil(fecha_ref):
        proximo = fecha_ref + timedelta(days=1)
        while proximo.weekday() >= 5 or proximo in arg_holidays:
            proximo += timedelta(days=1)
        return proximo

    hoy = datetime.now()
    t_mas_1 = proximo_habil(hoy)
    t_mas_2 = proximo_habil(t_mas_1)

    return t_mas_2.strftime("%d/%m/%Y"), ""


def run():
    print("🚀 Iniciando Actualización Única de Carteras...")

    fecha_desde, fecha_hasta = obtener_fechas_habiles()
    print(f"📅 Consulta Desde (T+2): {fecha_desde}")

    api_manager = AunesaApiManager()
    excel_manager = ExcelCarterasManager()

    try:
        # 1. Consultar a la API
        df_carteras = api_manager.consultar_cuentas(
            CUENTAS_OBJETIVO,
            desde=fecha_desde,
            hasta=fecha_hasta
        )

        # 2. Escribir en Excel
        if df_carteras is not None and not df_carteras.empty:
            print(f"📊 Registros consolidados: {len(df_carteras)}")

            if excel_manager.escribir_data(df_carteras):
                print("✅ Hoja 'CARTERAS' actualizada correctamente.")
            else:
                print("❌ Error al escribir en Excel.")
        else:
            print("⚠️ No se recuperaron datos de la API.")

    except Exception as e:
        print(f"🔥 Error crítico en main: {e}")

    print("🏁 Proceso finalizado. Saliendo...")
    sys.exit()


if __name__ == "__main__":
    run()