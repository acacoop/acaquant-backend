import logging
import sys

def setup_logger():
    logger = logging.getLogger("TradingBot")
    logger.setLevel(logging.DEBUG)

    # Formato: Fecha - Nivel - Mensaje
    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s', '%H:%M:%S')

    # Handler para Consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # Handler para Archivo (para ver qué pasó ayer si se rompió)
    file_handler = logging.FileHandler("bot_debug.log")
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return logger