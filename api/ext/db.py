"""api/ext/db.py — las cuatro tablas del schema `ext` (quién entra y qué ve).

⚠️ ACÁ NO HAY NINGÚN DATO DE OPERACIONES. Este módulo sólo sabe de clientes,
keys, cuentas autorizadas y auditoría. Los boletos los lee `api/ext/lectura.py`
en vivo de `operaciones.operaciones`.

El schema se referencia SIEMPRE calificado (`ext.*`) — igual que `mcp`, no entra
al search_path de `core/postgres`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime

from psycopg.rows import dict_row

from config import EXT_JWT_SECRET
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Prefijo de las keys emitidas. `avk` = ACA Valores Key.
_KEY_PREFIJO = "avk_live_"
# Largo del pedazo público del prefijo (lo que se ve en logs/pantallas).
_PREFIJO_LEN = 4


# ── hashing ──────────────────────────────────────────────────────────────────
def hash_key(key: str) -> str:
    """sha256(pepper || key) en hex.

    El pepper es `EXT_JWT_SECRET`, que vive en el `.env` del Droplet y NO en la
    base: si alguien se lleva un dump de Postgres, no puede probar keys offline
    sin además tener el archivo de configuración. Por eso rotar esa variable
    invalida también las keys, no sólo los tokens (está avisado en config.py).

    sha256 y no argon2 a propósito: esto NO es una contraseña de humano (que es
    corta, adivinable y se reusa en cinco sitios). Es un secreto aleatorio de 256
    bits generado por nosotros — no hay diccionario que lo alcance, así que el
    costo de un KDF lento sólo compraría latencia en cada request.
    """
    return hashlib.sha256(f"{EXT_JWT_SECRET}{key}".encode()).hexdigest()


def generar_key() -> tuple[str, str]:
    """Devuelve (key_completa, prefijo). La key completa se muestra UNA vez."""
    cuerpo = secrets.token_urlsafe(32)
    prefijo = f"{_KEY_PREFIJO}{secrets.token_hex(_PREFIJO_LEN // 2 + 1)[:_PREFIJO_LEN]}"
    return f"{prefijo}_{cuerpo}", prefijo


def prefijo_de(key: str) -> str | None:
    """Saca el prefijo público de una key entera, sin validarla.

    Sirve para buscar la fila candidata en la base. La comparación real es
    después y va por hash con `secrets.compare_digest`.
    """
    if not key or not key.startswith(_KEY_PREFIJO):
        return None
    partes = key.split("_")
    if len(partes) < 4:
        return None
    return "_".join(partes[:3])


# ── lecturas ─────────────────────────────────────────────────────────────────
def buscar_key(prefijo: str) -> dict | None:
    """Fila de `ext.api_keys` + datos del cliente, o None si no existe.

    Trae la key aunque esté revocada o vencida: el que decide es el caller
    (`api/ext/auth.py`), en un solo lugar y con un solo criterio.
    """
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT k.prefijo, k.key_hash, k.cliente_id, k.revocada_at, k.expira_at,
                   c.nombre, c.activo, c.ip_allowlist, c.ver_aranceles
              FROM ext.api_keys k
              JOIN ext.clientes c ON c.id = k.cliente_id
             WHERE k.prefijo = %s
            """,
            (prefijo,),
        )
        return cur.fetchone()


def cliente_por_id(cliente_id: str) -> dict | None:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, nombre, activo, ip_allowlist, ver_aranceles "
            "FROM ext.clientes WHERE id = %s",
            (cliente_id,),
        )
        return cur.fetchone()


def key_vigente(prefijo: str) -> bool:
    """¿La key que emitió un token sigue viva?

    Se llama en CADA request con token. Es lo que hace que revocar una key mate
    al instante los tokens que ya emitió, sin necesidad de una tabla de tokens:
    el token dice con qué key nació (`kid`) y esa key se vuelve a mirar siempre.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM ext.api_keys k JOIN ext.clientes c ON c.id = k.cliente_id
             WHERE k.prefijo = %s AND k.revocada_at IS NULL AND c.activo
               AND (k.expira_at IS NULL OR k.expira_at > now())
            """,
            (prefijo,),
        )
        return cur.fetchone() is not None


def cuentas_autorizadas(cliente_id: str) -> tuple[str, ...]:
    """Las cuentas que este cliente puede ver. Tuple ordenado (hashable → cacheable).

    ⚠️ Se resuelve EN CADA REQUEST y NO viaja dentro del token. Un token que
    llevara las cuentas adentro haría que sacar una cuenta tardara lo que dure
    el token — y "revoqué pero sigue entrando" es exactamente la clase de falla
    que no avisa.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id_cuenta FROM ext.cuentas_autorizadas "
            "WHERE cliente_id = %s ORDER BY id_cuenta",
            (cliente_id,),
        )
        return tuple(r[0] for r in cur.fetchall())


# ── escrituras ───────────────────────────────────────────────────────────────
def marcar_uso(prefijo: str) -> None:
    """Sella `ultimo_uso`. Best-effort: nunca puede tumbar un request."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE ext.api_keys SET ultimo_uso = now() WHERE prefijo = %s",
                        (prefijo,))
    except Exception:
        logger.warning("ext: no se pudo sellar ultimo_uso de %s", prefijo, exc_info=True)


def log_request(
    *, cliente_id: str | None, prefijo: str | None, ip: str | None, metodo: str,
    path: str, filtros: dict | None, filas: int | None, status: int, ms: int,
) -> None:
    """Escribe la fila de auditoría. Best-effort, igual que arriba.

    Sin esto no hay forense: "¿quién bajó los boletos de agosto y cuándo?" se
    contesta con esta tabla o no se contesta.
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ext.requests_log "
                "(cliente_id, prefijo, ip, metodo, path, filtros, filas, status, ms) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (cliente_id, prefijo, ip, metodo, path,
                 json.dumps(filtros or {}, default=str), filas, status, ms),
            )
    except Exception:
        logger.warning("ext: no se pudo auditar %s %s", metodo, path, exc_info=True)


def ahora() -> datetime:
    return datetime.now(UTC)
