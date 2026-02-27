import pyRofex
from session_manager import inicializar_sesion
from datetime import datetime

# --- TU LISTA DE SELECCIONADOS ---
# Acá ponés exactamente los tickers que querés ver en el Excel
MIS_ELEGIDOS = [
    "MERV - XMEV - GGAL - 24hs",
    "MERV - XMEV - GFGC88515A - 24hs",
    "MERV - XMEV - GFGV88515A - 24hs",
    "MERV - XMEV - GFGC82282A - 24hs",
    "MERV - XMEV - GFGV82282A - 24hs",
    "MERV - XMEV - GFGC69282A - 24hs",
    "MERV - XMEV - GFGV69282A - 24hs",
    "MERV - XMEV - GFGC85754A - 24hs",
    "MERV - XMEV - GFGV85754A - 24hs",
    "MERV - XMEV - GFGC97054A - 24hs",
    "MERV - XMEV - GFGV97054A - 24hs",
    "MERV - XMEV - GFGC75282A - 24hs",
    "MERV - XMEV - GFGV75282A - 24hs",
    "MERV - XMEV - GFGC59754A - 24hs",
    "MERV - XMEV - GFGV59754A - 24hs",
    "MERV - XMEV - GFGC79515A - 24hs",
    "MERV - XMEV - GFGV79515A - 24hs",
    "MERV - XMEV - GFGV61515A - 24hs",
    "MERV - XMEV - GFGC61515A - 24hs",
    "MERV - XMEV - GFGC73515A - 24hs",
    "MERV - XMEV - GFGV73515A - 24hs",
    "MERV - XMEV - GFGC6600AB - 24hs",
    "MERV - XMEV - GFGV6600AB - 24hs",
    "MERV - XMEV - GFGC77515A - 24hs",
    "MERV - XMEV - GFGV77515A - 24hs"
]


def obtener_data_galicia():
    """
    Descarga toda la info de Rofex pero devuelve solo
    los metadatos de los activos en MIS_ELEGIDOS.
    """
    if not inicializar_sesion():
        return [], {}, {}

    res = pyRofex.get_detailed_instruments()
    if res['status'] != 'OK':
        return [], {}, {}

    # 1. Filtro base: Todo lo de Galicia
    gal = [i for i in res['instruments']
           if i.get('underlying') == "Grupo Financiero Galicia Merval"]

    hoy_dt = datetime.now()
    mapa_strikes = {}
    vencimientos_info = {}

    # 2. Mapeamos TODOS los instrumentos de Galicia para tener sus datos técnicos
    for inst in gal:
        symbol = inst['instrumentId']['symbol']
        vto = inst.get('maturityDate')

        # Clasificamos tipo
        cfi = inst.get('cficode', '')
        tipo = "CALL" if cfi == "OCASPS" else ("PUT" if cfi == "OPASPS" else "ACCION")

        # Guardamos en el mapa técnico
        mapa_strikes[symbol] = {
            'strike': inst.get('strike', 0) if tipo != "ACCION" else "-",
            'tipo': tipo,
            'vencimiento': vto if vto else "N/A"
        }

        # Si es un vencimiento futuro, calculamos días restantes para las funciones cuantitativas
        if vto and vto not in vencimientos_info:
            try:
                fecha_vto_dt = datetime.strptime(vto, "%Y%m%d")
                vencimientos_info[vto] = (fecha_vto_dt - hoy_dt).days
            except:
                pass

    # 3. FILTRADO FINAL: Solo lo que vos elegiste manualmente
    # Esto asegura que el WebSocket solo se suscriba a lo que querés
    tickers_a_monitorear = [t for t in MIS_ELEGIDOS if t in mapa_strikes]

    # Si algún elegido no está en el mapa (ej: error de tipeo), avisamos
    for t in MIS_ELEGIDOS:
        if t not in mapa_strikes:
            print(f"⚠️ Alerta: El ticker '{t}' no fue encontrado en Rofex. Revisar nombre.")

    return tickers_a_monitorear, mapa_strikes, vencimientos_info


# Exportación de variables para main_opciones y el manager
TICKERS_DINAMICOS, MAPA_STRIKES, VENCIMIENTOS_INFO = obtener_data_galicia()