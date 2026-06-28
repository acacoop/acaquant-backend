"""Router News: headlines agregados de RSS (News.Headlines) + reader mode."""
import ipaddress
import logging
import socket
from datetime import UTC, datetime
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request

from api.ratelimit import limiter
from api.services import news_sql

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/news", tags=["News"])


def _is_safe_external_url(url: str) -> tuple[bool, str]:
    """Valida URL antes de hacer fetch externo (anti-SSRF).

    Bloquea: scheme no-http(s), puertos no-estándar, IPs privadas/loopback/
    link-local/reserved (incluye metadata services cloud: 169.254.169.254,
    etc.).

    Devuelve (ok, reason). Si ok es False, reason explica por qué.
    """
    try:
        p = urlparse(url)
    except Exception as e:
        return False, f"URL inválida: {e}"
    if p.scheme not in ("http", "https"):
        return False, f"scheme no permitido: {p.scheme}"
    if not p.hostname:
        return False, "hostname vacío"
    # Solo puertos estándar web
    if p.port and p.port not in (80, 443):
        return False, f"puerto no permitido: {p.port}"
    # Resolver y chequear IP. Si el hostname resuelve a IP privada, bloquear.
    # Ojo: DNS rebinding sigue posible pero cubre el 95% de casos reales.
    try:
        resolved = socket.gethostbyname(p.hostname)
    except socket.gaierror as e:
        return False, f"DNS no resuelve: {e}"
    try:
        ip = ipaddress.ip_address(resolved)
    except ValueError:
        return False, f"IP inválida: {resolved}"
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return False, f"IP bloqueada por policy (privada/loopback/link-local): {resolved}"
    return True, ""


def _fetch_html_safe(url: str, max_redirects: int = 4) -> tuple[str | None, str]:
    """GET siguiendo redirects A MANO, re-validando CADA hop con
    `_is_safe_external_url`. El guard inicial no alcanza: un `302 →
    http://169.254.169.254/...` saltearía la validación si dejáramos que el
    cliente HTTP siga el redirect solo (SSRF). Devuelve (html | None, reason).
    """
    import urllib.error
    import urllib.request
    from urllib.parse import urljoin

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        # Devolver None → urllib NO sigue el redirect y levanta HTTPError,
        # que capturamos abajo para re-validar el destino a mano.
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    current = url
    for _ in range(max_redirects + 1):
        safe, reason = _is_safe_external_url(current)
        if not safe:
            return None, reason
        req = urllib.request.Request(
            current, headers={"User-Agent": "Mozilla/5.0 (compatible; acaquant-reader)"}
        )
        try:
            resp = opener.open(req, timeout=10)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get("Location")
                if not loc:
                    return None, "redirect sin Location"
                current = urljoin(current, loc)
                continue
            return None, f"HTTP {e.code}"
        except Exception as e:  # cualquier error de red = no leer
            return None, f"fetch error: {e}"
        raw = resp.read(8_000_000)  # cota 8MB
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, "replace"), ""
    return None, "demasiados redirects"

# Cache simple en memoria para articles extraídos (1 hora TTL).
_ARTICLE_CACHE: dict[str, tuple[float, dict]] = {}
_ARTICLE_CACHE_TTL = 3600.0
# Cota dura del cache: sin esto cada URL distinta deja una entrada
# permanente (el TTL decide frescura, no evicción) → DoS de memoria.
_ARTICLE_CACHE_MAX = 200


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # YYYY-MM-DD o ISO con tz
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        return datetime.fromisoformat(s).replace(tzinfo=UTC)
    except ValueError:
        return None


@router.get("")
def list_headlines(
    desde:     str | None = Query(None, description="Fecha desde ISO (YYYY-MM-DD o con T...)"),
    hasta:     str | None = Query(None, description="Fecha hasta ISO"),
    fuente:    str | None = Query(None, description="Filtrar por fuente"),
    categoria: str | None = Query(None, description="Filtrar por categoria"),
    keyword:   str | None = Query(None, description="Substring en titulo (case-insensitive)"),
    limit:     int = Query(100, ge=1, le=500),
    skip:      int = Query(0,   ge=0, le=5000),
):
    """Lista headlines ordenados desc por fecha_publicacion.

    Soporta filtros simples + paginación. El panel de la home polea este
    endpoint cada 60s con `limit` chico; la vista de noticias (si se arma)
    puede paginar con `skip`.
    """
    # SQL-native (decomiso Mongo): los writers news_finnhub/news_ingesta escriben
    # home.news_headlines directo; News.Headlines (Mongo) quedó stale.
    return news_sql.list_headlines(
        desde=_parse_date(desde), hasta=_parse_date(hasta), fuente=fuente,
        categoria=categoria, keyword=keyword, limit=limit, skip=skip)


