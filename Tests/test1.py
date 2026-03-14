import os
import pyRofex
from session_manager import inicializar_sesion

TICKER_PRUEBA = "MERV - XMEV - T30A7 - 24hs"


def escanear_entradas_validas():
    os.system('cls' if os.name == 'nt' else 'clear')
    print(f"🔍 Escaneando endpoints habilitados para: {TICKER_PRUEBA}\n")

    if not inicializar_sesion():
        print("❌ Error de logueo.")
        return

    entradas_validas = []
    entradas_rechazadas = []

    # Iteramos sobre TODOS los enumeradores que existen en pyRofex
    for entrada in pyRofex.MarketDataEntry:
        try:
            # Hacemos un "ping" preguntando por 1 sola entrada a la vez
            res = pyRofex.get_market_data(ticker=TICKER_PRUEBA, entries=[entrada])

            # Si Rofex devuelve status OK, significa que el bono soporta este dato
            if res and res.get("status") == "OK":
                print(f"✅ SOPORTADO: {entrada.name}")
                entradas_validas.append(entrada.name)
            else:
                print(f"❌ RECHAZADO: {entrada.name}")
                entradas_rechazadas.append(entrada.name)
        except Exception:
            # Si la librería tira una excepción interna, también está rechazado
            print(f"❌ RECHAZADO: {entrada.name}")
            entradas_rechazadas.append(entrada.name)

    # --- IMPRESIÓN DEL REPORTE FINAL ---
    print("\n" + "=" * 60)
    print("🎯 COPIÁ Y PEGÁ ESTA LISTA EN TU WEBSOCKET:")
    print("=" * 60)

    print("entradas_bonos = [")
    for v in entradas_validas:
        print(f"    pyRofex.MarketDataEntry.{v},")
    print("]")
    print("=" * 60)


if __name__ == "__main__":
    escanear_entradas_validas()