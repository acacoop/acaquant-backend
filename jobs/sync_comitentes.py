"""Sync de cuentas comitentes desde Aunesa → master `Clientes.Comitentes`.

Pobla/actualiza el master de clientes del Tablero de Control Comercial
(docs/TABLERO_COMERCIAL.md) desde `GET /api/cuentas/listadoCuentas`. Pensado
para correr 1×/día por cron: las cuentas nuevas se agregan solas (upsert por
`id_cuenta`), idempotente.

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
    trae todos los tipos/estados.

Uso:
    python -m jobs.sync_comitentes
    python -m jobs.sync_comitentes --dry-run
    python -m jobs.sync_comitentes --include-all
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

import requests
from pymongo import ASCENDING, UpdateOne

import config
from core.job_runs import JobRunLogger
from core.mongo import get_mongo_client

AUTH_URL = "https://aca.aunesa.com/Irmo/api/login"
LISTADO_URL = "https://aca.aunesa.com/Irmo/api/cuentas/listadoCuentas"

DB = "Clientes"
COL = "Comitentes"

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
# motor de segmentación patrimonial. Ver docs/SEGMENTACION_PATRIMONIAL.md.
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
    r = requests.post(
        AUTH_URL,
        json={
            "clientId": config.AUNESA_CLIENT_ID,
            "username": config.AUNESA_USERNAME,
            "password": config.AUNESA_PASSWORD,
        },
        headers={"Content-Type": "application/json"},
        timeout=15,
    )
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
    return {
        "id_cuenta":         str(c.get("id")) if c.get("id") is not None else None,
        "denominacion":      c.get("denominacion"),
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


def run(*, include_all: bool = False, dry_run: bool = False) -> None:
    with JobRunLogger("sync_comitentes") as jr:
        headers = _auth()
        params = {} if include_all else {"tipoCuenta": "Comitente"}
        r = requests.get(LISTADO_URL, headers=headers, params=params, timeout=180)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            data = data.get("cuentas") or data.get("data") or [data]
        jr.set_stat("recibidas", len(data))

        if not include_all:
            data = [c for c in data if (c.get("estado") or "") == "Activa"]
        jr.set_stat("a_procesar", len(data))

        col = get_mongo_client()[DB][COL]
        col.create_index([("id_cuenta", ASCENDING)], unique=True)
        col.create_index([("operador_email", ASCENDING)])

        now = datetime.now(UTC)
        ops: list[UpdateOne] = []
        saltadas = 0
        for c in data:
            doc = _map_cuenta(c)
            if not doc["id_cuenta"]:
                saltadas += 1
                continue
            set_fields = {
                k: v for k, v in doc.items()
                if k != "id_cuenta" and k not in INSERT_ONLY_FIELDS
            }
            set_fields["origen"] = "aunesa"
            set_fields["updated_at"] = now
            on_insert: dict = {"created_at": now}
            for mf in MANUAL_FIELDS:
                on_insert[mf] = None
            # Subdocs manuales: dict vacío, NO null, así dot-notation funciona
            # desde el primer write del endpoint/motor (ver MANUAL_SUBDOCS).
            for sd in MANUAL_SUBDOCS:
                on_insert[sd] = {}
            # Operador: valor de Aunesa SOLO al crear; en re-syncs no se toca
            # (un campo no puede estar en $set y $setOnInsert a la vez).
            for f in INSERT_ONLY_FIELDS:
                on_insert[f] = doc.get(f)
            ops.append(
                UpdateOne(
                    {"id_cuenta": doc["id_cuenta"]},
                    {"$set": set_fields, "$setOnInsert": on_insert},
                    upsert=True,
                )
            )

        jr.set_stat("saltadas_sin_id", saltadas)

        if dry_run:
            jr.set_stat("dry_run", True)
            jr.log(f"DRY-RUN: {len(ops)} upserts pendientes (no se escribió nada).")
            return

        if ops:
            res = col.bulk_write(ops, ordered=False)
            jr.set_stat("upserted", res.upserted_count)
            jr.set_stat("modified", res.modified_count)
            jr.set_stat("matched", res.matched_count)
        jr.log(
            f"sync_comitentes OK: {len(ops)} cuentas procesadas "
            f"({jr.stats.get('upserted', 0)} nuevas, {jr.stats.get('modified', 0)} actualizadas)."
        )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-all", action="store_true", help="trae todos los tipos/estados")
    ap.add_argument("--dry-run", action="store_true", help="no escribe, solo cuenta")
    args = ap.parse_args()
    run(include_all=args.include_all, dry_run=args.dry_run)
