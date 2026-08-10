"""jobs/fci_bilateral.py — lleva el FCI bilateral de `operaciones.negocio_movimientos`
a `operaciones.operaciones`, con el campo `etapa`.

Contexto: el API de informes (que alimenta Operaciones) NO trae los FCI bilateral;
solo vienen por el feed de consolidados (negocio_movimientos). El mismo FCI aparece
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

Idempotente: el primer run hace el backfill (taggea los CL históricos cargados a
mano + trae los DOC); el upsert es NO destructivo — en INSERT setea todos los campos,
pero ante conflicto de boleto SOLO pisa `etapa` + `ingestado_en` (y RELLENA `mep` si
está vacío), preservando la carga manual (ver _SQL_FCI_UPSERT). Encadenado a
negocio_movimientos en crontab.

Uso:
    python -m jobs.fci_bilateral
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, date, datetime, timedelta

sys.path.insert(0, ".")

from api.services import operaciones_informes as svc
from api.services._negocio_sql_read import negocio_movimientos_rows
from core import dolar_sql
from core.job_runs import JobRunLogger
from core.pg_mirror import write_native
from core.postgres import get_job_pool

# Upsert NO destructivo a SQL operaciones.operaciones: en INSERT setea todos los
# campos del FCI bilateral; en CONFLICT (boleto ya existe) SOLO pisa etapa +
# ingestado_en → preserva la carga manual histórica. `mep` se RELLENA (nunca se
# pisa): sin él la vista OPERACIONES en modo DOLARIZAR no puede convertir el
# volumen ARS del FCI bilateral y lo cuenta como cero (bug 2026-08-10).
_SQL_FCI_UPSERT = """
INSERT INTO operaciones
 (boleto, concertacion, id_cuenta, denominacion, tipo_operacion, instrumento,
  condiciones, cantidad, bruto, arancel, moneda, mercado, operacion, segmento,
  nivel_3, commodity, etapa, mep, ingestado_en)
VALUES (%(boleto)s, %(concertacion)s, %(id_cuenta)s, %(denominacion)s,
  %(tipo_operacion)s, %(instrumento)s, %(condiciones)s, %(cantidad)s, %(bruto)s,
  %(arancel)s, %(moneda)s, %(mercado)s, %(operacion)s, %(segmento)s, %(nivel_3)s,
  %(commodity)s, %(etapa)s, %(mep)s, %(ingestado_en)s)
ON CONFLICT (boleto) DO UPDATE SET
  etapa = EXCLUDED.etapa, ingestado_en = EXCLUDED.ingestado_en,
  mep = COALESCE(NULLIF(operaciones.mep, 0), EXCLUDED.mep)
