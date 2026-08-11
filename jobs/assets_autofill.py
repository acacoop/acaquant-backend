"""jobs/assets_autofill.py — autocompletado del catálogo `portafolio.assets`.

El panel Manager → TÍTULOS → ASSETS se llena a mano, pero una parte de los campos
NO es una decisión: sale de la propia `unidad` que manda Aunesa. Este job aplica
esas reglas determinísticas sobre los campos que están VACÍOS y deja para la mesa
lo que sí es criterio humano (EMISOR, CALIFICACIÓN).

El invariante que NO se negocia:
  * NUNCA pisa un valor cargado. Si la regla propone algo distinto de lo que ya
    hay, no escribe: lo reporta como CONFLICTO — así una etiqueta mal escrita en
    el catálogo se ve en el run en vez de duplicarse en silencio.

Eso es lo que hace seguro tener acá una regla HEURÍSTICA (ver más abajo): la
máquina propone para no clasificar miles de assets a mano, el humano corrige en
Manager, y a partir de ahí el job no vuelve a opinar sobre ese valor.

Cada regla es una función `fila → {columna: valor}` sobre la fila que le llega.
La mayoría deriva de la propia `unidad` (misma unidad → mismo resultado, para
siempre); `financiamiento_clase` es la excepción y usa el nominal de la última
tenencia, que `leer_catalogo` agrega a la fila.

Reglas v1:
  * financiamiento — pagarés/cheques del negocio de financiamiento. La unidad
    tiene firma propia (`[TICKER] TICKER Nro. <nro> Vto. <dd/mm/aaaa>`, el ticker
    repetido dentro y fuera del corchete) → CARTERA, TICKER y VENCIMIENTO.
    INSTRUMENTO y CODIGO_CNV no existen para estos papeles: no se tocan.
  * financiamiento_clase — CLASE_ACTIVO `HD`/`DL` de esos mismos papeles, según
    el nominal (≤ 5.000.000 → HD, > → DL). ÚNICA regla heurística del job:
    existe porque la vista FINANCIAMIENTO no puede graficar juntas dos escalas
    tan distintas y clasificar el catálogo a mano no era viable. Backfill y
    mantenimiento son el mismo comando (ver "Uso").
  * fci — `[<id>] CAFCI<n>-<m> - <nombre>` → CARTERA, TICKER (nombre del fondo) y
    CAFCI (código). Es la misma derivación que hace el auto-alta del writer diario
    (`core.cafci`), acá backfilleada sobre lo que ya está en el catálogo.
  * ticker — TICKER para el resto del catálogo, sea cual sea la cartera: sale del
    `[<id>] <descripción>` de Aunesa.

Sumar una regla = una función `fila → {columna: valor}` + una entrada en REGLAS;
el motor se ocupa del "solo si está vacío", del reporte y de la escritura.

Cron: L-V 11:40 UTC, después del writer diario (11:00) que da de alta las unidades
nuevas. La API relee el catálogo por TTL (`assets_sql`, 300s).

Uso:
    python -m jobs.assets_autofill                              # todas las reglas (cron)
    python -m jobs.assets_autofill --dry                        # qué completaría, sin escribir
    python -m jobs.assets_autofill --regla financiamiento_clase # backfill de HD/DL

El BACKFILL y el mantenimiento diario son EL MISMO comando: el job siempre mira
lo que está vacío, así que la primera corrida completa el histórico y las
siguientes sólo tocan lo que entró nuevo. Es idempotente por construcción.
"""
from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from core.cafci import es_fci_unidad, extract_cafci, nombre_fci
from core.job_runs import JobRunLogger
from core.postgres import get_pool

# Columnas que el job puede escribir. Es una allowlist de verdad: los nombres se
# interpolan en el UPDATE, así que una regla no puede inventar una columna.
_ESCRIBIBLES = frozenset({"cartera", "clase_activo", "ticker", "instrumento",
                          "cafci", "vencimiento", "codigo_cnv"})
_LEIBLES = ("unidad", "cartera", "clase_activo", "emisor", "ticker",
            "instrumento", "calificacion", "cafci", "vencimiento", "codigo_cnv")
_ACTOR = "job:assets_autofill"


def _vacio(v) -> bool:
    """Mismo criterio de "vacío" que el panel (`assets_sql._EMPTY`)."""
    s = "" if v is None else str(v).strip()
    return not s or s.upper() == "NO APLICA"


