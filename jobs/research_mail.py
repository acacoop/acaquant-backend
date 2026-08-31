"""jobs/research_mail.py — Ingesta del research diario por mail.

Lee la casilla por IMAP, detecta los mails de research (filtro por remitente) y
persiste el texto CRUDO en `ia.research` — que es lo que se MUESTRA, tal cual, en
la vista Research (docs/RESEARCH.md).

**Este job NO usa IA.** Tenía un DESTILADO opcional ({resumen, temas, hechos}
generado por un LLM) detrás del flag `--destilar`, y se borró el 2026-08-28 por
decisión del user: *«ese destilado no tiene sentido, no se usa en absoluto; el
research se guarda y se muestra así nomás»*. Y era exacto — el flag nunca estuvo
en el cron (solo 4 mails llegaron a tenerlo, corridos a mano) y **ninguna
pantalla lo dibujaba**: el campo viajaba en el payload y el front lo tiraba. Con
él se fue el gateway de IA entero, que existía para servirlo.

Idempotente: dedup por Message-ID (UNIQUE en la tabla) → re-correr no duplica.

Env vars (van al `.env` del Droplet — ver docs/SECRETS.md):
  RESEARCH_IMAP_USER      — casilla que recibe el research (ej. Gmail).
  RESEARCH_IMAP_PASSWORD  — app password de la casilla (NO la contraseña normal;
                            Gmail: Cuenta → Seguridad → Verificación en 2 pasos →
                            Contraseñas de aplicaciones).
  RESEARCH_MAIL_FROM      — remitente(s) del research, separados por coma
                            (alcanza el dominio: "1816.com.ar"). Match substring
                            case-insensitive sobre From + Asunto + Cuerpo → cubre
                            los mails REENVIADOS a mano (el From pasa a ser el del
                            que reenvía, pero el remitente original queda en el
                            cuerpo). Usar el dominio del research original
                            (1816.com.ar), NO tu propia dirección.
  RESEARCH_IMAP_HOST      — opcional, default imap.gmail.com.

Uso:
    python -m jobs.research_mail --dry-run    # qué mails ve y qué ingestaría (0 tokens)
    python -m jobs.research_mail              # corrida real (cron)
    python -m jobs.research_mail --dias 7     # ventana de búsqueda IMAP (default 3)
"""
from __future__ import annotations

import argparse
import email
import email.header
import email.utils
import html
import imaplib
import logging
import os
import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from core.postgres import get_pool

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

logger = logging.getLogger(__name__)

_ART = ZoneInfo("America/Argentina/Buenos_Aires")
_DIAS_DEFAULT = 3          # ventana IMAP (SINCE): cubre fin de semana largo


# ── Extracción de texto del mail ─────────────────────────────────────────────

