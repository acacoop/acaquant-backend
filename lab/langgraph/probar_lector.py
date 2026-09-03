"""`lab/langgraph/probar_lector.py` — ¿el usuario de solo lectura es REAL?

    python -m lab.langgraph.probar_lector

**No pregunta qué permisos tiene: se los pide y mira qué contesta la base.** Es
la misma idea de `agente/seguridad.py` — lo declarado se lee, lo efectivo se
prueba. Un permiso "en los papeles" y un permiso real se ven iguales hasta que
alguien empuja la puerta.

Cuatro preguntas, y **dos de ellas tienen que fallar** para que esté bien:

    1. ¿entra?                    → tiene que SÍ
    2. ¿lee lo que necesita?      → tiene que SÍ  (mercado, agente)
    3. ¿puede escribir?           → tiene que NO
    4. ¿ve datos del negocio?     → tiene que NO  (clientes, portafolio)

⚠️ El intento de escritura va adentro de una transacción que se DESHACE pase lo
que pase. Si los permisos estuvieran mal puestos, el UPDATE andaría — y este
script no puede ser el que ensucie la base mientras averigua eso.
"""
from __future__ import annotations

import os
import pathlib
import sys

from dotenv import load_dotenv

load_dotenv(pathlib.Path(__file__).resolve().parents[2] / ".env")

OK, MAL = "✅", "❌"


def _linea(n: int, pregunta: str, bien: bool, detalle: str) -> bool:
    print(f"  {OK if bien else MAL}  {n}. {pregunta:34} {detalle}")
    return bien


def main() -> int:
    uri = os.getenv("POSTGRES_URI_LECTOR", "").strip()
    if not uri:
        print("Falta POSTGRES_URI_LECTOR en el .env de la raíz del repo.")
        return 1
    usuario = uri.split("://", 1)[-1].split(":", 1)[0]
    print(f"\nProbando el usuario «{usuario}»\n")

    import psycopg
    todo_bien = True

    # 1 — ¿entra?
    try:
        conn = psycopg.connect(uri, connect_timeout=15)
    except Exception as e:
        _linea(1, "¿puede conectarse?", False, str(e).strip().splitlines()[0][:120])
        print("\nNo se pudo entrar. Revisá usuario y contraseña en la URL.\n")
        return 1
    todo_bien &= _linea(1, "¿puede conectarse?", True, "entró")

    with conn:
        # 2 — ¿lee lo que necesita?
        for tabla in ("mercado.curvas", "mercado.market_snapshot",
                      "agente.hallazgos", "agente.reincidencias",
                      "agente.acciones", "manager.job_runs"):
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT count(*) FROM {tabla}")
                    detalle = f"{tabla}: {cur.fetchone()[0]} filas"
                    bien = True
            except Exception as e:
                conn.rollback()
                detalle, bien = f"{tabla}: {str(e).strip().splitlines()[0][:70]}", False
            todo_bien &= _linea(2, "¿lee lo que necesita?", bien, detalle)

        # 3 — ¿puede escribir? Tiene que NO poder.
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE mercado.curvas SET emisor = emisor "
                            "WHERE ticker = (SELECT ticker FROM mercado.curvas LIMIT 1)")
            escribio = True
        except Exception as e:
            escribio, motivo = False, str(e).strip().splitlines()[0][:90]
        finally:
            # PASE LO QUE PASE. Si el permiso estaba mal, nada quedó escrito.
            conn.rollback()
        todo_bien &= _linea(
            3, "¿puede ESCRIBIR?", not escribio,
            "NO — la base lo rechazó: " + motivo if not escribio
            else "SÍ PUDO — el permiso está mal, revisá el GRANT")

        # 4 — ¿ve datos del negocio? Tiene que NO ver.
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM clientes.comitentes")
            vio, detalle = True, "SÍ VE clientes.comitentes — revisá el alcance"
        except Exception:
            conn.rollback()
            vio, detalle = False, "NO — clientes/ le es invisible"
        todo_bien &= _linea(4, "¿ve datos del negocio?", not vio, detalle)

    veredicto = ("✅ EL ROL ESTÁ BIEN: lee lo suyo y la base NO lo deja escribir."
                 if todo_bien else
                 "❌ ALGO NO CIERRA — mirá las líneas con ❌ antes de seguir.")
    print(f"\n{veredicto}\n")
    return 0 if todo_bien else 1


if __name__ == "__main__":
    sys.exit(main())