# ── Reglas ───────────────────────────────────────────────────────────────────

# `[*BIN031000050] *BIN031000050 Nro. 29805263 Vto. 03/10/2026`
# El ticker aparece DOS veces (dentro y fuera del corchete) y eso es lo que hace
# segura la firma: en el resto del catálogo el corchete lleva un id de especie que
# NO se repite (`[9131] YPFD - CEDEAR YPF`).
_RE_FINANCIAMIENTO = re.compile(
    r"^\[(?P<tk>[^\]]+)\]\s+(?P<tk2>\S+)\s+Nro\.?\s*\S+\s+"
    r"Vto\.?\s*(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{4})$")

CARTERA_FINANCIAMIENTO = "FINANCIAMIENTO"


def _regla_financiamiento(row: dict) -> dict[str, str]:
    m = _RE_FINANCIAMIENTO.match((row.get("unidad") or "").strip())
    if not m or m["tk"] != m["tk2"]:
        return {}
    try:
        vto = date(int(m["y"]), int(m["m"]), int(m["d"]))
    except ValueError:      # 31/02: la unidad miente, mejor no completar nada
        return {}
    return {"cartera": CARTERA_FINANCIAMIENTO, "ticker": m["tk"],
            "vencimiento": vto.isoformat()}


# CLASE_ACTIVO de financiamiento: HD (hard dollar) o DL (dólar linked).
#
# POR QUÉ EXISTE: son ESCALAS distintas. Un HD de 5.000 y un DL de 27.000.000 en
# el mismo gráfico dejan al HD invisible — la vista FINANCIAMIENTO no puede
# sumarlos ni graficarlos juntos, y necesita saber cuál es cuál.
#
# CÓMO SE DECIDE (regla del user, 2026-08-11): por el NOMINAL. Nominal chico =
# el papel está expresado en dólares de verdad (HD); nominal grande = está en la
# escala del dólar linked (DL).
#
# ⚠️ ES UNA HEURÍSTICA DE ARRANQUE, no una verdad. Existe para no clasificar
# 2.000 assets a mano. Se corrige en Manager → ASSETS y el job NUNCA pisa lo
# corregido (invariante del módulo): cada valor que un humano toca queda fijo y
# la heurística no vuelve a opinar sobre él.
# <= 5.000.000 → HD · > 5.000.000 → DL
#
# POR QUÉ 5.000.000 (corregido 2026-08-11, el primer intento fue 5.000 y clasificó
# 260 papeles como DL cuando no lo eran). La evidencia son los nominales y sus
# tasas, que vienen del texto real de los boletos (ver `core/mav_tasa.py`):
#
#     30.000 @ 7%        100.000 @ 6%        500.000 @ -0,5%     → tasas de DÓLAR
#     27.000.000 @ 39,5%                                          → tasa de PESOS
#
# La frontera real está entre 500.000 y 27.000.000: un papel que rinde 6% anual
# no puede estar expresado en pesos. Con el corte en 5.000 caían del lado DL
# instrumentos que pagan tasa de dólar, y CASI TODO el catálogo terminaba en DL
# — que un lado se lleve todo es la señal de que el umbral está mal puesto.
_UMBRAL_HD = 5_000_000.0

# De dónde sale el nominal del asset: la SUMA de lo que hay en la última
# tenencia de esa unidad. Es lo más cercano al valor nominal del papel que se
# puede armar sin un campo propio (el asset no tiene "monto"): si el pagaré está
# repartido entre varios comitentes, las partes suman el total emitido.
_NOMINAL_COL = "nominal"


def _regla_financiamiento_clase(row: dict) -> dict[str, str]:
    if not _RE_FINANCIAMIENTO.match((row.get("unidad") or "").strip()):
        return {}
    nominal = row.get(_NOMINAL_COL)
    if nominal is None:
        return {}                       # sin tenencia hoy → no hay de qué inferir
    return {"clase_activo": "HD" if float(nominal) <= _UMBRAL_HD else "DL"}


def _regla_fci(row: dict) -> dict[str, str]:
    unidad = row.get("unidad") or ""
    nombre = nombre_fci(unidad)
    if not nombre:
        return {}
    out = {"cartera": "FCI", "ticker": nombre}
    codigo = extract_cafci(unidad)
    if codigo:
        out["cafci"] = codigo
    return out


