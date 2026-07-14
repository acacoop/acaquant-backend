"""diag_env.py — encuentra las líneas del `.env` que python-dotenv NO puede parsear.

Motivo: al correr cualquier job/script aparece el warning
    `python-dotenv could not parse statement starting at line N`
python-dotenv DESCARTA esa línea en silencio → si ahí vive una variable que un
job necesita, el job la ve como FALTANTE y falla (o peor: usa un default).

Este diag es READ-ONLY y **nunca imprime valores**: muestra número de línea,
nombre de la variable (si se puede inferir) y el motivo de la falla, con el
valor enmascarado. Seguro de correr en la consola web del Droplet.

Uso:  python -m scripts.diag_env            # revisa el .env de la raíz del repo
      python -m scripts.diag_env <ruta>    # revisa otro archivo
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Lo que python-dotenv acepta como línea válida: KEY=VALOR (KEY sin espacios ni
# comillas, opcionalmente precedida de `export`). Todo lo demás lo descarta.
_VALIDA = re.compile(r"^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=")
_COMENTARIO = re.compile(r"^\s*#")


def _mascara(valor: str) -> str:
    """Nunca mostramos el secreto: solo su forma."""
    v = valor.strip()
    if not v:
        return "(vacío)"
    return f"<{len(v)} chars>"


def _motivo(linea: str) -> str:
    """Por qué python-dotenv no puede con esta línea."""
    s = linea.rstrip("\n")
    if "=" not in s:
        return "no tiene '=' → no es una asignación (¿es una línea suelta / texto pegado?)"
    clave = s.split("=", 1)[0]
    if clave != clave.strip():
        return "hay espacios antes/después de la clave (KEY = valor no sirve, tiene que ser KEY=valor)"
    if " " in clave.strip():
        return "la clave tiene espacios en el medio"
    if not clave.strip():
        return "no hay clave a la izquierda del '='"
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", clave.strip()):
        return "la clave tiene caracteres no permitidos (solo letras, números y '_', sin empezar con número)"
    return "formato no reconocido"


def main() -> None:
    ruta = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(__file__).resolve().parents[1] / ".env"
    )
    print(f"\nRevisando: {ruta}\n")

    if not ruta.exists():
        print("❌ No existe el archivo .env en la raíz del repo.")
        return

    lineas = ruta.read_text(encoding="utf-8", errors="replace").splitlines()
    rotas: list[tuple[int, str]] = []
    claves: list[str] = []

    # Estado: una línea puede ser la continuación de un valor multilínea abierto
    # con comillas (eso SÍ lo soporta dotenv) → no la marcamos como rota.
    dentro_de_comillas = False
    comilla = ""

    for i, linea in enumerate(lineas, start=1):
        if dentro_de_comillas:
            if comilla in linea:
                dentro_de_comillas = False
            continue
        if not linea.strip() or _COMENTARIO.match(linea):
            continue
        if _VALIDA.match(linea):
            clave, _, valor = linea.partition("=")
            claves.append(clave.strip().removeprefix("export").strip())
            v = valor.strip()
            # ¿abre comillas y no las cierra en la misma línea? → multilínea
            for q in ('"', "'"):
                if v.startswith(q) and not (len(v) > 1 and v.endswith(q)):
                    dentro_de_comillas, comilla = True, q
                    break
            continue
        rotas.append((i, linea))

    print(f"Variables que SÍ se cargan: {len(claves)}")
    if claves:
        print("  " + ", ".join(sorted(claves)))

    if not rotas:
        print("\n✅ No hay líneas rotas — python-dotenv parsea todo el archivo.")
        return

    print(f"\n❌ {len(rotas)} línea(s) que python-dotenv DESCARTA en silencio:\n")
    for n, linea in rotas:
        clave, sep, valor = linea.partition("=")
        etiqueta = clave.strip() if sep else "(sin clave)"
        print(f"  línea {n}: {etiqueta} = {_mascara(valor) if sep else '—'}")
        print(f"            ↳ {_motivo(linea)}")
        # Contexto sin valores: solo los primeros caracteres de la clave.
        crudo = linea.strip()
        forma = re.sub(r"[A-Za-z0-9]", "·", crudo[:60])
        print(f"            ↳ forma de la línea (sin datos): {forma}\n")

    print("Arreglo típico: si el valor tiene espacios, '#' o caracteres raros,")
    print("encomillalo →  MI_VAR=\"valor con espacios\"")
    print("Si la línea es basura/texto pegado por error, borrala.")
    print("\nOJO: si alguna de esas variables la necesita un job, hoy la está")
    print("viendo como FALTANTE. Revisá contra la lista de arriba.")


if __name__ == "__main__":
    main()
