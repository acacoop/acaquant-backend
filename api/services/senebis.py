"""api/services/senebis.py — SENEBIS (vista BACK OFFICE → SENEBIS).

Órdenes que los TRADERS cargan para que el BACK OFFICE las procese en el
sistema externo. Flujo mínimo a propósito:

    trader crea la orden ('pendiente') → aparece en la vista → el back office
    la carga afuera y la marca 'completada'. Dos estados, nada más.

Derivados / defaults (una sola fuente de verdad, calculados acá):
    monto        = vn × px / 100          (px viene cada 100 VN)
    concertacion = HOY (ART) si no viene
    cp           = '255' si no viene
    plazo ↔ liquidacion se infieren entre sí (core.calendario):
        CI → liquidacion = concertacion; 24 → liquidacion = próximo hábil.
        Con liquidacion y sin plazo: mismo día → CI, si no → 24.

Presencia: el front hace heartbeat (o simplemente pollea la lista, que ya
marca presencia) → `operaciones.senebis_presencia` guarda el último visto_at
por email; conectado = visto en los últimos PRESENCIA_TTL_S segundos. Así el
equipo ve quién está en la vista y no se pisan al completar órdenes.

Contraparte del senebi (la clave del Excel):
    'interno'  → contra un cliente de la ALyC: el trader carga la cuenta (cc)
                 por NÚMERO o por DENOMINACIÓN (se resuelve contra
                 clientes.cuentas y se snapshotea cc + cc_denominacion).
    'externo'  → contra un AGENTE de afuera (COCOS, ALLARIA…): el trader elige
                 el NOMBRE del catálogo `operaciones.senebis_agentes`; el
                 NÚMERO (el que espera el sistema destino) se snapshotea en
                 agente_numero al guardar.

Marcas de edición (2026-08-05) — reemplazan al amarillo que el back office
pintaba a mano en la planilla vieja, y son PERSISTENTES:
    campos_editados    → qué campos se tocaron después del alta (acumulativo);
                         el front les pone un * al lado. El estado no cambia:
                         una pendiente editada sigue pendiente.
    editada_completada → se editó algo que YA estaba 'completada' (el caso
                         peligroso: el back office la cargó en Quantex y tiene
                         que enterarse) → el front pinta toda la fila amarilla.
Se calculan en editar_op comparando before/after de los campos persistidos —
no dependen de lo que mande el front (que suele echoar la fila entera).

Destino MAE (2026-08-05, base del Excel MAE): el MAE identifica cada destino
con un código. En la base vive SOLO EL NÚMERO — la letra la pone el sistema
según la orden (_destino_con_letra): interno → F (fondo), externo → A
(agente). Agente externo: senebis_agentes.codigo_mae (convive con el número
BYMA/Quantex). Cuenta interna: es un ATRIBUTO de la contraparte,
clientes.contrapartes.codigo_mae, editable en Manager → CONTRAPARTES.

Excel MAE (2026-08-05): tab EXCEL MAE espejo del archivo del MAE — SOLO
pendientes es_mae (misma lógica temporal que Quantex; "cargan ellos" también
queda afuera). Columnas: Operacion · Instrumento · Plazo · Moneda ('ARS' fijo
hasta que la orden tenga el campo) · Precio (UNITARIO: px÷100) · Cantidad ·
Destino · Segmento (manual en el archivo). DESTINO se resuelve EN VIVO al
generar (excel_mae_preview / export_mae_xlsx): interno → cc →
contrapartes.codigo_mae; externo → agente → senebis_agentes.codigo_mae — el
trader no carga nada; si falta el código la celda va vacía y el espejo lo
marca (sin_destino).

Cargan ellos (2026-08-05): flag SI/NO (antes era observación de texto libre).
SI = la orden la carga la CONTRAPARTE en Quantex → queda FUERA del Excel y
del espejo (igual que MAE), pero visible en la vista con su flujo
pendiente → completada normal.

Export: `export_xlsx()` arma el .xlsx EXACTO que el back office carga en el
otro sistema: ID · OPERACION · INSTRUMENTO · PLAZO · PRECIO · CANTIDAD ·
CONTRAPARTE · COMITENTE · CARTERA PROPIA · MERCADO. Reglas (2026-08-05):
    externo               → COMITENTE vacío, CONTRAPARTE = número del agente.
    interno GARANTIZADO   → COMITENTE = cp (la 255), CONTRAPARTE vacío.
    interno NO GARANT./s-d→ COMITENTE = cc, CONTRAPARTE vacío.
El ID es el de la tabla: secuencia GLOBAL que espeja la numeración Quantex y
nunca se resetea — visible y ajustable desde la vista ("PRÓXIMO ID",
set_proximo_id, mientras conviva el Excel viejo). Los campos numéricos salen
como número cuando el valor lo es (el sistema destino los espera así).
openpyxl con import lazy para no tumbar la API si falta la lib (REGLA #1).

Permisos: módulo `back-office` (gate en api/main.py) para LEER, marcar estado
y gestionar el catálogo de agentes. CARGAR/EDITAR/BORRAR órdenes exige además
la allowlist PROPIA `operaciones.senebis_escritores` (+ admin — separada de la
de Mesa de Dinero el 2026-08-05, se gestiona igual en Manager → MESA).
Trazabilidad: cada cambio inserta un evento en
`operaciones.senebis_audit` con before/after. Servicio puro (sin FastAPI).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from api.services._sql import _f, _q
from core.calendario import proximo_habil
from core.postgres import get_pool

ESTADOS = ("pendiente", "completada")
PLAZOS = ("CI", "24")
TIPOS_CONTRAPARTE = ("interno", "externo")
# Filtro MAE de la vista: sin valor = todas (con MAE incluidas).
FILTROS_MAE = ("solo", "sin")
PRESENCIA_TTL_S = 90  # visto hace ≤90s = conectado (el front pollea cada ~30s)
_TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")

# Campos editables de una orden (el resto es derivado o metadata de auditoría).
_CAMPOS_OP = (
    "operacion", "concertacion", "liquidacion", "plazo", "especie",
    "vn", "px", "monto", "cp", "cc", "contraparte", "nro_contraparte",
    "mercado", "cargan_ellos", "tipo", "tipo_contraparte", "agente", "es_mae",
    "segmento",
)

# SEGMENTO del Excel MAE. El universo válido vive en el catálogo
# `operaciones.senebis_segmentos` (ABM en la vista, igual que el de agentes);
# esto es solo con cuál nace una orden MAE si el trader no elige otro. Se
# aplica al GUARDAR (no al leer) para que la orden tenga el valor explícito,
# y además se usa de fallback en el export para las órdenes anteriores a este
# campo, que lo tienen en NULL.
SEGMENTO_MAE_DEFAULT = "Bilateral MAEClear"

# Headers del Excel que el back office carga en el otro sistema (imagen de
# referencia 2026-08-05): el ID va primero y es la secuencia global de la tabla.
_HEADERS_XLSX = ("ID", "OPERACION", "INSTRUMENTO", "PLAZO", "PRECIO", "CANTIDAD",
                 "CONTRAPARTE", "COMITENTE", "CARTERA PROPIA", "MERCADO")

# Headers del Excel MAE (imagen de referencia 2026-08-05). Sin ID: el MAE no
# usa la secuencia Quantex. SEGMENTO ya NO se completa a mano: sale del campo
# `segmento` de la orden, que se elige al cargarla entre los del catálogo
# `senebis_segmentos` y nace en SEGMENTO_MAE_DEFAULT.
_HEADERS_MAE = ("Operacion", "Instrumento", "Plazo", "Moneda", "Precio",
                "Cantidad", "Destino", "Segmento")


def _exec(sql: str, params: dict) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        n = cur.rowcount
        conn.commit()
        return n


def _audit(actor: str, action: str, target: str, data: dict | None = None) -> None:
    """Evento de auditoría — nunca rompe la operación principal."""
    try:
        _exec(
            "INSERT INTO operaciones.senebis_audit (ts, actor, action, target, data) "
            "VALUES (%(ts)s, %(actor)s, %(action)s, %(target)s, %(data)s)",
            {"ts": datetime.now(UTC), "actor": actor or None, "action": action,
             "target": target, "data": Jsonb(_jsonable(data or {}))},
        )
    except Exception:  # el audit no bloquea la escritura real
        import logging
        logging.getLogger(__name__).exception("senebis: audit insert falló")


def _jsonable(v: Any) -> Any:
    """dict/list con date/datetime/Decimal → tipos serializables."""
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if hasattr(v, "is_finite"):  # Decimal
        return float(v)
    return v


# ─────────────────────────────────────────────────────────────
# Presencia (quién tiene la vista abierta)
# ─────────────────────────────────────────────────────────────

def marcar_presencia(email: str) -> None:
    e = (email or "").lower().strip()
    if not e:
        return
    _exec(
        "INSERT INTO operaciones.senebis_presencia (email, visto_at) "
        "VALUES (%(e)s, %(at)s) "
        "ON CONFLICT (email) DO UPDATE SET visto_at = EXCLUDED.visto_at",
        {"e": e, "at": datetime.now(UTC)},
    )


def conectados() -> list[dict]:
    """Quiénes vieron la vista en los últimos PRESENCIA_TTL_S segundos."""
    rows = _q(
        "SELECT email, visto_at FROM operaciones.senebis_presencia "
        "WHERE visto_at >= now() - make_interval(secs => %(ttl)s) ORDER BY email",
        {"ttl": PRESENCIA_TTL_S},
    )
    return [{"email": r["email"], "visto_at": r["visto_at"].isoformat()} for r in rows]


# ─────────────────────────────────────────────────────────────
# Agentes externos (catálogo nombre → número del sistema destino)
# ─────────────────────────────────────────────────────────────

def listar_agentes() -> list[dict]:
    return [{"nombre": r["nombre"], "numero": r["numero"],
             "codigo_mae": r["codigo_mae"]} for r in _q(
        "SELECT nombre, numero, codigo_mae FROM operaciones.senebis_agentes ORDER BY nombre")]


def _numero_agente(nombre: str) -> str | None:
    rows = _q("SELECT numero FROM operaciones.senebis_agentes WHERE nombre = %(n)s",
              {"n": nombre})
    return rows[0]["numero"] if rows else None


def upsert_agente(nombre: str, numero: str, actor: str,
                  codigo_mae: str | None = None) -> dict:
    n = (nombre or "").strip().upper()
    num = str(numero or "").strip()
    cod = (str(codigo_mae or "").strip().upper()) or None
    if not n:
        raise ValueError("falta 'nombre'")
    if not num:
        raise ValueError("falta 'numero' (el número de agente del sistema destino)")
    at = datetime.now(UTC)
    por = (actor or "").lower() or None
    # COALESCE: re-guardar el agente SIN código MAE no pisa el que ya tiene
    # (el catálogo se completa de a poco; para borrarlo, quitar y re-cargar).
    _exec(
        "INSERT INTO operaciones.senebis_agentes "
        "(nombre, numero, codigo_mae, creado_por, creado_at, actualizado_por, actualizado_at) "
        "VALUES (%(n)s, %(num)s, %(cod)s, %(por)s, %(at)s, %(por)s, %(at)s) "
        "ON CONFLICT (nombre) DO UPDATE SET numero = EXCLUDED.numero, "
        "codigo_mae = COALESCE(EXCLUDED.codigo_mae, senebis_agentes.codigo_mae), "
        "actualizado_por = EXCLUDED.actualizado_por, actualizado_at = EXCLUDED.actualizado_at",
        {"n": n, "num": num, "cod": cod, "por": por, "at": at},
    )
    _audit(actor, "upsert_agente", n, {"numero": num, "codigo_mae": cod})
    return {"nombre": n, "numero": num, "codigo_mae": cod}


def quitar_agente(nombre: str, actor: str) -> dict:
    n = (nombre or "").strip().upper()
    if not n:
        raise ValueError("falta 'nombre'")
    borrado = _exec("DELETE FROM operaciones.senebis_agentes WHERE nombre = %(n)s", {"n": n})
    _audit(actor, "remove_agente", n, {})
    return {"borrado": borrado}


# ─────────────────────────────────────────────────────────────
# Segmentos MAE (catálogo de la columna SEGMENTO del Excel MAE)
# ─────────────────────────────────────────────────────────────

def listar_segmentos() -> list[dict]:
    """El catálogo, con el default marcado para que el front lo preseleccione
    sin duplicar la constante del lado del navegador."""
    return [{"nombre": r["nombre"], "es_default": r["nombre"] == SEGMENTO_MAE_DEFAULT}
            for r in _q("SELECT nombre FROM operaciones.senebis_segmentos ORDER BY nombre")]


def upsert_segmento(nombre: str, actor: str) -> dict:
    """Alta de un segmento. A diferencia del agente NO se normaliza a mayúsculas:
    el nombre viaja TAL CUAL a la columna SEGMENTO del Excel MAE, y ahí el
    sistema destino espera 'Bilateral MAEClear', no 'BILATERAL MAECLEAR'."""
    n = (nombre or "").strip()
    if not n:
        raise ValueError("falta 'nombre'")
    at = datetime.now(UTC)
    por = (actor or "").lower() or None
    _exec(
        "INSERT INTO operaciones.senebis_segmentos "
        "(nombre, creado_por, creado_at, actualizado_por, actualizado_at) "
        "VALUES (%(n)s, %(por)s, %(at)s, %(por)s, %(at)s) "
        "ON CONFLICT (nombre) DO UPDATE SET "
        "actualizado_por = EXCLUDED.actualizado_por, actualizado_at = EXCLUDED.actualizado_at",
        {"n": n, "por": por, "at": at},
    )
    _audit(actor, "upsert_segmento", n, {})
    return {"nombre": n}


def quitar_segmento(nombre: str, actor: str) -> dict:
    """Baja del catálogo. El DEFAULT no se puede borrar: sin él las órdenes MAE
    nuevas nacerían con un segmento que ya no existe. Las órdenes viejas que
    usaban un segmento borrado NO se tocan — guardan el texto, no un id."""
    n = (nombre or "").strip()
    if not n:
        raise ValueError("falta 'nombre'")
    if n == SEGMENTO_MAE_DEFAULT:
        raise ValueError(
            f"{n!r} es el segmento por defecto de las órdenes MAE y no se puede borrar")
    borrado = _exec("DELETE FROM operaciones.senebis_segmentos WHERE nombre = %(n)s", {"n": n})
    _audit(actor, "remove_segmento", n, {})
    return {"borrado": borrado}


def opciones(email: str = "") -> dict:
    """Opciones del form de carga: catálogo de agentes (desplegable del
    externo) + valores válidos. Marca presencia del caller."""
    if email:
        marcar_presencia(email)
    return {
        "agentes": listar_agentes(),
        # Catálogo de la columna SEGMENTO del Excel MAE + con cuál nace una orden
        # MAE. El front no hardcodea el default: lo lee de acá.
        "segmentos": listar_segmentos(),
        "segmento_default": SEGMENTO_MAE_DEFAULT,
        "tipos_contraparte": list(TIPOS_CONTRAPARTE),
        "plazos": list(PLAZOS),
        "conectados": conectados(),
        # Cargar/editar/borrar órdenes: allowlist PROPIA de SENEBIS (+ admin),
        # se gestiona en Manager → MESA. El front esconde la edición sin esto,
        # pero el enforcement real es server-side en cada write del router.
        "puede_escribir": puede_escribir(email),
        # Mover la secuencia de IDs es SOLO admin: desalinearla rompe la
        # numeración que espeja Quantex para todo el mundo.
        "es_admin": es_admin(email),
    }


def es_admin(email: str) -> bool:
    e = (email or "").lower().strip()
    if not e:
        return False
    from core.roles import get_user_role
    return get_user_role(e) == "admin"


# ──────────────────────────────────────────────────────────
# Allowlist de ESCRITURA (Manager → MESA). Separada de la de Mesa de Dinero:
# los que cargan senebis no son necesariamente los que registran la mesa.
# ──────────────────────────────────────────────────────────

def puede_escribir(email: str) -> bool:
    """True si puede crear/editar/borrar órdenes SENEBIS. Default-deny."""
    e = (email or "").lower().strip()
    if not e:
        return False
    if es_admin(e):
        return True
    return bool(_q("SELECT 1 FROM operaciones.senebis_escritores WHERE email = %(e)s",
                   {"e": e}))


def listar_escritores() -> dict:
    rows = _q("SELECT email, agregado_por, agregado_at FROM operaciones.senebis_escritores "
              "ORDER BY email")
    return {"escritores": [
        {"email": r["email"], "agregado_por": r["agregado_por"],
         "agregado_at": r["agregado_at"].isoformat() if r["agregado_at"] else None}
        for r in rows
    ]}


def candidatos_escritores(q: str = "", limit: int = 30) -> dict:
    """Usuarios de la app que todavía no están en la allowlist de SENEBIS."""
    rows = _q(
        "SELECT u.email, u.role FROM manager.manager_users u "
        "WHERE u.email NOT IN (SELECT email FROM operaciones.senebis_escritores) "
        "AND u.email ILIKE %(t)s ORDER BY u.email LIMIT %(lim)s",
        {"t": f"%{(q or '').strip()}%", "lim": limit},
    )
    return {"candidatos": rows}


def agregar_escritor(email: str, actor: str) -> dict:
    e = (email or "").lower().strip()
    if not e:
        raise ValueError("falta 'email'")
    _exec(
        "INSERT INTO operaciones.senebis_escritores (email, agregado_por, agregado_at) "
        "VALUES (%(e)s, %(por)s, %(at)s) ON CONFLICT (email) DO NOTHING",
        {"e": e, "por": (actor or "").lower() or None, "at": datetime.now(UTC)},
    )
    _audit(actor, "add_escritor", e, {})
    return {"email": e}


def quitar_escritor(email: str, actor: str) -> dict:
    e = (email or "").lower().strip()
    if not e:
        raise ValueError("falta 'email'")
    borrado = _exec("DELETE FROM operaciones.senebis_escritores WHERE email = %(e)s", {"e": e})
    _audit(actor, "remove_escritor", e, {})
    return {"borrado": borrado}


# ─────────────────────────────────────────────────────────────
# Comitentes (clientes de la ALyC) — búsqueda y resolución
# ─────────────────────────────────────────────────────────────

def _denominacion_limpia(d: str | None) -> str | None:
    """clientes.cuentas.denominacion viene '[805] NOMBRE' o 'NOMBRE' a secas."""
    s = (d or "").strip()
    if s.startswith("[") and "]" in s:
        s = s.split("]", 1)[1].strip()
    return s or None


def buscar_comitentes(q: str = "", limit: int = 20) -> list[dict]:
    """Autocomplete del form (interno): matchea por NÚMERO de cuenta o por
    DENOMINACIÓN, para que el trader cargue con lo que sepa de la cuenta."""
    term = (q or "").strip()
    if not term:
        return []
    rows = _q(
        "SELECT id_cuenta, denominacion FROM clientes.cuentas "
        "WHERE id_cuenta ILIKE %(pref)s OR denominacion ILIKE %(sub)s "
        "ORDER BY id_cuenta LIMIT %(lim)s",
        {"pref": f"{term}%", "sub": f"%{term}%", "lim": int(limit)},
    )
    return [{"id_cuenta": r["id_cuenta"],
             "denominacion": _denominacion_limpia(r["denominacion"])} for r in rows]


def _buscar_cuenta_exacta(term: str) -> dict | None:
    """Cuenta cuyo id_cuenta O denominación matchea EXACTO (case-insensitive).
    Devuelve {id_cuenta, denominacion} o None si no hay match único."""
    rows = _q(
        "SELECT id_cuenta, denominacion FROM clientes.cuentas "
        "WHERE id_cuenta = %(t)s "
        "   OR upper(denominacion) = upper(%(t)s) "
        "   OR upper(denominacion) LIKE upper(%(brack)s) LIMIT 2",
        {"t": term, "brack": f"[%] {term}"},
    )
    if len(rows) != 1:
        return None  # 0 = no existe; 2+ = ambiguo → se guarda lo tipeado tal cual
    return {"id_cuenta": rows[0]["id_cuenta"],
            "denominacion": _denominacion_limpia(rows[0]["denominacion"])}


def _resolver_interno(cc: str | None) -> tuple[str | None, str | None]:
    """(cc, cc_denominacion) para un senebi interno: si lo tipeado matchea una
    cuenta (por número o denominación) se normaliza al NÚMERO + snapshot de la
    denominación; si no matchea queda lo tipeado tal cual (texto libre)."""
    term = (str(cc or "")).strip()
    if not term:
        return None, None
    hit = _buscar_cuenta_exacta(term)
    if hit:
        return hit["id_cuenta"], hit["denominacion"]
    return term, None


# ─────────────────────────────────────────────────────────────
# Órdenes (CRUD + estado)
# ─────────────────────────────────────────────────────────────

def _fila_op(r: dict) -> dict:
    return {
        "id": r["id"],
        "operacion": r["operacion"],
        "concertacion": r["concertacion"].isoformat() if r["concertacion"] else None,
        "liquidacion": r["liquidacion"].isoformat() if r["liquidacion"] else None,
        "plazo": r["plazo"],
        "especie": r["especie"],
        "vn": _f(r["vn"]), "px": _f(r["px"]), "monto": _f(r["monto"]),
        "cp": r["cp"], "cc": r["cc"], "cc_denominacion": r["cc_denominacion"],
        "contraparte": r["contraparte"], "nro_contraparte": r["nro_contraparte"],
        "mercado": r["mercado"],
        # bool(): tolera filas viejas con texto libre pre-migración a boolean.
        "cargan_ellos": bool(r["cargan_ellos"]), "tipo": r["tipo"],
        "tipo_contraparte": r["tipo_contraparte"],
        "agente": r["agente"], "agente_numero": r["agente_numero"],
        "es_mae": bool(r["es_mae"]),
        # SEGMENTO del Excel MAE. .get() + fallback: una orden MAE cargada ANTES
        # de que existiera el campo lo tiene en NULL y tiene que verse igual que
        # sale en el Excel, no vacía.
        "segmento": (r.get("segmento") or (SEGMENTO_MAE_DEFAULT if r["es_mae"] else None)),
        # .get(): tolera un deploy de código anterior al apply_schema.
        "campos_editados": list(r.get("campos_editados") or []),
        "editada_completada": bool(r.get("editada_completada")),
        # Tilde propia de la tab EXCEL MAE (la pone el TRADER) — NO es `estado`.
        "mae_completada": bool(r.get("mae_completada")),
        "mae_completada_por": r.get("mae_completada_por"),
        "mae_completada_at": (r["mae_completada_at"].isoformat()
                              if r.get("mae_completada_at") else None),
        "mae_editada_completada": bool(r.get("mae_editada_completada")),
        "estado": r["estado"],
        "completada_por": r["completada_por"],
        "completada_at": r["completada_at"].isoformat() if r["completada_at"] else None,
        "creado_por": r["creado_por"],
        "creado_at": r["creado_at"].isoformat() if r["creado_at"] else None,
        "actualizado_por": r["actualizado_por"],
    }


def listar_ops(desde: str | None = None, hasta: str | None = None,
               estado: str | None = None, especie: str | None = None,
               mae: str | None = None, email: str = "") -> dict:
    """Órdenes del período (default: todas), más nuevas primero, + presencia.

    Cada lectura marca presencia del caller — pollear la lista ES el heartbeat:
    el front no necesita un ping aparte mientras la vista esté abierta."""
    if email:
        marcar_presencia(email)
    conds, params = [], {}
    if desde:
        conds.append("concertacion >= %(desde)s")
        params["desde"] = desde
    if hasta:
        conds.append("concertacion <= %(hasta)s")
        params["hasta"] = hasta
    if estado:
        if estado not in ESTADOS:
            raise ValueError(f"estado {estado!r} inválido: {' | '.join(ESTADOS)}")
        conds.append("estado = %(estado)s")
        params["estado"] = estado
    if especie:
        conds.append("especie ILIKE %(especie)s")
        params["especie"] = f"%{especie.strip()}%"
    if mae:
        if mae not in FILTROS_MAE:
            raise ValueError(f"mae {mae!r} inválido: {' | '.join(FILTROS_MAE)}")
        conds.append("es_mae = %(mae)s")
        params["mae"] = (mae == "solo")
    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    rows = _q(
        f"SELECT * FROM operaciones.senebis {where} ORDER BY concertacion DESC, id DESC",
        params,
    )
    pendientes = sum(1 for r in rows if r["estado"] == "pendiente")
    return {
        "total": len(rows),
        "pendientes": pendientes,
        "ordenes": [_fila_op(r) for r in rows],
        "conectados": conectados(),
    }


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    return float(v)


def _derivar_monto(p: dict) -> float | None:
    """monto = vn × px / 100 (px viene cada 100 VN). Si viene `monto` explícito
    en el payload, se respeta (override manual)."""
    manual = _num(p.get("monto"))
    if manual is not None:
        return manual
    vn, px = _num(p.get("vn")), _num(p.get("px"))
    if vn is not None and px is not None:
        return vn * px / 100
    return None


def _norm_cargan_ellos(v: Any) -> bool:
    """SI/NO. Tolera el payload viejo de texto libre mientras conviven front y
    back deployados en distinto momento: ''/'no'/'false' → False, otro texto
    (algo anotado ahí significaba que cargaban ellos) → True."""
    if isinstance(v, str):
        return v.strip().lower() not in ("", "no", "false", "0")
    return bool(v)


def _norm_plazo(v: Any) -> str | None:
    """'ci'/'CI' → 'CI'; '24'/'24hs'/'24HS' → '24'. Otro valor → ValueError."""
    s = str(v or "").strip().upper().replace("HS", "").replace(" ", "")
    if not s:
        return None
    if s == "CI":
        return "CI"
    if s == "24":
        return "24"
    raise ValueError(f"plazo {v!r} inválido: CI | 24")


def _completar_fechas(p: dict) -> dict:
    """Defaults + inferencia plazo ↔ liquidacion. Devuelve
    {concertacion, liquidacion, plazo} listos para persistir.

    - concertacion: default HOY (ART).
    - plazo sin liquidacion: CI → liq = concertacion; 24 → próximo hábil.
    - liquidacion sin plazo: mismo día → CI; posterior → 24.
    - ninguno: CI liquidando hoy (el caso más común de la mesa; editable).
    """
    conc = p.get("concertacion") or datetime.now(_TZ_AR).date().isoformat()
    conc_d = date.fromisoformat(str(conc))
    plazo = _norm_plazo(p.get("plazo"))
    liq = p.get("liquidacion") or None
    if liq:
        liq_d = date.fromisoformat(str(liq))
        if liq_d < conc_d:
            raise ValueError("'liquidacion' no puede ser anterior a 'concertacion'")
        if plazo is None:
            plazo = "CI" if liq_d == conc_d else "24"
    else:
        if plazo is None:
            plazo = "CI"
        liq_d = conc_d if plazo == "CI" else proximo_habil(conc_d)
    return {"concertacion": conc_d.isoformat(), "liquidacion": liq_d.isoformat(),
            "plazo": plazo}


def _validar(p: dict) -> None:
    op = (p.get("operacion") or "").strip().upper()
    if op not in ("COMPRA", "VENTA"):
        raise ValueError("'operacion' tiene que ser COMPRA o VENTA")
    if not (p.get("especie") or "").strip():
        raise ValueError("falta 'especie'")
    tc = (p.get("tipo_contraparte") or "interno").strip().lower()
    if tc not in TIPOS_CONTRAPARTE:
        raise ValueError(
            f"tipo_contraparte {tc!r} inválido: {' | '.join(TIPOS_CONTRAPARTE)}")
    if tc == "externo" and not (p.get("agente") or "").strip():
        raise ValueError("senebi externo: falta 'agente' (elegirlo del catálogo)")
    # El SEGMENTO va tal cual a la columna del Excel MAE: si no está en el
    # catálogo, el sistema destino lo rechaza. Se valida solo cuando la orden es
    # MAE y trae uno explícito — vacío significa "el default", no un error.
    seg = (p.get("segmento") or "").strip()
    if p.get("es_mae") and seg and seg not in {x["nombre"] for x in listar_segmentos()}:
        raise ValueError(
            f"segmento {seg!r} no está en el catálogo — se agrega desde el botón "
            "SEGMENTOS MAE de la vista SENEBIS")


def _row_de_payload(p: dict, actor: str) -> dict:
    fechas = _completar_fechas(p)
    tc = (p.get("tipo_contraparte") or "interno").strip().lower()
    if tc == "externo":
        agente = (p.get("agente") or "").strip().upper()
        numero = _numero_agente(agente)
        if numero is None:
            raise ValueError(
                f"agente {agente!r} no está en el catálogo — el back office lo "
                "agrega con su número desde la vista SENEBIS")
        cc, cc_den = None, None
    else:
        agente, numero = None, None
        cc, cc_den = _resolver_interno(p.get("cc"))
    es_mae = bool(p.get("es_mae"))
    # MAE se carga en el MAE, no en Quantex: el TIPO queda fijo 'MAE' (marca
    # visible en la vista) y el export/espejo la excluyen.
    tipo = "MAE" if es_mae else ((p.get("tipo") or "").strip() or None)
    return {
        "operacion": (p["operacion"] or "").strip().upper(),
        **fechas,
        "especie": (p.get("especie") or "").strip().upper(),
        "vn": _num(p.get("vn")), "px": _num(p.get("px")),
        "monto": _derivar_monto(p),
        "cp": (str(p.get("cp") or "").strip()) or "255",   # default cartera propia 255
        "cc": cc, "cc_denominacion": cc_den,
        "contraparte": (p.get("contraparte") or "").strip() or None,
        "nro_contraparte": (str(p.get("nro_contraparte") or "").strip()) or None,
        "mercado": (p.get("mercado") or "").strip().upper() or None,
        "cargan_ellos": _norm_cargan_ellos(p.get("cargan_ellos")),
        "tipo": tipo,
        "tipo_contraparte": tc, "agente": agente, "agente_numero": numero,
        "es_mae": es_mae,
        # Solo tiene sentido en una orden MAE (es una columna del Excel MAE): en
        # una orden Quantex se guarda NULL aunque venga en el payload.
        "segmento": ((p.get("segmento") or "").strip() or SEGMENTO_MAE_DEFAULT)
                    if es_mae else None,
        "por": (actor or "").lower() or None, "at": datetime.now(UTC),
    }


def crear_op(payload: dict, actor: str) -> dict:
    p = {k: payload.get(k) for k in _CAMPOS_OP}
    _validar(p)
    row = _row_de_payload(p, actor)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO operaciones.senebis "
            "(operacion, concertacion, liquidacion, plazo, especie, vn, px, monto, "
            " cp, cc, cc_denominacion, contraparte, nro_contraparte, mercado, "
            " cargan_ellos, tipo, tipo_contraparte, agente, agente_numero, es_mae, "
            " segmento, estado, creado_por, creado_at, actualizado_por, actualizado_at) "
            "VALUES (%(operacion)s, %(concertacion)s, %(liquidacion)s, %(plazo)s, "
            " %(especie)s, %(vn)s, %(px)s, %(monto)s, %(cp)s, %(cc)s, "
            " %(cc_denominacion)s, %(contraparte)s, %(nro_contraparte)s, %(mercado)s, "
            " %(cargan_ellos)s, %(tipo)s, %(tipo_contraparte)s, %(agente)s, "
            " %(agente_numero)s, %(es_mae)s, %(segmento)s, 'pendiente', "
            " %(por)s, %(at)s, %(por)s, %(at)s) "
            "RETURNING id",
            row,
        )
        op_id = cur.fetchone()[0]
        conn.commit()
    _audit(actor, "create_op", str(op_id), {"after": row})
    return _get_op(op_id)


def _get_op(op_id: int) -> dict:
    rows = _q("SELECT * FROM operaciones.senebis WHERE id = %(id)s", {"id": op_id})
    if not rows:
        raise ValueError(f"orden {op_id} no existe")
    return _fila_op(rows[0])


def _diff_campos(before: dict, after: dict) -> list[str]:
    """Qué campos de la orden cambiaron (lógica pura, sobre los valores ya
    persistidos — no sobre el payload, que el front echoa entero)."""
    return [k for k in _CAMPOS_OP if before.get(k) != after.get(k)]


def _marcar_edicion(op_id: int, before: dict, after: dict) -> dict:
    """Marcas de edición persistentes (el amarillo que se pintaba a mano).

    Acumula los campos tocados y, si la orden ya estaba 'completada', prende
    editada_completada: el back office ya la cargó en Quantex y tiene que
    revisarla.

    La tab EXCEL MAE tiene su ESPEJO de esa marca (`mae_editada_completada`,
    2026-08-13): se prende si se editó una orden que ya estaba TILDADA ahí —
    el MAE quedó cargado con los datos viejos y hay que corregirlo a mano.
    Son dos marcas y no una porque las baja OTRO equipo cada una (el back
    office mira Quantex, el trader mira el MAE) y el visto de uno no puede
    tapar el del otro. La orden NO vuelve al Excel: igual que en Quantex, lo
    ya cargado se corrige del otro lado, no se re-exporta."""
    cambios = _diff_campos(before, after)
    if not cambios:
        return after
    acumulado = sorted(set(before.get("campos_editados") or []) | set(cambios))
    flag = bool(before.get("editada_completada")) or before["estado"] == "completada"
    flag_mae = bool(before.get("mae_editada_completada")) or bool(before.get("mae_completada"))
    _exec(
        "UPDATE operaciones.senebis SET campos_editados=%(campos)s, "
        "editada_completada=%(flag)s, mae_editada_completada=%(flag_mae)s "
        "WHERE id=%(id)s",
        {"id": op_id, "campos": acumulado, "flag": flag, "flag_mae": flag_mae},
    )
    return _get_op(op_id)


def editar_op(op_id: int, payload: dict, actor: str) -> dict:
    before = _get_op(op_id)
    # Merge: lo que no viene en el payload se mantiene; el monto se recalcula
    # salvo que venga un override manual REAL (distinto del monto ya guardado —
    # si el front solo echoa la fila, se re-deriva con los vn/px nuevos).
    p = {k: (payload[k] if k in payload else before.get(k)) for k in _CAMPOS_OP}
    if _num(p.get("monto")) == before.get("monto"):
        p["monto"] = None
    _validar(p)
    row = {"id": op_id, **_row_de_payload(p, actor)}
    _exec(
        "UPDATE operaciones.senebis SET operacion=%(operacion)s, "
        "concertacion=%(concertacion)s, liquidacion=%(liquidacion)s, "
        "plazo=%(plazo)s, especie=%(especie)s, vn=%(vn)s, px=%(px)s, "
        "monto=%(monto)s, cp=%(cp)s, cc=%(cc)s, cc_denominacion=%(cc_denominacion)s, "
        "contraparte=%(contraparte)s, "
        "nro_contraparte=%(nro_contraparte)s, mercado=%(mercado)s, "
        "cargan_ellos=%(cargan_ellos)s, tipo=%(tipo)s, "
        "tipo_contraparte=%(tipo_contraparte)s, agente=%(agente)s, "
        "agente_numero=%(agente_numero)s, es_mae=%(es_mae)s, segmento=%(segmento)s, "
        "actualizado_por=%(por)s, actualizado_at=%(at)s WHERE id=%(id)s",
        row,
    )
    after = _get_op(op_id)
    after = _marcar_edicion(op_id, before, after)
    _audit(actor, "update_op", str(op_id), {"before": before, "after": after})
    return after


def limpiar_marcas(op_id: int, actor: str) -> dict:
    """"Visto" de QUANTEX: borra las marcas de edición de una orden (el back
    office ya la revisó/corrigió en Quantex). No toca los datos, ni el estado,
    ni la marca del MAE — esa la baja el trader desde su tab."""
    before = _get_op(op_id)
    _exec(
        "UPDATE operaciones.senebis SET campos_editados='{}', "
        "editada_completada=false WHERE id=%(id)s",
        {"id": op_id},
    )
    _audit(actor, "limpiar_marcas", str(op_id),
           {"before": {"campos_editados": before["campos_editados"],
                       "editada_completada": before["editada_completada"]}})
    return _get_op(op_id)


def limpiar_marcas_mae(op_id: int, actor: str) -> dict:
    """"Visto" del MAE: baja el amarillo de una orden tildada que se editó
    después (el trader ya la corrigió en el MAE). Espejo de limpiar_marcas,
    con su propia marca: el visto del back office no puede bajar el del
    trader ni al revés. No toca los datos, ni el estado, ni la tilde."""
    before = _get_op(op_id)
    _exec(
        "UPDATE operaciones.senebis SET mae_editada_completada=false WHERE id=%(id)s",
        {"id": op_id},
    )
    _audit(actor, "limpiar_marcas_mae", str(op_id),
           {"before": {"mae_editada_completada": before["mae_editada_completada"]}})
    return _get_op(op_id)


def borrar_op(op_id: int, actor: str) -> dict:
    before = _get_op(op_id)
    n = _exec("DELETE FROM operaciones.senebis WHERE id = %(id)s", {"id": op_id})
    _audit(actor, "delete_op", str(op_id), {"before": before})
    return {"borrado": n}


def set_estado(op_id: int, estado: str, actor: str) -> dict:
    """Marca 'completada' (con quién y cuándo) o vuelve a 'pendiente'.

    Robustez (2026-08-05): el estado de una orden de un DÍA ANTERIOR no se
    toca — navegando el histórico (FECHA → TODO) un click de más no debe
    mover nada. Solo admin puede (corrección consciente, queda auditada)."""
    if estado not in ESTADOS:
        raise ValueError(f"estado {estado!r} inválido: {' | '.join(ESTADOS)}")
    before = _get_op(op_id)
    if before["estado"] == estado:
        return before  # idempotente: dos clicks simultáneos no duplican audit
    conc = date.fromisoformat(before["concertacion"])
    if conc < datetime.now(_TZ_AR).date() and not es_admin(actor):
        raise ValueError(
            f"la orden #{op_id} es del {conc.strftime('%d/%m/%Y')}: el estado de "
            "días anteriores no se cambia (solo un admin puede corregirlo)")
    por = (actor or "").lower() or None
    at = datetime.now(UTC)
    _exec(
        "UPDATE operaciones.senebis SET estado=%(estado)s, "
        "completada_por=%(comp_por)s, completada_at=%(comp_at)s, "
        "actualizado_por=%(por)s, actualizado_at=%(at)s WHERE id=%(id)s",
        {"id": op_id, "estado": estado,
         "comp_por": por if estado == "completada" else None,
         "comp_at": at if estado == "completada" else None,
         "por": por, "at": at},
    )
    after = _get_op(op_id)
    _audit(actor, "set_estado", str(op_id), {"before": before, "after": after})
    return after


def set_mae_completada(op_id: int, completada: bool, actor: str) -> dict:
    """Tilde de la tab EXCEL MAE: 'ya la cargué en el MAE'.

    Es OTRA cosa que `estado` (2026-08-13): `estado` lo mueve el BACK OFFICE
    por su carga en Quantex y no puede gobernar el archivo del MAE, que lo
    genera el TRADER. Tildar saca la orden del .xlsx (que se genera varias
    veces por día) pero la deja visible y grisada en la tab, así se puede
    destildar. NO toca `estado` ni los datos de la orden.

    Sin corte por fecha, a diferencia de set_estado: una orden vieja que quedó
    sin tildar sigue entrando al archivo, y sacarla de ahí es justamente para
    lo que existe la tilde.

    DESTILDAR baja también el amarillo de `mae_editada_completada`: ese aviso
    dice "el MAE quedó con los datos viejos", y al destildar la orden vuelve
    al Excel y se carga de nuevo con los datos buenos → el aviso ya no aplica.
    (Tildar de nuevo NO lo revive: la marca la prende una EDICIÓN posterior.)"""
    before = _get_op(op_id)
    if not before["es_mae"]:
        raise ValueError(
            f"la orden #{op_id} no es MAE — la tilde es de la tab EXCEL MAE "
            "(el resto se completa desde la lista de órdenes)")
    if bool(before["mae_completada"]) == bool(completada):
        return before  # idempotente: dos clicks simultáneos no duplican audit
    por = (actor or "").lower() or None
    at = datetime.now(UTC)
    _exec(
        "UPDATE operaciones.senebis SET mae_completada=%(mae)s, "
        "mae_completada_por=%(mae_por)s, mae_completada_at=%(mae_at)s, "
        "mae_editada_completada=(mae_editada_completada AND %(mae)s) "
        "WHERE id=%(id)s",
        {"id": op_id, "mae": bool(completada),
         "mae_por": por if completada else None,
         "mae_at": at if completada else None},
    )
    after = _get_op(op_id)
    _audit(actor, "set_mae_completada", str(op_id),
           {"before": before["mae_completada"], "after": after["mae_completada"]})
    return after


# ─────────────────────────────────────────────────────────────
# Próximo ID (la secuencia global que espeja la numeración Quantex)
# ─────────────────────────────────────────────────────────────
# Mientras conviven la app y el Excel viejo, la numeración real avanza AFUERA
# y la app no puede verla → el contador se muestra SIEMPRE en la vista y el
# back office lo alinea (último ID del Excel viejo + 1) antes de arrancar.

def proximo_id() -> int:
    """Próximo ID que va a asignar la identity de operaciones.senebis, SIN
    consumirlo. El nombre de la secuencia sale de pg (no es input de usuario)."""
    seq = _q("SELECT pg_get_serial_sequence('operaciones.senebis','id') AS s")[0]["s"]
    r = _q(f"SELECT last_value, is_called FROM {seq}")[0]
    return int(r["last_value"]) + (1 if r["is_called"] else 0)


def set_proximo_id(siguiente: int, actor: str) -> dict:
    """Fija el próximo ID (para alinear con la numeración del Excel viejo).
    Solo mueve la secuencia — no toca filas — y se niega a retroceder por
    debajo del máximo ID ya cargado (PK duplicada en el próximo insert)."""
    n = int(siguiente)
    max_id = int(_q("SELECT COALESCE(MAX(id), 0) AS m FROM operaciones.senebis")[0]["m"])
    if n <= max_id:
        raise ValueError(
            f"el próximo ID ({n}) tiene que ser mayor al último ya cargado ({max_id})")
    before = proximo_id()
    _exec(f"ALTER TABLE operaciones.senebis ALTER COLUMN id RESTART WITH {n}", {})
    _audit(actor, "set_proximo_id", str(n), {"before": before, "after": n})
    return {"proximo_id": n}


def reasignar_id(op_id: int, actor: str) -> dict:
    """Le da a la orden el siguiente ID libre y QUEMA el anterior.

    Quantex consume el número al cargar el archivo, aunque después la orden
    falle por mercado: reintentar con el mismo ID lo rechaza. El viejo queda
    muerto a propósito — es exactamente lo que pasó del otro lado. NO se
    renumera nada más: las otras órdenes ya viajaron con su número.
    """
    before = _get_op(op_id)
    if before["estado"] != "pendiente":
        raise ValueError(
            "solo se reasigna el ID de una orden PENDIENTE — la completada ya "
            "entró en Quantex con su número")
    max_id = int(_q("SELECT COALESCE(MAX(id), 0) AS m FROM operaciones.senebis")[0]["m"])
    nuevo = max(proximo_id(), max_id + 1)
    _exec(
        "UPDATE operaciones.senebis SET id = %(nuevo)s, actualizado_por = %(por)s, "
        "actualizado_at = %(at)s WHERE id = %(old)s",
        {"nuevo": nuevo, "old": op_id, "por": (actor or "").lower() or None,
         "at": datetime.now(UTC)},
    )
    _exec(f"ALTER TABLE operaciones.senebis ALTER COLUMN id RESTART WITH {nuevo + 1}", {})
    _audit(actor, "reasignar_id", str(op_id), {"before": op_id, "after": nuevo})
    return _get_op(nuevo)


# ─────────────────────────────────────────────────────────────
# Export Excel (el archivo que se carga en el sistema destino)
# ─────────────────────────────────────────────────────────────

def _numero_si_se_puede(v: Any) -> Any:
    """El sistema destino espera número en CONTRAPARTE/COMITENTE/CARTERA PROPIA;
    si el valor es texto (cuenta sin resolver) queda texto y el back office lo
    corrige a mano."""
    if isinstance(v, str) and v.isdigit():
        return int(v)
    return v


def _fila_export(o: dict) -> list:
    """Una orden → los 10 valores del Excel destino. Acá viven las reglas de
    contraparte (2026-08-05):
        externo             → COMITENTE vacío, CONTRAPARTE = número del agente.
        interno GARANTIZADO → COMITENTE = cp (cartera propia), CONTRAPARTE vacío.
        interno resto       → COMITENTE = cc, CONTRAPARTE vacío."""
    if o["tipo_contraparte"] == "externo":
        contraparte = _numero_si_se_puede(o["agente_numero"])
        comitente = None
    else:
        contraparte = None
        fuente = o["cp"] if (o["mercado"] or "") == "GARANTIZADO" else o["cc"]
        comitente = _numero_si_se_puede(fuente)
    return [o["id"], o["operacion"], o["especie"], o["plazo"], o["px"], o["vn"],
            contraparte, comitente, _numero_si_se_puede(o["cp"]), o["mercado"]]


def _filas_quantex(ordenes: list[dict]) -> list[dict]:
    """Qué entra al Excel Quantex (espejo Y archivo — UNA sola regla): SOLO
    'pendiente', no-MAE y que NO carguen ellos. El back office sube el archivo
    varias veces por día: lo completado YA está cargado en Quantex
    (re-exportarlo lo duplicaría), lo MAE se carga en el MAE y lo que cargan
    ellos lo carga la contraparte."""
    return sorted((o for o in ordenes if o["estado"] == "pendiente"
                   and not o["es_mae"] and not o["cargan_ellos"]),
                  key=lambda o: o["id"])


def excel_preview(desde: str | None = None, hasta: str | None = None,
                  email: str = "") -> dict:
    """Espejo EN VIVO del Excel destino para la tab EXCEL QUANTEX: mismas filas
    y mismas reglas que export_xlsx (una sola fuente de verdad: _fila_export +
    _filas_quantex), en JSON."""
    data = listar_ops(desde=desde, hasta=hasta, email=email)
    ordenes = _filas_quantex(data["ordenes"])
    return {
        "headers": list(_HEADERS_XLSX),
        "filas": [{"id": o["id"], "estado": o["estado"], "valores": _fila_export(o)}
                  for o in ordenes],
        "conectados": data["conectados"],
        # Siempre visible para detectar el desfase con el Excel viejo a ojo.
        "proximo_id": proximo_id(),
    }


def _armar_xlsx(headers: tuple, filas: list[list], titulo: str, anchos: tuple) -> bytes:
    """Workbook estándar de los exports senebis: header azul, freeze, anchos."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError as e:  # lazy: sin openpyxl la API sigue viva (REGLA #1)
        raise RuntimeError(
            "openpyxl no está instalado — correr `pip install -r requirements.txt` "
            "en el venv del Droplet") from e
    wb = Workbook()
    ws = wb.active
    ws.title = titulo
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F4E79")
    for col, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
    ws.freeze_panes = "A2"
    for i, valores in enumerate(filas, start=2):
        for col, valor in enumerate(valores, start=1):
            ws.cell(row=i, column=col, value=valor)
    for col, ancho in enumerate(anchos, start=1):
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = ancho
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_xlsx(desde: str | None = None, hasta: str | None = None) -> tuple[bytes, str]:
    """Devuelve (bytes del .xlsx, nombre de archivo), columnas EXACTAS del
    sistema destino (ID primero), más viejas primero (por ID asc = orden de
    carga). SOLO pendientes no-MAE que no carguen ellos (_filas_quantex) — el
    archivo se sube varias veces por día y lo completado ya está cargado."""
    data = listar_ops(desde=desde, hasta=hasta)
    ordenes = _filas_quantex(data["ordenes"])
    contenido = _armar_xlsx(
        _HEADERS_XLSX, [_fila_export(o) for o in ordenes], "SENEBIS",
        anchos=(8, 12, 14, 8, 14, 16, 16, 14, 15, 18))
    hoy = datetime.now(_TZ_AR).strftime("%Y%m%d")
    return contenido, f"senebis_{hoy}.xlsx"