def _es_fci(row: dict) -> bool:
    cartera = str(row.get("cartera") or "").strip().upper()
    return cartera in {"FCI", "CARTERA FCI"} or es_fci_unidad(row.get("unidad"))


# `[<id de especie>] <descripción opcional>` — la forma general de Aunesa. Las
# unidades de cash (`ARS`, `USDL`) no la cumplen y quedan afuera solas.
_RE_UNIDAD = re.compile(r"^\[(?P<id>[^\]]*)\]\s*(?P<resto>.*)$", re.DOTALL)


def _regla_ticker(row: dict) -> dict[str, str]:
    """TICKER para cualquier cartera, derivado de la unidad:

        `[DLR012026]`                          → DLR012026   (sin descripción: el id)
        `[43070] NZC6O - NZC6O - T.DEUDA BNA`  → NZC6O       (hasta el primer guion)
        `[10390] Depósito U$S Ext`             → Depósito U$S Ext  (sin guion: todo)

    Se autoexcluye de las dos carteras que ya tienen su propia derivación: en FCI
    el ticker es el NOMBRE del fondo, que va DESPUÉS del código CAFCI (cortar en el
    primer guion daría 'CAFCI518'); en financiamiento la descripción es el propio
    ticker seguido de Nro./Vto.
    """
    unidad = (row.get("unidad") or "").strip()
    if _es_fci(row) or _RE_FINANCIAMIENTO.match(unidad):
        return {}
    m = _RE_UNIDAD.match(unidad)
    if not m:
        return {}
    resto = m["resto"].strip()
    # `or resto` cubre la descripción que ARRANCA con guion: mejor el texto
    # completo que un ticker vacío.
    tk = (resto.split("-", 1)[0].strip() or resto) if resto else (m["id"] or "").strip()
    return {"ticker": tk} if tk else {}


@dataclass(frozen=True)
class Regla:
    id: str
    titulo: str
    fn: Callable[[dict], dict[str, str]]


REGLAS: list[Regla] = [
    Regla("financiamiento", "Pagarés/cheques de FINANCIAMIENTO", _regla_financiamiento),
    Regla("financiamiento_clase", "HD/DL de financiamiento (por nominal)",
          _regla_financiamiento_clase),
    Regla("fci", "Fondos comunes (código CAFCI + nombre)", _regla_fci),
    Regla("ticker", "Ticker derivado de la unidad (resto del catálogo)", _regla_ticker),
]


# ── Motor ────────────────────────────────────────────────────────────────────

