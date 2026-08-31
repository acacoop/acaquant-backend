"""Feed Eikon — TITULARES de noticias Reuters para la watchlist del HOME.

Mismo riel que precios/Chicago/bonos: el MISMO script de oficina pollea
`ek.get_news_headlines` cada ~10 min (una llamada por RIC del universo, con
cache de storyId para no repetir) y postea a `POST /api/ingest/eikon/news`;
la API persiste en `mercado.eikon_news`. Solo TITULARES (la nota completa no
se guarda — decisión v1: tamaño acotado). Estudio y validación en vivo:
docs/RENTA_VARIABLE.md §6b (2026-07-16).

Universo CURADO server-side (pedido del user 2026-07-24): los papeles más
relevantes de la mesa + los soberanos offshore. Editar acá = el feed lo toma
al reiniciar. Retención: al ingestar se borra lo más viejo de RETENCION_DIAS
→ la tabla queda acotada a unos pocos MB para siempre.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.eikon_bonos import BONOS_OFF
from core.postgres import get_pool

TABLE = "mercado.eikon_news"
RETENCION_DIAS = 7

# RICs de equities/ETFs a seguir (los mismos del catálogo de precios).
RICS_NEWS_EQUITIES = [
    "NVDA.O", "MSFT.O", "AAPL.O", "META.O", "TSLA.O",
    "SPY", "QQQ.O",
    "RKLB.O", "SPCX.O", "KO", "LMT",
    "GGAL.O", "YPF", "MELI.O",
]


def universo_news() -> list[dict]:
    """Lista de suscripción del feed de noticias: [{ric}]. Equities curados +
    los RICs de los soberanos offshore (BONOS_OFF)."""
    rics = RICS_NEWS_EQUITIES + list(BONOS_OFF)
    return [{"ric": r} for r in rics]


def upsert_news(docs: list[dict]) -> int:
    """Inserta titulares nuevos (dedup por story_id — ON CONFLICT DO NOTHING:
    un titular puede venir por más de un RIC, gana el primero). Aplica la
    retención en la misma pasada. Devuelve cuántos se insertaron."""
    if not docs:
        return 0
    now = datetime.now(UTC)
    rows = []
    for d in docs:
        if not isinstance(d, dict):
            continue
        sid = (d.get("story_id") or "").strip()
        titular = (d.get("titular") or "").strip()
        if not sid or not titular:
            continue
        rows.append((sid, (d.get("ric") or "").strip(), d.get("fecha"),
                     titular[:500], (d.get("fuente") or "").strip(), now))
    if not rows:
        return 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO {TABLE} (story_id, ric, fecha, titular, fuente, updated_at) "
            f"VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (story_id) DO NOTHING",
            rows)
        n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        cur.execute(f"DELETE FROM {TABLE} WHERE updated_at < %s",
                    (now - timedelta(days=RETENCION_DIAS),))
        conn.commit()
    return n


def listar_news(limit: int = 80) -> list[dict]:
    """Titulares más recientes para la tab NOTICIAS de la watchlist. El ticker
    'display' sale del RIC (sin sufijo .O/.N; los RICs de bonos se traducen al
    ticker local vía BONOS_OFF)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT story_id, ric, fecha, titular, fuente FROM {TABLE} "
            f"ORDER BY fecha DESC NULLS LAST LIMIT %s", (int(limit),))
        filas = cur.fetchall()
    out = []
    for sid, ric, fecha, titular, fuente in filas:
        ticker = BONOS_OFF.get(ric) or (ric or "").split(".")[0].split("=")[0]
        out.append({
            "story_id": sid,
            "ticker":   ticker,
            "ric":      ric,
            "fecha":    fecha.isoformat() if fecha else None,
            "titular":  titular,
            "fuente":   fuente or None,
        })
    return out
