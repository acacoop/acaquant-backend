"""jobs/ia_calidad.py — control de calidad de las conversaciones de IA.

Cierra el loop que hoy es MANUAL: en vez de que un humano lea trazas con
`diag_ia_trazas --full` para cazar respuestas malas, este job las trae solas.
Una vez por día:

  1. 👎 del usuario (ia.trazas.feedback = -1)  → se marcan directo (0 tokens).
  2. El resto de los turnos nuevos             → un PRE-FILTRO barato (regex, 0
     tokens) deja solo los que MUESTRAN una señal de falla conocida (una tabla
     que dice "ordenado", una respuesta que termina preguntando, un "sin datos",
     un "así que" que encadena métricas). Sobre ESOS —y solo esos— corre un
     crítico LLM barato que confirma y clasifica el modo de falla.
  3. Digest a Telegram con las sospechosas del día, ya clasificadas.

Lo que NO hace (lección del buzón de pedidos): NO auto-corrige, NO toca el repo,
NO lee de Telegram (solo manda). Es un DETECTOR, mismo molde que jobs/triage.py.

Las trazas guardan el texto YA TOKENIZADO (ia.trazas.detalle/respuesta) — la
aduana ya paso, así que el crítico lee texto PII-safe por construcción.

Guardas de costo (mismo criterio que triage):
  1. Watermark — cada corrida procesa solo lo nuevo (idempotente).
  2. Pre-filtro determinista — el LLM solo ve los candidatos, no todo.
  3. Tope por corrida (--max) — una ráfaga no dispara la cuenta.
  4. Presupuesto — superado → se marca lo que se pudo, sin romper.

Uso:
    python -m jobs.ia_calidad --dry-run          # qué vería y qué mandaría (0 tokens)
    python -m jobs.ia_calidad                     # corrida real
    python -m jobs.ia_calidad --lookback-horas 48 # ignora el watermark
    python -m jobs.ia_calidad --sin-aviso         # marca pero no manda Telegram
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import UTC, datetime, timedelta

from psycopg.rows import dict_row

from core import ai
from core.job_runs import JobRunLogger
from core.notify import send_telegram
from core.postgres import get_pool

logger = logging.getLogger(__name__)

TIPO_JOB = "ia_calidad"
_TAREAS_INTERACTIVAS = ("asistente_negocio", "copiloto_vista", "copiloto_vista_pro")
_MAX_POR_CORRIDA = 25
_LOOKBACK_DEFAULT_HORAS = 24
_MODOS = ("ranking_a_mano", "deflexion", "causalidad", "tool_muda",
          "dato_equivocado", "otro")

# ── Pre-filtro determinista (0 tokens): señales BARATAS de un posible problema.
# No decide nada — solo evita mandarle al crítico los turnos obviamente sanos.
_TABLA_RE = re.compile(r"\|.*\|.*\n\s*\|?\s*[-:]+")           # tabla markdown
_ORDEN_RE = re.compile(r"ordenad|ranking|de mayor a menor|top \d", re.IGNORECASE)
_PREGUNTA_FINAL_RE = re.compile(r"\?\s*$")
_MUDA_RE = re.compile(r"sin datos|no lo tengo|no tengo ese dato|lo dejo planteado|"
                      r"no puedo (?:darte|responder|modificar)", re.IGNORECASE)
_CAUSA_RE = re.compile(r"así que|por eso|con lo cual|lo que (?:indica|sugiere) que",
                       re.IGNORECASE)


def _senal_barata(detalle: str, respuesta: str) -> bool:
    """True si el turno MERECE mirada del crítico. Falso = obviamente sano."""
    r = respuesta or ""
    if _TABLA_RE.search(r) and _ORDEN_RE.search(r):
        return True                                  # tabla + "ordenado" → hand-sort
    if r.count("?") >= 2 or _PREGUNTA_FINAL_RE.search(r.strip()):
        return True                                  # deflexión
    if _MUDA_RE.search(r):
        return True                                  # tool muda / compromiso
    return bool(_CAUSA_RE.search(r))                 # causalidad encadenada


_SYSTEM = """Sos un revisor de CALIDAD de un copiloto de trading. Te doy UN turno \
(la pregunta del operador y la respuesta del copiloto) y decidís si la respuesta \
tiene un problema de calidad CONOCIDO. Devolvés SOLO un JSON, sin texto alrededor:

{"sospechoso": true|false, "modo": "<uno>", "severidad": "alto|medio|bajo", "nota": "<1 frase>"}

