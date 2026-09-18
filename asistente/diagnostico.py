"""EL DIAGNÓSTICO: por qué apareció un hallazgo del AV AGENT y qué hacer.

Copia el método de una persona que investiga antes de tocar: junta contexto
estándar, verifica contra una fuente primaria, concluye, y recién ahí decide.
Son cinco etapas y solo la del medio tiene libertad:

    [1] DOSIER      código      todo lo estándar, siempre igual (memoria del pasado incluida)
    [2] INVESTIGAR  modelo      loop con herramientas de SOLO lectura (asistente/agentes/diagnostico.py)
    [3] CONCLUIR    modelo      una llamada sin herramientas, con esquema cerrado
    [4] VALIDAR     código      las reglas de la casa: lo que NO se hace, aunque el modelo lo diga
    [5] GUARDAR     código      en la fila del hallazgo (agente/registro.py), con evidencias y control

No ejecuta nada: recomienda. La ejecución (arreglos, relanzar, mensajes) es
otra etapa que pasa por las puertas que ya existen. Doc: docs/AvAgentAI.md §15.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta

from langchain_core.messages import HumanMessage, SystemMessage
from psycopg.rows import dict_row

from agente import registro
from agente import reloj as RJ
from agente import tipos as T
from asistente import control as CTL
from asistente import eventos as EV
from asistente import memoria
from core import modelos
from core.postgres import get_pool
from core.traza import Traza

logger = logging.getLogger(__name__)

AGENTE_NOMBRE = "diagnostico"
TAREA_CONCLUIR = "asistente_diagnostico_concluir"
VERSION = 1
_RE_PREGUNTA = re.compile(r"diagnosticar hallazgo #(\d+)")

CAUSAS = ("transitorio", "configuracion", "bug_codigo", "fuera_de_alcance",
          "detector_desactualizado", "incidente", "sin_verificar")
ACCIONES = ("aplicar_arreglo", "esperar_hasta", "escalar_a", "cambiar_codigo", "nada_porque", "no_se")
# Sin negación adelante: «no relanzar» es una recomendación, no un reinicio.
_RE_REF = re.compile(r"[0-9a-f]{12}")
_REINICIO = re.compile(r"(?<!no )(?<!ni )(reinici|systemctl\s+(re)?start|relanz)", re.I)

ESQUEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "diagnostico_hallazgo",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "causa": {"type": "string", "enum": list(CAUSAS)},
                "resumen": {"type": "string",
                            "description": "Dos frases: qué pasa y por qué. Castellano, sin markdown."},
                "verificado": {
                    "type": "array",
                    "description": "Cada afirmación en la que se apoya la causa, con su cita y si se verificó.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "afirmacion": {"type": "string"},
                            "cita": {"type": "string",
                                     "description": "La referencia [E:ref:campo] de la lectura que la respalda, o vacío."},
                            "estado": {"type": "string", "enum": ["verificado", "hipotesis"]},
                        },
                        "required": ["afirmacion", "cita", "estado"],
                        "additionalProperties": False,
                    },
                },
                "accion": {"type": "string", "enum": list(ACCIONES)},
                "accion_detalle": {"type": "string",
                                   "description": "Qué exactamente: qué arreglo, hasta cuándo esperar, a quién y qué decirle, qué cambiar."},
                "no_hacer": {"type": "array", "items": {"type": "string"},
                             "description": "Qué NO hacer y por qué, uno por renglón."},
                "archivo": {"type": ["string", "null"],
                            "description": "Si es bug de código: el archivo del repo, relativo a la raíz."},
                "motivo_codigo": {"type": ["string", "null"],
                                  "description": "Si es bug de código: qué falta o qué está mal, en una frase."},
                "escalar_a": {"type": ["string", "null"],
                              "description": "Si hay que escalar: a quién (rol o persona) y por qué a esa persona."},
            },
            "required": ["causa", "resumen", "verificado", "accion", "accion_detalle",
                         "no_hacer", "archivo", "motivo_codigo", "escalar_a"],
            "additionalProperties": False,
        },
    },
}

_INSTRUCCION_CONCLUIR = """\
Sos quien REDACTA la conclusión de un diagnóstico técnico. Te dan el DOSIER
(el hallazgo, su historia y el reloj) y las NOTAS del investigador con sus
lecturas citadas. No inventás lecturas nuevas: concluís sobre lo que hay.

Contestá SOLO un JSON con esta forma exacta (sin texto alrededor):
{"causa": una de %s,
 "resumen": "dos frases",
 "verificado": [{"afirmacion": "...", "cita": "[E:ref:campo] o vacío", "estado": "verificado|hipotesis"}],
 "accion": una de %s,
 "accion_detalle": "qué exactamente",
 "no_hacer": ["qué no hacer y por qué"],
 "archivo": "ruta o null", "motivo_codigo": "frase o null", "escalar_a": "quién o null"}

