"""`agente/explicar.py` — «EXPLICÁMELO»: el error crudo, contado por la IA con el
repo en la mano. Doc: `docs/AGENT.md` §0.dh.

**Cómo sabe.** No se le manda el error solo: se le manda lo que ya está en el
repo. El traceback entero (`habilidades.ultimo_traceback`), el código del
detector, qué fuentes lee, las entradas del diario que ese código cita (cada
`§0.x` cuenta por qué las cosas quedaron así) y la última corrida del job que
aparezca en el error. Con ese paquete el modelo lee, no adivina — y cada
respuesta viaja con la lista de lo que se le dio (`fuentes`), igual que la
evidencia de un hallazgo.

**Lo que hace además de explicar**: dice de quién es (nuestro código, un dato
roto en origen, el proveedor), convierte la explicación en un qué hacer
concreto, redacta el test que congelaría el caso y deja una tarea lista para
una sesión de Claude Code. **Lo que no hace**: tocar código ni decidir nada.
El invariante #12 (el agente no se autoevalúa) sigue: esto es una lectura a
pedido de una persona, con la firma de quién la pidió.

**Cacheado por hash** del error: el mismo error no se paga dos veces, y lo que
se explicó queda con fecha y quién en `agente.explicaciones`.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import pathlib
import re

from core.postgres import get_pool

logger = logging.getLogger(__name__)

RAIZ = pathlib.Path(__file__).resolve().parents[1]
TAREA = "explicar_error"
_MAX_FUENTE = 6000
_MAX_DIARIO = 1800

_SYSTEM = """Sos el explicador de errores del AV AGENT de TradingAV (una mesa de
capitales argentina: bonos, Primary/ROFEX, 1816, Aunesa). Te llega un error de
una habilidad del agente junto con el código que lo produjo, las fuentes que
lee y las entradas del diario del proyecto que ese código cita. Leé, no
adivines: si algo no está en lo que te dieron, decí que no lo sabés.

