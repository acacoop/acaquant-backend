import pyRofex
from session_manager import inicializar_sesion


def listar_underlyings_oca():
    # 1. Inicialización de sesión con tus credenciales y URLs
    if not inicializar_sesion():
        return

    try:
        print("🔍 Filtrando subyacentes con Calls (OCASPS) habilitados...")

        # 2. Obtenemos la maestra detallada para acceder al cficode
        res = pyRofex.get_detailed_instruments()

        if res['status'] == 'OK':
            instrumentos = res['instruments']

            # 3. Filtrado quirúrgico:
            # Solo tomamos el 'underlying' si el 'cficode' es exactamente 'OCASPS'
            target_cfi = "OCASPS"
            underlyings_filtrados = set()

            for inst in instrumentos:
                if inst.get('cficode') == target_cfi:
                    u = inst.get('underlying')
                    if u:
                        underlyings_filtrados.add(u)

            # 4. Mostrar resultados ordenados
            lista_final = sorted(list(underlyings_filtrados))

            print(f"\n✅ Se encontraron {len(lista_final)} subyacentes con Calls disponibles:")
            print("—" * 60)
            for nombre in lista_final:
                print(f"• {nombre}")
            print("—" * 60)

        else:
            print(f"❌ Error de Rofex: {res}")

    except Exception as e:
        print(f"⚠️ Error en la ejecución: {e}")


if __name__ == "__main__":
    listar_underlyings_oca()