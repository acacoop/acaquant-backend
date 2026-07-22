"""jobs/pedidos_triage.py — TRIAJE del buzón de pedidos + aviso con botones.

El problema que resuelve: la mesa le dice al copiloto lo que le falta al
producto (`manager.pedidos`) y ahí quedaba. Nadie se enteraba hasta que alguien
corría el export a mano. Un buzón que hay que ir a mirar es un buzón muerto.

El circuito completo, del pedido al código:

    la mesa habla con el copiloto      →  manager.pedidos (estado 'nuevo')
    ESTE JOB (1×/día)                  →  duplicados, impacto, esfuerzo, spec
                                          + un mensaje al celular con botones
    el jefe toca ✅ / ❌                →  jobs/pedidos_inbox.py (estado)
    `python -m scripts.gen_pedidos`     →  docs/PEDIDOS.md con la COLA
    Claude Code: `/pedidos`             →  implementa de a uno, con el OK final

Lo que agrega el LLM es lo que hace APROBABLE un pedido de un vistazo: no
repetir lo que pidieron, sino decir QUÉ habría que hacer y DÓNDE, más el par
impacto/esfuerzo que ordena la cola. La decisión sigue siendo humana: este job
NUNCA acepta ni descarta nada por su cuenta.

Guardas de costo (mismo criterio que jobs/triage.py):
  1. Solo pedidos en estado 'nuevo' → cada pedido se tría UNA vez.
  2. Los duplicados EXACTOS se detectan sin LLM (0 tokens) antes de llamar.
  3. Tope de pedidos por corrida (--max) para que una ráfaga no dispare la cuenta.
  4. Si el gateway no responde (sin credencial, presupuesto agotado), el pedido
     queda 'nuevo' SIN triar y se reintenta mañana — nunca se pierde.

PRIVACIDAD: el texto del pedido lo escribió una persona y puede nombrar a un
cliente ("no encuentro las operaciones de Fulano"). Antes de salir al proveedor
pasa por la ADUANA (core/pii_gateway) igual que el asistente; la spec que
vuelve se destokeniza para guardarla, porque la tabla vive en nuestro perímetro.

Uso:
    python -m jobs.pedidos_triage --dry-run   # qué vería y qué mandaría (0 tokens)
    python -m jobs.pedidos_triage             # corrida real
    python -m jobs.pedidos_triage --max 5     # techo de pedidos a triar
    python -m jobs.pedidos_triage --sin-aviso # tría pero no manda Telegram
"""
from __future__ import annotations

import argparse
import json
import logging
import re

from psycopg.rows import dict_row

from core import ai, pii_gateway
from core.job_runs import JobRunLogger
from core.notify import send_telegram_botones
from core.postgres import get_pool

logger = logging.getLogger(__name__)

TIPO_JOB = "pedidos_triage"
_MAX_POR_CORRIDA = 10
_IMPACTOS = ("alto", "medio", "bajo")
_ESFUERZOS = ("chico", "medio", "grande")
# Orden de la cola: lo que más rinde primero (mucho impacto, poco esfuerzo).
_PESO_IMPACTO = {"alto": 0, "medio": 1, "bajo": 2}
_PESO_ESFUERZO = {"chico": 0, "medio": 1, "grande": 2}

_SYSTEM = """Sos el product manager técnico de TradingAV, una plataforma quant de \
una mesa de dinero argentina (renta fija, renta variable, derivados, agro, \
operaciones, portfolios, back office, manager).

Te llega UN pedido que alguien de la mesa le dijo al copiloto mientras trabajaba, \
y una lista de los pedidos que YA están registrados. Devolvés SOLO un JSON, sin \
texto alrededor y sin bloque de código:

{"duplicado_de": <id o null>,
 "impacto": "alto|medio|bajo",
 "esfuerzo": "chico|medio|grande",
 "spec": "<2-4 oraciones>"}

- duplicado_de: el id de un pedido ya registrado que pide LO MISMO (aunque esté \
dicho distinto). Si no hay, null. No marques duplicado por parecerse de tema: \
tiene que ser el mismo pedido.
- impacto: cuánta gente/plata/tiempo afecta. Un bug que muestra un número mal es \
ALTO aunque sea chico de arreglar: un número equivocado se usa para decidir. Una \
comodidad para una sola persona es BAJO.
- esfuerzo: chico = un filtro, una columna, un texto. medio = una tool nueva, una \
query nueva, un cambio de vista. grande = una vista nueva, un motor, un modelo de \
datos distinto.
- spec: QUÉ habría que hacer y DÓNDE, en lenguaje claro. Es lo que alguien lee \
para decidir si lo aprueba, así que decí la solución concreta, no repitas el \
pedido. Si el pedido es ambiguo, la spec ARRANCA con "AMBIGUO:" y dice qué habría \
que preguntar antes de tocar nada.

No inventes nombres de archivos ni de tablas que no te hayan dicho. Si no sabés \
dónde vive algo, describí la vista o la pantalla."""


