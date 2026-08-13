"""core/curvas_sql.py — lectura del master de renta fija desde SQL.

El master vive en `mercado.curvas` (Postgres). La columna
`data` jsonb = el doc COMPLETO (mismo shape: fechas como strings ISO, flujos
anidados) → los readers que hacen `.get('campo')` sobre el doc funcionan
sin cambios. Las columnas tipadas (`curva`, `ticker_corto`, `fecha_vencimiento`)
se usan SOLO para filtrar barato.

Capa `core/` → usable por engines (loader de motores) y por api/services.
Master chico (~57 instrumentos) → se traen los docs completos sin problema.
"""
import threading
import time

from core.postgres import get_pool

# Cache en memoria del MASTER completo (perf 2026-06-29). El master cambia ~1×/semana
# (alta/baja de instrumento) pero 6 services (renta_fija, fair_value, carry_trade,
# sinteticos, order_book, acreencias) lo leían en CADA request → traían + deserializaban
# los ~223 docs jsonb (con `flujos`) cada vez (cargar_todos medía 56ms). Lo cacheamos
# 300s y servimos por_curva/find_one filtrando en memoria (master chico → instantáneo).
# Tras un alta/baja de bono llamar `invalidar()` para no esperar el TTL.
_TTL = 300.0
_cache: dict = {"data": None, "ts": 0.0}
_lock = threading.Lock()


def _load_all() -> list[dict]:
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT data FROM mercado.curvas")
            return [r[0] for r in cur.fetchall() if r[0] is not None]
    except Exception:
        return []


def _master() -> list[dict]:
    """Lista RAW cacheada del master (uso interno, NO mutar)."""
    now = time.monotonic()
    if _cache["data"] is not None and (now - _cache["ts"]) < _TTL:
        return _cache["data"]
    with _lock:
        if _cache["data"] is None or (time.monotonic() - _cache["ts"]) >= _TTL:
            _cache["data"] = _load_all()
            _cache["ts"] = time.monotonic()
        return _cache["data"]


def invalidar() -> None:
    """Forzar recarga del master en la próxima lectura (tras alta/baja de instrumento)."""
    _cache["data"] = None
    _cache["ts"] = 0.0


# Las públicas devuelven SHALLOW-COPIES de los docs (el cache comparte objetos; copiar
# preserva el contrato "dicts frescos" del path Mongo a costo ~nulo vs re-query/deserializar).
def cargar_todos() -> list[dict]:
    """Todos los docs del master (cacheado 300s, in-process). Equivale a find({})."""
    return [dict(d) for d in _master()]


def por_curva(curva: str) -> list[dict]:
    """Docs de una curva (tasa_fija | cer | soberanos | on_<sector> | ...)."""
    return [dict(d) for d in _master() if d.get("curva") == curva]


def por_curva_like(patron: str) -> list[dict]:
    """find({'curva': {'$regex': '^on'}}) → por_curva_like('on%'). Prefijo == patron sin '%'."""
    pre = patron.rstrip("%")
    return [dict(d) for d in _master() if (d.get("curva") or "").startswith(pre)]


def por_curva_not_like(patron: str) -> list[dict]:
    """find({'curva': {'$not': {'$regex': '^on'}}}) → por_curva_not_like('on%').
    Incluye los docs con curva NULL (igual que el $not de Mongo)."""
    pre = patron.rstrip("%")
    return [dict(d) for d in _master() if not (d.get("curva") or "").startswith(pre)]


def find_one(ticker_corto: str) -> dict | None:
    """Doc por ticker_corto (PK), o None. Equivale a find_one({'ticker_corto': X})."""
    d = next((x for x in _master() if x.get("ticker_corto") == ticker_corto), None)
    return dict(d) if d is not None else None


def agrupado_por_curva() -> dict[str, list[dict]]:
    """Docs agrupados por `curva` (ignora sin curva). forwards/breakevens."""
    grupos: dict[str, list[dict]] = {}
    for d in cargar_todos():
        c = d.get("curva")
        if c:
            grupos.setdefault(c, []).append(d)
    return grupos


def indexado_por_ticker() -> dict[str, dict]:
    """Dict ticker → doc (ignora sin ticker). curvas enriquece TimeSales."""
    return {d["ticker"]: d for d in cargar_todos() if d.get("ticker")}
