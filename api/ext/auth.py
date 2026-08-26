"""api/ext/auth.py — autenticación y SCOPE de la API externa.

Cuatro capas, y ninguna alcanza sola:

  0. **Cloudflare Access (Service Auth)** — el borde. Un request sin service
     token válido no llega al Droplet. No se implementa acá: se configura en
     Zero Trust. Este módulo asume que ya pasó.
  1. **API key → token corto** (`emitir_token`). La key dura meses y viaja UNA
     vez por día; el token dura 30 min y es el que viaja en cada llamada. Cuanto
     más corta la vida de una credencial, menos importa dónde queda registrada.
  2. **Revocación instantánea** — el token dice con qué key nació (`kid`) y esa
     key se re-valida en CADA request. Revocar la key mata sus tokens ya
     emitidos, sin tabla de tokens ni esperar el vencimiento.
  3. **Scope de cuentas FAIL-CLOSED** (`scope_ext`) — sin cuentas autorizadas,
     403. Es la inversión deliberada de `core/grupos.py::cuentas_visibles`, que
     ante un error de base devuelve `None` = ve TODO. Adentro de la mesa esa
     decisión es razonable; hacia afuera sería una fuga silenciosa.
"""
from __future__ import annotations

import ipaddress
import logging
import secrets
from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException, Request

from api.ext import db
from config import EXT_ISSUER, EXT_JWT_SECRET, EXT_TOKEN_TTL_SECONDS

logger = logging.getLogger(__name__)

_ALG = "HS256"


@dataclass(frozen=True)
class Cliente:
    """El consumidor externo ya autenticado, con su scope ya resuelto."""
    id: str
    nombre: str
    prefijo: str                  # qué key usó (para auditar, no es la key)
    cuentas: tuple[str, ...]      # SU scope — nunca vacío (fail-closed)
    ver_aranceles: bool


# ── errores con shape estable ────────────────────────────────────────────────
def _error(status: int, code: str, message: str) -> HTTPException:
    """Todos los errores de `/ext` tienen la misma forma.

    Un consumidor externo programa contra estos códigos: si cada endpoint
    devolviera su propio shape, cualquier manejo de errores del otro lado
    tendría que adivinar. `code` es estable; `message` es para el humano.
    """
    return HTTPException(status_code=status, detail={"code": code, "message": message})


# ── capa 1: API key → token ──────────────────────────────────────────────────
def emitir_token(api_key: str, ip: str | None) -> tuple[str, int, dict]:
    """Valida la API key y devuelve (token, ttl_segundos, fila_de_la_key).

    Todos los rechazos son el MISMO error (`credencial_invalida`), a propósito:
    distinguir "esa key no existe" de "esa key está revocada" o "ese cliente está
    desactivado" le regala a quien prueba keys un oráculo para saber cuáles
    existieron alguna vez.
    """
    if not EXT_JWT_SECRET:
        raise _error(503, "no_configurado", "la API externa no está habilitada")

    prefijo = db.prefijo_de(api_key or "")
    if not prefijo:
        raise _error(401, "credencial_invalida", "API key inválida")

    fila = db.buscar_key(prefijo)
    if fila is None:
        raise _error(401, "credencial_invalida", "API key inválida")

    # compare_digest sobre el HASH y no sobre la key: comparar strings con `==`
    # corta en el primer byte distinto, y esa diferencia de tiempo es medible.
    esperado = fila["key_hash"].encode()
    recibido = db.hash_key(api_key).encode()
    if not secrets.compare_digest(recibido, esperado):
        raise _error(401, "credencial_invalida", "API key inválida")

    ahora = db.ahora()
    if fila["revocada_at"] is not None or not fila["activo"]:
        raise _error(401, "credencial_invalida", "API key inválida")
    if fila["expira_at"] is not None and fila["expira_at"] <= ahora:
        raise _error(401, "credencial_invalida", "API key inválida")

    _verificar_ip(fila.get("ip_allowlist") or [], ip)

    payload = {
        "iss": EXT_ISSUER,
        "sub": fila["cliente_id"],
        "kid": prefijo,                       # con qué key nació → permite revocar
        "iat": int(ahora.timestamp()),
        "exp": int(ahora.timestamp()) + EXT_TOKEN_TTL_SECONDS,
    }
    token = jwt.encode(payload, EXT_JWT_SECRET, algorithm=_ALG)
    db.marcar_uso(prefijo)
    return token, EXT_TOKEN_TTL_SECONDS, fila


