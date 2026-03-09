import pyRofex


def obtener_caucion_mas_corta():
    """
    Se conecta al padrón de Rofex, busca todos los instrumentos de caución en pesos,
    y devuelve la de menor plazo (ej: 1D en días hábiles normales, o 3D/4D los viernes o pre-feriados).

    Retorna:
        tuple: (ticker_caucion, dias_int) -> Ej: ('MERV - XMEV - PESOS - 1D', 1)
    """
    print("🔍 Escaneando el mercado en busca de la Caución más corta...")

    response = pyRofex.get_all_instruments()

    if not response or response.get("status") != "OK":
        print("❌ Error crítico: No se pudo obtener la lista de instrumentos para la caución.")
        return None, 0

    cauciones_disponibles = []

    for item in response['instruments']:
        symbol = item['instrumentId']['symbol']

        # Filtramos estrictamente las cauciones en pesos
        if symbol.startswith("MERV - XMEV - PESOS - ") and symbol.endswith("D"):
            try:
                # Extraemos la última parte (ej: "1D", "3D") y le sacamos la "D" para convertirlo a número
                partes = symbol.split(" - ")
                dias_str = partes[-1].replace("D", "")
                dias = int(dias_str)

                cauciones_disponibles.append({
                    "ticker": symbol,
                    "dias": dias
                })
            except ValueError:
                # Si por algún motivo el formato es raro, lo ignoramos para no romper el bot
                continue

    if not cauciones_disponibles:
        print("⚠️ Advertencia: No se encontraron cauciones activas en el padrón de hoy.")
        return None, 0

    # Ordenamos la lista de menor a mayor cantidad de días
    cauciones_disponibles.sort(key=lambda x: x["dias"])

    # Nos quedamos con la de menor plazo (la primera de la lista)
    caucion_optima = cauciones_disponibles[0]

    print(f"✅ Caución detectada y seleccionada: {caucion_optima['ticker']} ({caucion_optima['dias']} días)")

    return caucion_optima["ticker"], caucion_optima["dias"]