# ─────────────────────────────────────────────────────────────
# Excel MAE (las órdenes es_mae, que NO van a Quantex)
# ─────────────────────────────────────────────────────────────

def _filas_mae(ordenes: list[dict], incluir_completadas: bool = False) -> list[dict]:
    """Qué entra al Excel MAE. Base: es_mae, que no carguen ellos y que siga
    'pendiente' (misma lógica temporal que Quantex).

    Encima de eso manda la TILDE PROPIA de la tab (`mae_completada`, 2026-08-13):
    el trader marca la orden cuando ya la cargó en el MAE y deja de salir en el
    ARCHIVO — que se genera varias veces por día, así no re-carga lo ya cargado.
    En el ESPEJO (incluir_completadas=True) la tildada SIGUE visible, grisada,
    para poder destildarla. Es independiente de `estado` a propósito: ese es el
    tablero del BACK OFFICE y no tiene por qué gobernar el archivo del MAE."""
    def _entra(o: dict) -> bool:
        if not (o["es_mae"] and not o["cargan_ellos"]):
            return False
        # .get(): tolera un deploy de código anterior al apply_schema.
        if o.get("mae_completada"):
            return incluir_completadas
        return o["estado"] == "pendiente"
    return sorted((o for o in ordenes if _entra(o)), key=lambda o: o["id"])


