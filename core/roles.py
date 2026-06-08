"""Roles y matriz de permisos por módulo.

Cloudflare Access decide **quién puede entrar** al sitio (email OTP). Este
módulo decide **qué ve** cada usuario una vez adentro.

Modelo:
    Manager.Users       → {email, role, enabled, created_at, updated_at}
    Manager.RoleMatrix  → {role, modules: [str], updated_by, updated_at}
    Manager.RoleAudit   → append-only {ts, actor, action, target, before, after}

Módulos = carpetas de vistas del frontend + sus endpoints backend. La
lista canónica (`MODULES`) es hardcoded porque agregar un módulo nuevo
requiere código nuevo. Los roles y la matriz sí son editables en runtime
desde el panel Manager.

Fallback de bootstrap: si el email no está en `Manager.Users`, se cae a
`MANAGER_EMAILS` del env — si está ahí, se trata como `admin`; si no,
se asigna `DEFAULT_ROLE`. Esto evita que nadie quede afuera durante el
cutover (el gate fuerte sigue siendo Cloudflare Access en la entrada).

Cache: lookups de role por email cachean TTL 60s. Cuando el admin edita
usuarios o la matriz, `invalidate_cache()` purga todo.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from config import MANAGER_EMAILS
from core.mongo import get_mongo_client

logger = logging.getLogger(__name__)


def _auth_sql() -> bool:
    """True si las lecturas de AUTH leen de Postgres (flag AUTH_SQL=1). Default OFF = Mongo.
    Se lee por-llamada para poder prender/apagar sin restart. El fallback (try SQL → except →
    Mongo) garantiza que prenderlo NUNCA puede lockear: ante cualquier error SQL, cae a Mongo."""
    return os.getenv("AUTH_SQL") == "1"


# ─────────────────────────────────────────────────────────────
# Módulos canónicos
# ─────────────────────────────────────────────────────────────
# Cada módulo representa una unidad de negocio: una vista del frontend
# + los endpoints que esa vista consume. Agregar módulos acá requiere:
#   1. Sumar el string a MODULES.
#   2. Actualizar ENDPOINT_MODULE_PREFIXES en api/auth.py.
#   3. Editar la matriz en Manager.RoleMatrix (o DEFAULT_MATRIX).

MODULES: tuple[str, ...] = (
    "home",           # / + market + news
    "renta-fija",     # /renta-fija + cotizaciones + curvas
    "derivados",      # /derivados (solo opciones — agro y sintéticos son módulos aparte)
    "agro",           # /agro (pizarra + mejoras precio dispo + datos cámara cereales)
    "sinteticos",     # /sinteticos (long ROFEX + LECAP, short ROFEX + DLK)
    "renta-variable", # /renta-variable + smart money 13F/Form 4 sobre CEDEARs
    "estrategia",     # /retorno (sensibilidad, canje, carry)
    "operar",         # /operar (DOLAR MEP) + /api/ordenes + /api/operativa + /api/risk
    "operaciones",    # /operaciones (mesa, flujo) + /api/cuentas
    "portfolios",     # /portfolios + /aum + carteras + AuM + titulos
    "back-office",    # /back-office (títulos a enviar/recibir al mercado, conciliación)
    "manager",        # /manager + intel + jobs + logs (umbrella — tabs admin)
    # Sub-módulos de Manager: cobertura granular para el rol `asistente_comercial`
    # (acceso SOLO a las tabs Comercial + Clientes, sin ver el resto). El sub-router
    # respectivo lleva su propio require_module() en api/routers/manager/__init__.py.
    "manager_comercial",      # /api/manager/comercial/* (read-only: tablero por operador)
    "manager_clientes",       # /api/manager/clientes + /clientes/values + PATCH (edición fila)
    "manager_clientes_bulk",  # /api/manager/clientes/bulk + /bulk-fondeo (carga masiva — admin)
    "manager_compliance",     # /api/manager/compliance/* (read-only: operador nuestro vs Aunesa)
    "manager_titulos",        # /api/manager/assets (Títulos: Instrumentos + Assets, edición)
    "manager_contrapartes",   # /api/manager/contrapartes/* (segmentación + conciliador Aunesa)
)


# Matriz por defecto (se seedea en Manager.RoleMatrix la primera vez).
# Si la colección está vacía o el role no existe en ella, se cae acá.
# Nota: si Manager.RoleMatrix ya está poblada (caso prod), agregar un
# módulo nuevo NO se propaga automáticamente — el admin debe editar la
# matriz desde el panel para asignar el módulo nuevo a los roles que
# correspondan. Default es el bootstrap inicial.
DEFAULT_MATRIX: dict[str, tuple[str, ...]] = {
    "admin":  MODULES,  # todo (incluye los 3 sub-módulos de manager)
    "trader": (
        "home", "renta-fija", "derivados", "agro", "sinteticos",
        "renta-variable", "estrategia",
        "operaciones", "portfolios", "back-office",
    ),
    "sales":  (
        "home", "renta-fija", "derivados", "agro", "sinteticos",
        "renta-variable", "estrategia",
        "back-office",
    ),
    # Asistente comercial: mismas vistas que trader + entra a Manager pero SOLO a
    # las tabs Comercial, Clientes y Títulos (edición fila a fila, sin acceso a
    # los bulks). Sin `manager` umbrella → no ve las tabs admin (Jobs, Usuarios,
    # Roles, etc.). Sin `manager_clientes_bulk` → no puede ejecutar carga masiva.
    "asistente_comercial": (
        "home", "renta-fija", "derivados", "agro", "sinteticos",
        "renta-variable", "estrategia",
        "operaciones", "portfolios", "back-office",
        "manager_comercial", "manager_clientes", "manager_titulos",
        "manager_contrapartes",
    ),
    # Compliance: HOME + todos los mercados + Manager SOLO Clientes + Compliance
    # (sin `manager` umbrella → no ve las tabs de admin). Detecta diferencias de
    # operador (nuestro vs Aunesa). Mismo patrón de gate fino que asistente_comercial.
    "compliance": (
        "home", "renta-fija", "derivados", "agro", "sinteticos",
        "renta-variable", "estrategia",
        "manager_clientes", "manager_compliance",
    ),
}
# `operar` (DOLAR MEP + envío de órdenes) queda SOLO para admin de momento
# — decisión 2026-05-17. Si Manager.RoleMatrix ya está poblada, además hay
# que sacarlo de trader/sales desde /manager → ROLES Y PERMISOS (la DB pisa
# este default).
# renta-variable: habilitado para todos los roles (2026-05-13). Smart Money
# se eliminó del producto, el módulo ahora hospeda el Scanner de CEDEARs.

# Role asignado a emails que pasaron Cloudflare pero no están seedeados
# en Manager.Users. Preferimos "sales" (módulos públicos) para no dejar
# a nadie sin vista; el admin puede reasignar desde el panel.
DEFAULT_ROLE = "sales"

# Role para identidades NO autenticadas (anon / token de máquina sin user).
# No está en ninguna matriz → `has_access` lo trata fail-closed (0 módulos).
# NO usar DEFAULT_ROLE para esto: `sales` incluye `operar`.
_NO_ACCESS_ROLE = "none"


# ─────────────────────────────────────────────────────────────
# Cache in-memory (TTL 60s)
# ─────────────────────────────────────────────────────────────

_CACHE_TTL = 60.0
_cache_lock = threading.RLock()
_role_by_email: dict[str, tuple[float, str | None]] = {}
_matrix_cache: tuple[float, dict[str, tuple[str, ...]]] | None = None

# Last-seen throttle: solo escribimos `last_seen_at` en Mongo si pasaron
# más de N segundos del último valor — evita 1 write por request en horas
# pico (cada user suele pegar al backend muchas veces por minuto). 5 min
# es suficiente granularidad para "últ. visto" en el panel de usuarios.
_LAST_SEEN_THROTTLE_S = 300


def invalidate_cache() -> None:
    """Limpia el cache. Llamar post-mutación (admin editó usuarios/matriz)."""
    global _matrix_cache
    with _cache_lock:
        _role_by_email.clear()
        _matrix_cache = None


# ─────────────────────────────────────────────────────────────
# Lectura de Mongo
# ─────────────────────────────────────────────────────────────

class UserDoc(TypedDict, total=False):
    email: str
    role: str
    enabled: bool
    notes: str
    created_at: datetime
    updated_at: datetime


def _users_col():
    return get_mongo_client()["Manager"]["Users"]


def _matrix_col():
    return get_mongo_client()["Manager"]["RoleMatrix"]


def _audit_col():
    return get_mongo_client()["Manager"]["RoleAudit"]


def _load_matrix_from_db() -> dict[str, tuple[str, ...]]:
    """Lee la matriz completa de Manager.RoleMatrix; si está vacía, cae al default."""
    try:
        rows = list(_matrix_col().find({}, {"_id": 0, "role": 1, "modules": 1}))
    except Exception as e:
        logger.warning("RoleMatrix: error leyendo Mongo (%s), usando DEFAULT_MATRIX", e)
        return {k: tuple(v) for k, v in DEFAULT_MATRIX.items()}

    if not rows:
        return {k: tuple(v) for k, v in DEFAULT_MATRIX.items()}

    out: dict[str, tuple[str, ...]] = {}
    for r in rows:
        role = r.get("role")
        mods = r.get("modules") or []
        if role:
            # Filtramos a MODULES canónicos para evitar strings zombies
            out[str(role)] = tuple(m for m in mods if m in MODULES)
    return out


def _load_matrix() -> dict[str, tuple[str, ...]]:
    """Matriz con FALLBACK: si AUTH_SQL y SQL trae datos → SQL; vacío o error → Mongo
    (que a su vez cae a DEFAULT_MATRIX). Nunca devuelve matriz vacía."""
    if _auth_sql():
        try:
            from core import roles_sql
            m = roles_sql.load_matrix_sql()
            if m:
                return m
        except Exception as e:
            logger.warning("AUTH_SQL: RoleMatrix SQL falló (%s) → fallback Mongo", e)
    return _load_matrix_from_db()


def get_matrix() -> dict[str, tuple[str, ...]]:
    """Devuelve la matriz role → modules, con cache TTL 60s."""
    global _matrix_cache
    now = time.time()
    with _cache_lock:
        if _matrix_cache and now - _matrix_cache[0] < _CACHE_TTL:
            return _matrix_cache[1]
        m = _load_matrix()
        _matrix_cache = (now, m)
        return m


# ─────────────────────────────────────────────────────────────
# Resolución email → role
# ─────────────────────────────────────────────────────────────

def _touch_last_seen(email_norm: str) -> None:
    """Marca al user como visto ahora. Throttled: solo escribe si el
    último `last_seen_at` es más viejo que _LAST_SEEN_THROTTLE_S.

    Solo aplica a users reales (los que están en Manager.Users con un
    email humano). Falla silenciosa — no es crítica para el flujo de
    auth, solo alimenta la columna "ÚLT. VISTO" del panel.
    """
    if not email_norm or email_norm == "anon" or email_norm.startswith("service:"):
        return
    threshold = datetime.now(UTC) - timedelta(seconds=_LAST_SEEN_THROTTLE_S)
    try:
        # Update only-if filter: respeta el throttle sin necesidad de leer
        # antes. `last_seen_at` ausente cuenta como "viejo" (legacy docs).
        _users_col().update_one(
            {
                "email": email_norm,
                "$or": [
                    {"last_seen_at": {"$lt": threshold}},
                    {"last_seen_at": {"$exists": False}},
                ],
            },
            {"$set": {"last_seen_at": datetime.now(UTC)}},
        )
    except Exception as e:
        logger.debug("touch_last_seen falló para %s: %s", email_norm, e)


def _lookup_role_db(email: str) -> str | None:
    """Lee Manager.Users. Devuelve None si no está o enabled=False."""
    if not email or email == "anon":
        return None
    try:
        doc = _users_col().find_one(
            {"email": email},
            {"_id": 0, "role": 1, "enabled": 1},
        )
    except Exception as e:
        logger.warning("Users: error leyendo Mongo (%s) para email=%s", e, email)
        return None

    if not doc:
        return None
    if doc.get("enabled") is False:
        return None
    role = doc.get("role")
    return str(role) if role else None


def _lookup_role(email: str) -> str | None:
    """Role con FALLBACK: si AUTH_SQL → SQL; ante CUALQUIER error SQL → Mongo. El resultado
    SQL es autoritativo (None = no existe / deshabilitado → el caller auto-registra)."""
    if _auth_sql():
        try:
            from core import roles_sql
            return roles_sql.lookup_role_sql(email)
        except Exception as e:
            logger.warning("AUTH_SQL: lookup_role SQL falló (%s) → fallback Mongo", e)
    return _lookup_role_db(email)


def _auto_register(email_norm: str) -> str:
    """Registra un email visto por primera vez en Manager.Users.

    Se dispara cuando `get_user_role()` no encuentra el email en la DB.
    Regla de asignación:
      - Si está en MANAGER_EMAILS → role=admin (legacy).
      - Si no → DEFAULT_ROLE (sales).

    Marca el doc con `auto_registered=True` para que la UI lo pinte como
    "detectado automáticamente" y el admin sepa que hay que revisarlo.
    Idempotente: el upsert no duplica ni sobrescribe si alguien ya lo
    ajustó a mano. Falla silenciosa si Mongo no responde (el caller cae
    a DEFAULT_ROLE igual).
    """
    role = "admin" if email_norm in MANAGER_EMAILS else DEFAULT_ROLE
    now = datetime.now(UTC)
    try:
        _users_col().update_one(
            {"email": email_norm},
            {
                "$set": {
                    "email": email_norm,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "role": role,
                    "enabled": True,
                    "auto_registered": True,
                    "notes": "auto-registrado en primera visita",
                    "created_at": now,
                },
            },
            upsert=True,
        )
    except Exception as e:
        logger.warning("auto-register falló para %s: %s", email_norm, e)
    return role


def get_user_role(email: str) -> str:
    """Devuelve el role de un email, con cache TTL 60s.

    Resolución:
      1. Manager.Users con enabled=True → role del doc.
      2. Email no registrado → _auto_register() crea el doc (admin si está
         en MANAGER_EMAILS, sino DEFAULT_ROLE). Así el admin ve en la tab
         USUARIOS a todos los emails que pasaron CF Access.
      3. email "anon" / vacío / service token sin user (frontend no
         propagó x-acaquant-user-email) → DEFAULT_ROLE (sales) — fail-closed.

    Historia (importante para no recaer):
    - El `service:<cn>` aparece cuando un request entra al backend
      autenticado por service token (acaquant-web SSR → api.acaquant.com)
      y CF Access estripa el header cf-access-authenticated-user-email
      que el origin intentó mandar.
    - El frontend resuelve eso mandando `x-acaquant-user-email` (CF no
      controla ese namespace, lo deja pasar). Cuando llega, api/auth.py
      lo lee con prioridad y devuelve el email del user real, no
      `service:*`.
    - Entonces `service:*` solo aparece en el backend cuando NO hay
      x-acaquant-user-email — request de máquina legítimo (cron, smoke).
      Esos NO son admin: caen a DEFAULT_ROLE como cualquier desconocido.
    - Antes esta rama mapeaba `service:* → admin`, lo cual anulaba RBAC
      por completo. Ver commits 12b7008 (fix backend), 64156e2 (fix
      frontend), 2281cf5 (rollback temporal mientras Vercel deployaba).
    """
    if not email:
        return _NO_ACCESS_ROLE
    email_norm = email.lower().strip()

    # Identidad NO autenticada (anon / vacío / token de máquina sin user):
    # role SIN módulos — fail-closed real. DEFAULT_ROLE (sales) NO va acá:
    # incluye `operar` (envío de órdenes), y dárselo a una identidad no
    # autenticada es escalada de privilegios. Una integración de máquina
    # que necesite acceso se registra explícita en Manager.Users.
    if email_norm in ("anon", "") or email_norm.startswith("service:"):
        return _NO_ACCESS_ROLE

    now = time.time()
    with _cache_lock:
        hit = _role_by_email.get(email_norm)
        if hit and now - hit[0] < _CACHE_TTL:
            # Cache hit: igual marcamos visto (throttled). Sin esto el
            # last_seen_at solo se actualizaría 1 vez/min en el primer
            # tick del cache, lo que se ve como "no actualiza" cuando
            # el user navega activamente.
            _touch_last_seen(email_norm)
            cached = hit[1]
            return cached if cached is not None else DEFAULT_ROLE

    # Rama 2: lookup en Manager.Users (SQL con fallback a Mongo si AUTH_SQL)
    role = _lookup_role(email_norm)

    # Rama 3: primera visita → auto-registrar
    if role is None:
        role = _auto_register(email_norm)

    # Rama legacy: si _auto_register falló y no está en MANAGER_EMAILS,
    # este check ya no aplica porque _auto_register lo maneja. Se mantiene
    # por defensa si upsert tiró excepción.
    if role is None and email_norm in MANAGER_EMAILS:
        role = "admin"

    # Rama 4/5: nadie le asignó role → DEFAULT_ROLE (no rechazamos; que se
    # encargue require_module() de rechazar si el módulo está restringido)
    effective = role if role is not None else DEFAULT_ROLE

    # Actualizar last_seen_at (throttled — barata aunque parezca cara).
    _touch_last_seen(email_norm)

    with _cache_lock:
        _role_by_email[email_norm] = (now, role)
    return effective


def get_user_modules(email: str) -> tuple[str, ...]:
    """Módulos que el email puede ver según su role y la matriz vigente."""
    role = get_user_role(email)
    return get_matrix().get(role, ())


def has_access(email: str, module: str) -> bool:
    """True si el email puede ver el módulo dado."""
    if module not in MODULES:
        # Módulo desconocido → fail-closed. Previene typos en callers.
        logger.warning("has_access: módulo desconocido %r", module)
        return False
    return module in get_user_modules(email)


# ─────────────────────────────────────────────────────────────
# Mutaciones (usadas por los endpoints CRUD de Manager.Users)
# ─────────────────────────────────────────────────────────────

def upsert_user(email: str, role: str, enabled: bool = True,
                notes: str | None = None, actor: str = "system") -> dict:
    """Crea o actualiza un usuario. Audit-log incluido."""
    email_norm = email.lower().strip()
    matrix = get_matrix()
    if role not in matrix:
        raise ValueError(f"role desconocido: {role!r} (válidos: {list(matrix)})")

    col = _users_col()
    now = datetime.now(UTC)
    before = col.find_one({"email": email_norm}, {"_id": 0})

    update = {
        "$set": {
            "email": email_norm,
            "role": role,
            "enabled": bool(enabled),
            "notes": notes or "",
            "updated_at": now,
        },
        "$setOnInsert": {"created_at": now},
    }
    col.update_one({"email": email_norm}, update, upsert=True)
    after = col.find_one({"email": email_norm}, {"_id": 0})

    _audit_col().insert_one({
        "ts": now,
        "actor": actor,
        "action": "upsert_user",
        "target": email_norm,
        "before": before,
        "after": after,
    })
    invalidate_cache()
    return after or {}


def delete_user(email: str, actor: str = "system") -> bool:
    """Elimina un usuario. Audit-log incluido."""
    email_norm = email.lower().strip()
    col = _users_col()
    before = col.find_one({"email": email_norm}, {"_id": 0})
    if not before:
        return False
    col.delete_one({"email": email_norm})

    _audit_col().insert_one({
        "ts": datetime.now(UTC),
        "actor": actor,
        "action": "delete_user",
        "target": email_norm,
        "before": before,
        "after": None,
    })
    invalidate_cache()
    return True


def set_role_modules(role: str, modules: list[str], actor: str = "system") -> dict:
    """Actualiza los módulos de un role. Audit-log incluido."""
    mods_norm = [m for m in modules if m in MODULES]
    now = datetime.now(UTC)

    col = _matrix_col()
    before = col.find_one({"role": role}, {"_id": 0})

    col.update_one(
        {"role": role},
        {"$set": {
            "role": role,
            "modules": mods_norm,
            "updated_by": actor,
            "updated_at": now,
        }},
        upsert=True,
    )
    after = col.find_one({"role": role}, {"_id": 0})

    _audit_col().insert_one({
        "ts": now,
        "actor": actor,
        "action": "set_role_modules",
        "target": role,
        "before": before,
        "after": after,
    })
    invalidate_cache()
    return after or {}


def list_users() -> list[dict]:
    """Todos los usuarios, ordenados por email."""
    return list(_users_col().find({}, {"_id": 0}).sort("email", 1))


def list_audit(limit: int = 50) -> list[dict]:
    """Últimos N eventos del audit log, del más reciente al más viejo."""
    return list(
        _audit_col()
        .find({}, {"_id": 0})
        .sort("ts", -1)
        .limit(int(limit))
    )
