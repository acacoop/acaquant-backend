import psutil
import os


def limpieza_quirurgica():
    # Solo estos archivos son los que queremos cerrar
    scripts_objetivo = [
        "main_ts.py",
        "main_options.py",
        "main_arbitrage.py",
        "test_auditor.py"
    ]

    mi_pid = os.getpid()
    conteo_cerrados = 0

    print("--- INICIANDO LIMPIEZA DE TRADING ---")

    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info['cmdline']
            if cmdline:
                # Convertimos la línea de comandos a un solo texto para buscar
                linea_completa = " ".join(cmdline)

                # Verificamos si el proceso es uno de nuestros scripts de trading
                # PERO nos aseguramos de no matarnos a nosotros mismos (este script)
                if any(script in linea_completa for script in scripts_objetivo):
                    pid = proc.info['pid']
                    if pid != mi_pid:
                        print(f"❌ Cerrando proceso de trading: {linea_completa} (PID: {pid})")
                        proc.kill()
                        conteo_cerrados += 1

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if conteo_cerrados == 0:
        print("✨ No se encontraron zombies de trading activos.")
    else:
        print(f"✅ Se limpiaron {conteo_cerrados} procesos. Anaconda y el resto siguen intactos.")


if __name__ == "__main__":
    limpieza_quirurgica()
    input("\nPresioná Enter para salir...")