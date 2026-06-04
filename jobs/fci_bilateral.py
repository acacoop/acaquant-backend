"""jobs/fci_bilateral.py — lleva el FCI bilateral de CashFlow.NegocioMovimientos a
CashFlow.Operaciones, con el campo `etapa`.

Contexto: el API de informes (que alimenta Operaciones) NO trae los FCI bilateral;
solo vienen por el feed de consolidados (NegocioMovimientos). El mismo FCI aparece
en dos etapas:
  - SOLICITUD (comprobante `DOC…`, categoria `solicitud_*_fci`) = el movimiento del
    día (el pedido) → `etapa: "solicitud"`.
  - LIQUIDACIÓN (comprobante `CL…`, categoria `suscripcion_fci`/`rescate_fci`) = la
    plata liquidada, día siguiente → `etapa: "liquidacion"`.

OJO: `suscripcion_fci`/`rescate_fci` también traen comprobantes `BOL` (FCI normales
que YA entran como boleto por el API de informes) → se EXCLUYEN por prefijo `CL`
para no duplicar.

Las vistas suman por defecto excluyendo `etapa: "solicitud"` (ver _ops_match en
api/routers/operaciones.py) → el volumen no se dobla.

ADEMÁS corrige el bruto de las suscripciones FCI normales (comprobante BOL): el
API de informes las trae con `bruto=0`, pero el monto correcto está en
NegocioMovimientos. Se pisa por boleto SOLO donde está en 0/None (los rescates ya
vienen bien → intactos). Es un UPDATE puro (sin upsert) → NUNCA inserta, así que
no puede crear duplicados. Garantía estructural extra: índices únicos
`Operaciones.uq_boleto` (boleto) y `NegocioMovimientos.uq_fecha_comprobante`.

Idempotente: el primer run hace el backfill (taggea los CL históricos cargados a
mano + trae los DOC); upsert NO destructivo (`$setOnInsert` preserva la carga
manual, solo agrega `etapa`). Encadenado a negocio_movimientos en crontab.

Uso:
    python -m jobs.fci_bilateral
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, ".")

from pymongo import UpdateOne

from api.services import operaciones_informes as svc
from core.job_runs import JobRunLogger
from core.mongo import get_mongo_client

_MERCADO = "FCI Bilateral"
# El FCI bilateral se liquida en T+1/T+2 → solo hace falta mirar lo reciente.
# Acota la lectura de NegocioMovimientos a esta ventana (usa índice fecha_categoria)
# en vez de escanear toda la colección cada hora. `--full` ignora la ventana.
_LOOKBACK_DIAS = 10
_CATS_LIQ = ("suscripcion_fci", "rescate_fci")                       # comprobante CL
_CATS_SOL = ("solicitud_suscripcion_fci", "solicitud_rescate_fci")  # comprobante DOC

# categoria → (tipo_operacion, operacion, etapa)
_MAP: dict[str, tuple[str, str, str]] = {
    "suscripcion_fci":           ("Liquidación de suscripción de FCI ACDI", "Suscripción", "liquidacion"),
    "rescate_fci":               ("Liquidación de rescate de FCI ACDI",     "Rescate",     "liquidacion"),
    "solicitud_suscripcion_fci": ("Solicitud de suscripción de FCI",        "Suscripción", "solicitud"),
    "solicitud_rescate_fci":     ("Solicitud de rescate de FCI",            "Rescate",     "solicitud"),
}

# Entradas de catálogo a asegurar (consistencia con las "Liquidación…" ya existentes).
_CATALOGO = [
    {"tipo_operacion": "Solicitud de suscripción de FCI", "mercado": _MERCADO, "operacion": "Suscripción"},
    {"tipo_operacion": "Solicitud de rescate de FCI",     "mercado": _MERCADO, "operacion": "Rescate"},
]

_DENOM_RE = re.compile(r"^\s*\[\d+\]\s*")  # "[101] NOMBRE" → "NOMBRE"


def _denom(cuenta_raw) -> str | None:
    s = str(cuenta_raw or "").strip()
    return _DENOM_RE.sub("", s) or None


def _assets_cafci_map(client) -> dict[str, str]:
    """ticker CAFCI → `unidad` (nombre rico del fondo) desde Valuaciones.Assets,
    para que el `instrumento` quede igual al de los CL cargados a mano."""
    out: dict[str, str] = {}
    for d in client["Valuaciones"]["Assets"].find(
        {"CAFCI": {"$nin": [None, ""]}}, {"_id": 0, "CAFCI": 1, "unidad": 1}
    ):
        c, u = d.get("CAFCI"), d.get("unidad")
        if c and u:
            out[str(c).strip()] = u
    return out


def _map_doc(d: dict, niveles: dict, assets: dict, now: datetime) -> tuple[str, dict]:
    tipo_op, operacion, etapa = _MAP[d["categoria"]]
    cuenta = str(d.get("id_cuenta") or "").strip()
    ticker = str(d.get("ticker") or "").strip()
    importe = d.get("importe")
    moneda = d.get("moneda")
    nv = niveles.get(cuenta, {})
    base = {
        "boleto":         d.get("comprobante"),
        "cuenta":         cuenta,
        "concertacion":   d.get("fecha"),
        "denominacion":   _denom(d.get("cuenta")),
        "tipo_operacion": tipo_op,
        "instrumento":    assets.get(ticker) or ticker or None,
        "condiciones":    f"{moneda} Inm" if moneda else None,
        "cantidad":       d.get("cantidad"),
        "bruto":          abs(importe) if importe is not None else None,
        "arancel":        0.0,
        "moneda":         moneda,
        "mercado":        _MERCADO,
        "operacion":      operacion,
        "segmento":       nv.get("n1", ""),
        "nivel_3":        nv.get("n3", ""),
        "commodity":      None,
        "ingestado_en":   now,
    }
    return etapa, base


def run(full: bool = False) -> dict:
    with JobRunLogger("fci_bilateral") as jr:
        client = get_mongo_client()
        db = client["CashFlow"]
        ops = db["Operaciones"]
        mov = db["NegocioMovimientos"]
        now = datetime.now(UTC)

        # 1) Catálogo (idempotente).
        for c in _CATALOGO:
            db["TiposOperacion"].update_one(
                {"tipo_operacion": c["tipo_operacion"]}, {"$set": c}, upsert=True)

        # 2) Taggear los CL históricos ya en Operaciones (carga manual) que no
        #    están en NegocioMovimientos → no se pisarían en el paso 5. Es un
        #    backfill de una vez y escanea el subconjunto FCI Bilateral de
        #    Operaciones (mercado sin índice) → solo con --full, no cada hora.
        tag_mod = 0
        if full:
            tag = ops.update_many(
                {"mercado": _MERCADO,
                 "tipo_operacion": {"$regex": "Liquidaci", "$options": "i"},
                 "etapa": {"$exists": False}},
                {"$set": {"etapa": "liquidacion"}},
            )
            tag_mod = tag.modified_count

        # 3) Maps de enriquecimiento (segmento/nivel_3 por cuenta) + Assets (instrumento).
        _, niveles = svc.cargar_maps_enrich(db)
        assets = _assets_cafci_map(client)

        # 4) Leer FCI bilateral: liquidaciones SOLO CL (las BOL ya son boletos),
        #    solicitudes todas (son DOC). Acotado a los últimos _LOOKBACK_DIAS
        #    (usa índice fecha_categoria; el regex ^CL queda como filtro residual
        #    barato sobre la ventana chica). --full mira toda la historia.
        q: dict = {"$or": [
            {"categoria": {"$in": list(_CATS_LIQ)}, "comprobante": {"$regex": "^CL", "$options": "i"}},
            {"categoria": {"$in": list(_CATS_SOL)}},
        ]}
        if not full:
            hoy = (now - timedelta(hours=3)).date()   # ART
            q = {"fecha": {"$gte": (hoy - timedelta(days=_LOOKBACK_DIAS)).isoformat()}, **q}
        proj = {"_id": 0, "comprobante": 1, "categoria": 1, "fecha": 1, "cuenta": 1,
                "id_cuenta": 1, "ticker": 1, "importe": 1, "cantidad": 1, "moneda": 1}
        por_boleto: dict[str, tuple[str, dict]] = {}
        for d in mov.find(q, proj):
            if not d.get("comprobante") or d.get("categoria") not in _MAP:
                continue
            etapa, base = _map_doc(d, niveles, assets, now)
            b = base["boleto"]
            prev = por_boleto.get(b)  # dedup defensivo por comprobante: gana mayor |bruto|
            if prev is None or (base["bruto"] or 0) > (prev[1]["bruto"] or 0):
                por_boleto[b] = (etapa, base)

        # 5) Upsert NO destructivo: campos mapeados solo on-insert (preserva la
        #    carga manual histórica); `etapa` siempre seteada.
        bulk = [
            UpdateOne({"boleto": base["boleto"]},
                      {"$setOnInsert": base, "$set": {"etapa": etapa}}, upsert=True)
            for etapa, base in por_boleto.values()
        ]
        up = mod = 0
        if bulk:
            res = ops.bulk_write(bulk, ordered=False)
            up, mod = res.upserted_count, res.modified_count

        # 6) Corregir bruto=0 de las suscripciones FCI normales (comprobante BOL):
        #    el API de informes las trae con bruto=0, pero el monto correcto está
        #    en NegocioMovimientos (verificado: diag_fci_match_boleto, match 1:1).
        #    UPDATE PURO por boleto (el doc YA existe vía API) — `upsert` ausente →
        #    NUNCA inserta → cero riesgo de duplicado. Solo pisa donde bruto está
        #    en 0/None: los rescates (bruto correcto) no matchean → intactos.
        q_bol: dict = {"categoria": {"$in": list(_CATS_LIQ)},
                       "comprobante": {"$regex": "^BOL", "$options": "i"}}
        if not full:
            hoy = (now - timedelta(hours=3)).date()  # ART
            q_bol["fecha"] = {"$gte": (hoy - timedelta(days=_LOOKBACK_DIAS)).isoformat()}
        corr_bulk = [
            UpdateOne({"boleto": str(d["comprobante"]).strip(), "bruto": {"$in": [0, None]}},
                      {"$set": {"bruto": abs(d["importe"])}})
            for d in mov.find(q_bol, {"_id": 0, "comprobante": 1, "importe": 1})
            if d.get("comprobante") and d.get("importe") is not None
        ]
        corr = ops.bulk_write(corr_bulk, ordered=False).modified_count if corr_bulk else 0

        jr.set_stat("cl_tag_etapa", tag_mod)
        jr.set_stat("fci_comprobantes", len(por_boleto))
        jr.set_stat("insertados", up)
        jr.set_stat("actualizados", mod)
        jr.set_stat("bruto_corregidos", corr)
        jr.log(f"FCI bilateral: {len(por_boleto)} comprobantes · {up} nuevos · {mod} act · "
               f"{corr} bruto suscripción corregidos · {tag_mod} CL históricos taggeados · "
               f"{'FULL' if full else f'últ {_LOOKBACK_DIAS}d'}")
        return {"leidos": len(por_boleto), "insertados": up, "actualizados": mod,
                "cl_tag": tag_mod, "bruto_corr": corr}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="procesa TODA la historia (default: últimos "
                         f"{_LOOKBACK_DIAS} días + sin re-tag de CL históricos)")
    args = ap.parse_args()
    res = run(full=args.full)
    print(f"→ {res}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
