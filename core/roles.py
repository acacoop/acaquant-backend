"""Roles y matriz de permisos por módulo.

Cloudflare Access decide **quién puede entrar** al sitio (email OTP). Este
módulo decide **qué ve** cada usuario una vez adentro.

Modelo (SQL-native — decomiso Mongo; Manager.* dropeadas):
    manager.manager_users → {email, role, enabled, created_at, updated_at}
    manager.role_matrix   → {role, modules: [str], updated_by, updated_at}
    manager.role_audit    → append-only {ts, actor, action, target, before, after}

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
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from config import MANAGER_EMAILS

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
    "derivados",      # /derivados (solo opciones — agro y sintéticos son módulos aparte)
    "agro",           # /agro (pizarra + mejoras precio dispo + datos cámara cereales)
    "sinteticos",     # /sinteticos (long ROFEX + LECAP, short ROFEX + DLK)
    "renta-variable", # /renta-variable + smart money 13F/Form 4 sobre CEDEARs
    "trading",        # /trading (panel intradía de CEDEARs — 5 sistemas, admin-only)
    "estrategia",     # /retorno (sensibilidad, canje, carry)
    "operar",         # /operar (DOLAR MEP) + /api/ordenes + /api/operativa + /api/risk
    "operaciones",    # /operaciones (mesa, flujo) + /api/cuentas
    "portfolios",     # /portfolios + /aum + carteras + AuM + titulos
    "back-office",    # /back-office (títulos a enviar/recibir al mercado, conciliación)
    "research",       # /research (vista Research: research diario 1816 por mail +
                      # market data 1816 — docs/VISTA_RESEARCH.md). Gate de
                      # /api/research1816/*. INTERNA — JAMÁS invitado (REGLA #8).
    "ia",             # features de IA (QuantAI, docs/QUANTAI.md) — gate de /api/ia/*.
                      # JAMÁS agregarlo a `invitado` (REGLA #8): es la marca AI interna.
    "manager",        # /manager + intel + jobs + logs (umbrella — tabs admin)
    # Sub-módulos de Manager: cobertura granular para el rol `asistente_comercial`
    # (acceso SOLO a la tab Clientes, sin ver el resto). El sub-router
    # respectivo lleva su propio require_module() en api/routers/manager/__init__.py.
    "manager_clientes",       # /api/manager/clientes + /clientes/values + PATCH (edición fila)
    "manager_clientes_bulk",  # /api/manager/clientes/bulk + /bulk-fondeo (carga masiva — admin)
    "manager_compliance",     # /api/manager/compliance/* (read-only: operador nuestro vs Aunesa)
    "manager_titulos",        # /api/manager/assets + /ons (Títulos: Assets + ONs, edición maestro)
    "manager_instrumentos",   # /api/manager/checks/{discovery-pyrofex,instruments-by-cfi} (Títulos→Instrumentos, SOLO lectura)
    "manager_contrapartes",   # /api/manager/contrapartes/* (segmentación + conciliador Aunesa)
    "manager_aunesa",         # /api/manager/import-* (AUNESA → IMPORTAR AUM: precios + tenencia SQL)
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
        "manager_clientes", "manager_instrumentos",
        "manager_contrapartes", "manager_aunesa",
    ),
    # Compliance: HOME + todos los mercados + Manager SOLO Clientes + Compliance
    # (sin `manager` umbrella → no ve las tabs de admin). Detecta diferencias de
    # operador (nuestro vs Aunesa). Mismo patrón de gate fino que asistente_comercial.
    "compliance": (
        "home", "renta-fija", "derivados", "agro", "sinteticos",
        "renta-variable", "estrategia",
        "manager_clientes", "manager_compliance",
    ),
    # Back Office: SOLO Home + Back Office (títulos a enviar/recibir al mercado,
    # conciliación, tenencia valorizada). Default mínimo A PROPÓSITO — el admin amplía
    # o recorta desde /manager → ROLES Y PERMISOS.
    "back_office": (
        "home", "back-office",
    ),
    # Invitado / cliente externo del portal www.acaquant.com: SOLO vistas de
    # mercado (read-only). NADA privado de la mesa — sin operaciones, portfolios,
    # back-office, operar ni manager. Las restricciones finas (ocultar AGRO→DATOS,
    # tasa R read-only en derivados) las aplica el frontend del portal guest, no
    # el gate de módulo. Se asigna por venir de www (no por email) — ver el
    # forzado de rol en api/auth.py, no por Manager.Users.
    "invitado": (
        "home", "renta-fija", "derivados", "agro", "sinteticos",
        "renta-variable", "estrategia",
    ),
}
# `operar` (DOLAR MEP + envío de órdenes) queda SOLO para admin de momento
# — decisión 2026-05-17. Si Manager.RoleMatrix ya está poblada, además hay
# que sacarlo de trader/sales desde /manager → ROLES Y PERMISOS (la DB pisa
# este default).
# renta-variable: habilitado para todos los roles (2026-05-13). Smart Money
# se eliminó del producto, el módulo ahora hospeda el Scanner de CEDEARs.
# ia: default SOLO admin (canary/rollout gradual, docs/QUANTAI.md 2026-07-10).
# Como la role_matrix de prod está poblada (pisa este default), activarlo es
# una acción del admin en /manager → ROLES Y PERMISOS: primero para `admin`,
# después rol por rol con evidencia. Sacarlo del rol = kill switch (~60s, TTL
# del cache). NUNCA para `invitado` (REGLA #8).

# Role asignado a emails que pasaron Cloudflare pero no están seedeados
# en Manager.Users. Preferimos "sales" (módulos públicos) para no dejar
# a nadie sin vista; el admin puede reasignar desde el panel.
DEFAULT_ROLE = "sales"

# Role para identidades NO autenticadas (anon / token de máquina sin user).
# No está en ninguna matriz → `has_access` lo trata fail-closed (0 módulos).
# NO usar DEFAULT_ROLE para esto: `sales` incluye `operar`.
_NO_ACCESS_ROLE = "none"

# Módulos del rol invitado (portal www.acaquant.com). Fuente única para el gate
# del backend (api/auth.py::require_module) y para /api/me. NO depende de la
# RoleMatrix viva: el invitado se fuerza por venir de www, no por su email, así
# que su set de módulos tiene que ser estable y no editable desde el panel.
INVITADO_MODULES: tuple[str, ...] = DEFAULT_MATRIX["invitado"]


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
    control_comercial: bool
    notes: str
    created_at: datetime
    updated_at: datetime


def _audit_insert(doc: dict) -> None:
    """Inserta un evento de auditoría SQL-NATIVE en manager.role_audit (decomiso Mongo:
    ya NO escribe Manager.RoleAudit). PK = uuid (antes era str(ObjectId) del insert Mongo).
    `ts` es AWARE UTC → timestamptz no corre la hora. Best-effort: si SQL falla NO debe
    tumbar la mutación de usuario/rol (que ya persistió en su propio write).

    Lectura del panel: api/routers/manager/roles.py → manager_infra_sql.list_audit_sql
    cuando el flag de lectura MANAGER_SQL=1 está prendido. Con MANAGER_SQL=0 el router cae
    a roles.list_audit (Mongo), que tras este cutover NO verá los eventos nuevos."""
    try:
        from uuid import uuid4

        from core.pg_mirror import doc_iso, write_native
        write_native("manager.role_audit", ["audit_id"], [{
            "audit_id": str(uuid4()),
            "ts":       doc.get("ts"),
            "actor":    doc.get("actor"),
            "action":   doc.get("action"),
            "target":   doc.get("target"),
            "data":     doc_iso(doc),
        }])
    except Exception as e:
        logger.warning("RoleAudit: write SQL falló (%s) — la mutación ya persistió", e)


def _load_matrix() -> dict[str, tuple[str, ...]]:
    """Matriz role→modules desde SQL (manager.role_matrix). SQL-native (decomiso Mongo:
    Manager.RoleMatrix dropeada). Vacío o error SQL → DEFAULT_MATRIX (bootstrap hardcoded).
    Nunca devuelve matriz vacía → nunca lockea."""
    try:
        from core import roles_sql
        m = roles_sql.load_matrix_sql()
        if m:
            return m
    except Exception as e:
        logger.warning("RoleMatrix SQL falló (%s) → DEFAULT_MATRIX", e)
    return {k: tuple(v) for k, v in DEFAULT_MATRIX.items()}


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
    now = datetime.now(UTC)
    threshold = now - timedelta(seconds=_LAST_SEEN_THROTTLE_S)
    # SQL-ONLY (decomiso Mongo 2026-06-28): manager.manager_users es la fuente de verdad.
    # Best-effort SIEMPRE: esta función corre en el hot-path de get_user_role (cada request);
    # un fallo de SQL NUNCA puede tumbar un request → throttle replicado en el WHERE del UPDATE.
    try:
        from core import roles_sql
        roles_sql.touch_last_seen_sql(email_norm, now, threshold)
    except Exception as e:
        logger.debug("touch_last_seen: SQL falló para %s: %s", email_norm, e)


def _lookup_role(email: str) -> str | None:
    """Role desde SQL (manager.manager_users). SQL-native (decomiso Mongo: Manager.Users
    dropeada). None = no existe / deshabilitado → el caller auto-registra. Ante CUALQUIER
    error SQL → None (NUNCA lockea: el caller cae a auto_register/DEFAULT_ROLE)."""
    if not email or email == "anon":
        return None
    try:
        from core import roles_sql
        return roles_sql.lookup_role_sql(email)
    except Exception as e:
        logger.warning("lookup_role SQL falló (%s) para %s → None (auto-register)", e, email)
        return None


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
    notes = "auto-registrado en primera visita"
    # SQL-ONLY (decomiso Mongo 2026-06-28): manager.manager_users es la fuente de verdad.
    # Best-effort: corre en el hot-path de get_user_role; si SQL falla el caller igual cae a
    # `role` (computado local). El upsert es idempotente (ON CONFLICT) → si falla, la próxima
    # visita lo reintenta sin duplicar.
    try:
        from core import roles_sql
        roles_sql.auto_register_sql(email_norm, role, notes, now)
    except Exception as e:
        logger.warning("auto-register: SQL falló para %s: %s", email_norm, e)
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

    # Rama 2: lookup en manager.manager_users (SQL). None = no existe → auto-registra.
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


def user_has_control_comercial(email: str) -> bool:
    """Permiso PER-USUARIO (no por rol) para VER/EDITAR Control Comercial.

    admin SIEMPRE (para no quedar afuera de la gestión); el resto solo si el admin
    tildó `control_comercial=true` en /manager → Usuarios. Default-deny."""
    email_norm = (email or "").lower().strip()
    if get_user_role(email_norm) == "admin":
        return True
    try:
        from core import roles_sql
        u = roles_sql.get_user_sql(email_norm)
        return bool(u and u.get("control_comercial"))
    except Exception as e:
        logger.debug("user_has_control_comercial(%s): %s", email_norm, e)
        return False


# ─────────────────────────────────────────────────────────────
# Mutaciones (usadas por los endpoints CRUD de Manager.Users)
# ─────────────────────────────────────────────────────────────

def upsert_user(email: str, role: str, enabled: bool = True,
                control_comercial: bool = False,
                notes: str | None = None, actor: str = "system") -> dict:
    """Crea o actualiza un usuario. Audit-log incluido."""
    email_norm = email.lower().strip()
    matrix = get_matrix()
    if role not in matrix:
        raise ValueError(f"role desconocido: {role!r} (válidos: {list(matrix)})")

    now = datetime.now(UTC)
    # SQL-ONLY (decomiso Mongo 2026-06-28): manager.manager_users es la fuente de verdad.
    # AUTORITATIVO: si SQL falla, propagamos (el panel ve el error). Mejor un 500 visible que
    # una edición de rol perdida en silencio. created_at/auto_registered no se pisan en el
    # upsert SQL (igual que el $setOnInsert/ausencia en el viejo Mongo).
    from core import roles_sql
    before = roles_sql.get_user_sql(email_norm)
    roles_sql.upsert_user_sql(email_norm, role, bool(enabled), bool(control_comercial),
                              notes or "", now)
    after = roles_sql.get_user_sql(email_norm)

    _audit_insert({
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
    # SQL-ONLY (decomiso Mongo 2026-06-28): manager.manager_users es la fuente de verdad.
    # AUTORITATIVO: si SQL falla, propagamos (el panel ve el error).
    from core import roles_sql
    before = roles_sql.get_user_sql(email_norm)
    if not before:
        return False
    roles_sql.delete_user_sql(email_norm)

    _audit_insert({
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

    # SQL-ONLY (decomiso Mongo 2026-06-28): manager.role_matrix es la fuente de verdad.
    # AUTORITATIVO: si SQL falla, propagamos. before/after replican el shape del viejo doc
    # Mongo ({role, modules, ...}) para el audit y el retorno al panel.
    from core import roles_sql
    before_mods = roles_sql.get_role_modules_sql(role)
    before = {"role": role, "modules": before_mods} if before_mods else None
    roles_sql.set_role_modules_sql(role, mods_norm)
    after = {"role": role, "modules": mods_norm, "updated_by": actor, "updated_at": now}

    _audit_insert({
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
    """Todos los usuarios, ordenados por email. SQL-ONLY (decomiso Mongo 2026-06-28):
    lee manager.manager_users (fuente de verdad)."""
    from core import roles_sql
    return roles_sql.list_users_sql()


def list_audit(limit: int = 50) -> list[dict]:
    """Últimos N eventos del audit log (más reciente primero). SQL-native (decomiso Mongo:
    Manager.RoleAudit dropeada) → manager.role_audit. El doc vive en `data` jsonb."""
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT data FROM manager.role_audit ORDER BY ts DESC LIMIT %s", (int(limit),))
        return [dict(r[0] or {}) for r in cur.fetchall()]
