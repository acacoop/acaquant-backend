import psutil
import os
from datetime import datetime


def escanear_procesos():
    print(f"{'=' * 100}")
    print(f"{'PID':<8} | {'HORA INICIO':<10} | {'ARCHIVO / SCRIPT':<40} | {'RUTA EJECUCIÓN'}")
    print(f"{'=' * 100}")

    proceso_actual = os.getpid()
    encontrados = 0

    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time', 'exe']):
        try:
            # Filtramos solo lo que sea Python
            if 'python' in proc.info['name'].lower():
                pid = proc.info['pid']

                # Hora en que se prendió ese proceso
                inicio = datetime.fromtimestamp(proc.info['create_time']).strftime("%H:%M:%S")

                # Qué script está corriendo (si lo tiene)
                cmd = proc.info['cmdline']
                script = cmd[-1] if len(cmd) > 1 else "Python Consola"

                # Desde qué carpeta se está ejecutando
                ruta = proc.info['exe']

                tag = "[ESTE]" if pid == proceso_actual else "      "

                print(f"{pid:<8} | {inicio:<10} | {script[:40]:<40} | {ruta}")
                encontrados += 1

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    if encontrados == 0:
        print("No se encontraron procesos de Python activos.")
    else:
        print(f"{'=' * 100}")
        print(f"Total de procesos Python: {encontrados}")


if __name__ == "__main__":
    try:
        escanear_procesos()
        print("\nSi ves procesos que no deberían estar, podés matarlos con: taskkill /F /PID <numero_pid>")
    except Exception as e:
        print(f"Error al escanear: {e}")

    input("\nPresioná Enter para cerrar...")