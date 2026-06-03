"""Autenticación de identidad — validación JWT de Cloudflare Access.

Reemplaza la lectura naïve del header `cf-access-authenticated-user-email`
(spoofable si alguien bypassa CF Access o pega con bearer válido localmente)
por validación criptográfica del JWT que emite Cloudflare Zero Trust.

Fail-open controlado: si CF_ACCESS_TEAM o CF_ACCESS_AUD no están
configurados en `.env`, el módulo loggea warning y cae al header sin
validar. Esto preserva el comportamiento actual en dev.

Uso:
    @router.post("")
    def chat(email: str = Depends(get_user_email), ...):
        # email viene del JWT validado, o del header si JWT no disponible
        ...

    # Gate de admin (MANAGER_EMAILS)
    app.include_router(manager.router, dependencies=[Depends(require_manager)])
"""
from __future__ import annotations

import logging
from functools import lru_cache

from fastapi import Depends, Header, HTTPException

from config import CF_ACCESS_AUD, CF_ACCESS_TEAM, CF_TRUSTED_SERVICE_TOKENS

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _jwks_client():
    """Cliente JWKS de Cloudflare Access (cachea las claves públicas).

    Se instancia solo si CF_ACCESS_TEAM está configurado. Lazy import de
    PyJWT para no forzar la dep en dev si no se usa.
    """
    if not CF_ACCESS_TEAM:
        return None
    try:
        import jwt  # PyJWT
    except ImportError:
        logger.warning("PyJWT no instalado — JWT de CF Access no se valida")
        return None
    url = f"https://{CF_ACCESS_TEAM}.cloudflareaccess.com/cdn-cgi/access/certs"
    return jwt.PyJWKClient(url)


def _verify_cf_jwt(cf_jwt: str) -> dict | None:
    """Valida el JWT firmado por Cloudflare Access y devuelve los claims.

    Devuelve None si no se pudo validar (firma inválida, exp, audience
    incorrecta, claves no disponibles). El caller extrae lo que necesita
    del dict de claims.
    """
    if not (CF_ACCESS_TEAM and CF_ACCESS_AUD):
        return None
    client = _jwks_client()
    if client is None:
        return None
    try:
        import jwt
        signing_key = client.get_signing_key_from_jwt(cf_jwt).key
        claims = jwt.decode(
            cf_jwt,
            signing_key,
            audience=CF_ACCESS_AUD,
            algorithms=["RS256"],
        )
        return claims
    except Exception as e:
        logger.warning("CF JWT inválido: %s", e)
        return None


