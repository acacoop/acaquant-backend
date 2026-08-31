"""Sync de cuentas comitentes desde Aunesa → master SQL `clientes.comitentes`
(+ `clientes.cuentas` con denominacion + `clientes.operadores`).

Pobla/actualiza el master de clientes del Tablero de Control Comercial
(docs/CLIENTES.md) desde `GET /api/cuentas/listadoCuentas`. Pensado
para correr 1×/día por cron: las cuentas nuevas se agregan solas (upsert por
`id_cuenta`), idempotente. Fuente ÚNICA SQL (Mongo Clientes.Comitentes deprecado).

Reglas (mismo criterio que el master de Assets en aum.py):
  - `$set` de los campos de Aunesa (denominación, estado, teléfono, etc.),
    EXCEPTO el operador.
  - El OPERADOR (operador_email/operador_nombre, `INSERT_ONLY_FIELDS`) se escribe
    SOLO al insertar la cuenta y NUNCA se pisa en re-syncs: el de Aunesa no es
    confiable y lo gestiona la mesa a mano (override desde /manager → CLIENTES).
  - Los campos de SEGMENTACIÓN MANUAL (`MANUAL_FIELDS`, incluye nivel_1..5) se
    inicializan en null SOLO al insertar (`$setOnInsert`) y NUNCA se pisan →
    preservan lo que la mesa cargó a mano.
  - Filtra tipo=Comitente + estado=Activa (igual que el AuM). `--include-all`
    trae todos los tipos/estados. Las comitentes que dejaron de estar Activas NO
    se insertan, pero si ya existen en SQL se les actualiza el `estado` (si no,
    una baja quedaba 'Activa' para siempre y seguía contando como cliente).

Uso:
    python -m jobs.sync_comitentes
    python -m jobs.sync_comitentes --dry-run
    python -m jobs.sync_comitentes --include-all
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

import config
from core import proveedores
from core.doc_fiscal import parse_titular
from core.job_runs import JobRunLogger
from core.postgres import get_job_pool

# ⚠️ **DEJA RASTRO** (2026-08-20). Este módulo le pega a Aunesa por FUERA
# de `core/aunesa`, así que sus fallas eran invisibles para el detector de
# caídas: el 2026-08-20 Aunesa devolvió 500, el AuM del día no se escribió y
# el agente no pudo decir por qué. El hook anota cada respuesta sola, así que
# una llamada nueva en este archivo queda cubierta sin acordarse de nada.
_SES = proveedores.sesion_vigilada("aunesa", "comitentes")


AUTH_URL = "https://aca.aunesa.com/Irmo/api/login"
LISTADO_URL = "https://aca.aunesa.com/Irmo/api/cuentas/listadoCuentas"

# Campos de segmentación manual — se crean en null al insertar y el sync no
# los toca nunca más (los edita la mesa desde la UI). nivel_1..5 = árbol de
# segmentación; el resto, atributos comerciales / compliance.
MANUAL_FIELDS = (
    "nivel_1", "nivel_2", "nivel_3", "nivel_4", "nivel_5",
    "primer_contacto_comercial", "riesgo_la_ft", "division",
    "adc", "dma", "observaciones", "sucursal", "referido",
)

# Subdocumentos manuales — se inicializan como dict vacío (no null) para que
# los $set con dot-notation (ej. `cupo.transaccional_ars`) funcionen desde el
# primer write. Igual que MANUAL_FIELDS: el sync no los toca nunca. Lo escribe
# `POST /api/manager/clientes/bulk-fondeo` (carga del cupo del custodio) y el
# motor de segmentación patrimonial. Ver docs/CLIENTES.md.
MANUAL_SUBDOCS = ("cupo",)

# Campos derivados que el motor escribe — no se inicializan acá (el motor los
# crea con $set cuando corre). Listados para documentar el contrato: el sync
# tampoco los toca porque _map_cuenta no los devuelve.
# - segmento_patrimonial          (string: PH_RETAIL | ... | PJ_GRANDE | null)
# - segmento_patrimonial_calc     (subdoc: auditabilidad del cálculo)

# Campos de Aunesa que se escriben SOLO al crear la cuenta y luego NO se pisan
# en re-syncs (a diferencia de MANUAL_FIELDS, se inicializan con el valor de
# Aunesa, no en null). El operador de Aunesa no es confiable → la mesa lo
# corrige a mano y esa corrección debe sobrevivir el cron nocturno.
INSERT_ONLY_FIELDS = ("operador_email", "operador_nombre")


def _auth() -> dict[str, str]:
    r = _SES.post(
        AUTH_URL,
        json={
            "clientId": config.AUNESA_CLIENT_ID,
            "username": config.AUNESA_USERNAME,
            "password": config.AUNESA_PASSWORD,
        },
        headers={"Content-Type": "application/json"},
        timeout=15,
    )
    # El rastro para el detector de caídas (§0.an): este módulo NO pasa por
    # `core/aunesa`, así que sin esto su fallo es invisible para el agente.
    r.raise_for_status()
    return {"Content-Type": "application/json", "Authorization": f"Bearer {r.json().get('token')}"}


def _parse_fecha(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _provincia(cuenta: dict) -> str | None:
    doms = cuenta.get("domicilios") or []
    legal = next((d for d in doms if (d.get("uso") or "").lower() == "legal"), None)
    d = legal or (doms[0] if doms else {})
    return d.get("provincia")


def _medio(cuenta: dict, tipos: tuple[str, ...]) -> str | None:
    """Valor (`medio`) del medio de comunicación cuyo `tipo` matchea alguno de
    `tipos` (substring, case-insensitive). Prioriza vigentes (sin
    `vigenciaHasta`) y `principal`. Aunesa usa tipo='Movil'/'Telefono'/'Fijo'
    para teléfono y 'E-Mail' para mail; el valor va en el campo `medio`."""
    cand = [
        m for m in (cuenta.get("mediosComunicacion") or [])
        if isinstance(m, dict) and m.get("medio")
        and any(t in (m.get("tipo") or "").lower() for t in tipos)
    ]
    if not cand:
        return None
    cand.sort(key=lambda m: (
        bool(m.get("vigenciaHasta")),                  # vigentes primero
        (m.get("principal") or "").lower() != "true",  # principal primero
    ))
    return str(cand[0]["medio"]).strip() or None


def _map_cuenta(c: dict) -> dict:
    """Doc del master a partir del item de listadoCuentas. Solo los campos
    pedidos (auto). Los manuales NO van acá — se inicializan en el upsert."""
    dg = c.get("disposicionesGenerales") or {}
    op = (c.get("administrador") or {}).get("operador") or {}
    # Documento fiscal: del `titular` de Aunesa ('[DNI 93698623] NOMBRE'). Lo
    # persistimos para el cruce del Control Automático (Excel de CUITs ↔ cuentas).
    tipo_doc, nro_doc = parse_titular(c.get("titular"))
    return {
        "id_cuenta":         str(c.get("id")) if c.get("id") is not None else None,
        "denominacion":      c.get("denominacion"),
        "tipo_doc":          tipo_doc,
        "nro_doc":           nro_doc,
        "tipo_titular":      c.get("tipoTitular"),
        "tipo":              c.get("tipo"),
        "estado":            c.get("estado"),
        "clase":             c.get("clase"),
        "fecha_alta_legajo": _parse_fecha(c.get("fechaAltaLegajo")),
        "tipo_cliente":      dg.get("tipoCliente"),
        "perfil_inversion":  dg.get("perfilInversion"),
        "operador_email":    op.get("email"),
        "operador_nombre":   op.get("nombreReal") or op.get("nombre"),
        "provincia":         _provincia(c),
        "telefono":          _medio(c, ("movil", "cel", "tel", "fij")),
        "email":             _medio(c, ("mail", "correo")),
    }


def _sync_propia(headers: dict) -> list[tuple]:
    """Cuentas tipo='Propia' + estado='Activa' (la mesa propia de la empresa, ej.
    1839 'ACA VALORES S.A - TRADING'). NO son clientes → van SOLO a `clientes.cuentas`
    (id+denominación, el FK target), NUNCA a `comitentes` (no contaminan el Tablero
    Comercial). operaciones_informes las suma a su universo para ingestar sus boletos.
    Devuelve [(id_cuenta, denominacion)]."""
    r = _SES.get(LISTADO_URL, headers=headers, params={"tipoCuenta": "Propia"}, timeout=180)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        data = data.get("cuentas") or data.get("data") or [data]
    out: list[tuple] = []
    for c in data:
        # Filtro defensivo: si el endpoint ignora el param tipoCuenta, igual quedan
        # solo las Propia+Activa.
        if (c.get("tipo") or "") != "Propia" or (c.get("estado") or "") != "Activa":
            continue
        idc = str(c.get("id")) if c.get("id") is not None else None
        if idc:
            out.append((idc, c.get("denominacion")))
    return out


def run(*, include_all: bool = False, dry_run: bool = False) -> None:
    with JobRunLogger("sync_comitentes") as jr:
        headers = _auth()
        params = {} if include_all else {"tipoCuenta": "Comitente"}
        r = _SES.get(LISTADO_URL, headers=headers, params=params, timeout=180)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            data = data.get("cuentas") or data.get("data") or [data]
        jr.set_stat("recibidas", len(data))
        data_all = list(data)

        if not include_all:
            data = [c for c in data if (c.get("estado") or "") == "Activa"]
        jr.set_stat("a_procesar", len(data))

        now = datetime.now(UTC)
        # Comitentes que Aunesa ya NO reporta Activa (PreAlta/Inhibida/Baja/Inactiva). NO se
        # insertan (no son clientes del padrón), pero si YA existen en SQL hay que bajarles el
        # estado: sin esto una cuenta dada de baja queda 'Activa' para siempre (el sync solo
        # veía las Activas) y sigue contando en el padrón, el AuM y el Tablero Comercial.
        no_activas = [
            (c.get("estado"), now, str(c["id"]))
            for c in data_all
            if c.get("id") is not None and (c.get("estado") or "") != "Activa"
        ]

        operadores: dict[str, str | None] = {}    # email → nombre
        cuentas: list[tuple] = []                 # (id_cuenta, denominacion)
        comitentes: list[tuple] = []
        saltadas = 0
        for c in data:
            doc = _map_cuenta(c)
            idc = doc["id_cuenta"]
            if not idc:
                saltadas += 1
                continue
            email = doc.get("operador_email")
            if email:
                operadores[email] = doc.get("operador_nombre")
            cuentas.append((idc, doc.get("denominacion")))
            fa = doc.get("fecha_alta_legajo")
            comitentes.append((
                idc, email, doc.get("tipo_doc"), doc.get("nro_doc"), doc.get("tipo_titular"),
                doc.get("tipo"), doc.get("estado"), doc.get("clase"),
                fa.date() if isinstance(fa, datetime) else fa,
                doc.get("tipo_cliente"), doc.get("perfil_inversion"), doc.get("provincia"),
                doc.get("telefono"), doc.get("email"), "aunesa", now, now,
            ))
        jr.set_stat("saltadas_sin_id", saltadas)

        if dry_run:
            jr.set_stat("dry_run", True)
            jr.log(f"DRY-RUN: {len(comitentes)} comitentes, {len(no_activas)} no-activas a "
                   f"actualizar (no se escribió nada).")
            return

        # Escritura SQL. Orden FK-safe: operadores + cuentas (destinos del FK) → comitentes.
        with get_job_pool().connection() as conn, conn.cursor() as cur:
            if operadores:
                cur.executemany(
                    "INSERT INTO operadores (email, nombre) VALUES (%s, %s) "
                    "ON CONFLICT (email) DO UPDATE SET nombre = EXCLUDED.nombre",
                    list(operadores.items()))
            cur.executemany(
                "INSERT INTO cuentas (id_cuenta, denominacion) VALUES (%s, %s) "
                "ON CONFLICT (id_cuenta) DO UPDATE SET denominacion = EXCLUDED.denominacion",
                cuentas)
            # ON CONFLICT pisa SOLO los campos AUTO de Aunesa. operador_email, created_at y
            # los manuales (nivel_*, cupo, observaciones, segmento_patrimonial, etc.) NO se
            # tocan en re-syncs — los gestiona la mesa desde /manager → CLIENTES.
            cur.executemany(
                "INSERT INTO comitentes (id_cuenta, operador_email, tipo_doc, nro_doc, "
                "tipo_titular, tipo, estado, clase, fecha_alta_legajo, tipo_cliente, "
                "perfil_inversion, provincia, telefono, email, origen, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (id_cuenta) DO UPDATE SET "
                "tipo_doc=EXCLUDED.tipo_doc, nro_doc=EXCLUDED.nro_doc, "
                "tipo_titular=EXCLUDED.tipo_titular, tipo=EXCLUDED.tipo, estado=EXCLUDED.estado, "
                "clase=EXCLUDED.clase, fecha_alta_legajo=EXCLUDED.fecha_alta_legajo, "
                "tipo_cliente=EXCLUDED.tipo_cliente, perfil_inversion=EXCLUDED.perfil_inversion, "
                "provincia=EXCLUDED.provincia, telefono=EXCLUDED.telefono, email=EXCLUDED.email, "
                "origen=EXCLUDED.origen, updated_at=EXCLUDED.updated_at",
                comitentes)
            if no_activas:
                cur.executemany(
                    "UPDATE comitentes SET estado = %s, updated_at = %s WHERE id_cuenta = %s",
                    no_activas)
            conn.commit()

        jr.set_stat("upserted", len(comitentes))
        jr.set_stat("estado_bajado", len(no_activas))

        # Pass aparte: cuentas Propia (mesa propia) → SOLO a `cuentas`. Defensivo:
        # un fallo acá NO debe romper el sync de comitentes (que ya commiteó).
        try:
            propias = _sync_propia(headers)
            if propias and not dry_run:
                with get_job_pool().connection() as conn, conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO cuentas (id_cuenta, denominacion) VALUES (%s, %s) "
                        "ON CONFLICT (id_cuenta) DO UPDATE SET denominacion = EXCLUDED.denominacion",
                        propias)
                    conn.commit()
            jr.set_stat("propias", len(propias))
        except Exception as e:
            jr.log(f"propias: falló ({str(e).splitlines()[0][:120]}) — no crítico")

        jr.log(f"sync_comitentes OK (SQL): {len(comitentes)} comitentes, "
               f"{len(cuentas)} cuentas, {len(operadores)} operadores.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-all", action="store_true", help="trae todos los tipos/estados")
    ap.add_argument("--dry-run", action="store_true", help="no escribe, solo cuenta")
    args = ap.parse_args()
    run(include_all=args.include_all, dry_run=args.dry_run)