def _pendientes(cur, limite: int) -> list[dict]:
    cur.execute(
        "SELECT id, ts, usuario, vista, tipo, titulo, texto, contexto "
        "FROM manager.pedidos WHERE estado = 'nuevo' AND triado_at IS NULL "
        "ORDER BY ts LIMIT %s", (limite,))
    return cur.fetchall()


def _ya_registrados(cur) -> list[dict]:
    """Los pedidos vivos contra los que se compara para detectar duplicados.
    Los descartados entran a propósito: si algo se descartó y lo vuelven a
    pedir, queremos verlo marcado, no volver a triarlo de cero."""
    cur.execute(
        "SELECT id, titulo, texto, estado FROM manager.pedidos "
        "WHERE triado_at IS NOT NULL AND duplicado_de IS NULL "
        "ORDER BY ts DESC LIMIT 60")
    return cur.fetchall()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def _duplicado_exacto(pedido: dict, previos: list[dict]) -> int | None:
    """Duplicado obvio SIN gastar un token: mismo texto normalizado. Los casos
    sutiles (lo mismo dicho distinto) los resuelve el modelo."""
    t = _norm(pedido.get("texto"))
    if not t:
        return None
    for p in previos:
        if _norm(p.get("texto")) == t:
            return int(p["id"])
    return None


