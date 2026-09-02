"""jobs/licitaciones.py — LAS LICITACIONES QUE ANUNCIAN LOS MAILS DE 1816.

Doc: `docs/AGENT.md` §0.di. El user (2026-09-01), sobre S29E7: *«no detectó
otros bonos nuevos que se licitaron»*. Un bono existe para el agente cuando
Primary lo lista o 1816 lo pone en una curva, y eso pasa DESPUÉS de la
licitación. Pero el anuncio llega antes, en los mails de research que
`jobs/research_mail` ya guarda en `ia.research` todos los días.

Qué hace: lee los mails de los últimos días que todavía no procesó, se queda
solo con los que hablan de una licitación (prefiltro por palabras: sin eso no
se paga ni una llamada), le pide a la IA (`licitacion_extraer`) que saque
ticker, tipo, ajuste, fechas y moneda en JSON estricto, y lo guarda en
`mercado.licitaciones`. Cada mail queda marcado en `mercado.licitaciones_mail`
con cuántos instrumentos trajo: re-correr no vuelve a pagar. La habilidad
`licitacion_anunciada` del agente cruza esa tabla contra el master, Primary y
el catálogo de 1816, y avisa lo que todavía no existe en ningún lado.

Uso:
    python -m jobs.licitaciones            # los mails no procesados de los últimos 5 días
    python -m jobs.licitaciones --dias 15  # más atrás
    python -m jobs.licitaciones --dry      # muestra qué mandaría, no llama a la IA ni escribe
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import UTC, datetime

from core.job_runs import JobRunLogger
from core.postgres import get_pool

logger = logging.getLogger(__name__)

TAREA = "licitacion_extraer"
# Sin una de estas palabras el mail no habla de una licitación: no se paga.
_PALABRAS = re.compile(r"licitaci[oó]n|licita\b|se licit|colocaci[oó]n de|nuevas? letras?|"
                       r"nuevo bono|canje\b|liquida el|liquidaci[oó]n el", re.I)
_MAX_CUERPO = 12000
_TICKER = re.compile(r"^[A-Z0-9]{3,6}$")

_SYSTEM = """Sos el lector de licitaciones de una mesa de capitales argentina. Te
llega el texto de un mail de research (1816). Extraé SOLO los instrumentos que
el mail anuncia como licitados, colocados o a licitar por el Tesoro Nacional,
el BCRA o provincias, con su ticker de mercado (BYMA/A3) si el mail lo dice.
Si el mail no anuncia ninguna licitación, contestá [].

Contestá SOLO un JSON: una lista de objetos con estas claves (cadena vacía si
no está en el texto, nunca inventes):
[{"ticker": "S29E7", "denominacion": "Lecap 29-ene-2027", "tipo": "letra|bono",
  "ajuste": "fija|cer|tamar|badlar|dolar_linked|dual|otro",
  "moneda": "ARS|USD", "fecha_licitacion": "YYYY-MM-DD", "fecha_liquidacion": "YYYY-MM-DD",
  "vencimiento": "YYYY-MM-DD", "emisor": "Tesoro|BCRA|<provincia>"}]