def _destino_con_letra(codigo: str | None, tipo_contraparte: str) -> str | None:
    """La LETRA del destino la pone el SISTEMA según la orden — el usuario
    carga solo el NÚMERO (ej. '062'): interno → F (fondo, el flujo grande),
    externo → A (agente). Si lo cargado no es puramente numérico se respeta
    tal cual (deja pasar C+CUIT / SXXX hasta afinar esa distinción)."""
    s = str(codigo or "").strip()
    if not s:
        return None
    if not s.isdigit():
        return s
    return ("A" if tipo_contraparte == "externo" else "F") + s


def _destinos_mae(ordenes: list[dict]) -> dict[int, str | None]:
    """DESTINO por orden, resuelto EN VIVO contra la base (2 queries batch):
        interno → clientes.contrapartes.codigo_mae por la cc → 'F' + número
        externo → senebis_agentes.codigo_mae por el nombre  → 'A' + número
    (la letra la agrega _destino_con_letra; en la base vive solo el número).
    Sin código cargado → None (celda vacía y marca en el espejo: se completa
    en Manager → CONTRAPARTES o en el catálogo de agentes, no acá)."""
    ccs = sorted({o["cc"] for o in ordenes
                  if o["tipo_contraparte"] == "interno" and o["cc"]})
    agentes = sorted({o["agente"] for o in ordenes
                      if o["tipo_contraparte"] == "externo" and o["agente"]})
    por_cc: dict = {}
    if ccs:
        por_cc = {r["id_cuenta"]: r["codigo_mae"] for r in _q(
            "SELECT id_cuenta, codigo_mae FROM clientes.contrapartes "
            "WHERE id_cuenta = ANY(%(ccs)s)", {"ccs": ccs})}
    por_agente: dict = {}
    if agentes:
        por_agente = {r["nombre"]: r["codigo_mae"] for r in _q(
            "SELECT nombre, codigo_mae FROM operaciones.senebis_agentes "
            "WHERE nombre = ANY(%(ags)s)", {"ags": agentes})}
    return {o["id"]: _destino_con_letra(
                por_agente.get(o["agente"]) if o["tipo_contraparte"] == "externo"
                else por_cc.get(o["cc"]),
                o["tipo_contraparte"])
            for o in ordenes}


