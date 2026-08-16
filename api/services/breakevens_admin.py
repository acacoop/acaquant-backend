"""api/services/breakevens_admin.py — curaduría de pares de breakevens.

El motor `engines/breakevens.py::cargar_pares` empareja cada Lecap/Boncap con el
CER de vto más cercano (±20 días) de forma AUTOMÁTICA. A veces empareja mal (ej.
S13N6 ↔ TX26 → BE mensual −35%), y a veces directamente NO arma un par que sí
interesa (el dedup deja un solo Lecap por CER, la tolerancia de ±20 días deja
afuera al resto).

Este módulo es la curaduría COMPLETA, en los dos sentidos, sin tocar el motor:

- **EXCLUIR** un par malo → `mercado.breakevens_overrides`. El motor lo sigue
  calculando; el reader (`mercado_hist_sql.get_breakevens`) lo filtra al leer.
- **AGREGAR** un par manual Lecap↔CER elegido a mano →
  `mercado.breakevens_manuales`. El motor NO lo conoce (carga sus pares una vez
  al arrancar), así que el BE se calcula EN LA LECTURA con la MISMA función del
  motor (`calcular_breakevens`) — misma fórmula, cero copias.

Los dos tienen efecto instantáneo y sobreviven al reinicio del motor.

También vive acá el DIAGNÓSTICO de cobertura (`diagnostico()`): por qué cada bono
`tasa_fija` del master entra o no entra a la matriz. Lo consumen el endpoint de
Manager y `scripts/diag_breakevens_cobertura.py` — una sola fuente de verdad,
para que la pantalla y el script no puedan contradecirse.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from api.cache import cached

_TABLE = "mercado.breakevens_overrides"
_TABLE_MANUAL = "mercado.breakevens_manuales"


def _ensure(cur) -> None:
    """Self-create de la tabla de overrides (tolera drift de schema)."""
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
        "lecap text NOT NULL, cer text NOT NULL, "
        "updated_by text, updated_at timestamptz, "
        "PRIMARY KEY (lecap, cer))")


def _ensure_manual(cur) -> None:
    """Self-create de la tabla de pares manuales (mismo patrón que _ensure)."""
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {_TABLE_MANUAL} ("
        "lecap text NOT NULL, cer text NOT NULL, "
        "creado_por text, creado_at timestamptz, "
        "PRIMARY KEY (lecap, cer))")


# ── Exclusiones ──────────────────────────────────────────────────────────────

def get_excluidos() -> set[tuple[str, str]]:
    """Set de pares (lecap, cer) excluidos — claves = tickers CORTOS (los que trae
    el doc del motor: S13N6, TX26…). set() ante error (fail-open: preferimos mostrar
    de más y no romper la vista de breakevens)."""
    from psycopg.rows import dict_row

    from core.postgres import get_pool
    try:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            _ensure(cur)
            cur.execute(f"SELECT lecap, cer FROM {_TABLE}")
            rows = cur.fetchall()
            conn.commit()
        return {(r["lecap"], r["cer"]) for r in rows}
    except Exception:
        return set()


def set_exclusion(lecap: str, cer: str, excluir: bool, email: str) -> dict[str, Any]:
    """Excluir (excluir=True) o reincluir (False) el par (lecap, cer). Idempotente."""
    if not lecap or not cer:
        raise ValueError("lecap y cer son obligatorios")

    from core.postgres import get_pool
    now = datetime.now(UTC)
    with get_pool().connection() as conn, conn.cursor() as cur:
        _ensure(cur)
        if excluir:
            cur.execute(
                f"INSERT INTO {_TABLE} (lecap, cer, updated_by, updated_at) "
                f"VALUES (%s, %s, %s, %s) ON CONFLICT (lecap, cer) "
                f"DO UPDATE SET updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at",
                (lecap, cer, email, now))
        else:
            cur.execute(f"DELETE FROM {_TABLE} WHERE lecap = %s AND cer = %s", (lecap, cer))
        conn.commit()
    return {"lecap": lecap, "cer": cer, "excluido": excluir}


# ── Pares manuales ───────────────────────────────────────────────────────────

def get_manuales() -> set[tuple[str, str]]:
    """Set de pares (lecap, cer) agregados a mano. Fail-open a set(): si la lectura
    falla preferimos servir solo los pares del motor antes que romper la vista."""
    from psycopg.rows import dict_row

    from core.postgres import get_pool
    try:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            _ensure_manual(cur)
            cur.execute(f"SELECT lecap, cer FROM {_TABLE_MANUAL}")
            rows = cur.fetchall()
            conn.commit()
        return {(r["lecap"], r["cer"]) for r in rows}
    except Exception:
        return set()


def set_manual(lecap: str, cer: str, agregar: bool, email: str) -> dict[str, Any]:
    """Agregar (agregar=True) o borrar (False) un par manual. Idempotente.

    Valida contra `mercado.curvas` que el lecap sea `tasa_fija` y el cer sea `cer`:
    un par al revés daría un BE sin sentido y el error sería mudo (número raro en
    la vista, sin pista de por qué)."""
    lecap, cer = (lecap or "").strip(), (cer or "").strip()
    if not lecap or not cer:
        raise ValueError("lecap y cer son obligatorios")

    from core import curvas_sql
    if agregar:
        ld, cd = curvas_sql.find_one(lecap), curvas_sql.find_one(cer)
        if ld is None:
            raise ValueError(f"'{lecap}' no existe en mercado.curvas")
        if cd is None:
            raise ValueError(f"'{cer}' no existe en mercado.curvas")
        # Mismo predicado que `candidatos()` (los ejes, no la columna `curva`): si
        # divergieran, un bono que el combo ofrece se rechazaría al guardarlo.
        if not curvas_sql.esta_en_curva(ld, "tasa_fija"):
            raise ValueError(f"'{lecap}' no es de la curva tasa_fija")
        if not curvas_sql.esta_en_curva(cd, "cer"):
            raise ValueError(f"'{cer}' no es de la curva cer")

    from core.postgres import get_pool
    now = datetime.now(UTC)
    with get_pool().connection() as conn, conn.cursor() as cur:
        _ensure_manual(cur)
        if agregar:
            cur.execute(
                f"INSERT INTO {_TABLE_MANUAL} (lecap, cer, creado_por, creado_at) "
                f"VALUES (%s, %s, %s, %s) ON CONFLICT (lecap, cer) "
                f"DO UPDATE SET creado_por = EXCLUDED.creado_por, creado_at = EXCLUDED.creado_at",
                (lecap, cer, email, now))
        else:
            cur.execute(f"DELETE FROM {_TABLE_MANUAL} WHERE lecap = %s AND cer = %s", (lecap, cer))
        conn.commit()
    return {"lecap": lecap, "cer": cer, "manual": agregar}


def candidatos() -> dict[str, Any]:
    """Los dos selectores del '+': bonos `tasa_fija` a la izquierda, `cer` a la
    derecha, ordenados por vencimiento. `apto` dice si el bono tiene el campo que
    el BE necesita — sin `flujo_vencimiento` (lecap) o sin `cer_emision` (CER) el
    par se puede crear igual, pero el BE va a salir vacío. Mejor avisarlo en el
    combo que devolver un '--' inexplicable."""
    from core import curvas_sql
    hoy = date.today()

    def _fila(d: dict, campo: str) -> dict:
        v = _vto(d)
        return {
            "ticker_corto": d.get("ticker_corto"),
            "fecha_vencimiento": v.isoformat() if v else None,
            "dias": (v - hoy).days if v else None,
            "apto": bool(d.get(campo)),
        }

    lec = [_fila(d, "flujo_vencimiento") for d in curvas_sql.por_curva("tasa_fija")
           if d.get("ticker_corto")]
    cer = [_fila(d, "cer_emision") for d in curvas_sql.por_curva("cer")
           if d.get("ticker_corto")]
    key = lambda x: (x["fecha_vencimiento"] or "9999-12-31", x["ticker_corto"])  # noqa: E731
    return {"tasa_fija": sorted(lec, key=key), "cer": sorted(cer, key=key)}


def _calcular_manuales(manuales: set[tuple[str, str]]) -> list[dict]:
    """BE de los pares manuales, calculado con la MISMA función que usa el motor.

    El motor arma sus pares una sola vez al arrancar → no puede conocer un par
    creado hoy. En vez de duplicar la fórmula acá (que driftearía), se arman los
    pares con la shape que produce `cargar_pares()` y se llama a
    `calcular_breakevens` tal cual, con `min_dias=0` y sin filtro de IPC: los
    filtros del motor son heurísticas para no ensuciar la matriz automática, y un
    par que una persona eligió a dedo no se descarta en silencio."""
    if not manuales:
        return []

    from core import curvas_sql
    idx = {d["ticker_corto"]: d for d in curvas_sql.cargar_todos() if d.get("ticker_corto")}

    pares = []
    for lecap, cer in sorted(manuales):
        ld, cd = idx.get(lecap), idx.get(cer)
        # El bono pudo darse de baja (jobs/cleanup_curvas borra los vencidos) →
        # el par manual queda huérfano y simplemente no se muestra.
        if not ld or not cd or not ld.get("fecha_vencimiento"):
            continue
        pares.append({
            "lecap_ticker":      ld.get("ticker"),
            "lecap_corto":       ld.get("ticker_corto"),
            "cer_ticker":        cd.get("ticker"),
            "cer_corto":         cd.get("ticker_corto"),
            "fecha_vencimiento": str(ld["fecha_vencimiento"])[:10],
            "flujo_vto_lecap":   ld.get("flujo_vencimiento"),
            "vn_cer":            cd.get("valor_nominal") or 100,
            "cer_emision":       cd.get("cer_emision"),
        })
    if not pares:
        return []

    from engines.breakevens import (
        _filtrar,
        calcular_breakevens,
        cargar_dias_habiles,
        obtener_metricas,
        obtener_valor_cer,
        ultimo_cer_publicado,
    )

    lec_t = [p["lecap_ticker"] for p in pares if p.get("lecap_ticker")]
    cer_t = [p["cer_ticker"] for p in pares if p.get("cer_ticker")]
    snap = obtener_metricas(lec_t + cer_t)
    cer_max = ultimo_cer_publicado()

    out = calcular_breakevens(
        pares,
        _filtrar(snap, lec_t, "tem"),
        _filtrar(snap, cer_t, "paridad"),
        _filtrar(snap, cer_t, "tea"),
        date.today(),
        ultimo_ipc_mes=None,          # un par elegido a mano no se filtra por IPC
        dias_habiles=cargar_dias_habiles(),
        fecha_cer_max=cer_max,
        precios=_filtrar(snap, lec_t + cer_t, "last_price", positivo=True),
        cer_actual=obtener_valor_cer(cer_max) if cer_max else None,
        min_dias=0,                   # ni por plazo mínimo
    )
    for e in out:
        e["manual"] = True
    return out


@cached(ttl=15)
def pares_manuales_calculados() -> list[dict]:
    """Versión cacheada para el reader público (`get_breakevens`, que a su vez ya
    está cacheado 30s aguas arriba). Sin pares manuales el costo es una query
    trivial; con pares, una pasada de snapshot cada 15s. Manager NO usa esta:
    llama a `_calcular_manuales` directo para que el '+' se vea al instante."""
    try:
        return _calcular_manuales(get_manuales())
    except Exception:
        return []   # fail-open: un par manual roto no puede tumbar Renta Fija


# ── Matriz de Manager ────────────────────────────────────────────────────────

def list_pares_con_estado() -> dict[str, Any]:
    """Pares VIVOS del motor + los MANUALES, cada uno con su flag, para la matriz
    de Manager. Muestra TODOS (incluidos los excluidos) para poder reincluirlos."""
    from api.services.mercado_hist_sql import breakevens_docs_raw
    docs = breakevens_docs_raw()
    pares = (docs[0].get("pares") if docs else None) or []
    excl = get_excluidos()
    manuales = get_manuales()

    out = [{**p, "manual": False, "excluido": (p.get("lecap"), p.get("cer")) in excl}
           for p in pares]
    # Un par manual que el motor YA arma no se duplica (puede pasar tras un
    # reinicio: lo agregaste a mano y después el motor lo empezó a emparejar solo).
    del_motor = {(p.get("lecap"), p.get("cer")) for p in pares}
    for p in _calcular_manuales({m for m in manuales if m not in del_motor}):
        out.append({**p, "manual": True, "excluido": (p.get("lecap"), p.get("cer")) in excl})

    return {
        "updated_at":  docs[0].get("updated_at") if docs else None,
        "pares":       out,
        "n":           len(out),
        "n_excluidos": len(excl),
        "n_manuales":  len(manuales),
    }


# ── Diagnóstico de cobertura ─────────────────────────────────────────────────
#
# Responde "¿por qué este bono NO aparece en BREAKEVENS?". Replica el
# emparejamiento del motor guardando el MOTIVO de cada descarte, en vez de
# tirarlo en silencio como hace `cargar_pares()`.

def _vto(doc: dict) -> date | None:
    v = doc.get("fecha_vencimiento")
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _mes_inflacion(fecha_vto: date) -> str:
    """Mismo cálculo que el motor: el BE de un par que vence en M pricea el IPC de M−2."""
    y, m = fecha_vto.year, fecha_vto.month - 2
    if m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def _emparejar(grupos: dict[str, list[dict]], hoy: date, ipc_mes: str | None,
               max_diff: int, min_dias: int) -> list[dict]:
    """Una fila por bono `tasa_fija`, con `estado` (PAR / FILTRADO / FUERA) y el
    motivo textual si no entra."""
    lecaps, cers = grupos.get("tasa_fija", []), grupos.get("cer", [])

    filas: list[dict] = []
    for lecap in lecaps:
        v_lec = _vto(lecap)
        fila: dict[str, Any] = {"lecap": lecap.get("ticker_corto"),
                                "vto": v_lec.isoformat() if v_lec else None,
                                "cer": None, "diff": None, "dias": None,
                                "mes_inflacion": None, "estado": "", "motivo": ""}
        if v_lec is None:
            fila["estado"], fila["motivo"] = "FUERA", "sin fecha_vencimiento válida"
            filas.append(fila)
            continue

        mejor, mejor_diff = None, None
        for cer in cers:
            v_cer = _vto(cer)
            if v_cer is None:
                continue
            diff = abs((v_lec - v_cer).days)
            if mejor_diff is None or diff < mejor_diff:
                mejor, mejor_diff = cer, diff

        if mejor is None:
            fila["estado"], fila["motivo"] = "FUERA", "no hay ningún CER en el master"
        elif mejor_diff > max_diff:
            fila["estado"] = "FUERA"
            fila["cer"], fila["diff"] = mejor.get("ticker_corto"), mejor_diff
            fila["motivo"] = f"CER más cercano a {mejor_diff}d (tolerancia ±{max_diff}d)"
        else:
            fila["cer"], fila["diff"] = mejor.get("ticker_corto"), mejor_diff
            fila["estado"] = "PAR"
        filas.append(fila)

    # Dedup del motor: un CER solo puede estar en UN par (gana la menor diff).
    # OJO — el motor NO le busca al perdedor el segundo CER más cercano: lo tira.
    mejor_por_cer: dict[str, dict] = {}
    for f in filas:
        if f["estado"] != "PAR":
            continue
        c = f["cer"]
        if c not in mejor_por_cer or f["diff"] < mejor_por_cer[c]["diff"]:
            mejor_por_cer[c] = f
    ganadores = {id(f) for f in mejor_por_cer.values()}
    for f in filas:
        if f["estado"] == "PAR" and id(f) not in ganadores:
            f["estado"] = "FUERA"
            f["motivo"] = (f"dedup: el CER {f['cer']} se lo quedó "
                           f"{mejor_por_cer[f['cer']]['lecap']} (diff menor)")

    # Filtros que aplica calcular_breakevens() sobre los pares que sobrevivieron.
    for f in filas:
        if f["estado"] != "PAR":
            continue
        v = date.fromisoformat(f["vto"])
        f["dias"] = (v - hoy).days
        f["mes_inflacion"] = _mes_inflacion(v)
        if f["dias"] < min_dias:
            f["estado"] = "FILTRADO"
            f["motivo"] = f"vto a {f['dias']}d (mínimo {min_dias}d)"
        elif ipc_mes and f["mes_inflacion"] <= ipc_mes:
            f["estado"] = "FILTRADO"
            f["motivo"] = (f"pricea el IPC {f['mes_inflacion']}, ya publicado "
                           f"(último: {ipc_mes})")

    return sorted(filas, key=lambda x: (x["vto"] or "9999-12-31", x["lecap"] or ""))


def diagnostico() -> dict[str, Any]:
    """Cobertura completa de la matriz. Lo consumen el panel de Manager y
    `scripts/diag_breakevens_cobertura.py` — una sola fuente para los dos."""
    from core import curvas_sql
    from engines.breakevens import MAX_DIFF_DIAS, MIN_DIAS_PLAZO, ultimo_ipc_publicado

    hoy = date.today()
    grupos = curvas_sql.agrupado_por_curva()
    try:
        ipc_mes = ultimo_ipc_publicado()
    except Exception:
        ipc_mes = None

    filas = _emparejar(grupos, hoy, ipc_mes, MAX_DIFF_DIAS, MIN_DIAS_PLAZO)
    usados = {f["cer"] for f in filas if f["estado"] == "PAR"}

    # Frescura de lo que ve la vista + cuántos pares quedaron sin número.
    from api.services.mercado_hist_sql import breakevens_docs_raw
    docs = breakevens_docs_raw()
    doc = docs[0] if docs else {}
    pares_pub = doc.get("pares") or []
    sin_be = [{"lecap": p.get("lecap"), "cer": p.get("cer"),
               "falta": [k for k in ("precio_lecap", "precio_cer", "meses_pendientes")
                         if not p.get(k)]}
              for p in pares_pub if p.get("breakeven_mensual") is None]

    return {
        "hoy": hoy.isoformat(),
        "ultimo_ipc": ipc_mes,
        "max_diff_dias": MAX_DIFF_DIAS,
        "min_dias_plazo": MIN_DIAS_PLAZO,
        "master": {
            curva: {
                "n": len(docs_c),
                "vto_max": max((v.isoformat() for v in
                                (_vto(d) for d in docs_c) if v), default=None),
                "emision_max": max((str(d["fecha_emision"])[:10] for d in docs_c
                                    if d.get("fecha_emision")), default=None),
            }
            for curva, docs_c in sorted(grupos.items())
        },
        "filas": filas,
        "n_par": sum(1 for f in filas if f["estado"] == "PAR"),
        "n_lecaps": len(filas),
        "cer_sin_par": sorted(str(d.get("ticker_corto")) for d in grupos.get("cer", [])
                              if d.get("ticker_corto") and d["ticker_corto"] not in usados),
        "publicado": {
            "fecha":      doc.get("fecha"),
            "updated_at": doc.get("updated_at"),
            "n_pares":    len(pares_pub),
            "n_con_be":   sum(1 for p in pares_pub if p.get("breakeven_mensual") is not None),
            "sin_be":     sin_be,
        },
        "excluidos": sorted(get_excluidos()),
        "manuales":  sorted(get_manuales()),
    }
