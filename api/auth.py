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
import threading
from functools import lru_cache

from fastapi import Depends, Header, HTTPException, Request

from config import CF_ACCESS_AUD, CF_ACCESS_TEAM, CF_TRUSTED_SERVICE_TOKENS

logger = logging.getLogger(__name__)


# Evidencia POSITIVA de que la allowlist de service tokens está bien puesta.
# El camino feliz (token en la allowlist) es silencioso por diseño: sólo se
# loguean los rechazos. Eso deja un diagnóstico ambiguo — "no veo service
# tokens en el log" puede significar "anda todo bien" o "no hay tráfico de
# máquina", que son estados MUY distintos cuando la allowlist recién se activa.
# Con esto queda una marca por common_name aceptado, una sola vez por proceso
# (el unit recicla cada 8h, así que se re-emite sola y no envejece).
# Lo lee `scripts.diag_auth_postura`.
_service_tokens_ok: set[str] = set()
_service_tokens_lock = threading.Lock()


def _log_service_token_aceptado(cn: str) -> None:
    if cn in _service_tokens_ok:
        return
    with _service_tokens_lock:
        if cn in _service_tokens_ok:
            return
        _service_tokens_ok.add(cn)
    logger.info("service token ACEPTADO por allowlist (cn=%r)", cn)


def is_guest_portal(request: Request) -> bool:
    """True si el request entra por el portal de invitados (www.acaquant.com).

    El frontend de www agrega el header `x-acaquant-portal: guest` (server-side,
    según el hostname) en CADA llamada al backend. El browser del cliente NO
    controla ese header (lo pone el proxy de Next, no el JS del cliente), y un
    invitado no puede llegar a `trading` (CF Access lo frena por su email) → queda
    siempre tagueado guest y no puede destagearse.

    Sirve para forzar al invitado a SOLO los módulos de mercado: en los routers
    gated (`require_module`/`require_any_module`) cualquier módulo que NO esté en
    `INVITADO_MODULES` devuelve 403, sin importar el rol del email. Default-deny.

    Capa estricta adicional (service token dedicado de www, no spoofeable ni con
    el API key) se suma encima en un segundo paso.
    """
    return request.headers.get("x-acaquant-portal", "").strip().lower() == "guest"


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
                # ORDEN CRÍTICO (fix de seguridad): la allowlist se chequea
                # ANTES de creerle al email forwardeado. Antes era al revés:
                # cualquier service token válido para el AUD —esté o no en la
                # allowlist— podía mandar `x-acaquant-user-email: <admin>` y
                # quedarse con esa identidad. Es la MISMA escalada que la rama
                # 3 de abajo ya rechaza explícitamente; acá se colaba un bloque
                # más arriba. Afecta a todo token de máquina que no sea el SSR
                # de trading (el del portal www, el de la PC de ingesta, crons).
                #
                # Fail-closed SOLO si la allowlist está configurada: con
                # CF_TRUSTED_SERVICE_TOKENS vacío (default de config.py) no
                # podemos distinguir el SSR legítimo de un token cualquiera, y
                # cerrar ahí dejaría a TODA la mesa afuera (cada request del
                # frontend caería en anon). En ese caso se preserva el
                # comportamiento previo y se loguea fuerte para que se
                # configure. Chequeo: python -m scripts.diag_auth_postura
                if CF_TRUSTED_SERVICE_TOKENS and cn not in CF_TRUSTED_SERVICE_TOKENS:
                    logger.warning(
                        "service token NO autorizado (cn=%r) intentó afirmar identidad "
                        "email=%r — rechazado (agregalo a CF_TRUSTED_SERVICE_TOKENS si "
                        "es legítimo)", cn, forwarded_email or cf_email,
                    )
                    return "anon"
                if not CF_TRUSTED_SERVICE_TOKENS:
                    logger.warning(
                        "CF_TRUSTED_SERVICE_TOKENS vacío — no se puede validar el service "
                        "token cn=%r; se acepta el email forwardeado SIN allowlist. "
                        "Configurá la env var en el unit de systemd para cerrar esto.", cn,
                    )
                else:
                    _log_service_token_aceptado(cn)
                # 2a: el frontend propaga el email del user en
                # x-acaquant-user-email (CF NO estripa este header — no es
                # CF-controlled). Es el camino oficial. cf_email queda como
                # fallback histórico por si en algún env CF lo pasa.
                user_email = forwarded_email or cf_email
                if user_email:
                    return str(user_email).lower().strip()
                # 2b: service token sin email del user — request de máquina
                # legítimo (cron, smoke). Devolvemos sintético "service:<cn>"
                # que get_user_role() trata como _NO_ACCESS_ROLE (sin módulos).
                # Si alguna integración de máquina necesita permisos, hay que
                # registrarla explícitamente en manager.manager_users.
                if cn in CF_TRUSTED_SERVICE_TOKENS:
                    return f"service:{cn}"
                # 2c: sin allowlist configurada y sin email — identidad de
                # máquina no verificable. Fail-closed → anon.
                logger.warning(
                    "service token sin email y sin allowlist (cn=%r) — anon", cn,
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
    ("/api/manager/clientes",          "manager_clientes"),
    ("/api/manager/clientes/bulk",     "manager_clientes_bulk"),
    ("/api/manager/clientes/bulk-fondeo", "manager_clientes_bulk"),
    ("/api/manager/assets",            "manager_titulos"),
    ("/api/manager/ons",               "manager_titulos"),
    # Títulos → Instrumentos (solo lectura): gate fino para asistente_comercial.
    ("/api/manager/checks/instruments-by-cfi", "manager_instrumentos"),
    ("/api/manager/checks/discovery-pyrofex",  "manager_instrumentos"),
    ("/api/manager/contrapartes",      "manager_contrapartes"),
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
    # /api/mesa-dinero → módulo `operaciones` (Mesa de Dinero, vista NEGOCIO).
    # La ESCRITURA suma allowlist per-usuario (operaciones.mesa_dinero_escritores).
    ("/api/mesa-dinero", "operaciones"),
    ("/api/trading",     "trading"),  # vista TRADING (pivots CEDEAR), admin-only
    # /api/mm → módulo `mm` (MM Workstation: order book + timesales — en reconstrucción)
    ("/api/mm",          "mm"),
    # /api/ia → módulo `ia` (features de IA — QuantAI, docs/QUANTAI.md). El gate
    # queda cableado ANTES de que exista el primer endpoint: cualquier router
    # futuro bajo /api/ia nace default-deny (solo roles con el módulo tildado).
    ("/api/ia",          "ia"),
)
# Nota: el módulo `asistente` (ASISTENTE DE NEGOCIO, QuantAI P7) no tiene
# prefijo propio — vive como vista `negocio` del copiloto bajo /api/ia y el
# gate fino lo aplica el registro del copiloto (derivacion._acceso).


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
    from core.roles import INVITADO_MODULES, has_access

    def _dep(request: Request, email: str = Depends(get_user_email)) -> str:
        # Portal invitado (www): default-deny → solo los módulos de mercado.
        # No mira el rol del email; el invitado se define por venir de www.
        if is_guest_portal(request):
            if module in INVITADO_MODULES:
                return email
            logger.warning("guest portal: módulo %r bloqueado para invitado", module)
            raise HTTPException(status_code=403, detail=f"módulo {module} no disponible para invitado")
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