Cómo decidir: la ACCIÓN sale de la CAUSA, nunca al revés. bug_codigo →
cambiar_codigo (con archivo y motivo). configuracion → escalar_a (quién decide
el cron, el umbral o la cadencia) o cambiar_codigo si el umbral vive en el
código. transitorio → esperar_hasta (cuándo se verifica solo). fuera_de_alcance
→ escalar_a (quien tiene esa máquina o ese proveedor). detector_desactualizado
→ cambiar_codigo sobre el detector. incidente → aplicar_arreglo si la
habilidad declara uno para esa regla, si no escalar_a. Si no se pudo verificar
nada, causa sin_verificar y accion no_se: no adivines.

Una afirmación sin cita es una hipótesis y se marca así. Reiniciar un proceso
por un bug de código es taparlo: va en no_hacer. Lo crónico no se parchea.
"""


# ── [1] DOSIER ──────────────────────────────────────────────────────────────


def dosier(hallazgo_id: int) -> dict | None:
    """Todo lo estándar sobre un hallazgo, armado por código y siempre igual.
    Acá entra la memoria del pasado: episodios, acciones y sus resultados,
    reincidencias y diagnósticos anteriores del mismo problema."""
    from agente import catalogo

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM agente.hallazgos WHERE id = %s", (int(hallazgo_id),))
        h = cur.fetchone()
        if not h:
            return None
        trio = (h["habilidad"], h["sujeto"], h["regla"])
        desde = datetime.now(UTC) - timedelta(days=T.VENTANA_CRONICO_D)
        cur.execute(
            "SELECT count(*) AS episodios FROM agente.hallazgos"
            " WHERE habilidad = %s AND sujeto = %s AND regla = %s AND detectado_at >= %s",
            (*trio, desde))
        episodios = int(cur.fetchone()["episodios"])
        cur.execute(
            "SELECT at, arreglo, por, ok, error FROM agente.acciones"
            " WHERE habilidad = %s AND sujeto = %s AND regla = %s ORDER BY at DESC LIMIT 10", trio)
        acciones = [_publico(dict(r)) for r in cur.fetchall()]
        cur.execute(
            "SELECT count(*) AS n, max(volvio_at) AS ultima FROM agente.reincidencias"
            " WHERE habilidad = %s AND sujeto = %s AND regla = %s", trio)
        rein = _publico(dict(cur.fetchone() or {}))
        cur.execute(
            "SELECT id, estado, cerrado_como, diagnosticado_at, diagnostico FROM agente.hallazgos"
            " WHERE habilidad = %s AND sujeto = %s AND regla = %s AND id <> %s"
            " AND diagnostico IS NOT NULL ORDER BY diagnosticado_at DESC LIMIT 3",
            (*trio, int(hallazgo_id)))
        precedentes = []
        for r in cur.fetchall():
            d = dict(r["diagnostico"] or {})
            precedentes.append(_publico({
                "hallazgo_id": r["id"], "estado_final": r["estado"], "cerrado_como": r["cerrado_como"],
                "diagnosticado_at": r["diagnosticado_at"], "causa": d.get("causa"),
                "accion": d.get("accion"), "resumen": d.get("resumen")}))
    hab = catalogo.HABILIDADES.get(h["habilidad"])
    naturaleza = hab.naturaleza.get(h["regla"], T.INCIDENTE) if hab else T.INCIDENTE
    fila = _publico({k: v for k, v in h.items() if k not in ("diagnostico",)})
    ahora = RJ.ahora_utc()
    return {
        "hallazgo": fila,
        "detector": {
            "que_mira": hab.que_mira if hab else None,
            "cada_segundos": hab.cada_segundos if hab else None,
            "ventana": hab.ventana if hab else None,
            "umbrales": dict(hab.umbrales) if hab else {},
            "arreglo_declarado": hab.arreglo_de(h["regla"]) if hab else "",
            "automatico": h["regla"] in (hab.automatico if hab else {}),
            "naturaleza": naturaleza,
        },
        "historia": {
            "episodios_30d": episodios,
            "cronico": naturaleza == T.INCIDENTE and episodios >= T.EPISODIOS_CRONICO,
            "veces_visto": h["veces"],
            "acciones": acciones,
            "reincidencias": rein,
            "precedentes": precedentes,
        },
        "reloj": {"ahora_utc": ahora.isoformat(), "hora_mesa": RJ.hhmm(ahora),
                  "dia_habil": RJ.dia_habil(ahora), "en_rueda": RJ.en_rueda(ahora)},
    }


def _publico(d: dict) -> dict:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in d.items()}


# ── [2] INVESTIGAR ──────────────────────────────────────────────────────────


def investigar(dosier_: dict, *, run_id: str, usuario: str) -> dict:
    """El loop con herramientas de solo lectura, como cualquier agente del
    asistente (mismo subgrafo, mismos eventos, mismas evidencias)."""
    from asistente import grafo

    hid = dosier_["hallazgo"]["id"]
    consigna = (f"Diagnosticá el hallazgo #{hid}. Ya tenés su DOSIER abajo (es lo que devuelve "
                f"`dosier({hid})`; no hace falta volver a pedirlo). Verificá el supuesto del "
                f"detector contra una fuente primaria antes de concluir.\n\nDOSIER:\n"
                + json.dumps(dosier_, ensure_ascii=False, default=str))
    r = grafo._SUBGRAFOS[AGENTE_NOMBRE].invoke({
        "mensajes": [HumanMessage(content=consigna)], "foco": {},
        "pregunta": consigna, "usuario": usuario, "sesion": run_id, "rol": "admin",
        "portal": "trading", "run_id": run_id, "cuentas": (),
        "vueltas": 0, "tokens_in": 0, "tokens_out": 0, "llamadas": [], "eventos": [], "datos": [],
        "evidencias": [],
    })
    return {
        "notas": r.get("respuesta") or r.get("crudo") or "",
        "error": r.get("error"),
        "evidencias": list(r.get("evidencias") or []),
        "datos": list(r.get("datos") or []),
        "vueltas": r.get("vueltas", 0),
        "tokens_in": r.get("tokens_in", 0), "tokens_out": r.get("tokens_out", 0),
        "llamadas": list(r.get("llamadas") or []),
        "contexto": memoria.contexto(memoria.a_dicts(list(r.get("mensajes") or []))),
    }


# ── [3] CONCLUIR ────────────────────────────────────────────────────────────


def concluir(dosier_: dict, investigacion: dict, *, run_id: str, usuario: str) -> dict:
    """Una llamada sin herramientas, con esquema si el proveedor lo soporta.
    Devuelve la conclusión leída más el costo; `error` si no salió."""
    fuentes = [{"ref": e.get("ref"), "herramienta": e.get("herramienta"), "sujeto": e.get("sujeto"),
                "campos": sorted((e.get("campos") or {}).keys())[:12]}
               for e in investigacion.get("evidencias") or []][:80]
    entrada = [
        SystemMessage(content=_INSTRUCCION_CONCLUIR % (list(CAUSAS), list(ACCIONES))),
        HumanMessage(content=(
            "DOSIER:\n" + json.dumps(dosier_, ensure_ascii=False, default=str)
            + "\n\nNOTAS DEL INVESTIGADOR:\n" + (investigacion.get("notas") or "(sin notas)")
            + ("\n\nEl investigador no pudo terminar: " + investigacion["error"]
               if investigacion.get("error") else "")
            + "\n\nFUENTES (las citas válidas son estas):\n"
            + json.dumps(fuentes, ensure_ascii=False))),
    ]
    try:
        t = modelos.resolver(TAREA_CONCLUIR)
        tr = Traza(t.nombre, t.modelo, usuario=usuario, sesion=run_id, run_id=run_id,
                   detalle=f"concluir · hallazgo #{dosier_['hallazgo']['id']}",
                   guardar_texto=not t.traza_sin_texto)
        msg = modelos.modelo(TAREA_CONCLUIR, usuario=usuario, sesion=run_id, esquema=ESQUEMA,
                             traza=tr).invoke(entrada)
    except Exception as e:
        return {"error": f"la conclusión no salió: {type(e).__name__}: {e}", "conclusion": None,
                "tokens_in": 0, "tokens_out": 0, "llamadas": []}
    crudo = memoria.texto(msg.content)
    uso = msg.usage_metadata or {}
    conclusion = leer_conclusion(crudo)
    # La etapa 3 también deja rastro en el ciclo: qué devolvió y cuánto costó.
    # Sin esto «no parseó» se leía sin poder ver el texto que no parseó.
    EV.emitir({"tipo": "diagnostico_concluir", "agente": AGENTE_NOMBRE, "texto": crudo[:2000],
               "parseo": conclusion is not None,
               "tokens_in": uso.get("input_tokens", 0), "tokens_out": uso.get("output_tokens", 0)})
    return {"conclusion": conclusion, "crudo": crudo, "error": None,
            "tokens_in": uso.get("input_tokens", 0), "tokens_out": uso.get("output_tokens", 0),
            "llamadas": list(tr.ids)}


def leer_conclusion(texto: str | None) -> dict | None:
    """El JSON de la conclusión, venga solo o adentro de prosa (DeepSeek no
    acepta esquema). None si no hay nada parseable."""
    t = (texto or "").strip()
    if not t:
        return None
    # Cercos de código (```json … ```) y prosa alrededor: se busca el objeto.
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    candidatos = [t]
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        candidatos.append(t[i:j + 1])
    for c in candidatos:
        try:
            d = json.loads(c)
        except (ValueError, TypeError):
            continue
        if isinstance(d, dict) and "causa" in d and "accion" in d:
            return d
    return None


# ── [4] VALIDAR ─────────────────────────────────────────────────────────────


def validar(conclusion: dict | None, dosier_: dict, *, en_rueda: bool) -> dict:
    """PURA. Las reglas de la casa aplicadas a lo que dijo el modelo. Devuelve
    la conclusión ajustada, con `validado.cambios` diciendo qué se tocó y por qué.
    Lo que el modelo no puede saltear vive acá, no en el prompt."""
    cambios: list[str] = []
    if not conclusion:
        return {"causa": "sin_verificar", "resumen": "El modelo no devolvió una conclusión legible.",
                "verificado": [], "accion": "no_se", "accion_detalle": "", "no_hacer": [],
                "archivo": None, "motivo_codigo": None, "escalar_a": None,
                "validado": {"cambios": ["sin conclusión parseable: queda en no sé"]}}
    c = {
        "causa": str(conclusion.get("causa") or "sin_verificar"),
        "resumen": str(conclusion.get("resumen") or "").strip()[:1200],
        "verificado": [v for v in (conclusion.get("verificado") or []) if isinstance(v, dict)][:12],
        "accion": str(conclusion.get("accion") or "no_se"),
        "accion_detalle": str(conclusion.get("accion_detalle") or "").strip()[:800],
        "no_hacer": [str(x)[:300] for x in (conclusion.get("no_hacer") or []) if str(x).strip()][:8],
        "archivo": (str(conclusion.get("archivo")).strip() or None) if conclusion.get("archivo") else None,
        "motivo_codigo": (str(conclusion.get("motivo_codigo")).strip()[:400] or None)
        if conclusion.get("motivo_codigo") else None,
        "escalar_a": (str(conclusion.get("escalar_a")).strip()[:200] or None)
        if conclusion.get("escalar_a") else None,
    }
    if c["causa"] not in CAUSAS:
        cambios.append(f"causa {c['causa']!r} no es de la lista: sin_verificar")
        c["causa"] = "sin_verificar"
    if c["accion"] not in ACCIONES:
        cambios.append(f"acción {c['accion']!r} no es de la lista: no_se")
        c["accion"] = "no_se"
    detector = dosier_.get("detector") or {}
    historia = dosier_.get("historia") or {}
    arreglo = detector.get("arreglo_declarado") or ""

    # Una causa afirmada necesita al menos UNA afirmación verificada con cita. La
    # cita vale con la forma completa `[E:ref:campo]` o con el ref solo: lo que
    # importa es que apunte a una lectura real, no el formato.
    verificadas = [v for v in c["verificado"]
                   if v.get("estado") == "verificado" and _RE_REF.search(str(v.get("cita") or ""))]
    if c["causa"] not in ("transitorio", "sin_verificar") and not verificadas:
        cambios.append(f"causa {c['causa']} sin ninguna afirmación verificada con cita: sin_verificar / no_se")
        c["causa"], c["accion"] = "sin_verificar", "no_se"
    # Lo que no se verificó no se actúa; lo transitorio se espera; lo de afuera se escala.
    if c["causa"] == "sin_verificar" and c["accion"] != "no_se":
        cambios.append("sin verificar no se recomienda ninguna acción: no_se")
        c["accion"] = "no_se"
    if c["causa"] == "transitorio" and c["accion"] == "aplicar_arreglo":
        cambios.append("lo transitorio se resuelve solo: esperar_hasta, no aplicar")
        c["accion"] = "esperar_hasta"
    if c["causa"] == "fuera_de_alcance" and c["accion"] == "aplicar_arreglo":
        cambios.append("fuera de alcance del Droplet no se aplica desde acá: escalar_a")
        c["accion"] = "escalar_a"
    # La acción sale de la causa: un bug o una configuración no se arreglan apretando.
    if c["causa"] in ("bug_codigo", "detector_desactualizado") and c["accion"] in ("aplicar_arreglo",):
        cambios.append("bug de código no se arregla aplicando un arreglo: cambiar_codigo")
        c["accion"] = "cambiar_codigo"
    if c["causa"] == "configuracion" and c["accion"] == "aplicar_arreglo":
        cambios.append("una configuración mal puesta no se parchea con el arreglo: escalar_a")
        c["accion"] = "escalar_a"
    # Solo se aplica lo que la habilidad declara para esa regla.
    if c["accion"] == "aplicar_arreglo" and not arreglo:
        cambios.append("la regla no declara arreglo: escalar_a")
        c["accion"] = "escalar_a"
    # Lo crónico no se parchea: si el mismo arreglo ya se aplicó 3 veces, es configuración.
    if c["accion"] == "aplicar_arreglo" and historia.get("cronico"):
        aplicadas = sum(1 for a in historia.get("acciones") or [] if a.get("arreglo") == arreglo)
        if aplicadas >= T.EPISODIOS_CRONICO:
            cambios.append(f"crónico y {arreglo} ya se aplicó {aplicadas} veces: escalar_a como configuración")
            c["accion"], c["causa"] = "escalar_a", "configuracion"
    # Nada se reinicia ni relanza en rueda: se espera al cierre. Solo sobre lo
    # que el agente aplicaría; una escalación es un mensaje a una persona, que decide.
    if en_rueda and c["accion"] == "aplicar_arreglo" and _REINICIO.search(c["accion_detalle"]):
        cambios.append("en rueda no se reinicia ni relanza: esperar_hasta el cierre de rueda")
        c["accion"] = "esperar_hasta"
        c["accion_detalle"] = "Hasta el cierre de rueda. Después: " + c["accion_detalle"]
    # Un cambio de código apunta a un archivo que existe.
    if c["accion"] == "cambiar_codigo" and c["archivo"]:
        from asistente.agentes.diagnostico import _ruta_segura

        p = _ruta_segura(c["archivo"])
        if p is None or not p.is_file():
            cambios.append(f"el archivo {c['archivo']!r} no existe en el repo: queda sin archivo")
            c["archivo"] = None
    c["validado"] = {"cambios": cambios}
    return c


# ── [5] GUARDAR + orquestación ──────────────────────────────────────────────


def correr(hallazgo_id: int, *, run_id: str, usuario: str = T.ACTOR_AGENTE) -> dict:
    """Las cinco etapas sobre un hallazgo. Devuelve el diagnóstico guardado
    (o `error`). Los eventos salen por `asistente.eventos` como en cualquier run."""
    d = dosier(int(hallazgo_id))
    if d is None:
        return {"error": f"no existe el hallazgo {hallazgo_id}", "respuesta": None}
    if d["hallazgo"].get("estado") == T.IGNORADO:
        return {"error": "una persona lo marcó «no me interesa»: no se diagnostica", "respuesta": None}
    EV.emitir({"tipo": "diagnostico_dosier", "agente": AGENTE_NOMBRE, "hallazgo_id": int(hallazgo_id),
               "episodios": d["historia"]["episodios_30d"], "cronico": d["historia"]["cronico"]})
    inv = investigar(d, run_id=run_id, usuario=usuario)
    con = concluir(d, inv, run_id=run_id, usuario=usuario)
    ok = validar(con.get("conclusion"), d, en_rueda=bool(d["reloj"]["en_rueda"]))
    texto = " ".join([ok["resumen"], ok["accion_detalle"],
                      *[str(v.get("afirmacion") or "") + " " + str(v.get("cita") or "") for v in ok["verificado"]]])
    control = CTL.revisar(texto, contexto=inv.get("contexto") or "",
                          pregunta=json.dumps(d, ensure_ascii=False, default=str),
                          evidencias=inv.get("evidencias") or [])
    diagnostico = {
        **ok, "version": VERSION, "run_id": run_id, "at": datetime.now(UTC).isoformat(),
        "estado_hallazgo": d["hallazgo"].get("estado"),
        "notas": (inv.get("notas") or "")[:4000],
        # Si la conclusión no parseó, el texto crudo es lo único que explica por qué.
        "conclusion_cruda": (con.get("crudo") or "")[:4000] if con.get("conclusion") is None else None,
        "modelos": _modelos_usados(),
        "error_investigacion": inv.get("error"), "error_conclusion": con.get("error"),
        "control": {"ok": control["ok"], "hallazgos": control["hallazgos"]},
        "tokens_in": inv["tokens_in"] + con.get("tokens_in", 0),
        "tokens_out": inv["tokens_out"] + con.get("tokens_out", 0),
        "vueltas": inv["vueltas"],
    }
    registro.anotar_diagnostico(int(hallazgo_id), diagnostico)
    EV.emitir({"tipo": "diagnostico_conclusion", "agente": AGENTE_NOMBRE,
               "hallazgo_id": int(hallazgo_id), "causa": ok["causa"], "accion": ok["accion"],
               "cambios": ok["validado"]["cambios"]})
    return {
        "respuesta": f"{ok['causa']} → {ok['accion']}: {ok['resumen']}",
        "falta": None, "error": None, "diagnostico": diagnostico,
        "hallazgo_id": int(hallazgo_id), "control": control,
        "vueltas": inv["vueltas"], "tokens_in": diagnostico["tokens_in"],
        "tokens_out": diagnostico["tokens_out"],
        "llamadas": list(inv["llamadas"]) + list(con.get("llamadas") or []),
        "agentes": [AGENTE_NOMBRE], "estado": {},
    }


def _modelos_usados() -> dict:
    """Con qué corrió cada etapa (lo decide el panel): queda en el diagnóstico
    para que «no parseó» se pueda leer junto con «lo contestó tal modelo»."""
    out = {}
    for etapa, tarea in (("lector", "asistente_diagnostico_lector"),
                         ("investigar", "asistente_diagnostico_investigar"),
                         ("concluir", TAREA_CONCLUIR)):
        try:
            t = modelos.resolver(tarea)
            out[etapa] = f"{t.proveedor}/{t.modelo}" + ("" if t.elegido else " (default, sin elegir en el panel)")
        except Exception as e:
            out[etapa] = f"? ({e})"
    return out


def correr_run(run: dict) -> dict:
    """La entrada del worker: un run `tipo = diagnostico` cuya pregunta es
    `diagnosticar hallazgo #N`."""
    m = _RE_PREGUNTA.search(str(run.get("pregunta") or ""))
    if not m:
        return {"error": f"no entiendo el pedido {run.get('pregunta')!r}", "respuesta": None}
    return correr(int(m.group(1)), run_id=run["run_id"], usuario=run.get("usuario") or T.ACTOR_AGENTE)


# ── el disparo a reacción ───────────────────────────────────────────────────


def _elegir(abiertos: list[dict], en_cola: set[int], hoy: int, ahora: datetime, *,
            tope_pasada: int, tope_dia: int, refresco_h: int) -> list[int]:
    """PURA. Qué hallazgos abiertos toca diagnosticar ahora: sin diagnóstico,
    con el estado cambiado desde el último, o con uno viejo y el hallazgo aún
    abierto. Nunca los que ya están en cola, nunca más que los topes."""
    cupo = min(int(tope_pasada), max(0, int(tope_dia) - int(hoy)))
    if cupo <= 0:
        return []
    orden = {"alta": 0, "media": 1, "baja": 2}
    candidatos = []
    for h in abiertos:
        if h["id"] in en_cola or h.get("estado") not in T.ABIERTOS:
            continue
        d = h.get("diagnostico") or {}
        cuando = h.get("diagnosticado_at")
        viejo = cuando is not None and (ahora - cuando) > timedelta(hours=refresco_h)
        if cuando is None or d.get("estado_hallazgo") != h.get("estado") or viejo:
            candidatos.append(h)
    candidatos.sort(key=lambda h: (orden.get(h.get("severidad"), 9),
                                   -(h.get("detectado_at") or ahora).timestamp()))
    return [int(h["id"]) for h in candidatos[:cupo]]


def pedir(hallazgo_id: int) -> dict:
    """Una persona pide el diagnóstico de UN hallazgo desde AHORA. Misma puerta
    que el disparo automático (un run `tipo = diagnostico`), sin los topes:
    los topes acotan al daemon, no a quien mira la pantalla. No encola dos
    veces el mismo mientras hay uno en cola, y no diagnostica lo ignorado."""
    from asistente import ejecuciones

    hid = int(hallazgo_id)
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT estado FROM agente.hallazgos WHERE id = %s", (hid,))
        fila = cur.fetchone()
        if not fila:
            return {"ok": False, "error": f"no existe el hallazgo {hid}"}
        if fila["estado"] == T.IGNORADO:
            return {"ok": False, "error": "una persona lo marcó «no me interesa»: no se diagnostica"}
        cur.execute(
            "SELECT run_id FROM ia.ejecuciones WHERE tipo = 'diagnostico' AND pregunta = %s"
            " AND estado = ANY(%s) ORDER BY creada_at DESC LIMIT 1",
            (_pregunta(hid), list(ejecuciones.ACTIVOS)))
        activo = cur.fetchone()
    if activo:
        return {"ok": True, "run_id": activo["run_id"], "ya_estaba": True}
    run = ejecuciones.crear(_pregunta(hid), usuario=T.ACTOR_AGENTE, rol="admin",
                            portal="trading", tipo="diagnostico")
    return {"ok": True, "run_id": run.get("run_id"), "ya_estaba": False}


def traza(hallazgo_id: int, run_id: str | None = None) -> dict:
    """Lo que hizo EL DIAGNÓSTICO de un hallazgo, para verlo en el LAB: sus runs
    (el último primero) y el ciclo entero de uno (por defecto el último): cada
    vuelta, qué dijo el modelo y qué le costó, qué pidió, qué le volvió, y la
    conclusión cruda. Lo mismo que ya quedó en `ia.eventos_ejecucion`; acá no se
    resume nada. Los runs son del agente (`av-agent`), por eso no pasan por el
    filtro de dueño del LAB."""
    from asistente import ejecuciones

    hid = int(hallazgo_id)
    runs = ejecuciones.runs_de(_pregunta(hid))
    elegido = next((r for r in runs if r["run_id"] == run_id), None) if run_id else (runs[0] if runs else None)
    eventos = ejecuciones.eventos_de(elegido["run_id"]) if elegido else []
    return {"hallazgo_id": hid, "runs": runs, "run": elegido, "eventos": eventos}


def listado(limite: int = 50) -> dict:
    """Los diagnósticos como lista, para el LAB: una fila por corrida, la más
    nueva primero, con su hallazgo (habilidad, sujeto, problema), su estado, y
    si terminó, causa → acción, resumen y lo que costó en tokens. La fuente es
    `ia.ejecuciones` (`tipo = diagnostico`): no hay una copia en conversaciones.
    Es lo que reemplaza al contador de «atascados» como forma de ver la cola."""
    from asistente import ejecuciones

    runs = ejecuciones.runs_de_tipo("diagnostico", limite)
    por_run = {}
    for r in runs:
        m = _RE_PREGUNTA.search(r.get("pregunta") or "")
        por_run[r["run_id"]] = int(m.group(1)) if m else None
    ids = sorted({h for h in por_run.values() if h is not None})
    hallazgos = {}
    if ids:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id, habilidad, sujeto, severidad, estado, problema FROM agente.hallazgos"
                        " WHERE id = ANY(%s)", (ids,))
            hallazgos = {int(f["id"]): dict(f) for f in cur.fetchall()}
    filas = []
    for r in runs:
        hid = por_run[r["run_id"]]
        h = hallazgos.get(hid) or {}
        res = r.get("resultado") if isinstance(r.get("resultado"), dict) else {}
        d = res.get("diagnostico") if isinstance(res.get("diagnostico"), dict) else {}
        filas.append({
            "run_id": r["run_id"], "estado": r["estado"], "error": r.get("error"),
            "creada_at": r.get("creada_at"), "iniciada_at": r.get("iniciada_at"),
            "finalizada_at": r.get("finalizada_at"),
            "hallazgo_id": hid, "habilidad": h.get("habilidad"), "sujeto": h.get("sujeto"),
            "severidad": h.get("severidad"), "estado_hallazgo": h.get("estado"),
            "problema": h.get("problema"),
            "causa": d.get("causa"), "accion": d.get("accion"), "resumen": d.get("resumen"),
            "vueltas": res.get("vueltas"), "tokens_in": res.get("tokens_in"),
            "tokens_out": res.get("tokens_out"),
        })
    activos = sum(1 for f in filas if f["estado"] in ejecuciones.ACTIVOS)
    # El interruptor viaja con la lista: es la misma pantalla, y así el front no
    # tiene una segunda lectura que pueda quedar desfasada de la primera.
    return {"diagnosticos": filas, "activos": activos, "automatico": estado_automatico()}


def cancelar(run_id: str) -> dict:
    """Cancela un run de diagnóstico (en cola: muere ya; corriendo: el worker
    corta en el próximo paso). Solo runs del agente y solo de este tipo."""
    from asistente import ejecuciones

    run = ejecuciones.obtener(run_id, T.ACTOR_AGENTE)
    if not run or run.get("tipo") != "diagnostico":
        return {"ok": False, "error": "ese run no es un diagnóstico"}
    r = ejecuciones.cancelar(run_id, T.ACTOR_AGENTE) or run
    return {"ok": True, "run_id": run_id, "estado": r.get("estado")}


def _pregunta(hallazgo_id: int) -> str:
    return f"diagnosticar hallazgo #{int(hallazgo_id)}"


# ── el interruptor: MANUAL, salvo que el LAB diga lo contrario ──────────────
#
# El daemon encola solo ÚNICAMENTE si `ia.config` tiene `diagnostico:automatico`
# = "1". Sin fila, con otro valor, o con la base sin contestar: MANUAL. Es
# fail-closed a propósito: un diagnóstico gasta tokens de verdad, y «no pude
# leer el interruptor» no puede significar «prendido». Vivía en el `.env`
# (`DIAGNOSTICO_AUTOMATICO`), y para apagarlo había que entrar al Droplet y
# reiniciar el daemon; ahora lo prende y apaga el LAB, y vale en la pasada
# siguiente. Las otras puertas (el botón «diagnosticar», la consola) son una
# persona pidiendo: no pasan por acá.
CLAVE_AUTOMATICO = "diagnostico:automatico"
_SQL_ENCOLADOS_HOY = ("SELECT count(*) FROM ia.ejecuciones WHERE tipo = 'diagnostico'"
                      " AND creada_at >= date_trunc('day', now())")


def automatico() -> bool:
    """¿El daemon encola solo? Se lee en cada pasada, sin caché: apagar tiene
    que valer en la pasada siguiente, no dentro de un minuto."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT valor FROM ia.config WHERE clave = %s", (CLAVE_AUTOMATICO,))
            fila = cur.fetchone()
    except Exception as e:
        logger.warning("diagnostico: no pude leer el interruptor (%s) — queda MANUAL", e)
        return False
    return bool(fila) and str(fila[0]).strip() == "1"


