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
import threading
import time
from datetime import UTC, datetime
from typing import TypedDict

from config import MANAGER_EMAILS
from core.mongo import get_mongo_client

logger = logging.getLogger(__name__)


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
    "derivados",      # /derivados + opciones
    "estrategia",     # /retorno (sensibilidad, canje, carry)
    "operaciones",    # /operaciones + cuentas
    "portfolios",     # /portfolios + /aum + carteras + AuM + titulos
    "asistente",      # /asistente + /api/chat
    "manager",        # /manager + intel + jobs + logs
)


# Matriz por defecto (se seedea en Manager.RoleMatrix la primera vez).
# Si la colección está vacía o el role no existe en ella, se cae acá.
DEFAULT_MATRIX: dict[str, tuple[str, ...]] = {
    "admin":  MODULES,  # todo
    "trader": (
        "home", "renta-fija", "derivados", "estrategia",
        "operaciones", "portfolios", "asistente",
    ),
    "sales":  (
        "home", "renta-fija", "derivados", "estrategia",
    ),
}

# Role asignado a emails que pasaron Cloudflare pero no están seedeados
# en Manager.Users. Preferimos "sales" (módulos públicos) para no dejar
# a nadie sin vista; el admin puede reasignar desde el panel.
DEFAULT_ROLE = "sales"


# ─────────────────────────────────────────────────────────────
# Cache in-memory (TTL 60s)
# ─────────────────────────────────────────────────────────────

_CACHE_TTL = 60.0
_cache_lock = threading.RLock()
_role_by_email: dict[str, tuple[float, str | None]] = {}
_matrix_cache: tuple[float, dict[str, tuple[str, ...]]] | None = None


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


def get_matrix() -> dict[str, tuple[str, ...]]:
    """Devuelve la matriz role → modules, con cache TTL 60s."""
    global _matrix_cache
    now = time.time()
    with _cache_lock:
        if _matrix_cache and now - _matrix_cache[0] < _CACHE_TTL:
            return _matrix_cache[1]
        m = _load_matrix_from_db()
        _matrix_cache = (now, m)
        return m


# ─────────────────────────────────────────────────────────────
# Resolución email → role
# ─────────────────────────────────────────────────────────────

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
      1. Service tokens (`service:<cn>`) — TEMPORAL: admin. Ver nota.
      2. Manager.Users con enabled=True → role del doc.
      3. Email no registrado → _auto_register() crea el doc (admin si está
         en MANAGER_EMAILS, sino DEFAULT_ROLE). Así el admin ve en la tab
         USUARIOS a todos los emails que pasaron CF Access.
      4. email "anon" / vacío → DEFAULT_ROLE (sales) sin persistir.

    NOTA TÉCNICA — `service:*` = admin (transitorio).

    Hasta 2026-04-28 esta rama se justificaba con "el frontend gateó al
    user en proxy.ts antes de pegar al API". Eso era falso: proxy.ts solo
    gatea por path, y CF Access estripa cf-access-authenticated-user-email
    cuando el request entra autenticado por service token, así que el
    backend nunca recibía el email del user real → todo el RBAC se anulaba.

    El fix completo (commit 12b7008 + frontend 64156e2) introduce
    x-acaquant-user-email para que el frontend identifique al user
    explícitamente. Una vez que esos commits estén deployados en ambos
    repos Y verifiquemos que x-acaquant-user-email llega al backend, esta
    rama se puede eliminar (los `service:` caerán a DEFAULT_ROLE como
    cualquier desconocido). Mientras tanto la dejamos para no dejar al
    admin sin acceso si Vercel demora el deploy.
    """
    if not email:
        return DEFAULT_ROLE
    email_norm = email.lower().strip()

    # Rama 1 transitoria — ver docstring. Sacar cuando x-acaquant-user-email
    # esté verificado en producción.
    if email_norm.startswith("service:"):
        return "admin"

    if email_norm in ("anon", ""):
        return DEFAULT_ROLE

    now = time.time()
    with _cache_lock:
        hit = _role_by_email.get(email_norm)
        if hit and now - hit[0] < _CACHE_TTL:
            cached = hit[1]
            return cached if cached is not None else DEFAULT_ROLE

    # Rama 2: lookup en Manager.Users
    role = _lookup_role_db(email_norm)

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