def _fila_export_mae(o: dict, destino: str | None) -> list:
    """Una orden MAE → los 8 valores del Excel MAE.
    Precio UNITARIO (px viene cada 100 VN → ÷100). MONEDA fija 'ARS' hasta que
    la orden tenga el campo (pendiente). SEGMENTO sale de la orden; el fallback
    al default cubre las cargadas ANTES de que el campo existiera, que lo tienen
    en NULL — así ninguna fila del Excel sale con la celda vacía."""
    px_unit = (o["px"] / 100) if o["px"] is not None else None
    return [(o["operacion"] or "").capitalize(), o["especie"], o["plazo"],
            "ARS", px_unit, o["vn"], destino,
            o.get("segmento") or SEGMENTO_MAE_DEFAULT]


def excel_mae_preview(desde: str | None = None, hasta: str | None = None,
                      email: str = "") -> dict:
    """Espejo EN VIVO del Excel MAE para la tab EXCEL MAE: mismas filas y
    reglas que export_mae_xlsx, MÁS las ya tildadas (`mae_completada`), que se
    muestran grisadas para poder destildarlas — al archivo NO van.
    `sin_destino` marca las órdenes cuya contraparte/agente todavía no tiene
    código MAE cargado."""
    data = listar_ops(desde=desde, hasta=hasta, email=email)
    ordenes = _filas_mae(data["ordenes"], incluir_completadas=True)
    destinos = _destinos_mae(ordenes)
    return {
        "headers": list(_HEADERS_MAE),
        "filas": [{"id": o["id"], "estado": o["estado"],
                   "sin_destino": destinos.get(o["id"]) is None,
                   "mae_completada": bool(o.get("mae_completada")),
                   "mae_completada_por": o.get("mae_completada_por"),
                   "mae_completada_at": o.get("mae_completada_at"),
                   # Tildada y editada DESPUÉS → el MAE quedó con los datos
                   # viejos: fila amarilla + ⚠ EDITADA (igual que en Quantex).
                   "mae_editada_completada": bool(o.get("mae_editada_completada")),
                   "campos_editados": list(o.get("campos_editados") or []),
                   "valores": _fila_export_mae(o, destinos.get(o["id"]))}
                  for o in ordenes],
        "conectados": data["conectados"],
    }


def export_mae_xlsx(desde: str | None = None, hasta: str | None = None) -> tuple[bytes, str]:
    """El .xlsx del MAE: Operacion · Instrumento · Plazo · Moneda · Precio
    (unitario) · Cantidad · Destino (resuelto en vivo) · Segmento (manual).
    SOLO pendientes MAE (_filas_mae)."""
    data = listar_ops(desde=desde, hasta=hasta)
    ordenes = _filas_mae(data["ordenes"])
    destinos = _destinos_mae(ordenes)
    contenido = _armar_xlsx(
        _HEADERS_MAE,
        [_fila_export_mae(o, destinos.get(o["id"])) for o in ordenes],
        "SENEBIS MAE", anchos=(12, 14, 8, 10, 12, 16, 14, 22))
    hoy = datetime.now(_TZ_AR).strftime("%Y%m%d")
    return contenido, f"senebis_mae_{hoy}.xlsx"