def _parsear(bruto: str | None) -> dict | None:
    """El modelo devuelve JSON. Se acepta envuelto en ```…``` (pasa) pero NO se
    adivina nada más: sin JSON válido el pedido queda sin triar y se reintenta,
    que es mejor que inventarle un impacto."""
    if not bruto:
        return None
    txt = bruto.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-z]*\s*|\s*```$", "", txt)
    try:
        d = json.loads(txt)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", txt, re.DOTALL)
        if not m:
            return None
        try:
            d = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(d, dict):
        return None
    return d


def _sanear(d: dict, ids_validos: set[int]) -> dict:
    """La jaula: el modelo propone, el código valida. Un impacto fuera del enum
    o un `duplicado_de` que apunta a un pedido inexistente se descartan — no se
    guarda nada que después haya que interpretar."""
    dup = d.get("duplicado_de")
    try:
        dup = int(dup) if dup is not None else None
    except (TypeError, ValueError):
        dup = None
    if dup not in ids_validos:
        dup = None
    imp = str(d.get("impacto") or "").lower()
    esf = str(d.get("esfuerzo") or "").lower()
    return {
        "duplicado_de": dup,
        "impacto": imp if imp in _IMPACTOS else "medio",
        "esfuerzo": esf if esf in _ESFUERZOS else "medio",
        "spec": str(d.get("spec") or "").strip()[:2000] or None,
    }


def _triar_uno(pedido: dict, previos: list[dict]) -> dict | None:
    """Un pedido → su triaje. None si el gateway no respondió (queda para
    mañana). El texto sale por la ADUANA y la spec vuelve destokenizada."""
    catalogo_ok = pii_gateway.catalogo_disponible()
    mapping = pii_gateway._mapping_nuevo()

    def _limpiar(s: str) -> str:
        nonlocal mapping
        if not catalogo_ok:
            return s
        limpio, mapping = pii_gateway.tokenize(s or "", mapping)
        return limpio

    lista = "\n".join(f"#{p['id']} [{p['estado']}] {_limpiar(str(p['titulo'] or ''))}"
                      for p in previos) or "(no hay pedidos previos)"
    user = (
        f"PEDIDO NUEVO #{pedido['id']}\n"
        f"vista: {pedido.get('vista') or '—'} · tipo declarado: {pedido.get('tipo') or '—'}\n"
        f"título: {_limpiar(str(pedido.get('titulo') or ''))}\n"
        f"texto: {_limpiar(str(pedido.get('texto') or ''))}\n"
        + (f"contexto de la conversación: {_limpiar(str(pedido['contexto']))}\n"
           if pedido.get("contexto") else "")
        + f"\nPEDIDOS YA REGISTRADOS:\n{lista}"
    )
    bruto = ai.completar("triage_pedido", system=_SYSTEM, user=user,
                         detalle=f"pedido #{pedido['id']}: {pedido.get('titulo')}")
    d = _parsear(bruto)
    if d is None:
        logger.warning("pedidos_triage: #%s sin JSON usable — queda sin triar", pedido["id"])
        return None
    out = _sanear(d, {int(p["id"]) for p in previos})
    if out["spec"] and catalogo_ok:
        out["spec"] = pii_gateway.detokenize(out["spec"], mapping)
    return out


def _persistir(cur, pedido_id: int, t: dict) -> None:
    cur.execute(
        "UPDATE manager.pedidos SET impacto = %s, esfuerzo = %s, spec = %s, "
        "duplicado_de = %s, triado_at = now(), "
        # un duplicado no se revisa dos veces: queda descartado apuntando al original
        "estado = CASE WHEN %s IS NULL THEN estado ELSE 'descartado' END, "
        "notas = CASE WHEN %s IS NULL THEN notas "
        "             ELSE COALESCE(notas || ' · ', '') || 'duplicado del #' || %s END "
        "WHERE id = %s",
        (t["impacto"], t["esfuerzo"], t["spec"], t["duplicado_de"],
         t["duplicado_de"], t["duplicado_de"], t["duplicado_de"], pedido_id))


def _orden(p: dict) -> tuple:
    return (_PESO_IMPACTO.get(p.get("impacto"), 1),
            _PESO_ESFUERZO.get(p.get("esfuerzo"), 1), p["id"])


def _mensaje(triados: list[dict]) -> tuple[str, list[list[tuple[str, str]]]]:
    """El digest + un par de botones POR PEDIDO. Se manda ordenado por lo que
    más rinde (impacto alto / esfuerzo chico primero) para que el orden de
    lectura ya sea el orden de prioridad."""
    lineas = [f"📥 *{len(triados)} pedido(s) nuevo(s) de la mesa*", ""]
    botones: list[list[tuple[str, str]]] = []
    for p in sorted(triados, key=_orden):
        lineas.append(f"*#{p['id']} · {p['titulo']}*")
        lineas.append(f"impacto {p['impacto']} · esfuerzo {p['esfuerzo']} · "
                      f"desde `{p.get('vista') or '—'}`")
        if p.get("spec"):
            lineas.append(p["spec"])
        lineas.append("")
        botones.append([(f"✅ #{p['id']}", f"pedido:aceptar:{p['id']}"),
                        (f"❌ #{p['id']}", f"pedido:descartar:{p['id']}")])
    lineas.append("_Aceptado = entra a la cola de trabajo._")
    return "\n".join(lineas), botones


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="qué pedidos vería y qué haría — NO llama al LLM ni escribe")
    ap.add_argument("--max", type=int, default=_MAX_POR_CORRIDA,
                    help=f"techo de pedidos a triar por corrida (default {_MAX_POR_CORRIDA})")
    ap.add_argument("--sin-aviso", action="store_true", help="tría pero no manda Telegram")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    with JobRunLogger(TIPO_JOB) as run, get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            pendientes = _pendientes(cur, max(1, args.max))
            previos = _ya_registrados(cur)
        if not pendientes:
            run.log("sin pedidos nuevos para triar")
            run.set_stat("triados", 0)
            return
        run.log(f"{len(pendientes)} pedido(s) sin triar "
                f"(hay {len(previos)} ya registrados)")

        if args.dry_run:
            for p in pendientes:
                dup = _duplicado_exacto(p, previos)
                marca = f"duplicado exacto del #{dup} (0 tokens)" if dup else "iría al LLM"
                run.log(f"  #{p['id']} · {p['titulo']} → {marca}")
            run.log("--dry-run: no se llamó al LLM, no se escribió, no se avisó.")
            return

        triados: list[dict] = []
        for p in pendientes:
            dup = _duplicado_exacto(p, previos)
            if dup is not None:
                t = {"duplicado_de": dup, "impacto": "bajo", "esfuerzo": "chico",
                     "spec": None}
            else:
                t = _triar_uno(p, previos)
                if t is None:
                    run.error(f"#{p['id']} sin triar (el gateway no respondió)")
                    continue
            with conn.cursor() as cur:
                _persistir(cur, int(p["id"]), t)
            conn.commit()
            if t["duplicado_de"] is not None:
                run.log(f"  #{p['id']} → duplicado del #{t['duplicado_de']} (descartado)")
                continue
            triados.append({**p, **t})
            run.log(f"  #{p['id']} → {t['impacto']}/{t['esfuerzo']}")

        run.set_stat("triados", len(triados))
        if triados and not args.sin_aviso:
            texto, botones = _mensaje(triados)
            if send_telegram_botones(texto, botones):
                run.log(f"aviso enviado con {len(botones)} pedido(s) para aprobar")
            else:
                run.log("Telegram no configurado — revisar con: "
                        "python -m scripts.gen_pedidos")


if __name__ == "__main__":
    main()