"""


def _fci_params(etapa: str, base: dict, now: datetime,
                meps: dict[str, float | None]) -> dict:
    """(etapa, base del _map_doc) → params SQL. `cuenta`→id_cuenta; fecha ISO→date.
    `meps` es el mapa fecha→MEP precalculado en una query (ver `run`)."""
    conc = base.get("concertacion")
    return {
        "boleto":         base["boleto"],
        "concertacion":   date.fromisoformat(conc) if conc else None,
        "id_cuenta":      base.get("cuenta"),
        "denominacion":   base.get("denominacion"),
        "tipo_operacion": base.get("tipo_operacion"),
        "instrumento":    base.get("instrumento"),
        "condiciones":    base.get("condiciones"),
        "cantidad":       base.get("cantidad"),
        "bruto":          base.get("bruto"),
        "arancel":        base.get("arancel"),
        "moneda":         base.get("moneda"),
        "mercado":        base.get("mercado"),
        "operacion":      base.get("operacion"),
        "segmento":       base.get("segmento"),
        "nivel_3":        base.get("nivel_3"),
        "commodity":      base.get("commodity"),
        "etapa":          etapa,
        "mep":            meps.get(conc) if conc else None,
        "ingestado_en":   now,
    }

_MERCADO = "FCI Bilateral"
# El FCI bilateral se liquida en T+1/T+2 → solo hace falta mirar lo reciente.
# Acota la lectura de NegocioMovimientos a esta ventana (usa índice fecha_categoria)
# en vez de escanear toda la tabla cada hora. `--full` ignora la ventana.
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


def _assets_cafci_map() -> dict[str, str]:
    """ticker CAFCI → `unidad` (nombre rico del fondo) desde SQL portafolio.assets,
    para que el `instrumento` quede igual al de los CL cargados a mano."""
    out: dict[str, str] = {}
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT cafci, unidad FROM portafolio.assets "
                    "WHERE cafci IS NOT NULL AND cafci <> ''")
        for c, u in cur.fetchall():
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
        now = datetime.now(UTC)

        # 1) Catálogo (idempotente) — operaciones.tipos_operacion: upsert por
        #    tipo_operacion, doc completo en `data` jsonb.
        write_native("tipos_operacion", ["tipo_operacion"],
                     [{"tipo_operacion": c["tipo_operacion"], "data": c} for c in _CATALOGO])

        # 2) Taggear los CL históricos ya en SQL operaciones (carga manual) que no
        #    están en negocio_movimientos → no se pisarían en el paso 5. Es un
        #    backfill de una vez → solo con --full, no cada hora.
        tag_mod = 0
        if full:
            with get_job_pool().connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE operaciones SET etapa = 'liquidacion' "
                    "WHERE mercado = %s AND tipo_operacion ILIKE '%%Liquidaci%%' "
                    "AND etapa IS NULL", (_MERCADO,))
                tag_mod = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                conn.commit()

        # 3) Maps de enriquecimiento (segmento/nivel_3 por cuenta) + Assets (instrumento).
        _, niveles = svc.cargar_maps_enrich()
        assets = _assets_cafci_map()

        # 4) Leer FCI bilateral desde SQL operaciones.negocio_movimientos:
        #    liquidaciones SOLO CL (las BOL ya son boletos), solicitudes todas
        #    (son DOC). Acotado a los últimos _LOOKBACK_DIAS; --full = toda la
        #    historia. Son dos queries (LIQ con prefijo CL + SOL sin prefijo).
        fecha_gte = None if full else (
            ((now - timedelta(hours=3)).date()   # ART
             - timedelta(days=_LOOKBACK_DIAS)).isoformat())
        _f4 = ("comprobante", "categoria", "fecha", "cuenta",
               "id_cuenta", "ticker", "importe", "cantidad", "moneda")
        registros = (
            negocio_movimientos_rows(fields=_f4, categorias=list(_CATS_LIQ),
                                     comprobante_prefix="CL", fecha_gte=fecha_gte)
            + negocio_movimientos_rows(fields=_f4, categorias=list(_CATS_SOL),
                                       fecha_gte=fecha_gte))
        por_boleto: dict[str, tuple[str, dict]] = {}
        for d in registros:
            if not d.get("comprobante") or d.get("categoria") not in _MAP:
                continue
            etapa, base = _map_doc(d, niveles, assets, now)
            b = base["boleto"]
            prev = por_boleto.get(b)  # dedup defensivo por comprobante: gana mayor |bruto|
            if prev is None or (base["bruto"] or 0) > (prev[1]["bruto"] or 0):
                por_boleto[b] = (etapa, base)

        # 5) Upsert NO destructivo a SQL: en INSERT setea los campos del boleto;
        #    en CONFLICT SOLO pisa etapa + ingestado_en (preserva la carga manual).
        #    `ingestado_en` se bumpea SIEMPRE (también cuando solo cambia etapa).
        up = mod = 0
        if por_boleto:
            # MEP por fecha de concertación en UNA query (no una por boleto).
            meps = dolar_sql.mep_por_fecha(
                b["concertacion"] for _e, b in por_boleto.values() if b.get("concertacion"))
            params = [_fci_params(etapa, base, now, meps)
                      for etapa, base in por_boleto.values()]
            with get_job_pool().connection() as conn, conn.cursor() as cur:
                cur.executemany(_SQL_FCI_UPSERT, params)
                conn.commit()
            up = len(params)   # SQL no separa insert/update barato → total escrito

        # 6) Corregir bruto=0 de las suscripciones FCI normales (comprobante BOL):
        #    el API de informes las trae con bruto=0; el monto correcto está en
        #    negocio_movimientos. SCOPEADO: primero busca los REALMENTE rotos
        #    (bruto 0/null) y solo pisa esos → escrituras mínimas. UPDATE puro.
        imp_por_boleto = {
            str(d["comprobante"]).strip(): abs(d["importe"])
            for d in negocio_movimientos_rows(
                fields=("comprobante", "importe"), categorias=list(_CATS_LIQ),
                comprobante_prefix="BOL", fecha_gte=fecha_gte)
            if d.get("comprobante") and d.get("importe") is not None
        }
        corr = 0
        if imp_por_boleto:
            with get_job_pool().connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT boleto FROM operaciones WHERE boleto = ANY(%s) "
                    "AND (bruto = 0 OR bruto IS NULL)", (list(imp_por_boleto),))
                rotos = [str(r[0]).strip() for r in cur.fetchall()]
                if rotos:
                    cur.executemany(
                        "UPDATE operaciones SET bruto = %(bruto)s "
                        "WHERE boleto = %(boleto)s AND (bruto = 0 OR bruto IS NULL)",
                        [{"boleto": b, "bruto": imp_por_boleto[b]} for b in rotos])
                    corr = cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(rotos)
                conn.commit()

        jr.set_stat("cl_tag_etapa", tag_mod)
        jr.set_stat("fci_comprobantes", len(por_boleto))
        jr.set_stat("insertados", up)
        jr.set_stat("actualizados", mod)
        jr.set_stat("bruto_corregidos", corr)
        jr.log(f"FCI bilateral: {len(por_boleto)} comprobantes · {up} nuevos · {mod} act · "
               f"{corr} bruto suscripción corregidos · {tag_mod} CL taggeados · "
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
