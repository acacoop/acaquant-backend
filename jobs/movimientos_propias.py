"""movimientos_propias.py — vuelca la CARTERA PROPIA de la casa a
`operaciones.movimientos_propias` (Postgres), a grano LÍNEA y SIN filtros.

MISMO endpoint que el job de negocio (`operaciones/consolidadosGenerales`) y la
MISMA lógica de parseo y categorización — lo único que cambia es el valor de
`tiposCuenta`: **Propia** en vez de Comitente. Hasta hoy esos movimientos no
existían en ninguna tabla del sistema, porque el job de negocio pide siempre
"Comitente" y la propia (ej. `[1839] ACA VALORES TRADING`, la que más se mueve
por cauciones) quedaba afuera entera.

⚠️ TRES diferencias con `jobs/negocio_movimientos.py`, todas deliberadas:

1. **GRANO LÍNEA.** Una fila = una línea de Aunesa. NO se consolida por
   comprobante. El consolidador de comitentes es correcto para lo que sirve,
   pero PIERDE filas: de una compra en USD con comisión en ARS se queda con la
   línea USD y descarta la de ARS (sumarlas daría un importe mezclado en dos
   monedas), y de una solicitud FCI se queda solo con las líneas `DIS`. Acá lo
   administrativo ES el dato. Consolidar después es un `GROUP BY comprobante`;
   recuperar las líneas de una fila ya consolidada no se puede nunca.

2. **SIN FILTROS** (`aplicar_filtros=False`). La propia opera OTC y cauciones —
   `EXCLUIR_SUBSTRINGS` le borraría justo lo que se quiere ver.

3. **PK por HASH.** El feed no trae id de línea (medido 2026-09-04: las 10
   claves son lugar · uso · estado · unidad · cuenta · fecha · comprobante ·
   informacion · total · operador) y `comprobante` identifica al BOLETO, no a
   la línea. `id_linea` = md5 de la fila cruda → re-ingerir lo mismo es un
   no-op; `ocurrencia` desempata dos líneas byte-idénticas del mismo día.

Idempotente por `(fecha, id_linea, ocurrencia)`. La reconciliación MARCA
(`anulado_en`) lo que Aunesa deja de devolver, nunca borra — con el mismo freno
del 20% que el job de negocio, para que una respuesta parcial no anule medio día.

Esta tabla NO la lee ninguna vista todavía: es un volcado. El categorizador es
el de siempre y lo que no matchea cae en `otro`, que acá es señal y no ruido.

Cron: cada 30' de 14 a 22 UTC L-V (ver deploy/crontab.txt), en línea propia y no
colgada de `negocio_chain`: si esto falla, no puede cortar la cadena que corre
aranceles / fci_bilateral / ops_tasa_mav con `&&`.

Uso:
    python -m jobs.movimientos_propias                                 # hoy + lookback
    python -m jobs.movimientos_propias --fecha 2026-09-03
    python -m jobs.movimientos_propias --desde 2026-07-31 --hasta 2026-09-04   # backfill
    python -m jobs.movimientos_propias --desde 2026-07-31 --dry
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

sys.path.insert(0, ".")

from api.services import aunesa_negocio as svc
from api.services._mep import get_mep_for_date
from core.postgres import get_job_pool

TIPO_JOB = "movimientos_propias"

# El valor de `tiposCuenta` que devuelve la cartera propia. Confirmado contra la
# API el 2026-09-04 con `scripts/diag_propias`, que además mandó un valor BASURA
# de control: devolvió 0 filas, o sea que el parámetro DISCRIMINA de verdad y
# estas no son las mismas filas de Comitente con otro nombre.
TIPO_CUENTA = "Propia"

# Mismo lookback que el job de negocio: las correcciones y las diferencias de
# futuros llegan a Aunesa T+1/T+2, así que re-ingerir los últimos hábiles es lo
# que evita perderlas. Idempotente → no duplica.
_LOOKBACK_HABILES = 2

# Las 10 claves del feed, tal cual las manda Aunesa. Si aparece una undécima, el
# `raw` jsonb ya la guarda; promoverla a columna es un ALTER y una línea acá.
_CRUDAS = ("comprobante", "cuenta", "informacion", "unidad", "estado",
           "lugar", "uso", "total", "operador")

# `cuenta` viene "[1839] ACA VALORES TRADING" → id de la cuenta, denormalizado
# para que filtrar por cuenta use índice en vez de un regex por fila.
_RE_ID_CUENTA = re.compile(r"^\[(\d+)\]")

# Freno de la reconciliación: si Aunesa contesta parcial (timeout a mitad de
# página), marcar todo lo ausente anularía medio día de golpe. Idéntico al del
# job de negocio, y por la misma razón.
_TOPE_ANULACION_PCT = 0.20
_TOPE_ANULACION_MIN = 5


def _num(x: Any) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _id_linea(raw: dict) -> str:
    """md5 de la fila cruda. Determinista: claves ordenadas y separadores fijos,
    así la misma línea da el mismo hash en cada corrida (y el upsert es un no-op)."""
    canon = json.dumps(raw, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), default=str)
    # md5 acá NO es criptografía: es una huella de contenido para desduplicar.
    return hashlib.md5(canon.encode("utf-8")).hexdigest()


def _extract_id_cuenta(cuenta: str | None) -> str | None:
    m = _RE_ID_CUENTA.match(cuenta or "")
    return m.group(1) if m else None


def _moneda(raw: dict, parsed: dict | None) -> str | None:
    """En una línea de DINERO la moneda es la `unidad` misma; en una de TÍTULO
    (unidad = "[4711] AL30") no hay, y se usa la que trae el texto parseado."""
    unidad = str(raw.get("unidad") or "").strip().upper()
    if unidad in svc.MONEDAS:
        return unidad
    return (parsed or {}).get("moneda")


def linea_a_doc(mov: dict, fecha_iso: str, ocurrencia: int,
                ahora: datetime, mep: float | None) -> dict:
    """Movimiento ENRIQUECIDO (`svc._enriquecer`) → fila de la tabla.

    `importe` es el `total` con signo CLIENTE (+ ingreso / − egreso), igual que
    en negocio_movimientos. Ojo: en una línea de TÍTULO eso son VN/cuotapartes,
    no plata — la columna `unidad` es la que dice de cuál de las dos se trata.
    `cantidad`/`precio` salen del TEXTO del boleto, así que se repiten en todas
    las líneas del mismo comprobante: son del boleto, no de la línea.
    """
    raw = {k: v for k, v in mov.items() if not k.startswith("_")}
    parsed = mov.get("_parsed") or {}
    return {
        "fecha":        fecha_iso,
        "id_linea":     _id_linea(raw),
        "ocurrencia":   ocurrencia,
        **{k: (raw.get(k) if k != "total" else _num(raw.get("total"))) for k in _CRUDAS},
        "id_cuenta":    _extract_id_cuenta(raw.get("cuenta")),
        "categoria":    mov.get("_categoria"),
        "op":           parsed.get("op"),
        "ticker":       parsed.get("ticker"),
        "cantidad":     parsed.get("cantidad"),
        "precio":       parsed.get("precio"),
        "importe":      mov.get("_total_cliente"),
        "moneda":       _moneda(raw, parsed),
        "plazo":        parsed.get("plazo"),
        "mep":          mep,
        "raw":          json.dumps(raw, ensure_ascii=False, default=str),
        "ingestado_en": ahora,
    }


def _numerar(docs: list[dict]) -> list[dict]:
    """Asigna `ocurrencia` 1..N a las líneas byte-idénticas del mismo día.
    Sin esto, dos filas realmente iguales colapsarían en una sola por la PK —
    que es justo lo que este job NO puede hacer (no se pierde ni una)."""
    vistos: Counter[str] = Counter()
    for d in docs:
        vistos[d["id_linea"]] += 1
        d["ocurrencia"] = vistos[d["id_linea"]]
    return docs


_SQL_VIVOS = ("SELECT 1 FROM unnest(%(ids)s::text[], %(occs)s::smallint[]) AS v(id, oc) "
              "WHERE v.id = m.id_linea AND v.oc = m.ocurrencia")


def _reconciliar(cur, fecha_iso: str, docs: list[dict], logger) -> tuple[int, int]:
    """Marca `anulado_en` en las líneas de `fecha_iso` que Aunesa ya no devuelve.

    Con PK por hash, una línea CORREGIDA entra como fila nueva y la vieja deja de
    volver → queda marcada acá. Devuelve (marcadas, candidatos_sin_marcar): el
    segundo es > 0 sólo cuando el tope abortó la anulación.
    """
    p = {"fecha": fecha_iso,
         "ids": [d["id_linea"] for d in docs],
         "occs": [d["ocurrencia"] for d in docs]}
    cur.execute(
        "SELECT count(*) AS total, count(*) FILTER (WHERE m.anulado_en IS NULL "
        f"  AND NOT EXISTS ({_SQL_VIVOS})) AS candidatos "
        "  FROM movimientos_propias m WHERE m.fecha = %(fecha)s", p)
    total, candidatos = cur.fetchone()
    if not candidatos:
        return 0, 0

    tope = max(_TOPE_ANULACION_MIN, int(total * _TOPE_ANULACION_PCT))
    if candidatos > tope:
        logger.error(
            "ANULACIÓN ABORTADA en %s: %d candidatos sobre %d líneas (tope %d). "
            "Aunesa probablemente respondió parcial. NO se marcó nada — revisar a mano.",
            fecha_iso, candidatos, total, tope)
        return 0, candidatos

    cur.execute(
        "UPDATE movimientos_propias m SET anulado_en = now() "
        " WHERE m.fecha = %(fecha)s AND m.anulado_en IS NULL "
        f"   AND NOT EXISTS ({_SQL_VIVOS})", p)
    logger.warning("Anuladas %d línea(s) en %s (Aunesa dejó de devolverlas).",
                   candidatos, fecha_iso)
    return candidatos, 0


_INSERT = (
    "INSERT INTO movimientos_propias "
    "(fecha, id_linea, ocurrencia, comprobante, cuenta, informacion, unidad, estado, "
    " lugar, uso, total, operador, id_cuenta, categoria, op, ticker, cantidad, precio, "
    " importe, moneda, plazo, mep, raw, ingestado_en) "
    "VALUES (%(fecha)s, %(id_linea)s, %(ocurrencia)s, %(comprobante)s, %(cuenta)s, "
    " %(informacion)s, %(unidad)s, %(estado)s, %(lugar)s, %(uso)s, %(total)s, "
    " %(operador)s, %(id_cuenta)s, %(categoria)s, %(op)s, %(ticker)s, %(cantidad)s, "
    " %(precio)s, %(importe)s, %(moneda)s, %(plazo)s, %(mep)s, %(raw)s, %(ingestado_en)s) "
    "ON CONFLICT (fecha, id_linea, ocurrencia) DO UPDATE SET "
    "comprobante=EXCLUDED.comprobante, cuenta=EXCLUDED.cuenta, "
    "informacion=EXCLUDED.informacion, unidad=EXCLUDED.unidad, estado=EXCLUDED.estado, "
    "lugar=EXCLUDED.lugar, uso=EXCLUDED.uso, total=EXCLUDED.total, "
    "operador=EXCLUDED.operador, id_cuenta=EXCLUDED.id_cuenta, "
    "categoria=EXCLUDED.categoria, op=EXCLUDED.op, ticker=EXCLUDED.ticker, "
    "cantidad=EXCLUDED.cantidad, precio=EXCLUDED.precio, importe=EXCLUDED.importe, "
    "moneda=EXCLUDED.moneda, plazo=EXCLUDED.plazo, mep=EXCLUDED.mep, raw=EXCLUDED.raw, "
    "ingestado_en=EXCLUDED.ingestado_en, anulado_en=NULL")


def run(fecha_d: date, dry: bool = False) -> dict:
    logger = logging.getLogger(TIPO_JOB)
    fecha_iso = fecha_d.isoformat()

    consolidado = svc.fetch_y_consolidar(
        fecha=fecha_d, tipos_cuenta=TIPO_CUENTA, aplicar_filtros=False)
    movimientos = consolidado["movimientos"]
    logger.info("%s · Aunesa devolvió %d líneas (tiposCuenta=%s, sin filtros)",
                fecha_iso, len(movimientos), TIPO_CUENTA)

    if not movimientos:
        return {"fecha": fecha_iso, "lineas": 0, "upsertadas": 0,
                "anuladas": 0, "anulacion_abortada": 0}

    mep = get_mep_for_date(fecha_iso)
    if mep is None:
        logger.warning("Sin MEP para %s — las líneas quedan con mep=null.", fecha_iso)
    ahora = datetime.now(UTC)
    docs = _numerar([linea_a_doc(m, fecha_iso, 1, ahora, mep) for m in movimientos])

    if dry:
        cats = Counter(d["categoria"] or "otro" for d in docs)
        logger.info("[DRY] %s · %d líneas persistirían · %s", fecha_iso, len(docs),
                    dict(cats.most_common(6)))
        return {"fecha": fecha_iso, "lineas": len(docs), "upsertadas": 0,
                "anuladas": 0, "anulacion_abortada": 0, "dry": True}

    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(_INSERT, docs)
        anuladas, abortadas = _reconciliar(cur, fecha_iso, docs, logger)
        conn.commit()
    logger.info("%s · upsert OK: %d líneas → operaciones.movimientos_propias",
                fecha_iso, len(docs))
    return {"fecha": fecha_iso, "lineas": len(docs), "upsertadas": len(docs),
            "anuladas": anuladas, "anulacion_abortada": abortadas}


def _parse_dia(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fecha", help="YYYY-MM-DD; ingesta SÓLO ese día")
    parser.add_argument("--desde", help="YYYY-MM-DD; backfill desde (default hoy)")
    parser.add_argument("--hasta", help="YYYY-MM-DD; backfill hasta (default hoy)")
    parser.add_argument("--dry", action="store_true", help="no escribe; reporta qué haría")
    args = parser.parse_args()

    hoy = (datetime.now(UTC) - timedelta(hours=3)).date()  # ART

    from core.calendario import habiles_entre, ultimos_habiles
    try:
        if args.fecha:
            dias = [_parse_dia(args.fecha)]
        elif args.desde or args.hasta:
            desde_d = _parse_dia(args.desde) if args.desde else hoy
            hasta_d = _parse_dia(args.hasta) if args.hasta else hoy
            if desde_d > hasta_d:
                print("--desde no puede ser mayor que --hasta")
                return 1
            dias = habiles_entre(desde_d, hasta_d)
        else:
            # Default del cron: hoy + los últimos hábiles (calendario ÚNICO, así un
            # feriado no se come un lugar de la ventana de recuperación T+1).
            dias = ultimos_habiles(hoy, _LOOKBACK_HABILES)
    except ValueError as e:
        print(f"fecha mal formada (YYYY-MM-DD): {e}")
        return 1

    if not dias:
        print("→ el rango no tiene días hábiles, nada que hacer")
        return 0

    from core.job_runs import JobRunLogger
    with JobRunLogger(TIPO_JOB) as jr:
        agg = {"dias": len(dias), "rango": f"{dias[0]}..{dias[-1]}", "lineas": 0,
               "upsertadas": 0, "anuladas": 0, "anulacion_abortada": 0}
        abortadas: list[str] = []
        for i, d in enumerate(dias):
            try:
                res = run(fecha_d=d, dry=args.dry)
            except Exception as e:
                # Un día que falla NO puede tumbar un backfill de 26: se anota y sigue.
                # El run queda `partial`, que es lo que el Manager tiene que mostrar.
                jr.error(f"{d}: {type(e).__name__}: {e}")
                continue
            for k in ("lineas", "upsertadas", "anuladas", "anulacion_abortada"):
                agg[k] += res.get(k, 0) or 0
            if res.get("anulacion_abortada"):
                abortadas.append(f"{d}: {res['anulacion_abortada']} candidatas sin marcar")
            if i < len(dias) - 1:
                time.sleep(2)  # throttle a la API del custodio (REGLA #4)
        for k, v in agg.items():
            jr.set_stat(k, v)
        jr.set_stat("anulacion_abortada_lista", abortadas)
        jr.set_stat("modo", "dry" if args.dry else "")
    print(f"\n→ {agg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
