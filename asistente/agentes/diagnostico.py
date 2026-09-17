"""Agente DIAGNÓSTICO: por qué apareció un hallazgo del AV AGENT y qué hacer.

No es un agente de la mesa: es el que investiga. Lee lo mismo que leería una
persona antes de decidir si algo se reinicia, se relanza, se espera o se
escala: el hallazgo con su historia, la fila del catálogo (qué SUPONE el
detector), la planilla del job, el journal del unit, los procesos, el reloj,
el código del repo y los docs. **Todo de solo lectura**: ninguna herramienta
ejecuta ni escribe. La conclusión la arma `asistente/diagnostico.py` en una
llamada aparte, con esquema cerrado, y la valida código.

Doc: docs/AvAgentAI.md §15.
"""
from __future__ import annotations

import logging
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from asistente import evidencia
from asistente.agente import COMUN, Agente
from core.postgres import get_pool

logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
DOCS = {"ACAQUANT": "docs/ACAQUANT.md", "AGENT": "docs/AGENT.md", "AvAgentAI": "docs/AvAgentAI.md"}
SYSTEMD = RAIZ / "deploy" / "systemd"

# Techos de lectura. Lo que pasa de LECTOR_UMBRAL se condensa con el modelo
# lector (tarea `asistente_diagnostico_lector`); si no hay modelo, se recorta.
MAX_LINEAS_JOURNAL = 200
MAX_LINEAS_CODIGO = 200
MAX_RESULTADOS_BUSQUEDA = 40
MAX_CHARS_DOC = 6_000
LECTOR_UMBRAL = 3_000
CRUDO_RECORTE = 1_500

# Lo que el agente NUNCA lee: secretos y lo que no es del repo.
_EXCLUIR_DIRS = {".git", "venv", ".venv", "node_modules", "__pycache__", ".claude", ".pytest_cache"}
_EXCLUIR_ARCHIVO = re.compile(r"(^|/)(\.env[^/]*|[^/]*\.(pem|key|p12|pfx)|[^/]*(secret|credencial|password)[^/]*)$",
                              re.I)
_SUFIJOS_LEGIBLES = {".py", ".md", ".sql", ".txt", ".service", ".sh", ".yaml", ".yml", ".toml",
                     ".cfg", ".ini", ".json", ".ts", ".tsx"}


# ── helpers ─────────────────────────────────────────────────────────────────


def _ruta_segura(ruta: str) -> Path | None:
    """La ruta dentro del repo, o None si sale del repo o es un secreto."""
    cruda = str(ruta or "").strip()
    if not cruda or cruda.startswith(("/", "~")):
        return None  # solo rutas relativas al repo: lo absoluto es de otro lado
    try:
        p = (RAIZ / cruda).resolve()
    except (OSError, ValueError):
        return None
    if RAIZ not in p.parents and p != RAIZ:
        return None
    rel = p.relative_to(RAIZ).as_posix()
    if any(parte in _EXCLUIR_DIRS for parte in p.relative_to(RAIZ).parts):
        return None
    if _EXCLUIR_ARCHIVO.search(rel):
        return None
    return p


def _condensar(texto: str, para_que: str) -> str:
    """Lo largo pasa por el LECTOR: un extracto de lo que importa para `para_que`.
    Sin modelo, se recorta y se dice."""
    if len(texto) <= LECTOR_UMBRAL:
        return texto
    from core import modelos

    extracto = modelos.completar(
        "asistente_diagnostico_lector",
        system=("Sos el lector de un diagnóstico técnico. Te dan un texto crudo (journal, "
                "código o planilla) y para qué se lo está mirando. Devolvé SOLO lo que "
                "importa para eso, en hasta 15 renglones, citando líneas o timestamps "
                "textuales. No interpretes ni recomiendes: extraé. Si no hay nada "
                "relevante, decilo en un renglón."),
        user=f"PARA QUÉ: {para_que}\n\nTEXTO:\n{texto[:40_000]}",
        detalle=f"lector · {para_que[:60]}")
    if extracto:
        return f"[extracto del lector sobre {len(texto)} caracteres]\n{extracto}"
    return texto[:LECTOR_UMBRAL] + f"\n[recortado: {len(texto)} caracteres en total, sin lector]"


