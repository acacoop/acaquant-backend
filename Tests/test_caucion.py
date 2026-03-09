import pyRofex
import sys
from session_manager import inicializar_sesion


def test_buscador():
    print("🚀 Iniciando Test del Buscador de Caución...")

    # 1. Necesitamos conectarnos al broker primero
    if not inicializar_sesion():
        print("❌ Error: No se pudo iniciar sesión.")
        sys.exit(1)

    print("🔍 Descargando el padrón completo de instrumentos...")
    response = pyRofex.get_all_instruments()

    if not response or response.get("status") != "OK":
        print("❌ Error crítico: No se pudo obtener la lista de instrumentos.")
        sys.exit(1)

    cauciones_disponibles = []

    # 2. Escaneamos buscando cauciones
    for item in response['instruments']:
        symbol = item['instrumentId']['symbol']

        if symbol.startswith("MERV - XMEV - PESOS - ") and symbol.endswith("D"):
            try:
                partes = symbol.split(" - ")
                dias_str = partes[-1].replace("D", "")
                dias = int(dias_str)

                cauciones_disponibles.append({
                    "ticker": symbol,
                    "dias": dias
                })
            except ValueError:
                continue

    if not cauciones_disponibles:
        print("⚠️ No se encontraron cauciones activas.")
        sys.exit(1)

    # 3. Mostramos todo lo que encontró
    cauciones_disponibles.sort(key=lambda x: x["dias"])

    print("\n📋 Cauciones en pesos detectadas en el mercado hoy:")
    for c in cauciones_disponibles:
        print(f"   -> Ticker: {c['ticker']:<30} | Plazo: {c['dias']} días")

    # 4. El resultado final que devolvería la función
    caucion_optima = cauciones_disponibles[0]

    print("\n🏆 RESULTADO FINAL (La que seleccionará el bot automáticamente):")
    print(f"   Ticker: {caucion_optima['ticker']}")
    print(f"   Días para el cálculo matemático: {caucion_optima['dias']}")


if __name__ == "__main__":
    test_buscador()