def get_user_email(
    cf_jwt: str | None = Header(default=None, alias="cf-access-jwt-assertion"),
    cf_email: str | None = Header(default=None, alias="cf-access-authenticated-user-email"),
    forwarded_email: str | None = Header(default=None, alias="x-acaquant-user-email"),
) -> str:
    """Devuelve el email del usuario autenticado.

    Hay DOS tipos de JWT emitidos por Cloudflare Access:

    1. **User JWT** (login OTP directo): trae `email` o `identity.email`.
       El user se autenticó directamente contra CF. Usamos ese email.

    2. **Service token JWT** (acaquant-web → api.acaquant.com SSR): trae
       `common_name` pero NO `email` — es identidad de máquina. CF Access
       ESTRIPA el header `cf-access-authenticated-user-email` que el
       frontend mandó (sólo emite ese header CF mismo, al validar un user
       JWT). El frontend tiene que propagar el email en un header custom
       que CF no controle: `x-acaquant-user-email`. Si llega ahí, ese es
       el user. Sin él, el request es identidad-de-máquina y va por la
       rama de service token.

    Si no hay JWT (o CF_ACCESS_TEAM/AUD no están configurados), cae al
    header directo (modo dev).
    """
    if cf_jwt:
        claims = _verify_cf_jwt(cf_jwt)
        if claims is None:
            # JWT presente pero con firma/audience inválidas → sospechoso
            if CF_ACCESS_TEAM and CF_ACCESS_AUD:
                raise HTTPException(status_code=401, detail="CF JWT inválido")
        else:
            # Rama 1: user JWT con email directo
            email_claim = (
                claims.get("email")
                or (claims.get("identity") or {}).get("email")
            )
            if email_claim:
                return str(email_claim).lower().strip()

            # Rama 2: service token JWT (sin email, con common_name).
            # Ej: acaquant-web (Vercel SSR) → api.acaquant.com.
            common_name = claims.get("common_name")
            if common_name:
                cn = str(common_name).lower().strip()
                # 2a: el frontend propaga el email del user en
                # x-acaquant-user-email (CF NO estripa este header — no es
                # CF-controlled). Es el camino oficial. cf_email queda como
                # fallback histórico por si en algún env CF lo pasa.
                user_email = forwarded_email or cf_email
                if user_email:
                    return str(user_email).lower().strip()
                # 2b: service token sin email del user — request de máquina
                # legítimo (cron, smoke). Devolvemos sintético "service:<cn>"
                # que get_user_role() trata como DEFAULT_ROLE (sales). Si
                # alguna integración de máquina necesita más permisos, hay
                # que registrarla explícitamente en Manager.Users.
                if cn in CF_TRUSTED_SERVICE_TOKENS:
                    return f"service:{cn}"
                # 2c: service token desconocido — 401 para que notemos.
                # Log el common_name en full para que puedas agregarlo a
                # CF_TRUSTED_SERVICE_TOKENS si es legítimo.
                logger.warning(
                    "service token no autorizado: common_name=%r "
                    "(agregalo a CF_TRUSTED_SERVICE_TOKENS si es legítimo)", cn,
                )

            # Rama 3: JWT válido pero no user ni service conocido.
            # Rama 3: JWT válido pero sin email claim y sin service token
            # conocido. NO se confía en forwarded_email/cf_email acá: la
            # identidad del JWT no se verificó como user ni como service
            # registrado, así que el header de email es spoofeable — un
            # JWT válido cualquiera del AUD + `x-acaquant-user-email`
            # forjado escalaría a la identidad que quiera. Fail-closed → anon.
            logger.warning(
                "JWT válido sin email claim ni service conocido (cn=%r) — anon",
                common_name,
            )
            return "anon"

    # Sin JWT: modo dev o request sin CF Access activo
    if forwarded_email:
        return forwarded_email.lower().strip()
    if cf_email:
        return cf_email.lower().strip()
    return "anon"


def require_manager(email: str = Depends(get_user_email)) -> str:
    """Exige role con acceso al módulo `manager`.

    Alias histórico que ahora delega a la matriz RBAC. El resultado es
    idéntico para admins: MANAGER_EMAILS queda como fallback (`core/roles.py`
    lo cacha en `get_user_role`) y los emails ya seedeados en
    `Manager.Users` con `role=admin` pasan naturalmente.

    Se mantiene por compat con callers externos; código nuevo usar
    `require_module("manager")` directamente.
    """
    from core.roles import has_access

    if has_access(email, "manager"):
        return email
    logger.warning("require_manager: rechazado email=%r", email)
    raise HTTPException(status_code=403, detail="acceso al módulo manager no autorizado")