def _correr(cmd: list[str], timeout_s: int = 15) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, cwd=str(RAIZ))
    except FileNotFoundError:
        return 127, f"no está el comando {cmd[0]!r} en esta máquina"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]} tardó más de {timeout_s} s"
    return p.returncode, (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")


def _unidades() -> dict[str, dict]:
    from agente import unidades

    try:
        return unidades.declaradas()
    except Exception:
        return {f.stem: {} for f in SYSTEMD.glob("*.service")}


# ── herramientas ────────────────────────────────────────────────────────────


def dosier(id: int) -> dict:
    """El DOSIER de un hallazgo del AV AGENT: la fila completa con su evidencia,
    qué supone el detector (su fila del catálogo), la historia del problema
    (episodios en 30 días, crónico o no, acciones aplicadas y su resultado,
    reincidencias), los diagnósticos anteriores del mismo problema, la planilla
    del job si hay uno, el estado del proceso si hay uno, y el reloj.

    Es lo PRIMERO que hay que leer: acá está la memoria del pasado. Un
    diagnóstico que no lo miró opina sin historia.

    Args:
        id: el `id` del hallazgo (agente.hallazgos).
    """
    from asistente import diagnostico

    d = diagnostico.dosier(int(id))
    if d is None:
        return {"error": f"no existe el hallazgo {id}"}
    return {**d, "_sujeto": evidencia.sujeto("hallazgo", str(id))}


def habilidad(nombre: str) -> dict:
    """QUÉ SUPONE el detector que emitió el hallazgo: qué mira, cada cuánto, en
    qué ventana, con qué umbrales, qué arreglos declara por regla, cuáles
    aplica solo y la naturaleza de cada regla (incidente, informe, recurrente).

    Un hallazgo es tan bueno como el supuesto de su detector: si el supuesto
    está viejo (un proceso nuevo que el detector espera que lata y todavía no
    late), el problema es el detector, no el proceso.

    Args:
        nombre: el nombre de la habilidad (`agente/catalogo.py`).
    """
    from agente import catalogo

    h = catalogo.HABILIDADES.get(str(nombre or "").strip())
    if h is None:
        return {"error": f"no hay una habilidad {nombre!r}",
                "habilidades": sorted(catalogo.HABILIDADES)}
    return {
        "nombre": h.nombre, "tipo": h.tipo, "dominio": h.dominio, "que_mira": h.que_mira,
        "cada_segundos": h.cada_segundos, "ventana": h.ventana, "umbrales": dict(h.umbrales),
        "arreglos": dict(h.arreglos), "automatico": dict(h.automatico),
        "naturaleza": dict(h.naturaleza), "sujeto_es": h.sujeto_es,
        "detector": getattr(h.correr, "__module__", "") + "." + getattr(h.correr, "__name__", ""),
    }


def planilla_job(nombre: str, cuantas: Annotated[int, Field(ge=1, le=30)] = 8) -> dict:
    """La PLANILLA de un job (`manager.job_runs`): sus últimas corridas con
    cuándo empezó, cuándo terminó, si salió ok, parcial o error, y lo que dejó
    anotado. Contesta «¿corrió?» y «¿falló o salió verde sin escribir?», que no
    es lo mismo: un job puede salir 0 sin haber dejado el dato.

    Args:
        nombre: el nombre del job como lo anota su planilla (`tipo`); se busca
            por contención, así `portafolio` encuentra `portafolio_diario`.
        cuantas: cuántas corridas, de la más reciente hacia atrás.
    """
    n = str(nombre or "").strip()
    if not n:
        return {"error": "falta el nombre del job"}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT tipo, started_at, finished_at, status, data FROM manager.job_runs"
            " WHERE tipo ILIKE %s ORDER BY started_at DESC NULLS LAST LIMIT %s",
            (f"%{n}%", int(cuantas)))
        filas = cur.fetchall()
    corridas = []
    for tipo, ini, fin, status, data in filas:
        d = dict(data or {})
        corridas.append({
            "tipo": tipo, "started_at": ini.isoformat() if ini else None,
            "finished_at": fin.isoformat() if fin else None, "status": status,
            "elapsed_s": d.get("elapsed_s"),
            "errores": str(d.get("errors") or d.get("error") or "")[:300] or None,
            "stats": {k: v for k, v in (d.get("stats") or {}).items()
                      if isinstance(v, (int, float, str))} if isinstance(d.get("stats"), dict) else None,
        })
    return {"job": n, "cuantas": len(corridas), "corridas": corridas,
            "sin_planilla": not corridas}