def estado_automatico() -> dict:
    """El interruptor y sus topes, como los muestra el LAB: prendido o no,
    cuántos se encolaron hoy y hasta cuántos puede."""
    from config import DIAGNOSTICO_REFRESCO_H, DIAGNOSTICO_TOPE_DIA, DIAGNOSTICO_TOPE_PASADA

    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(_SQL_ENCOLADOS_HOY)
            hoy = int(cur.fetchone()[0])
    except Exception as e:
        logger.warning("diagnostico: no pude contar los de hoy (%s)", e)
        hoy = None
    return {"automatico": automatico(), "hoy": hoy, "tope_dia": DIAGNOSTICO_TOPE_DIA,
            "tope_pasada": DIAGNOSTICO_TOPE_PASADA, "refresco_h": DIAGNOSTICO_REFRESCO_H}


def poner_automatico(prendido: bool, *, por: str) -> dict:
    """Prende o apaga el automático desde el LAB. Queda en `ia.config` con quién
    lo tocó. Apagar no cancela lo que ya está en cola: eso se hace desde la
    lista, de a uno, porque ahí puede haber pedidos de una persona."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ia.config (clave, valor, updated_by) VALUES (%s, %s, %s)"
                " ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor,"
                "  updated_at = now(), updated_by = EXCLUDED.updated_by",
                (CLAVE_AUTOMATICO, "1" if prendido else "0", por))
    except Exception as e:
        return {"ok": False, "error": f"no pude guardar el interruptor: {type(e).__name__}: {e}"}
    logger.info("diagnostico: automático %s por %s", "PRENDIDO" if prendido else "APAGADO", por)
    return {"ok": True, **estado_automatico()}


def encolar_pendientes(*, forzar: bool = False) -> dict:
    """Encola un run por hallazgo que toque, con los topes de `config.py`.

    Sin `forzar` es el daemon: sólo encola si el interruptor del LAB está
    prendido (`automatico()`); apagado, devuelve `apagado: True` sin leer
    nada. `forzar=True` es una persona en la consola (`scripts.diagnosticar
    --encolar`): manual, como apretar el botón."""
    from asistente import ejecuciones
    from config import DIAGNOSTICO_REFRESCO_H, DIAGNOSTICO_TOPE_DIA, DIAGNOSTICO_TOPE_PASADA

    if not forzar and not automatico():
        return {"encolados": [], "apagado": True, "hoy": None, "abiertos": None, "en_cola": []}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, estado, severidad, detectado_at, diagnosticado_at, diagnostico"
            " FROM agente.hallazgos WHERE estado = ANY(%s)", (list(T.ABIERTOS),))
        abiertos = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT pregunta FROM ia.ejecuciones WHERE tipo = 'diagnostico' AND estado = ANY(%s)",
            (list(ejecuciones.ACTIVOS),))
        en_cola = {int(m.group(1)) for r in cur.fetchall()
                   if (m := _RE_PREGUNTA.search(r["pregunta"] or ""))}
        cur.execute(_SQL_ENCOLADOS_HOY)
        hoy = int(cur.fetchone()["count"])
    ids = _elegir(abiertos, en_cola, hoy, datetime.now(UTC), tope_pasada=DIAGNOSTICO_TOPE_PASADA,
                  tope_dia=DIAGNOSTICO_TOPE_DIA, refresco_h=DIAGNOSTICO_REFRESCO_H)
    encolados = []
    for hid in ids:
        run = ejecuciones.crear(_pregunta(hid), usuario=T.ACTOR_AGENTE, rol="admin",
                                portal="trading", tipo="diagnostico")
        encolados.append({"hallazgo_id": hid, "run_id": run.get("run_id")})
    return {"encolados": encolados, "apagado": False, "hoy": hoy + len(encolados),
            "abiertos": len(abiertos), "en_cola": sorted(en_cola)}