def require_admin(request: Request, email: str = Depends(get_user_email)) -> str:
    """Exige rol `admin` DIRECTO — gate para vistas que nunca se delegan.

    A diferencia de `require_module()`, NO mira `Manager.RoleMatrix` (la matriz
    editable desde el panel): chequea `get_user_role(email) == "admin"`. Se usa
    para superficies que deben ser admin-only de forma dura y NO asignables a
    otros roles desde `/manager → ROLES Y PERMISOS` (ej. TRADE LAB intradía).

    Ventajas sobre crear un módulo nuevo admin-only:
      - No depende de que alguien edite la matriz viva → no se puede dejar el
        módulo sin asignar (que dejaría afuera hasta al propio admin).
      - No es delegable por error desde el panel.

    Portal invitado (www): se rechaza SIEMPRE — su rol se fuerza a `invitado`,
    jamás admin (default-deny, REGLA #8).
    """
    from core.roles import get_user_role

    if is_guest_portal(request):
        logger.warning("require_admin: bloqueado para portal invitado")
        raise HTTPException(status_code=403, detail="no disponible para invitado")
    if get_user_role(email) == "admin":
        return email
    logger.warning("require_admin: rechazado email=%r", email)
    raise HTTPException(status_code=403, detail="acceso restringido a administradores")


def require_control_comercial(request: Request, email: str = Depends(get_user_email)) -> str:
    """Permiso PER-USUARIO para VER/EDITAR Control Comercial (admin o flag en
    Manager.Users). Default-deny. Portal invitado siempre rechazado (REGLA #8).

    A diferencia de require_module(): NO mira la matriz de roles — el permiso es por
    usuario (columna `control_comercial` en manager_users, tildada en /manager → Usuarios)."""
    from core.roles import user_has_control_comercial

    if is_guest_portal(request):
        logger.warning("require_control_comercial: bloqueado para portal invitado")
        raise HTTPException(status_code=403, detail="no disponible para invitado")
    if user_has_control_comercial(email):
        return email
    logger.warning("require_control_comercial: rechazado email=%r", email)
    raise HTTPException(status_code=403, detail="acceso a Control Comercial no autorizado")


def require_no_invitado(request: Request) -> None:
    """Bloquea al portal invitado (www). Defense-in-depth para endpoints de ESCRITURA
    de la mesa que viven en un módulo de MERCADO (ej. PATCH agro pizarra/cámara): el gate
    de módulo deja entrar al invitado (puede VER agro), pero NUNCA debe ESCRIBIR. REGLA #8
    — no confiar en que el frontend de www oculte el botón."""
    if is_guest_portal(request):
        logger.warning("require_no_invitado: escritura bloqueada para portal invitado")
        raise HTTPException(status_code=403, detail="no disponible para invitado")


def require_any_module(modules: tuple[str, ...]):
    """Dependency factory: pasa si el user tiene CUALQUIERA de los módulos.

    Útil para sub-routers donde el umbrella (`manager`) Y el sub-módulo
    (`manager_comercial`) ambos deben dar acceso. Ej: admin tiene `manager`
    en Mongo y entra a todo `/api/manager/*`; `asistente_comercial` tiene
    solo `manager_comercial` y entra a `/api/manager/comercial/*`.

    NO requiere migración de la matriz Mongo existente — los roles que
    ya tenían el umbrella siguen funcionando sin tocar nada.
    """
    from core.roles import INVITADO_MODULES, has_access

    def _dep(request: Request, email: str = Depends(get_user_email)) -> str:
        # Portal invitado (www): pasa solo si ALGUNO de los módulos es de mercado.
        # Los sub-routers de manager piden ("manager", "manager_*") → ninguno está
        # en INVITADO_MODULES → 403 (el invitado nunca entra a manager).
        if is_guest_portal(request):
            if any(m in INVITADO_MODULES for m in modules):
                return email
            logger.warning("guest portal: %r bloqueado para invitado", list(modules))
            raise HTTPException(status_code=403, detail="no disponible para invitado")
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