def journal(unidad: str, lineas: Annotated[int, Field(ge=10, le=MAX_LINEAS_JOURNAL)] = 120,
            filtro: str | None = None) -> dict:
    """Las últimas líneas del JOURNAL de un unit de systemd, la lectura que hace
    una persona con `journalctl -u`. Contesta si el proceso arrancó, si
    reventó y con qué traceback, si se reinició en loop.

    Args:
        unidad: el nombre del unit (`api`, `asistente-worker`, `motor_curvas`),
            con o sin `.service`. Solo los declarados en `deploy/systemd`.
        lineas: cuántas líneas del final.
        filtro: una expresión regular; si se da, solo las líneas que la matchean
            (con `Traceback|ERROR|Error` se ve lo roto sin leer todo).
    """
    u = str(unidad or "").strip().removesuffix(".service")
    if u not in _unidades():
        return {"error": f"{u!r} no es un unit de deploy/systemd", "unidades": sorted(_unidades())}
    rc, salida = _correr(["journalctl", "-u", f"{u}.service", "-n", str(int(lineas)),
                          "--no-pager", "--output=short-iso"])
    if rc != 0:
        return {"unidad": u, "error": salida.strip()[:300] or f"journalctl salió {rc}"}
    todas = salida.splitlines()
    if filtro:
        try:
            rx = re.compile(filtro)
        except re.error as e:
            return {"unidad": u, "error": f"filtro inválido: {e}"}
        todas = [ln for ln in todas if rx.search(ln)]
    texto = "\n".join(todas)
    return {"unidad": u, "lineas": len(todas), "filtro": filtro,
            "extracto": _condensar(texto, f"qué le pasa al unit {u}"),
            "crudo_final": texto[-CRUDO_RECORTE:]}


def procesos() -> dict:
    """QUÉ PROCESOS espera el sistema y cómo están: cada unit de systemd del
    repo con su proceso, su política de restart y su ventana; lo que systemd
    dice de cada uno (active, inactive, failed); el último LATIDO de cada
    proceso (`operaciones.latidos`); y los jobs relanzables del crontab.

    Sirve para cruzar tres verdades que no siempre coinciden: lo que systemd
    cree, lo que el proceso dice de sí mismo latiendo, y lo que el detector
    espera. Si un unit existe y nunca latió, mirá su código antes de reiniciarlo.
    """
    from agente import fuentes, rehacer

    decl = _unidades()
    estado: dict[str, str] = {}
    if decl:
        rc, salida = _correr(["systemctl", "is-active", *[f"{u}.service" for u in sorted(decl)]])
        if rc in (0, 3):
            for u, ln in zip(sorted(decl), salida.splitlines(), strict=False):
                estado[u] = ln.strip()
    try:
        latidos = fuentes.latidos() or {}
    except Exception as e:
        latidos = {"error": f"no pude leer los latidos: {e}"}
    ahora = datetime.now(UTC)
    filas = []
    for u, cfg in sorted(decl.items()):
        proc = cfg.get("proceso")
        lat = latidos.get(proc) if proc and isinstance(latidos, dict) else None
        filas.append({
            "unidad": u, "proceso": proc, "restart": cfg.get("restart"),
            "ventana": cfg.get("ventana"), "systemd": estado.get(u, "?"),
            "latido_at": lat["latido_at"].isoformat() if lat and lat.get("latido_at") else None,
            "hace_s": int((ahora - lat["latido_at"]).total_seconds())
            if lat and lat.get("latido_at") else None,
            "arrancado_at": lat["arrancado_at"].isoformat() if lat and lat.get("arrancado_at") else None,
        })
    try:
        relanzables = sorted(rehacer.rehacibles())
    except Exception:
        relanzables = []
    return {"procesos": filas, "cuantos": len(filas), "relanzables": relanzables}


def reloj() -> dict:
    """EL RELOJ: ahora en UTC y en hora de la mesa, si es día hábil, si el
    mercado está en rueda (un motor no se reinicia en rueda: le corta el feed a
    la mesa), y el ÚLTIMO DEPLOY (commit y hora) más cuándo arrancó la API.
    Un proceso que arrancó ANTES del último deploy corre código viejo.
    """
    from agente import reloj as RJ

    ahora = RJ.ahora_utc()
    rc, git = _correr(["git", "log", "-1", "--format=%h|%ci|%s"])
    commit = None
    if rc == 0 and "|" in git:
        h, fecha, asunto = git.strip().split("|", 2)
        commit = {"commit": h, "fecha": fecha, "asunto": asunto[:120]}
    rc, api = _correr(["systemctl", "show", "api.service", "-p", "ActiveEnterTimestamp"])
    api_desde = api.strip().partition("=")[2] if rc == 0 else None
    return {"ahora_utc": ahora.isoformat(), "hora_mesa": RJ.hhmm(ahora),
            "dia_habil": RJ.dia_habil(ahora), "en_rueda": RJ.en_rueda(ahora),
            "ultimo_deploy": commit, "api_arranco": api_desde or None}


