import pyRofex
from config import Config

def inicializar_sesion():
    """Establece los parámetros de entorno e inicia sesión en pyRofex"""
    try:
        # Configuración de entorno LIVE
        pyRofex._set_environment_parameter("url", Config.URL, pyRofex.Environment.LIVE)
        pyRofex._set_environment_parameter("ws", Config.WS, pyRofex.Environment.LIVE)

        # Inicialización de la sesión
        pyRofex.initialize(
            user=Config.USER,
            password=Config.PASSWORD,
            account=Config.ACCOUNT,
            environment=pyRofex.Environment.LIVE
        )
        print("✅ Sesión inicializada correctamente en pyRofex.")
        return True
    except Exception as e:
        print(f"❌ Error al inicializar sesión: {e}")
        return False

if __name__ == "__main__":
    # Prueba rápida de conexión
    inicializar_sesion()