@router.get("/article")
@limiter.limit("20/minute;200/hour")
def article(
    request: Request,
    url: str = Query(..., description="URL original de la nota a leer inline."),
):
    """Fetcha la URL y extrae el artículo limpio con trafilatura (reader mode).

    Cachea 1h en memoria para no hammerear la fuente en recargas.
    Devuelve: {ok, title, author, date, text, hostname, excerpt}.
    """
    import time as _time
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL inválida")

    # SSRF guard: rechazar URLs que resuelvan a IPs privadas / loopback /
    # link-local / metadata services cloud (169.254.169.254). Si alguien
    # autenticado logra pedir al reader mode un URL interno, podría
    # leakear tokens del Droplet o pegar a servicios internos.
    safe, reason = _is_safe_external_url(url)
    if not safe:
        logger.warning("SSRF block: url=%s reason=%s", url[:100], reason)
        raise HTTPException(status_code=400, detail=f"URL no permitida: {reason}")

    now = _time.time()
    cached = _ARTICLE_CACHE.get(url)
    if cached and (now - cached[0]) < _ARTICLE_CACHE_TTL:
        return cached[1]

    try:
        import trafilatura
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail="trafilatura no instalado; correr pip install trafilatura",
        ) from e

    try:
        # Fetch propio con re-validación de cada redirect (anti-SSRF). NO usamos
        # trafilatura.fetch_url porque sigue redirects sin re-chequear el destino.
        downloaded, fetch_reason = _fetch_html_safe(url)
        if not downloaded:
            if "bloqueada" in fetch_reason or "IP inválida" in fetch_reason:
                logger.warning("SSRF block (redirect/fetch): url=%s reason=%s", url[:100], fetch_reason)
            return {"ok": False, "error": f"no pude descargar la URL ({fetch_reason})"}
        data = trafilatura.extract(
            downloaded,
            output_format="json",
            include_comments=False,
            include_links=False,
            include_tables=False,
            with_metadata=True,
            deduplicate=True,
            favor_precision=True,
        )
        if not data:
            return {"ok": False, "error": "no pude extraer texto (posible paywall)"}

        import json as _json
        parsed = _json.loads(data)
        out = {
            "ok":       True,
            "title":    parsed.get("title") or "",
            "author":   parsed.get("author") or "",
            "date":     parsed.get("date") or "",
            "hostname": parsed.get("hostname") or "",
            "excerpt":  parsed.get("excerpt") or "",
            "text":     parsed.get("text") or "",
            "url":      url,
        }
    except Exception as e:
        logger.exception("extracción falló para %s", url)
        out = {"ok": False, "error": f"extracción falló: {e}"}

    # Cota: purgar expirados y, si sigue lleno, el más viejo. Mantiene
    # _ARTICLE_CACHE acotado (ver _ARTICLE_CACHE_MAX).
    if len(_ARTICLE_CACHE) >= _ARTICLE_CACHE_MAX:
        for k in [k for k, (ts, _) in _ARTICLE_CACHE.items()
                  if now - ts >= _ARTICLE_CACHE_TTL]:
            del _ARTICLE_CACHE[k]
        if len(_ARTICLE_CACHE) >= _ARTICLE_CACHE_MAX:
            del _ARTICLE_CACHE[min(_ARTICLE_CACHE, key=lambda k: _ARTICLE_CACHE[k][0])]
    _ARTICLE_CACHE[url] = (now, out)
    return out


@router.get("/stats")
def stats(horas: int = Query(24, ge=1, le=720)):
    """Stats agregados por fuente en las últimas N horas (útil para debugging)."""
    # SQL-native (decomiso Mongo): home.news_headlines.
    return news_sql.stats(horas)
