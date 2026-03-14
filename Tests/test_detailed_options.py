import pyRofex
from session_manager import inicializar_sesion


def filtrar_y_ordenar_galicia():
    # 1. Iniciamos sesión con tu lógica de siempre
    if not inicializar_sesion():
        return

    try:
        print("🔍 Consultando maestra detallada y ordenando por fecha...")
        res = pyRofex.get_detailed_instruments()

        if res['status'] == 'OK':
            # Diccionario de meses para la terminal
            MESES_MAP = {
                "02": "FEBRERO (FE)",
                "04": "ABRIL (AB)",
                "06": "JUNIO (JU)",
                "08": "AGOSTO (AG)",
                "10": "OCTUBRE (OC)",
                "12": "DICIEMBRE (DI)"
            }

            all_inst = res['instruments']

            # Filtros solicitados
            TARGET_UNDERLYING = "Grupo Financiero Galicia Merval"
            TARGET_CFI = "OCASPS"  # Calls

            # 2. Filtrado inicial
            filtered = [
                i for i in all_inst
                if i.get('underlying') == TARGET_UNDERLYING
                   and i.get('cficode') == TARGET_CFI
            ]

            # 3. ORDENAMIENTO POR FECHA (MaturityDate: YYYYMMDD)
            # Esto pone los vencimientos más cercanos arriba
            filtered_sorted = sorted(filtered, key=lambda x: x.get('maturityDate', '99991231'))

            print(f"\n✅ Opciones de {TARGET_UNDERLYING} ordenadas por vencimiento")
            print("—" * 90)
            print(f"{'SÍMBOLO':<30} | {'STRIKE':^10} | {'VENCIMIENTO':^12} | {'MES / BASE':^15}")
            print("—" * 90)

            for opt in filtered_sorted:
                symbol = opt['instrumentId']['symbol']
                strike = opt.get('strike')
                vence_raw = opt.get('maturityDate')

                # Procesamiento del mes
                mes_num = vence_raw[4:6]
                mes_nombre = MESES_MAP.get(mes_num, "OTRO")

                print(f"{symbol:<30} | {strike:^10.2f} | {vence_raw:^12} | {mes_nombre:^15}")

            print("—" * 90)
        else:
            print(f"❌ Error: {res}")

    except Exception as e:
        print(f"⚠️ Error: {e}")


if __name__ == "__main__":
    filtrar_y_ordenar_galicia()