def leer_catalogo() -> list[dict]:
    """Catálogo + el NOMINAL de cada unidad en la última tenencia.

    El nominal no es una columna de `assets` — se agrega acá para que la regla
    `financiamiento_clase` siga siendo una función pura `fila → {columna: valor}`
    como todas las demás, en vez de tener que ir a la base por su cuenta.

    Es UNA query extra y sólo sobre el último día (`ix_tenencia_fecha_unidad`),
    no sobre la tenencia histórica. Las unidades sin tenencia hoy quedan con
    `nominal=None` y la regla no opina sobre ellas.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(_LEIBLES)} FROM portafolio.assets")
        rows = [dict(zip(_LEIBLES, r, strict=True)) for r in cur.fetchall()]
        cur.execute(
            "SELECT unidad, sum(cantidad) FROM portafolio.tenencia "
            "WHERE fecha = (SELECT max(fecha) FROM portafolio.tenencia) "
            "  AND cantidad IS NOT NULL "
            "GROUP BY unidad")
        nominal = {u: (float(c) if c is not None else None) for u, c in cur.fetchall()}
    for r in rows:
        r[_NOMINAL_COL] = nominal.get(r["unidad"])
    return rows


def planificar(rows: Iterable[dict], reglas: Iterable[Regla]) -> tuple[dict, dict]:
    """`({unidad: {columna: valor}}, {regla_id: reporte})`. No toca la base."""
    rows = list(rows)
    cambios: dict[str, dict[str, str]] = defaultdict(dict)
    reporte: dict[str, dict] = {}
    for regla in reglas:
        campos: Counter[str] = Counter()
        conflictos: list[str] = []
        matcheadas = 0
        for row in rows:
            propuesta = regla.fn(row)
            if not propuesta:
                continue
            matcheadas += 1
            unidad = row["unidad"]
            for col, val in propuesta.items():
                if col not in _ESCRIBIBLES:
                    raise ValueError(f"regla {regla.id}: columna no escribible {col!r}")
                planeado = cambios[unidad].get(col)
                if planeado is not None and planeado != val:
                    conflictos.append(f"{unidad} · {col}: otra regla ya propuso "
                                      f"{planeado!r}, esta propone {val!r}")
                    continue
                actual = row.get(col)
                if _vacio(actual):
                    cambios[unidad][col] = val
                    campos[col] += 1
                elif str(actual).strip() != val:
                    conflictos.append(f"{unidad} · {col}: catálogo={str(actual).strip()!r} "
                                      f"regla={val!r}")
        reporte[regla.id] = {"matcheadas": matcheadas, "campos": dict(campos),
                             "conflictos": conflictos}
    return {u: s for u, s in cambios.items() if s}, reporte


def aplicar(cambios: dict[str, dict[str, str]]) -> int:
    """UPDATE de los campos planificados. Agrupa por firma de columnas para mandar
    un `executemany` por combinación en vez de un UPDATE armado por fila."""
    if not cambios:
        return 0
    ts = datetime.now(UTC)
    por_firma: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for unidad, sets in cambios.items():
        por_firma[tuple(sorted(sets))].append(
            {**sets, "_unidad": unidad, "_actor": _ACTOR, "_ts": ts})
    n = 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        for cols, params in por_firma.items():
            asigna = ", ".join(f"{c} = %({c})s" for c in cols)
            cur.executemany(
                f"UPDATE portafolio.assets SET {asigna}, "
                "actualizado_por = %(_actor)s, actualizado_at = %(_ts)s "
                "WHERE unidad = %(_unidad)s", params)
            n += len(params)
        conn.commit()
    return n


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Autocompletado de portafolio.assets")
    ap.add_argument("--dry", action="store_true", help="reporta qué completaría, sin escribir")
    ap.add_argument("--regla", action="append", metavar="ID",
                    help=f"correr solo esta regla (repetible). Ids: {[r.id for r in REGLAS]}")
    args = ap.parse_args()

    pedidas = set(args.regla or ())
    if desconocidas := pedidas - {r.id for r in REGLAS}:
        raise SystemExit(f"regla(s) inexistente(s): {sorted(desconocidas)}")
    reglas = [r for r in REGLAS if not pedidas or r.id in pedidas]

    with JobRunLogger("assets_autofill") as jr:
        rows = leer_catalogo()
        cambios, reporte = planificar(rows, reglas)
        jr.log(f"catálogo: {len(rows)} assets · reglas: {[r.id for r in reglas]}")
        for r in reglas:
            rep = reporte[r.id]
            detalle = ", ".join(f"{c}={n}" for c, n in sorted(rep["campos"].items()))
            jr.log(f"  · {r.id}: {rep['matcheadas']} unidades matchean → "
                   f"{detalle or 'nada vacío que completar'}")
            # En cron los conflictos se capan (son estables: repetirlos enteros todos
            # los días inunda el log). Con --dry se listan todos: es el modo en el que
            # uno los va a revisar y corregir.
            tope = len(rep["conflictos"]) if args.dry else 10
            for c in rep["conflictos"][:tope]:
                jr.log(f"      ⚠ {c}")
            if len(rep["conflictos"]) > tope:
                jr.log(f"      … +{len(rep['conflictos']) - tope} conflicto(s) más "
                       f"(verlos: --dry)")
            jr.set_stat(f"{r.id}_matcheadas", rep["matcheadas"])
            jr.set_stat(f"{r.id}_campos", rep["campos"])
            jr.set_stat(f"{r.id}_conflictos", len(rep["conflictos"]))

        if args.dry:
            for unidad, sets in list(cambios.items())[:20]:
                jr.log(f"    [dry] {unidad} → {sets}")
            jr.log(f"[dry] {len(cambios)} assets se completarían — no se escribió nada")
            jr.set_stat("dry", True)
            return 0

        jr.set_stat("assets_actualizados", aplicar(cambios))
        jr.log(f"✅ {len(cambios)} assets completados (la API los relee en ≤5 min, TTL assets_sql)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
