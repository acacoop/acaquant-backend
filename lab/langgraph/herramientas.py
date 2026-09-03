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
            # ⚠️ `.txt` y `.md` NO son opcionales: el crontab del repo
            # (`deploy/crontab.txt`) es donde se ve QUÉ otro proceso corrió
            # cerca del momento en que algo se rompió — y muchas veces la causa
            # es otro proceso nuestro, no un bug.
            # ⚠️ Se EXCLUYE el propio laboratorio. Sus comentarios explican
            # bugs del agente citando los mismos nombres que se buscan
            # (`alta_bono`, `sale_del_master`), así que aparecían mezclados con
            # el código real: el investigador se leía a sí mismo hablando de lo
            # que estaba investigando.
            ["grep", "-rn", "--include=*.py", "--include=*.sql",
             "--include=*.txt", "--include=*.md",
             "--exclude-dir=lab", "--exclude-dir=venv", "--exclude-dir=venv-lab",
             "--exclude-dir=.git", "--exclude-dir=node_modules",
             "-E", patron, str(destino)],
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


@tool
def leer_archivo(ruta: str, desde: int = 1, cantidad: int = 120) -> str:
    """Lee un archivo del repo y devuelve sus líneas NUMERADAS. `ruta` es
    relativa a la raíz, como 'agente/arreglos.py' o 'jobs/cleanup_curvas.py'.
    `desde` es la primera línea y `cantidad` cuántas leer.

    Usar SIEMPRE después de `buscar_en_repo`: esa te dice DÓNDE está algo y te
    muestra una línea suelta; ésta te deja leer el código alrededor. Si buscaste
    dos veces lo mismo, lo que necesitás es esto."""
    limpia = ruta.strip().lstrip("/")
    try:
        # ⚠️ El destino se resuelve y se exige que caiga ADENTRO del repo.
        # `resolve()` colapsa los `..` y sigue los symlinks, así que una ruta
        # como `agente/../../etc/passwd` no llega a leerse: el chequeo se hace
        # sobre el path final, no sobre el texto que vino.
        destino = (RAIZ / limpia).resolve()
        destino.relative_to(RAIZ.resolve())
    except (ValueError, OSError):
        return f"«{ruta}» queda fuera del repo. Sólo se leen archivos de acá adentro."
    if not destino.is_file():
        return (f"«{limpia}» no existe o no es un archivo. "
                f"Buscá la ruta con `buscar_en_repo` primero.")
    if destino.suffix not in {".py", ".sql", ".md", ".txt", ".toml", ".cfg", ".sh"}:
        return f"«{limpia}» no es un archivo de texto que valga la pena leer."

    lineas = destino.read_text(encoding="utf-8", errors="replace").splitlines()
    ini = max(1, int(desde))
    fin = min(len(lineas), ini + max(1, min(int(cantidad), 200)) - 1)
    if ini > len(lineas):
        return f"«{limpia}» tiene {len(lineas)} líneas; pediste desde la {ini}."
    cuerpo = "\n".join(f"{n:5} {lineas[n - 1]}" for n in range(ini, fin + 1))
    cola = (f"\n… sigue hasta la línea {len(lineas)}" if fin < len(lineas) else "")
    return _recortar(f"--- {limpia} (líneas {ini}-{fin} de {len(lineas)}) ---\n"
                     + cuerpo + cola, 9000)


# ── EL REGISTRO ────────────────────────────────────────────────────────────
#
# El modelo sólo puede llamar a lo que esté acá — es la allowlist, y es lo que
# hace que "el modelo elige" no sea peligroso.
#
# Se juntan las que leen ARCHIVOS (este módulo) con las que leen la BASE
# (`datos.py`). Están en dos archivos porque tienen riesgos distintos: las de la
# base tocan producción, aunque sea de solo lectura.
from lab.langgraph.datos import HERRAMIENTAS_SQL  # noqa: E402

DEL_REPO = [listar_habilidades, codigo_de, buscar_en_repo, leer_diario,
            leer_archivo]

HERRAMIENTAS = [*DEL_REPO, *HERRAMIENTAS_SQL]

# ⚠️ **CUÁLES DAN SIEMPRE LO MISMO DENTRO DE UNA INVESTIGACIÓN.** El repo no
# cambia mientras se investiga, así que pedir dos veces la misma búsqueda
# devuelve, por definición, el mismo resultado: repetirla es tiempo y tokens
# tirados. La base SÍ puede cambiar entre una consulta y la siguiente, así que
# esas quedan libres.
#
# **Se DERIVA de dónde vive cada herramienta, no se escribe a mano.** Una lista
# escrita a mano es una lista paralela más que hay que acordarse de actualizar
# —y cuando se olvida no falla nada, sólo se deja de deduplicar en silencio.
# Así, una tool nueva en este archivo entra sola.
DETERMINISTAS = frozenset(h.name for h in DEL_REPO)