_RE_STYLE_SCRIPT = re.compile(r"<(style|script)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_RE_BR = re.compile(r"<br\s*/?>|</p>|</div>|</tr>|</h[1-6]>", re.IGNORECASE)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_BLANK = re.compile(r"\n{3,}")


def _html_a_texto(h: str) -> str:
    """HTML de mail → texto plano legible. Suficiente para prosa de research
    (no pretende parsear layouts): fuera estilos/scripts, breaks → \\n, fuera
    tags, entidades decodificadas, líneas en blanco colapsadas."""
    t = _RE_STYLE_SCRIPT.sub("", h)
    t = _RE_BR.sub("\n", t)
    t = _RE_TAG.sub("", t)
    t = html.unescape(t)
    lineas = [ln.strip() for ln in t.splitlines()]
    return _RE_BLANK.sub("\n\n", "\n".join(lineas)).strip()


def _decodificar_header(valor: str | None) -> str:
    """Header MIME (asunto/from con =?utf-8?...?=) → string legible."""
    if not valor:
        return ""
    partes = []
    for texto, charset in email.header.decode_header(valor):
        if isinstance(texto, bytes):
            partes.append(texto.decode(charset or "utf-8", errors="replace"))
        else:
            partes.append(texto)
    return "".join(partes).strip()


def _cuerpo_de(msg: email.message.Message) -> str:
    """Texto del mail: prefiere text/plain; si solo hay text/html, lo convierte."""
    plano, html_crudo = "", ""
    partes = msg.walk() if msg.is_multipart() else [msg]
    for parte in partes:
        ctype = parte.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        payload = parte.get_payload(decode=True)
        if not payload:
            continue
        charset = parte.get_content_charset() or "utf-8"
        texto = payload.decode(charset, errors="replace")
        if ctype == "text/plain" and not plano:
            plano = texto
        elif ctype == "text/html" and not html_crudo:
            html_crudo = texto
    return (plano or _html_a_texto(html_crudo)).strip()


def _fecha_art(msg: email.message.Message) -> date:
    """Día del research = header Date del mail llevado a ART. Sin Date → hoy ART."""
    try:
        dt = email.utils.parsedate_to_datetime(msg.get("Date"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(_ART).date()
    except (TypeError, ValueError):
        return datetime.now(_ART).date()


# ── IMAP ─────────────────────────────────────────────────────────────────────


def _buscar_mails(dias: int) -> list[email.message.Message]:
    """Mails de los remitentes configurados dentro de la ventana. El filtro por
    remitente se aplica en Python (substring case-insensitive sobre From) — el
    SEARCH de IMAP por FROM es quisquilloso con charsets y alias."""
    user = os.getenv("RESEARCH_IMAP_USER")
    password = os.getenv("RESEARCH_IMAP_PASSWORD")
    remitentes = [
        s.strip().lower()
        for s in (os.getenv("RESEARCH_MAIL_FROM") or "").split(",")
        if s.strip()
    ]
    if not user or not password or not remitentes:
        raise RuntimeError(
            "faltan env vars: RESEARCH_IMAP_USER / RESEARCH_IMAP_PASSWORD / "
            "RESEARCH_MAIL_FROM (ver docstring del job)"
        )
    host = os.getenv("RESEARCH_IMAP_HOST", "imap.gmail.com")
    desde = (datetime.now(UTC) - timedelta(days=dias)).strftime("%d-%b-%Y")

    out: list[email.message.Message] = []
    conn = imaplib.IMAP4_SSL(host)
    try:
        conn.login(user, password)
        conn.select("INBOX", readonly=True)   # readonly: no marca leídos ni toca nada
        _status, data = conn.search(None, f"(SINCE {desde})")
        ids = data[0].split() if data and data[0] else []
        for mid in ids:
            _status, partes = conn.fetch(mid, "(RFC822)")
            if not partes or not isinstance(partes[0], tuple):
                continue
            msg = email.message_from_bytes(partes[0][1])
            # Match por remitente en From + Asunto + Cuerpo. Los mails REENVIADOS a
            # mano (el caso del user: reenvía el research desde su corporativo)
            # traen el From del que reenvía, NO el de 1816 — pero el remitente
            # original queda en el cuerpo ("De: Research 1816 <research@1816.com.ar>")
            # y el asunto suele conservarse. Por eso se busca en los tres.
            de = _decodificar_header(msg.get("From")).lower()
            asunto = _decodificar_header(msg.get("Subject")).lower()
            cuerpo = _cuerpo_de(msg).lower()[:3000]
            if any(r in f"{de}\n{asunto}\n{cuerpo}" for r in remitentes):
                out.append(msg)
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return out


# ── Destilado (LLM vía gateway) ──────────────────────────────────────────────


# ── Persistencia ─────────────────────────────────────────────────────────────


def _ya_ingestados(cur, message_ids: list[str]) -> set[str]:
    if not message_ids:
        return set()
    cur.execute("SELECT message_id FROM ia.research WHERE message_id = ANY(%s)", (message_ids,))
    return {r[0] for r in cur.fetchall()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="muestra qué ingestaría; no escribe nada")
    ap.add_argument("--dias", type=int, default=_DIAS_DEFAULT,
                    help=f"ventana IMAP en días (default {_DIAS_DEFAULT})")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    mails = _buscar_mails(args.dias)
    print(f"{len(mails)} mails del remitente configurado en los últimos {args.dias} días")

    candidatos = []
    for msg in mails:
        message_id = (msg.get("Message-ID") or "").strip()
        cuerpo = _cuerpo_de(msg)
        if not message_id or not cuerpo:
            continue
        candidatos.append({
            "message_id": message_id,
            "fecha": _fecha_art(msg),
            "fuente": _decodificar_header(msg.get("From")),
            "asunto": _decodificar_header(msg.get("Subject")),
            "cuerpo": cuerpo,
        })

    if args.dry_run:
        with get_pool().connection() as conn, conn.cursor() as cur:
            vistos = _ya_ingestados(cur, [c["message_id"] for c in candidatos])
        for c in candidatos:
            estado = "YA INGESTADO" if c["message_id"] in vistos else "NUEVO → se ingestaría"
            print(f"  {c['fecha']} · {c['asunto'][:70]!r} · {len(c['cuerpo'])} chars · {estado}")
        print("(DRY-RUN — no se escribió nada)")
        return

    from core.job_runs import JobRunLogger
    with JobRunLogger("research_mail") as jr:
        n_nuevos = 0
        with get_pool().connection() as conn, conn.cursor() as cur:
            vistos = _ya_ingestados(cur, [c["message_id"] for c in candidatos])
            for c in candidatos:
                if c["message_id"] in vistos:
                    continue
                cur.execute(
                    "INSERT INTO ia.research (fecha, fuente, asunto, message_id, cuerpo)"
                    " VALUES (%s,%s,%s,%s,%s) ON CONFLICT (message_id) DO NOTHING",
                    (c["fecha"], c["fuente"], c["asunto"], c["message_id"], c["cuerpo"]),
                )
                n_nuevos += 1
                jr.log(f"ingestado research del {c['fecha']}")
        jr.set_stat("mails_vistos", len(candidatos))
        jr.set_stat("nuevos", n_nuevos)
    print(f"✅ {n_nuevos} nuevos")


if __name__ == "__main__":
    main()
