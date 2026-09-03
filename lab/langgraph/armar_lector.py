"""`lab/langgraph/armar_lector.py` — arma las conexiones del lab en el .env.

    python -m lab.langgraph.armar_lector

Existe porque armarlas a mano tiene tres formas de salir mal, y las tres dan un
error que habla de OTRA cosa: pisar el código del proyecto (Supabase contesta
«tenant/user no encontrado»), perder el `?sslmode=require`, o romper el valor
con un `source .env` de bash — que interpreta el `&` como «mandá esto al fondo»
y corta la línea ahí, dejando un error que habla de la contraseña.

No inventa nada: copia TU conexión de siempre y le cambia dos cosas — el
usuario (conservando el código del proyecto) y la contraseña, que pide por
teclado. **NO toca `POSTGRES_URI`.**
"""
from __future__ import annotations

import pathlib
import re
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
ENV = RAIZ / ".env"

# (variable del .env, rol de Postgres, para qué sirve). Sumar una conexión es
# una fila: el resto del script no las nombra de a una.
CONEXIONES = (
    ("POSTGRES_URI_LECTOR", "lector_lab",
     "lee producción y el diario · la base NO lo deja escribir"),
    ("POSTGRES_URI_ESCRITOR", "escritor_lab",
     "escribe SÓLO en lab.investigaciones · producción le es de solo lectura"),
)


def main() -> int:
    if not ENV.exists():
        print(f"No encuentro {ENV}")
        return 1

    lineas = ENV.read_text(encoding="utf-8").splitlines()
    actual = next((ln for ln in lineas if ln.startswith("POSTGRES_URI=")), "")
    if not actual:
        print("No hay una línea POSTGRES_URI= en el .env")
        return 1

    m = re.match(r"POSTGRES_URI=(\w+://)([^:]+):([^@]*)@(.+)$", actual)
    if not m:
        print("No pude entender la forma de POSTGRES_URI. Pegámela (tapando la "
              "contraseña) y la miramos.")
        return 1
    esquema, usuario, _vieja, resto = m.groups()
    print(f"\nTu usuario actual: {usuario}")
    print("(el servidor y el resto de la URL no se tocan)\n")

    nuevas = {}
    for var, rol, para_que in CONEXIONES:
        # `postgres.abcd1234` → `lector_lab.abcd1234`. El sufijo es el código
        # del proyecto y es lo que le dice al pooler de Supabase a qué base
        # mandarte: pisarlo es el error «tenant/user no encontrado».
        nuevo = rol + usuario[len("postgres"):] if usuario.startswith("postgres") else rol
        print(f"  {rol}  —  {para_que}")
        clave = input(f"  contraseña de «{rol}» (Enter para saltearlo): ").strip()
        print()
        if clave:
            nuevas[var] = f"{var}={esquema}{nuevo}:{clave}@{resto}"

    if not nuevas:
        print("No cargaste ninguna contraseña: no toqué el .env.")
        return 1

    # Se sacan las viejas antes de poner las nuevas: dos líneas con la misma
    # variable es peor que ninguna — gana una y no se sabe cuál.
    quedan = [ln for ln in lineas
              if not any(ln.startswith(v + "=") for v in nuevas)]
    ENV.write_text("\n".join([*quedan, *nuevas.values()]) + "\n", encoding="utf-8")

    print(f"✅ Escrito en {ENV}:")
    for v in nuevas:
        print(f"   {v}")
    print("\nAhora probalo:\n"
          "   PYTHONPATH=. venv-lab/bin/python -m lab.langgraph.probar_lector\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