# ─────────────────────────────────────────────────────────────
# RBAC por módulo — enforcement server-side
# ─────────────────────────────────────────────────────────────
# Mapeo de prefijo de path → módulo. Se usa para inferir qué módulo
# cubre un endpoint dado. Solo listamos los módulos que RESTRINGEN:
# home / renta-fija / derivados / estrategia los tienen todos los roles,
# así que no hace falta gatearlos (sale más barato un `_PUBLIC` sin
# require_module).
ENDPOINT_MODULE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/api/manager",     "manager"),
    # Sub-módulos de manager: el match longest-prefix-first los hace ganar
    # sobre el umbrella `/api/manager` → `manager`. Los bulks son más
    # específicos que `/api/manager/clientes` y por eso van listados (también
    # ganan por longitud).
    ("/api/manager/comercial",         "manager_comercial"),
    ("/api/manager/clientes",          "manager_clientes"),
    ("/api/manager/clientes/bulk",     "manager_clientes_bulk"),
    ("/api/manager/clientes/bulk-fondeo", "manager_clientes_bulk"),
    ("/api/portfolio",   "portfolios"),
    ("/api/titulos",     "portfolios"),
    # /api/ordenes + /api/operativa + /api/operar + /api/risk → módulo `operar` (acción)
    ("/api/ordenes",     "operar"),
    ("/api/operativa",   "operar"),
    ("/api/operar",      "operar"),
    ("/api/risk",        "operar"),
    # /api/operaciones + /api/cuentas → módulo `operaciones` (mesa, flujo, contrapartes)
    ("/api/operaciones", "operaciones"),
    ("/api/cuentas",     "operaciones"),
    # /api/mm → módulo `mm` (MM Workstation: order book + timesales — en reconstrucción)
    ("/api/mm",          "mm"),
)


def get_module_for_path(path: str) -> str | None:
    """Devuelve el módulo que cubre un path, o None si el path es público.

    Match por prefix más largo primero. Si ningún prefix matchea, el endpoint
    se considera `home` (o sea, accesible por todos los roles) y devuelve
    None para señalizar al caller que no hace falta check de módulo.
    """
    if not path:
        return None
    best_prefix = ""
    best_module: str | None = None
    for prefix, module in ENDPOINT_MODULE_PREFIXES:
        if path.startswith(prefix) and len(prefix) > len(best_prefix):
            best_prefix = prefix
            best_module = module
    return best_module


def require_module(module: str):
    """Dependency factory: exige que el user tenga acceso al módulo.

    Uso:
        app.include_router(
            manager.router,
            dependencies=[Depends(verify_api_key), Depends(require_module("manager"))],
        )

    Cloudflare Access ya validó que el email puede entrar al sitio; acá
    solo chequeamos que el role del email tenga el módulo en la matriz.

    En dev (sin MANAGER_EMAILS ni Mongo) deja pasar todo: get_user_role
    cae a DEFAULT_ROLE que tiene los módulos públicos. Módulos
    restringidos (manager/portfolios/etc) tiran 403.
    """
    # Import lazy para evitar ciclos core ↔ api en el arranque
    from core.roles import has_access

    def _dep(email: str = Depends(get_user_email)) -> str:
        # Dev: sin whitelist ni DB de roles, el código de abajo igual
        # funciona porque get_user_role cae a DEFAULT_ROLE.
        if has_access(email, module):
            return email
        logger.warning(
            "require_module(%s): rechazado email=%r", module, email,
        )
        raise HTTPException(status_code=403, detail=f"acceso al módulo {module} no autorizado")

    # Para que FastAPI diferencie cada instancia en la cache de deps
    _dep.__name__ = f"require_module_{module.replace('-', '_')}"
    return _dep


def require_any_module(modules: tuple[str, ...]):
    """Dependency factory: pasa si el user tiene CUALQUIERA de los módulos.

    Útil para sub-routers donde el umbrella (`manager`) Y el sub-módulo
    (`manager_comercial`) ambos deben dar acceso. Ej: admin tiene `manager`
    en Mongo y entra a todo `/api/manager/*`; `asistente_comercial` tiene
    solo `manager_comercial` y entra a `/api/manager/comercial/*`.

    NO requiere migración de la matriz Mongo existente — los roles que
    ya tenían el umbrella siguen funcionando sin tocar nada.
    """
    from core.roles import has_access

    def _dep(email: str = Depends(get_user_email)) -> str:
        for m in modules:
            if has_access(email, m):
                return email
        logger.warning(
            "require_any_module(%s): rechazado email=%r", list(modules), email,
        )
        raise HTTPException(
            status_code=403,
            detail=f"acceso requerido a alguno de: {', '.join(modules)}",
        )

    _dep.__name__ = "require_any_module_" + "_".join(m.replace("-", "_") for m in modules)
    return _dep