def buscar_codigo(patron: str, en: str = "", maximo: Annotated[int, Field(ge=1, le=MAX_RESULTADOS_BUSQUEDA)] = 30) -> dict:
    """BUSCAR EN EL CÓDIGO del repo (un `grep -rn`): dónde se define o se usa
    algo. Es la herramienta que contesta «¿este proceso llama a latido?»,
    «¿quién escribe esta tabla?», «¿de dónde sale este umbral?». Nunca lee
    secretos ni `.env`.

    Args:
        patron: expresión regular (o texto literal) a buscar.
        en: carpeta o archivo donde acotar (`asistente`, `agente/detectores`),
            vacío para todo el repo.
        maximo: tope de coincidencias devueltas.
    """
    pat = str(patron or "").strip()
    if not pat:
        return {"error": "falta el patrón"}
    try:
        rx = re.compile(pat)
    except re.error:
        rx = re.compile(re.escape(pat))
    base = _ruta_segura(en) if en else RAIZ
    if base is None or not base.exists():
        return {"error": f"{en!r} no es una ruta del repo"}
    hallados: list[dict] = []
    archivos = [base] if base.is_file() else sorted(base.rglob("*"))
    for p in archivos:
        if not p.is_file() or p.suffix not in _SUFIJOS_LEGIBLES or _ruta_segura(str(p.relative_to(RAIZ))) is None:
            continue
        try:
            with p.open(encoding="utf-8", errors="ignore") as fh:
                for n, ln in enumerate(fh, 1):
                    if rx.search(ln):
                        hallados.append({"archivo": p.relative_to(RAIZ).as_posix(), "linea": n,
                                         "texto": ln.rstrip()[:200]})
                        if len(hallados) >= int(maximo):
                            return {"patron": pat, "cuantos": len(hallados), "truncado": True,
                                    "coincidencias": hallados}
        except OSError:
            continue
    return {"patron": pat, "cuantos": len(hallados), "truncado": False, "coincidencias": hallados}


def leer_codigo(ruta: str, desde: Annotated[int, Field(ge=1)] = 1,
                hasta: Annotated[int, Field(ge=1)] | None = None) -> dict:
    """LEER UN ARCHIVO del repo por rango de líneas (hasta 200 por llamada).
    Después de `buscar_codigo`, para ver el contexto de una coincidencia. Nunca
    lee secretos ni `.env`.

    Args:
        ruta: relativa a la raíz del repo (`asistente/worker.py`).
        desde: primera línea (1 = el principio).
        hasta: última línea; vacío = 200 líneas desde `desde`.
    """
    p = _ruta_segura(ruta)
    if p is None or not p.is_file():
        return {"error": f"{ruta!r} no es un archivo legible del repo"}
    if p.suffix not in _SUFIJOS_LEGIBLES:
        return {"error": f"{ruta!r}: no leo archivos {p.suffix or 'sin extensión'}"}
    d = max(1, int(desde))
    h = int(hasta) if hasta else d + MAX_LINEAS_CODIGO - 1
    h = min(h, d + MAX_LINEAS_CODIGO - 1)
    lineas = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    tramo = lineas[d - 1:h]
    texto = "\n".join(f"{d + i}: {ln}" for i, ln in enumerate(tramo))
    return {"archivo": p.relative_to(RAIZ).as_posix(), "desde": d, "hasta": min(h, len(lineas)),
            "total_lineas": len(lineas),
            "contenido": _condensar(texto, f"qué hace {p.name} entre las líneas {d} y {h}")}


