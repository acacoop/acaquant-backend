"""`lab/langgraph/armar_lector.py` — arma la línea `POSTGRES_URI_LECTOR` en el .env.

    python -m lab.langgraph.armar_lector

Existe porque armarla a mano tiene tres formas de salir mal, y las tres dan un
error que habla de otra cosa: pisar el código del proyecto (Supabase contesta
«tenant/user no encontrado»), perder el `?sslmode=require`, o romper el valor
con un `source .env` de bash. **Ninguno de esos errores nombra la causa real.**

No inventa nada: copia TU conexión de siempre y le cambia dos cosas — el
usuario (`postgres…` → `lector_lab…`, conservando el código del proyecto) y la
contraseña, que te pide por teclado.

NO toca `POSTGRES_URI`. La de siempre queda igual.
"""
from __future__ import annotations

import pathlib
import re
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
ENV = RAIZ / ".env"
ROL = "lector_lab"


def main() -> int:
    if not ENV.exists():
        print(f"No encuentro {ENV}")
        return 1

    lineas = ENV.read_text(encoding="utf-8").splitlines()
    actual = next((ln for ln in lineas if ln.startswith("POSTGRES_URI=")), "")
    if not actual:
        print("No hay una línea POSTGRES_URI= en el .env")
        return 1

    # postgresql://USUARIO:CLAVE@resto…
    m = re.match(r"POSTGRES_URI=(\w+://)([^:]+):([^@]*)@(.+)$", actual)
    if not m:
        print("No pude entender la forma de POSTGRES_URI. Pegámela (tapando la "
              "contraseña) y la miramos.")
        return 1
    esquema, usuario, _clave_vieja, resto = m.groups()

    # `postgres.abcd1234` → `lector_lab.abcd1234`. El sufijo es el código del
    # proyecto y es lo que le dice al pooler de Supabase a qué base mandarte:
    # pisarlo es el error que da «tenant/user no encontrado».
    nuevo_usuario = ROL + usuario[len("postgres"):] if usuario.startswith("postgres") else ROL
    print(f"\nTu usuario actual : {usuario}")
    print(f"El usuario nuevo  : {nuevo_usuario}")
    print("(el servidor y el resto de la URL no se tocan)\n")

    clave = input(f"Contraseña que le pusiste a «{ROL}» en el CREATE ROLE: ").strip()
    if not clave:
        print("Sin contraseña no puedo armar nada.")
        return 1

    nueva = f"POSTGRES_URI_LECTOR={esquema}{nuevo_usuario}:{clave}@{resto}"

    # Se saca la vieja antes de poner la nueva: dos líneas con la misma variable
    # es peor que ninguna — gana una de las dos y no se sabe cuál.
    quedan = [ln for ln in lineas if not ln.startswith("POSTGRES_URI_LECTOR=")]
    pisada = len(lineas) - len(quedan)
    ENV.write_text("\n".join([*quedan, nueva]) + "\n", encoding="utf-8")

    print(f"\n✅ Escrito en {ENV}"
          + (f" (reemplacé {pisada} línea vieja)" if pisada else ""))
    print(f"   POSTGRES_URI_LECTOR={esquema}{nuevo_usuario}:{'*' * len(clave)}@{resto[:40]}…")
    print("\nAhora probalo:\n"
          "   PYTHONPATH=. venv-lab/bin/python -m lab.langgraph.probar_lector\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
