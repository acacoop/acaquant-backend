"""`lab/langgraph/herramientas.py` — LAS HERRAMIENTAS.

Esto es lo que en el curso llaman **tools**: funciones normales de Python con
un docstring. El docstring NO es documentación — **es el prompt**. Es lo único
que el modelo lee para decidir si esta herramienta le sirve.

Cuatro reglas que valen para cualquier tool, en cualquier framework:

  1. **Solo lectura.** Nada de lo que hay acá escribe. Una tool que muta se
     agrega después, con aprobación humana en el medio.
  2. **Devuelve TEXTO acotado.** Lo que devuelve entra al contexto del modelo y
     se paga por token. Un `SELECT *` sin límite es una factura, no una tool.
  3. **"No encontré" es una respuesta, no un error.** Si tira excepción, el
     grafo se corta; si devuelve «no hay», el modelo sigue razonando.
  4. **No importa el proyecto.** Lee ARCHIVOS. Así corre en cualquier lado sin
     credenciales, sin base y sin arrastrar las 130 dependencias del repo.
"""
from __future__ import annotations

import pathlib
import re
import subprocess

from langchain_core.tools import tool

RAIZ = pathlib.Path(__file__).resolve().parents[2]
_TOPE = 6000  # techo de caracteres por respuesta: el contexto se paga


def _recortar(txt: str, tope: int = _TOPE) -> str:
    return txt if len(txt) <= tope else txt[:tope] + f"\n… [recortado, {len(txt)} chars]"


@tool
def listar_habilidades() -> str:
    """Lista TODAS las habilidades del AV AGENT con su dominio, cada cuánto
    corren, en qué ventana horaria y qué miran. Usar esto PRIMERO cuando la
    pregunta sea sobre qué vigila el agente o qué habilidad se ocupa de algo."""
    txt = (RAIZ / "agente" / "catalogo.py").read_text(encoding="utf-8")
    out = []
    for m in re.finditer(
            r'nombre="(?P<n>[^"]+)".*?dominio="(?P<d>[^"]+)".*?'
            r'que_mira=(?P<q>.*?),\s*\n\s*cada_segundos=(?P<c>[^,]+),\s*'
            r'ventana="(?P<v>[^"]+)"', txt, re.S):
        que = " ".join(re.findall(r'"([^"]*)"', m.group("q")))
        out.append(f"· {m.group('n'):22} [{m.group('d'):9}] cada {m.group('c').strip():10} "
                   f"ventana={m.group('v'):8} — {que}")
    return f"{len(out)} habilidades:\n" + "\n".join(out)


@tool
def codigo_de(habilidad: str) -> str:
    """Devuelve el CÓDIGO FUENTE del detector de una habilidad (su fila en el
    catálogo y la función que la ejecuta). Usar cuando haya que explicar CÓMO
    decide algo una habilidad, o por qué emitiría un hallazgo."""
    cat = (RAIZ / "agente" / "catalogo.py").read_text(encoding="utf-8")
    # ⚠️ Se parte por `Habilidad(` en vez de hacer una regex que abarque la fila
    # entera. Una fila termina en `}),` o en `),` según tenga o no `arreglos`, y
    # una regex que apueste a un cierre concreto falla en silencio en la mitad
    # de los casos — que es exactamente lo que pasó en la primera versión.
    fila = ""
    for bloque in cat.split("Habilidad(")[1:]:
        if re.match(rf'\s*nombre="{re.escape(habilidad)}"', bloque):
            fila = "Habilidad(" + bloque.split("\n\n")[0].rstrip()
            break
    if not fila:
        return (f"«{habilidad}» no está en el catálogo. "
                f"Probá `listar_habilidades` para ver los nombres válidos.")

    # De la fila sale a qué función llama: `correr=mercado.bono_sin_precio`
    fn = re.search(r"correr=(\w+)\.(\w+)", fila)
    fuente = ""
    if fn:
        modulo, func = fn.group(1), fn.group(2)
        alias = {"cat": "catalogo"}
        for p in (RAIZ / "agente" / "detectores").glob("*.py"):
            if p.stem != alias.get(modulo, modulo):
                continue
            cuerpo = p.read_text(encoding="utf-8")
            d = re.search(rf"^def {func}\(.*?(?=\n(?:def |# ═|@))", cuerpo, re.S | re.M)
            if d:
                fuente = f"\n\n--- agente/detectores/{p.name} ---\n{d.group(0)}"
    return _recortar(f"--- fila del catálogo ---\n{fila}{fuente}")


@tool
def buscar_en_repo(patron: str, carpeta: str = "") -> str:
    """Busca un texto o expresión regular en el código del repo y devuelve las
    líneas que coinciden, con su archivo y número de línea. Usar cuando haga
    falta encontrar dónde vive algo (una tabla, una función, una constante).
    `carpeta` acota la búsqueda: 'agente', 'core', 'api', 'jobs', 'engines'."""
    destino = RAIZ / carpeta if carpeta else RAIZ
    if not destino.exists():
        return f"la carpeta «{carpeta}» no existe"
    try:
        r = subprocess.run(
            ["grep", "-rn", "--include=*.py", "--include=*.sql", "-E", patron, str(destino)],
            capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return "la búsqueda tardó demasiado — probá un patrón más específico"
    lineas = [ln.replace(str(RAIZ) + "/", "") for ln in r.stdout.splitlines()]
    if not lineas:
        return f"sin coincidencias para «{patron}» en {carpeta or 'el repo'}"
    return _recortar(f"{len(lineas)} coincidencias:\n" + "\n".join(lineas[:60]))


@tool
def leer_diario(entrada: str) -> str:
    """Lee una entrada del diario del agente (`docs/AGENT.md`), que explica POR
    QUÉ las cosas quedaron como quedaron. `entrada` es el número de sección sin
    el §, por ejemplo 'da' o 'cy'. Cada entrada es un bug real con su decisión.
    Usar cuando el código cite un §0.x y haga falta el contexto histórico."""
    doc = (RAIZ / "docs" / "AGENT.md").read_text(encoding="utf-8")
    m = re.search(rf"^### 0\.{re.escape(entrada)} .*?$", doc, re.M)
    if not m:
        return f"no existe la entrada §0.{entrada} en docs/AGENT.md"
    fin = doc.find("\n### 0.", m.end())
    return _recortar(doc[m.start():fin if fin > 0 else None], 4000)


# El REGISTRO. El modelo sólo puede llamar a lo que esté en esta lista —
# es la allowlist, y es lo que hace que "el modelo elige" no sea peligroso.
#
# Se juntan las que leen ARCHIVOS (acá arriba) con las que leen la BASE
# (`datos.py`). Para el modelo son todas iguales: una lista de cosas que puede
# pedir. Están en dos archivos porque tienen riesgos distintos — las de la base
# tocan producción, aunque sea de solo lectura.
from lab.langgraph.datos import HERRAMIENTAS_SQL  # noqa: E402

HERRAMIENTAS = [listar_habilidades, codigo_de, buscar_en_repo, leer_diario,
                *HERRAMIENTAS_SQL]