def _verificar_ip(allowlist: list[str], ip: str | None) -> None:
    """Allowlist de IPs por cliente. Vacía = sin restricción en la app.

    Es una segunda red debajo de la regla de Cloudflare, no un reemplazo: la del
    borde ahorra el viaje al Droplet, ésta sobrevive a que alguien toque el
    dashboard de CF sin avisar. Acepta IPs sueltas y rangos CIDR.
    """
    if not allowlist:
        return
    if not ip:
        raise _error(403, "ip_no_autorizada", "origen no autorizado")
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        raise _error(403, "ip_no_autorizada", "origen no autorizado") from None
    for entrada in allowlist:
        try:
            if addr in ipaddress.ip_network(entrada.strip(), strict=False):
                return
        except ValueError:
            logger.warning("ext: entrada inválida en ip_allowlist: %r", entrada)
    raise _error(403, "ip_no_autorizada", "origen no autorizado")


# ── capas 2 y 3: token → cliente con scope ───────────────────────────────────
def cliente_actual(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Cliente:
    """Dependency: valida el token y resuelve el scope. **Fail-closed en todo.**

    Es la ÚNICA puerta de entrada a los datos: ningún endpoint de `/ext` lee la
    base sin haber pasado por acá, y el `Cliente` que devuelve ya trae las
    cuentas resueltas. Un endpoint nuevo que se olvide de pedir esta dependency
    no compila un scope por su cuenta — no tiene de dónde sacarlo.
    """
    if not EXT_JWT_SECRET:
        raise _error(503, "no_configurado", "la API externa no está habilitada")

    if not authorization or not authorization.lower().startswith("bearer "):
        raise _error(401, "token_faltante", "falta el header Authorization: Bearer <token>")
    token = authorization[7:].strip()

    try:
        claims = jwt.decode(token, EXT_JWT_SECRET, algorithms=[_ALG], issuer=EXT_ISSUER)
    except jwt.ExpiredSignatureError:
        raise _error(401, "token_expirado", "el token venció; pedí uno nuevo en /v1/auth/token") from None
    except jwt.InvalidTokenError:
        raise _error(401, "token_invalido", "token inválido") from None

    cliente_id = str(claims.get("sub") or "")
    prefijo = str(claims.get("kid") or "")
    if not cliente_id or not prefijo:
        raise _error(401, "token_invalido", "token inválido")

    # Capa 2: la key que emitió este token, ¿sigue viva? Acá muere un token
    # cuya key se revocó hace un minuto.
    if not db.key_vigente(prefijo):
        raise _error(401, "credencial_revocada", "la credencial fue revocada")

    cli = db.cliente_por_id(cliente_id)
    if cli is None or not cli["activo"]:
        raise _error(401, "credencial_revocada", "la credencial fue revocada")

    _verificar_ip(cli.get("ip_allowlist") or [], ip_del_request(request))

    # Capa 3: el scope. Vacío → 403. NUNCA "ve todo".
    cuentas = db.cuentas_autorizadas(cliente_id)
    if not cuentas:
        logger.warning("ext: cliente %s sin cuentas autorizadas → 403", cliente_id)
        raise _error(403, "sin_cuentas", "el cliente no tiene cuentas autorizadas")

    return Cliente(
        id=cliente_id,
        nombre=str(cli["nombre"]),
        prefijo=prefijo,
        cuentas=cuentas,
        ver_aranceles=bool(cli["ver_aranceles"]),
    )


def ip_del_request(request: Request) -> str | None:
    """IP del cliente. Detrás del túnel de Cloudflare la real viene en `cf-connecting-ip`."""
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


def resolver_cuentas(cli: Cliente, pedidas: str | None) -> tuple[str, ...]:
    """Intersecta lo que el cliente PIDE con lo que TIENE. Es el corazón del gate.

    Si pide una cuenta que no es suya → **403, no una lista vacía**. Un 200 con
    cero filas le haría creer que esa cuenta existe y no operó; el 403 dice la
    verdad: esa cuenta no es tuya. (No filtra nada que no sepa: sus propias
    cuentas ya las conoce, y sobre las ajenas la respuesta es la misma exista o no.)
    """
    if not pedidas:
        return cli.cuentas
    pedido = tuple(x.strip() for x in str(pedidas).split(",") if x.strip())
    ajenas = [c for c in pedido if c not in cli.cuentas]
    if ajenas:
        raise _error(403, "cuenta_no_autorizada",
                     f"cuenta(s) fuera de tu alcance: {', '.join(sorted(ajenas))}")
    return pedido


# Alias explícito para los endpoints: `cli: Cliente = Depends(REQUIERE_CLIENTE)`.
REQUIERE_CLIENTE = Depends(cliente_actual)