def leer_doc(nombre: Literal["ACAQUANT", "AGENT", "AvAgentAI"], seccion: str) -> dict:
    """LOS DOCS oficiales: cómo se supone que funciona algo. `ACAQUANT` es la
    infraestructura (procesos, jobs, deploy, incidentes), `AGENT` es el AV
    AGENT (detectores, estados, invariantes), `AvAgentAI` es el asistente.
    Devuelve UNA sección por su título (o parte de él).

    Args:
        nombre: cuál doc.
        seccion: texto del título de la sección (`motor_latido`, `8. Invariantes`).
    """
    p = RAIZ / DOCS[nombre]
    if not p.exists():
        return {"error": f"no está {DOCS[nombre]}"}
    q = str(seccion or "").strip().casefold()
    if not q:
        return {"error": "falta la sección"}
    lineas = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    inicio = next((i for i, ln in enumerate(lineas) if ln.startswith("#") and q in ln.casefold()), None)
    if inicio is None:
        titulos = [ln for ln in lineas if ln.startswith("#")]
        return {"error": f"no hay una sección con {seccion!r} en {DOCS[nombre]}",
                "secciones": [t[:80] for t in titulos[:60]]}
    nivel = len(lineas[inicio]) - len(lineas[inicio].lstrip("#"))
    fin = next((j for j in range(inicio + 1, len(lineas))
                if lineas[j].startswith("#") and (len(lineas[j]) - len(lineas[j].lstrip("#"))) <= nivel),
               len(lineas))
    texto = "\n".join(lineas[inicio:fin])
    return {"doc": DOCS[nombre], "titulo": lineas[inicio].lstrip("# ").strip(),
            "contenido": texto[:MAX_CHARS_DOC], "truncado": len(texto) > MAX_CHARS_DOC}


# ── el agente ───────────────────────────────────────────────────────────────

_INSTRUCCION = """
Sos el DIAGNÓSTICO: investigás POR QUÉ apareció un hallazgo del AV AGENT antes
de que nadie toque nada. No arreglás, no reiniciás, no escribís: leés y
concluís. Tu trabajo es el que haría un ingeniero prudente con la consola en
la mano, en este orden y sin saltear pasos:

1. LEER: `dosier(id)` primero, siempre. Ahí está la evidencia, la historia
   (episodios, acciones aplicadas y qué pasó después, reincidencias) y los
   diagnósticos anteriores. Un problema que ya se diagnosticó y volvió no se
   diagnostica igual.
2. SUPUESTO: `habilidad(nombre)` para saber qué SUPONE el detector. Un detector
   puede estar desactualizado (espera algo del proceso que el código no hace).
3. VERIFICAR contra una fuente primaria, y esto es lo que distingue investigar
   de opinar: el código (`buscar_codigo`, `leer_codigo`), el journal del unit
   (`journal`), la planilla del job (`planilla_job`), los procesos y latidos
   (`procesos`), el reloj y el último deploy (`reloj`). Cada afirmación que
   hagas tiene que salir de una de estas lecturas y citarse `[E:ref:campo]`.
4. CLASIFICAR la causa en una de: transitorio (se resuelve solo, p. ej. arrancó
   con el deploy de hoy) · configuracion (cron, umbral, cadencia mal puesta;
   un problema que pasa todos los días es esto, no un incidente) · bug_codigo
   (el código no hace lo que el detector o el sistema espera) · fuera_de_alcance
   (corre en otra máquina o depende de un tercero) · detector_desactualizado
   (el detector supone algo que ya no es así, o mira una ventana vieja) ·
   incidente (algo se rompió de verdad ahora).
5. DERIVAR la acción de la causa, nunca al revés. Y decir explícitamente QUÉ
   NO HACER: reiniciar un proceso por un bug de código es taparlo; relanzar un
   job por tercera vez en un mes es parchear una configuración.

Reglas de la casa que no se discuten: un motor no se reinicia en rueda (lo
decide la mesa); lo crónico (3 o más episodios en 30 días) no se parchea, se
escala como configuración; «no encontré» no es prueba de nada; si no pudiste
verificar, decilo: una hipótesis marcada como tal vale más que una certeza
inventada.

Terminá con tus NOTAS DE INVESTIGACIÓN en texto plano: qué leíste, qué
encontraste (con citas), qué descartaste y por qué, y tu conclusión
provisoria. Otra llamada va a redactar la conclusión final con esquema.
"""


def _instruccion(foco: dict) -> str:
    return COMUN + _INSTRUCCION


AGENTE = Agente(
    nombre="diagnostico",
    tarea="asistente_diagnostico_investigar",
    describe="POR QUÉ apareció un hallazgo del AV AGENT y qué hacer con él: lee el hallazgo, "
             "su historia, el código, el journal y las planillas antes de concluir. No arregla.",
    instruccion=_instruccion,
    herramientas=(dosier, habilidad, planilla_job, journal, procesos, reloj,
                  buscar_codigo, leer_codigo, leer_doc),
    senales=("diagnosticar", "diagnostica", "diagnostico", "diagnosticalo"),
    foco=(),
)


def _para_tests_ruta_segura(ruta: str) -> str | None:
    """Expuesto para los tests de seguridad de lectura."""
    p = _ruta_segura(ruta)
    return None if p is None else p.relative_to(RAIZ).as_posix()