Los modos (elegí el que mejor aplique; si no hay problema, sospechoso=false):
- ranking_a_mano: presenta una tabla/lista "ordenada por X" pero NO está bien \
ordenada, o dice que ordenó algo que evidentemente no ordenó.
- deflexion: no entrega una lectura; contesta con preguntas o menús turno tras \
turno sin comprometer una conclusión, aunque el usuario ya dio el objetivo.
- causalidad: encadena métricas independientes con un "así que"/"por eso" que no \
se sostiene (ej. "viene bien en el pre-market, así que puede rendir más en el año").
- tool_muda: dice "sin datos"/"no lo tengo" o se disculpa por algo que debería poder \
responder, o promete hacer algo ("lo dejo planteado") sin hacerlo.
- dato_equivocado: responde con OTRO dato distinto al pedido (patrimonio cuando \
pidieron volumen, un nivel técnico cuando pidieron un máximo).
- otro: un problema real que no encaja en los anteriores.

Sé exigente con el falso positivo: una respuesta breve y correcta, o una \
NEGATIVA HONESTA por falta de datos, NO es sospechosa. Una repregunta en la \
PRIMERA pregunta abierta tampoco. Marcá sospechoso solo si un operador se \
quejaría con razón."""


def _get_watermark(cur, lookback_horas: int) -> datetime:
    cur.execute("SELECT ultimo_procesado FROM ia.calidad_estado WHERE id = 'watermark'")
    row = cur.fetchone()
    if row and row["ultimo_procesado"]:
        return row["ultimo_procesado"]
    return datetime.now(UTC) - timedelta(hours=lookback_horas)


def _set_watermark(cur, ts: datetime) -> None:
    cur.execute(
        "INSERT INTO ia.calidad_estado (id, ultimo_procesado) VALUES ('watermark', %s) "
        "ON CONFLICT (id) DO UPDATE SET ultimo_procesado = EXCLUDED.ultimo_procesado",
        (ts,))


def _leer_turnos(cur, desde: datetime) -> list[dict]:
    cur.execute(
        "SELECT id, ts, tarea, usuario, feedback, detalle, respuesta "
        "FROM ia.trazas WHERE ts > %s AND ok = true AND tarea = ANY(%s) "
        "  AND respuesta IS NOT NULL "
        "  AND id NOT IN (SELECT traza_id FROM ia.calidad_flags) "
        "ORDER BY ts",
        (desde, list(_TAREAS_INTERACTIVAS)))
    return cur.fetchall()


def _parsear(bruto: str | None) -> dict | None:
    if not bruto:
        return None
    txt = bruto.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-z]*\s*|\s*```$", "", txt)
    m = re.search(r"\{.*\}", txt, re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None


def _criticar(turno: dict) -> dict | None:
    """Un turno → veredicto saneado, o None si el gateway no respondió."""
    user = (f"PREGUNTA DEL OPERADOR:\n{(turno.get('detalle') or '')[:800]}\n\n"
            f"RESPUESTA DEL COPILOTO:\n{(turno.get('respuesta') or '')[:2000]}")
    bruto = ai.completar("critico_calidad", system=_SYSTEM, user=user,
                         detalle=f"QA traza #{turno['id']}")
    d = _parsear(bruto)
    if d is None:
        return None
    if not d.get("sospechoso"):
        return {"sospechoso": False}
    modo = str(d.get("modo") or "otro")
    sev = str(d.get("severidad") or "medio")
    return {
        "sospechoso": True,
        "modo": modo if modo in _MODOS else "otro",
        "severidad": sev if sev in ("alto", "medio", "bajo") else "medio",
        "nota": str(d.get("nota") or "").strip()[:280],
    }


def _guardar_flag(cur, turno: dict, modo: str, severidad: str, nota: str) -> None:
    cur.execute(
        "INSERT INTO ia.calidad_flags (traza_id, ts, tarea, usuario, modo, severidad, nota) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (traza_id) DO NOTHING",
        (turno["id"], turno["ts"], turno.get("tarea"), turno.get("usuario"),
         modo, severidad, nota))


_SEV_ORD = {"alto": 0, "medio": 1, "bajo": 2}


def _digest(flags: list[dict]) -> str:
    """Digest en TEXTO PLANO (sin Markdown): las notas las escribe el crítico y
    pueden traer cualquier caracter (`*`, `[`, `_`); interpoladas en Markdown
    rompían el parser de Telegram con HTTP 400 (visto en la 1ª corrida real
    2026-07-23). El digest se manda con markdown=False → nunca puede romperse
    por el contenido del modelo."""
    por_modo: dict[str, int] = {}
    for f in flags:
        por_modo[f["modo"]] = por_modo.get(f["modo"], 0) + 1
    lineas = [f"🔎 Calidad IA — {len(flags)} respuesta(s) para revisar",
              " · ".join(f"{m}: {n}" for m, n in sorted(por_modo.items())), ""]
    for f in sorted(flags, key=lambda x: _SEV_ORD.get(x["severidad"], 1))[:10]:
        lineas.append(f"[{f['severidad']}] {f['modo']} · traza #{f['traza_id']}")
        if f.get("nota"):
            lineas.append(f"  {f['nota']}")
    lineas.append("\nRevisá con: python -m scripts.diag_ia_trazas --buscar <id>")
    return "\n".join(lineas)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="qué vería y qué mandaría — NO llama al LLM, NO escribe, NO avisa")
    ap.add_argument("--lookback-horas", type=int, default=None,
                    help="lee desde now-N horas IGNORANDO el watermark")
    ap.add_argument("--max", type=int, default=_MAX_POR_CORRIDA,
                    help=f"techo de turnos a criticar por corrida (default {_MAX_POR_CORRIDA})")
    ap.add_argument("--sin-aviso", action="store_true", help="marca pero no manda Telegram")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    with JobRunLogger(TIPO_JOB) as run, get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            if args.lookback_horas is not None:
                desde = datetime.now(UTC) - timedelta(hours=args.lookback_horas)
            else:
                desde = _get_watermark(cur, _LOOKBACK_DEFAULT_HORAS)
            turnos = _leer_turnos(cur, desde)

        run.log(f"desde {desde:%Y-%m-%d %H:%M}Z · {len(turnos)} turnos nuevos")
        # 👎 explícitos: se marcan directo (el usuario ya dijo que está mal).
        votos = [t for t in turnos if t.get("feedback") == -1]
        # candidatos al crítico: los que muestran una señal barata (sin los 👎,
        # que ya se marcan), capados.
        candidatos = [t for t in turnos if t.get("feedback") != -1
                      and _senal_barata(t.get("detalle") or "", t.get("respuesta") or "")]
        run.log(f"{len(votos)} con 👎 · {len(candidatos)} candidatos por señal "
                f"(critico hasta {args.max})")

        if args.dry_run:
            for t in votos:
                run.log(f"  👎 traza #{t['id']} ({t['tarea']})")
            for t in candidatos[:args.max]:
                run.log(f"  ? traza #{t['id']} ({t['tarea']}) → iría al crítico")
            run.log("--dry-run: no se llamó al LLM, no se escribió, no se avisó.")
            return

        flags: list[dict] = []
        # 1) votos negativos → flag directo, 0 tokens
        for t in votos:
            with conn.cursor() as cur:
                _guardar_flag(cur, t, "voto_negativo", "alto", "👎 del usuario")
            conn.commit()
            flags.append({"traza_id": t["id"], "modo": "voto_negativo",
                          "severidad": "alto", "nota": "👎 del usuario"})

        # 2) candidatos → crítico (capado)
        for t in candidatos[:args.max]:
            v = _criticar(t)
            if v is None:
                run.error(f"traza #{t['id']} sin crítica (gateway) — queda para mañana")
                continue
            if not v.get("sospechoso"):
                continue
            with conn.cursor() as cur:
                _guardar_flag(cur, t, v["modo"], v["severidad"], v["nota"])
            conn.commit()
            flags.append({"traza_id": t["id"], **v})
            run.log(f"  ⚑ #{t['id']} {v['severidad']}/{v['modo']}: {v['nota']}")

        # watermark: solo si NO se corto por el tope (si se corto, dejamos que
        # la proxima corrida siga desde el mismo punto con los que faltaron)
        if len(candidatos) <= args.max:
            with conn.cursor() as cur:
                _set_watermark(cur, datetime.now(UTC))
            conn.commit()

        run.set_stat("marcadas", len(flags))
        if flags and not args.sin_aviso:
            # markdown=False: el digest lleva notas del crítico (contenido del
            # modelo) que pueden romper el parser Markdown de Telegram.
            if send_telegram(_digest(flags), markdown=False):
                run.log(f"digest enviado con {len(flags)} respuesta(s)")
            else:
                run.log("Telegram no configurado — las flags quedan en ia.calidad_flags")


if __name__ == "__main__":
    main()