Contestá SOLO un JSON con estas claves, en castellano rioplatense, sin markdown:
{
  "de_quien": "nuestro" | "dato" | "proveedor" | "no_se",
  "explicacion": "qué se rompió y dónde, en 2 o 3 frases, para alguien que no programa",
  "afecta": "qué deja de funcionar mientras dure y qué sigue andando",
  "que_hacer": "el paso concreto (un comando, un campo, un job a rehacer), no 'revisar'",
  "test": "un test pytest breve que congele este caso, o cadena vacía si no corresponde",
  "tarea": {"titulo": "verbo + qué, menos de 60 caracteres",
            "prompt": "la tarea para una sesión de Claude Code: archivos por path relativo, qué cambiar y por qué"}
}
"""


def _seccion_diario(ancla: str, doc: str) -> str:
    m = re.search(rf"^### 0\.{re.escape(ancla)} .*?$", doc, re.M)
    if not m:
        return ""
    fin = doc.find("\n### 0.", m.end())
    cuerpo = doc[m.start():fin if fin > 0 else None]
    return cuerpo[:_MAX_DIARIO]


def _job_del_error(texto: str) -> str:
    m = re.search(r"\b(jobs\.[a-z_0-9]+|[a-z_0-9]+_1816|portafolio_diario)\b", texto or "")
    return m.group(1) if m else ""


def contexto(nombre: str) -> dict | None:
    """El paquete que se le da al modelo, y la lista de dónde salió cada parte."""
    from agente import catalogo
    h = catalogo.HABILIDADES.get(nombre)
    if h is None:
        return None
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ultimo_error, ultimo_traceback, ultimo_resultado, "
                    "ultima_corrida_at FROM agente.habilidades WHERE nombre = %s", (nombre,))
        fila = cur.fetchone()
    if not fila:
        return None
    error, tb, resultado, corrida = fila
    fuentes_usadas: list[str] = []
    try:
        src = inspect.getsource(h.correr)
    except (OSError, TypeError):
        src = ""
    fuentes_usadas.append(f"código: {inspect.getsourcefile(h.correr) or '?'}::{h.correr.__name__}")
    lee = sorted(set(re.findall(r"fuentes\.(\w+)\(", src)))
    anclas = sorted(set(re.findall(r"§0\.([a-z]+)", src)))
    diario = ""
    try:
        doc = (RAIZ / "docs" / "AGENT.md").read_text(encoding="utf-8")
        partes = [_seccion_diario(a, doc) for a in anclas]
        diario = "\n\n".join(p for p in partes if p)
        fuentes_usadas += [f"diario: §0.{a}" for a in anclas if _seccion_diario(a, doc)]
    except OSError:
        pass
    corrida_job = {}
    job = _job_del_error(f"{error} {tb}")
    if job:
        try:
            from agente.detectores.datos import _ultima_corrida
            corrida_job = _ultima_corrida(job.removeprefix("jobs.")) or {}
            if corrida_job:
                fuentes_usadas.append(f"job_runs: {job}")
        except Exception:
            corrida_job = {}
    if lee:
        fuentes_usadas.append("fuentes: " + ", ".join(lee))
    return {
        "habilidad": nombre, "que_mira": h.que_mira, "dominio": h.dominio,
        "resultado": resultado, "corrida_at": corrida.isoformat() if corrida else None,
        "error": error or "", "traceback": tb or "", "fuentes_que_lee": lee,
        "codigo": src[:_MAX_FUENTE], "diario": diario,
        "corrida_job": {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                        for k, v in corrida_job.items()} if corrida_job else None,
        "_fuentes": fuentes_usadas,
    }


def _hash(nombre: str, error: str, tb: str) -> str:
    return hashlib.sha256(f"{nombre}\n{error}\n{tb}".encode()).hexdigest()[:32]


def _cache(hash_: str) -> dict | None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT respuesta, fuentes, modelo, por, at FROM agente.explicaciones "
                    "WHERE hash = %s", (hash_,))
        r = cur.fetchone()
    if not r:
        return None
    return {"respuesta": dict(r[0] or {}), "fuentes": list(r[1] or []),
            "modelo": r[2], "por": r[3], "at": r[4].isoformat()}


def _guardar(hash_: str, nombre: str, error: str, respuesta: dict,
             fuentes: list[str], modelo: str, por: str) -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agente.explicaciones (hash, habilidad, error, respuesta, fuentes, "
            "modelo, por) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (hash) DO NOTHING",
            (hash_, nombre, error[:500], json.dumps(respuesta, ensure_ascii=False),
             json.dumps(fuentes, ensure_ascii=False), modelo, por))


def _parsear(texto: str) -> dict | None:
    """El modelo tiene que contestar JSON; se tolera un cerco de ``` alrededor."""
    t = (texto or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < 0:
        return None
    try:
        d = json.loads(t[i:j + 1])
    except ValueError:
        return None
    if not isinstance(d, dict) or "explicacion" not in d:
        return None
    d.setdefault("de_quien", "no_se")
    if d["de_quien"] not in ("nuestro", "dato", "proveedor", "no_se"):
        d["de_quien"] = "no_se"
    return d


def explicar(nombre: str, *, por: str = "") -> dict:
    """La explicación de la última corrida rota de esa habilidad. Nunca levanta."""
    from core import modelos
    try:
        ctx = contexto(nombre)
    except Exception as e:
        return {"ok": False, "error": f"no pude leer la habilidad: {type(e).__name__}"}
    if ctx is None:
        return {"ok": False, "error": f"«{nombre}» no está en el catálogo"}
    if not ctx["error"]:
        return {"ok": False, "error": "la última corrida no dejó ningún error que explicar"}

    h = _hash(nombre, ctx["error"], ctx["traceback"])
    try:
        c = _cache(h)
    except Exception as e:
        logger.warning("agente/explicar: no pude leer el caché (%s)", e)
        c = None
    if c:
        return {"ok": True, "cacheada": True, **c}

    if not modelos.configurado(modelos.resolver(TAREA).proveedor):
        return {"ok": False, "error": ("la IA no está configurada en este entorno "
                                       "(falta la credencial del proveedor en el .env)")}
    user = json.dumps({k: v for k, v in ctx.items() if not k.startswith("_")},
                      ensure_ascii=False, default=str)
    texto = modelos.completar(TAREA, system=_SYSTEM, user=user, usuario=por or None,
                         detalle=f"{nombre}: {ctx['error'][:120]}")
    if not texto:
        return {"ok": False, "error": ("la IA no contestó: presupuesto agotado o proveedor "
                                       "caído — mirar ia.llamadas")}
    resp = _parsear(texto)
    if resp is None:
        return {"ok": False, "error": "la IA contestó algo que no es la forma pedida",
                "crudo": texto[:800]}
    modelo = ""
    try:
        modelo = modelos.resolver(TAREA).modelo or ""
    except Exception:
        pass
    try:
        _guardar(h, nombre, ctx["error"], resp, ctx["_fuentes"], modelo, por)
    except Exception as e:
        logger.warning("agente/explicar: no pude guardar la explicación (%s)", e)
    return {"ok": True, "cacheada": False, "respuesta": resp, "fuentes": ctx["_fuentes"],
            "modelo": modelo, "por": por, "at": None}