"""


def _mails(dias: int) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT r.id, r.fecha, r.fuente, r.asunto, r.cuerpo FROM ia.research r "
            " LEFT JOIN mercado.licitaciones_mail m ON m.research_id = r.id "
            "WHERE r.fecha >= current_date - %s AND m.research_id IS NULL "
            "ORDER BY r.fecha, r.id", (int(dias),))
        return [{"id": r[0], "fecha": r[1], "fuente": r[2], "asunto": r[3] or "",
                 "cuerpo": r[4] or ""} for r in cur.fetchall()]


def _marcar(research_id: int, encontrados: int, con_ia: bool) -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mercado.licitaciones_mail (research_id, encontrados, con_ia) "
            "VALUES (%s, %s, %s) ON CONFLICT (research_id) DO UPDATE SET "
            "encontrados = EXCLUDED.encontrados, con_ia = EXCLUDED.con_ia, procesado_at = now()",
            (int(research_id), int(encontrados), bool(con_ia)))


def _fecha(v) -> str | None:
    s = str(v or "").strip()
    return s if re.match(r"^\d{4}-\d{2}-\d{2}$", s) else None


def normalizar(items) -> list[dict]:
    """Lo que la IA contestó, saneado: ticker en mayúsculas con forma de ticker,
    fechas ISO o nada, ajuste dentro del vocabulario. Lo que no cumple, afuera."""
    out: list[dict] = []
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        tk = str(it.get("ticker") or "").strip().upper()
        if not _TICKER.match(tk):
            continue
        ajuste = str(it.get("ajuste") or "otro").strip().lower()
        if ajuste not in ("fija", "cer", "tamar", "badlar", "dolar_linked", "dual", "otro"):
            ajuste = "otro"
        out.append({
            "ticker": tk,
            "denominacion": str(it.get("denominacion") or "")[:160],
            "tipo": str(it.get("tipo") or "")[:20],
            "ajuste": ajuste,
            "moneda": str(it.get("moneda") or "").upper()[:3],
            "fecha_licitacion": _fecha(it.get("fecha_licitacion")),
            "fecha_liquidacion": _fecha(it.get("fecha_liquidacion")),
            "vencimiento": _fecha(it.get("vencimiento")),
            "emisor": str(it.get("emisor") or "")[:60],
        })
    return out


def _parsear(texto: str) -> list[dict]:
    t = (texto or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    i, j = t.find("["), t.rfind("]")
    if i < 0 or j < 0:
        return []
    try:
        return normalizar(json.loads(t[i:j + 1]))
    except ValueError:
        return []


def _guardar(items: list[dict], mail: dict) -> int:
    n = 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        for it in items:
            cur.execute(
                "INSERT INTO mercado.licitaciones (ticker, denominacion, tipo, ajuste, moneda, "
                " fecha_licitacion, fecha_liquidacion, vencimiento, emisor, research_id, "
                " asunto_mail, fecha_mail) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (ticker) DO UPDATE SET "
                "  denominacion = COALESCE(NULLIF(EXCLUDED.denominacion, ''), mercado.licitaciones.denominacion), "
                "  tipo = COALESCE(NULLIF(EXCLUDED.tipo, ''), mercado.licitaciones.tipo), "
                "  ajuste = EXCLUDED.ajuste, "
                "  moneda = COALESCE(NULLIF(EXCLUDED.moneda, ''), mercado.licitaciones.moneda), "
                "  fecha_licitacion = COALESCE(EXCLUDED.fecha_licitacion, mercado.licitaciones.fecha_licitacion), "
                "  fecha_liquidacion = COALESCE(EXCLUDED.fecha_liquidacion, mercado.licitaciones.fecha_liquidacion), "
                "  vencimiento = COALESCE(EXCLUDED.vencimiento, mercado.licitaciones.vencimiento), "
                "  emisor = COALESCE(NULLIF(EXCLUDED.emisor, ''), mercado.licitaciones.emisor), "
                "  research_id = EXCLUDED.research_id, asunto_mail = EXCLUDED.asunto_mail, "
                "  fecha_mail = EXCLUDED.fecha_mail, actualizado_at = now()",
                (it["ticker"], it["denominacion"], it["tipo"], it["ajuste"], it["moneda"],
                 it["fecha_licitacion"], it["fecha_liquidacion"], it["vencimiento"],
                 it["emisor"], mail["id"], mail["asunto"][:200], mail["fecha"]))
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=5)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from core import ai, llm
    with JobRunLogger("licitaciones") as jr:
        mails = _mails(args.dias)
        candidatos = [m for m in mails if _PALABRAS.search(m["asunto"] + "\n" + m["cuerpo"])]
        jr.set_stat("mails_vistos", len(mails))
        jr.set_stat("candidatos", len(candidatos))
        jr.log(f"{len(mails)} mail(s) sin procesar · {len(candidatos)} hablan de licitación")
        if args.dry:
            for m in candidatos:
                jr.log(f"  [dry] {m['fecha']} {m['asunto'][:80]}")
            jr.set_stat("dry", True)
            return 0
        if candidatos and not llm.configurado():
            jr.error("la IA no está configurada: los mails quedan sin procesar (no se marcan)")
            return 1
        encontrados: list[str] = []
        con_ia = 0
        for m in mails:
            if m not in candidatos:
                _marcar(m["id"], 0, False)          # no hablaba de eso: no se paga
                continue
            user = f"ASUNTO: {m['asunto']}\nFECHA: {m['fecha']}\n\n{m['cuerpo'][:_MAX_CUERPO]}"
            texto = ai.completar(TAREA, system=_SYSTEM, user=user,
                                 detalle=f"licitaciones: {m['asunto'][:100]}")
            if texto is None:
                jr.error(f"la IA no contestó para «{m['asunto'][:60]}» — queda para la próxima")
                continue
            con_ia += 1
            items = _parsear(texto)
            n = _guardar(items, m) if items else 0
            _marcar(m["id"], n, True)
            encontrados += [it["ticker"] for it in items]
            if items:
                jr.log(f"  {m['fecha']} «{m['asunto'][:60]}» → {', '.join(it['ticker'] for it in items)}")
        jr.set_stat("con_ia", con_ia)
        jr.set_stat("encontrados", len(encontrados))
        jr.set_stat("encontrados_lista", sorted(set(encontrados))[:200])
        jr.set_stat("fecha", datetime.now(UTC).date().isoformat())
    return 0


if __name__ == "__main__":
    sys.exit(main())
