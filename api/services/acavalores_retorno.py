"""ACA VALORES RETORNO TOTAL — lectura Y carga de `operaciones.acavalores_retorno`.

Módulo PURO (regla de capas de api/services): solo SQL vía `_q`/`_exec` y pandas,
sin FastAPI. Alimenta la tab "ACA VALORES RETORNO TOTAL" de Mesa de Dinero.

LECTURA — `panel()` devuelve las FILAS crudas del período (operación / agente /
papel / cash); la métrica es el CASH (columna "Moneda de Concertación Bruto").
Las sumatorias y el cross-filter interactivo (tocar un agente filtra los demás
paneles) se calculan en el frontend — el volumen por período es chico.

CARGA — `importar()` es la ÚNICA implementación del parseo + escritura, y tiene
DOS puertas que la invocan (REGLA #9: una sola verdad, dos formas de llegar):
    · `POST /api/mesa-dinero/retorno/import` (admin) — el botón de la tab.
    · `python -m scripts.import_acavalores_retorno <archivo.xls>` — la CLI.
Si el parseo viviera en el script, el endpoint tendría una segunda copia que se
desincroniza el día que el informe cambie de formato: dos mitades leyendo el
mismo Excel distinto, cada una coherente consigo misma.

Idempotente por PERÍODO ('YYYY-MM' de la fecha de concertación): importar un mes
BORRA y reinserta solo ese mes; los otros no se tocan. Cortarlo y re-correrlo no
duplica.
"""
from __future__ import annotations

import io
import math
from datetime import UTC, date, datetime
from typing import Any

from api.services._sql import _f, _q
from core.postgres import get_pool

_TABLA = "operaciones.acavalores_retorno"

# Extensiones aceptadas. El informe viene en .xls binario legacy (por eso `xlrd`
# está en requirements); se admiten los formatos nuevos por si Aunesa migra.
EXTENSIONES = (".xls", ".xlsx", ".xlsm")

# Tope del archivo subido. Un mes real ronda las 300 filas / decenas de KB: el
# límite no está para el caso bueno sino para que un PDF de 80 MB elegido por
# error no se parsee en el proceso de la API.
MAX_BYTES = 15 * 1024 * 1024

# Índice de columna en el Excel → nombre de columna en la tabla. El orden es fijo
# (informe estándar de Aunesa). Ver sql/schema.sql :: operaciones.acavalores_retorno.
_COLS: list[tuple[int, str]] = [
    (0, "fondo"),
    (1, "operacion"),
    (2, "fecha_concertacion"),
    (3, "plazo"),
    (4, "fecha_liquidacion"),
    (5, "papel_numero"),
    (6, "papel_descripcion"),
    (7, "depositario"),
    (8, "valor_nominal"),
    (9, "moneda_simbolo"),
    (10, "precio"),
    (11, "bruto"),
    (12, "gastos_total"),
    (13, "isin"),
    (14, "agente_descripcion"),
    (15, "liq_total"),
    (16, "papel_codigo"),
    (17, "liq_neto"),
    (18, "liq_precio"),
    (19, "fondo_neto"),
    (20, "tipo_especie"),
]
N_COLUMNAS = len(_COLS)
_DATE_COLS = {"fecha_concertacion", "fecha_liquidacion"}
_NUM_COLS = {"valor_nominal", "precio", "bruto", "gastos_total",
             "liq_total", "liq_neto", "liq_precio", "fondo_neto"}


# ─────────────────────────────────────────────────────────────
# Lectura (la tab)
# ─────────────────────────────────────────────────────────────

def _periodos() -> list[str]:
    """Períodos cargados, del más nuevo al más viejo."""
    filas = _q(
        f"SELECT DISTINCT periodo FROM {_TABLA} "
        "WHERE periodo IS NOT NULL ORDER BY periodo DESC"
    )
    return [f["periodo"] for f in filas]


def panel(periodo: str | None = None) -> dict:
    """Filas del período (default = más reciente) para agregar en el cliente."""
    periodos = _periodos()
    sel = periodo if (periodo and periodo in periodos) else (periodos[0] if periodos else None)

    filas: list[dict] = []
    if sel:
        rows = _q(
            "SELECT fecha_concertacion AS fecha, operacion, "
            "       agente_descripcion AS agente, "
            "       papel_descripcion AS papel, bruto AS cash "
            f"FROM {_TABLA} WHERE periodo = %(p)s",
            {"p": sel},
        )
        filas = [
            {
                "fecha": (r["fecha"].isoformat() if r["fecha"] else None),
                "operacion": (r["operacion"] or "(sin dato)"),
                "agente": (r["agente"] or "(sin dato)"),
                "papel": (r["papel"] or "(sin dato)"),
                "cash": _f(r["cash"]) or 0.0,
            }
            for r in rows
        ]

    return {"periodos": periodos, "periodo": sel, "filas": filas}


# ─────────────────────────────────────────────────────────────
# Parseo del informe
# ─────────────────────────────────────────────────────────────

def _is_nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _to_text(v) -> str | None:
    if _is_nan(v):
        return None
    s = str(v).strip()
    return s or None


def _to_num(v) -> float | None:
    if _is_nan(v) or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v) -> int | None:
    n = _to_num(v)
    return round(n) if n is not None else None


def _to_date(v) -> date | None:
    import pandas as pd

    if _is_nan(v):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    # Formato del informe: dd/mm/yyyy. Fallback a parseo flexible de pandas.
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return pd.to_datetime(s, dayfirst=True).date()
    except Exception:
        return None


def _find_data_start(raw) -> int:
    """Primera fila de DATOS. El header arranca en la fila con 'Fondo' en la col 0
    (título, header, subheader, blanco, luego datos → +3). Fallback: fila 4."""
    for i in range(min(15, len(raw))):
        if _to_text(raw.iat[i, 0]) == "Fondo":
            return i + 3
    return 4


def parsear(fuente: Any) -> list[dict]:
    """Excel del informe → filas listas para insertar. `fuente` = ruta o bytes.

    ⚠️ pandas se importa ACÁ y no arriba a propósito: este módulo lo carga la API
    en el arranque (lo importa el router de Mesa de Dinero) y pandas + su cadena
    de dependencias es lo más pesado del requirements. Importarlo al tope haría
    que CADA proceso de uvicorn pagara ~medio segundo y ~100 MB por una función
    que corre una vez por mes.

    Levanta ValueError con el motivo cuando el archivo no es el informe — el
    caller (endpoint o CLI) lo traduce a 400 o a un mensaje de consola.
    """
    import pandas as pd

    origen = io.BytesIO(fuente) if isinstance(fuente, bytes | bytearray) else fuente
    try:
        # SIN `engine=`: se deja que pandas sniffee el formato por contenido, que
        # es lo que viene funcionando con el .xls binario que manda Aunesa.
        raw = pd.read_excel(origen, header=None)
    except Exception as e:
        raise ValueError(f"No se pudo leer el archivo como Excel: {e}") from e

    if raw.shape[1] < N_COLUMNAS:
        raise ValueError(
            f"El archivo tiene {raw.shape[1]} columnas, se esperaban {N_COLUMNAS}. "
            "¿Es el informe 'OP Aca Valores FCI'? No se tocó la base.")

    start = _find_data_start(raw)
    filas: list[dict] = []
    for _, r in raw.iloc[start:].iterrows():
        fondo = _to_text(r.iat[0])
        # Fila vacía / footer / total → se ignora (sin col 0 no es una operación).
        if fondo is None:
            continue
        row: dict = {}
        for idx, name in _COLS:
            v = r.iat[idx]
            if name in _DATE_COLS:
                row[name] = _to_date(v)
            elif name == "plazo":
                row[name] = _to_int(v)
            elif name in _NUM_COLS:
                row[name] = _to_num(v)
            else:
                row[name] = _to_text(v)
        fc = row.get("fecha_concertacion")
        row["periodo"] = f"{fc.year:04d}-{fc.month:02d}" if fc else None
        filas.append(row)

    if not filas:
        raise ValueError("No se encontraron filas de operaciones en el archivo.")
    return filas


# ─────────────────────────────────────────────────────────────
# Escritura
# ─────────────────────────────────────────────────────────────

_INSERT = f"""
INSERT INTO {_TABLA}
    (periodo, fondo, operacion, fecha_concertacion, plazo, fecha_liquidacion,
     papel_numero, papel_descripcion, depositario, valor_nominal, moneda_simbolo,
     precio, bruto, gastos_total, isin, agente_descripcion, liq_total, papel_codigo,
     liq_neto, liq_precio, fondo_neto, tipo_especie, archivo, importado_en, importado_por)
VALUES
    (%(periodo)s, %(fondo)s, %(operacion)s, %(fecha_concertacion)s, %(plazo)s,
     %(fecha_liquidacion)s, %(papel_numero)s, %(papel_descripcion)s, %(depositario)s,
     %(valor_nominal)s, %(moneda_simbolo)s, %(precio)s, %(bruto)s, %(gastos_total)s,
     %(isin)s, %(agente_descripcion)s, %(liq_total)s, %(papel_codigo)s, %(liq_neto)s,
     %(liq_precio)s, %(fondo_neto)s, %(tipo_especie)s, %(archivo)s, %(importado_en)s,
     %(importado_por)s)
"""


def _resumen(filas: list[dict], periodos: list[str], sin_fecha: int) -> dict:
    """Números que describen lo que trae el archivo, ANTES de escribir nada.

    `existentes` = filas que HOY tiene la base en esos períodos, o sea lo que la
    importación va a reemplazar. Es el dato que convierte al preview en una
    decisión y no en un trámite: un archivo con 3 filas que pisa un mes de 287
    se ve acá y no después."""
    por_periodo = []
    for p in periodos:
        del_p = [f for f in filas if f["periodo"] == p]
        hoy = _q(f"SELECT count(*) AS n FROM {_TABLA} WHERE periodo = %(p)s", {"p": p})
        por_periodo.append({
            "periodo": p,
            "filas": len(del_p),
            "existentes": int(hoy[0]["n"]) if hoy else 0,
            "cash": sum(abs(f["bruto"] or 0) for f in del_p),
        })
    return {
        "periodos": periodos,
        "filas": len(filas),
        "total_vn": sum(f["valor_nominal"] or 0 for f in filas),
        "total_cash": sum(abs(f["bruto"] or 0) for f in filas),
        "detalle": por_periodo,
        "sin_fecha": sin_fecha,
    }


def importar(fuente: Any, archivo: str, actor: str = "",
             dry_run: bool = False) -> dict:
    """Parsea el informe y reemplaza los períodos que trae. Idempotente.

    `dry_run=True` parsea y devuelve el mismo resumen SIN tocar la base — es el
    preview del botón y el `--dry-run` de la CLI, la misma función.

    Levanta ValueError (→ 400) si el archivo no es el informe o si no se pudo
    derivar ningún período: sin período no hay clave de reemplazo, y escribir
    igual dejaría filas que ninguna re-importación posterior puede limpiar.
    """
    if not (archivo or "").lower().endswith(EXTENSIONES):
        raise ValueError(
            f"Extensión no soportada ({archivo!r}). Se espera un Excel: "
            f"{', '.join(EXTENSIONES)}.")

    crudas = parsear(fuente)
    # Una fila SIN fecha de concertación no tiene período, y `periodo` es NOT NULL
    # (es la clave del reemplazo idempotente). Se DESCARTA y se cuenta, en vez de
    # dejar que el INSERT reviente a mitad de camino: una línea de pie de página
    # que se cuele no puede voltear la importación entera del mes.
    sin_fecha = sum(1 for f in crudas if not f["periodo"])
    filas = [f for f in crudas if f["periodo"]]
    periodos = sorted({f["periodo"] for f in filas})
    if not periodos:
        raise ValueError(
            "No se pudo derivar ningún período: ninguna fila trae fecha de "
            "concertación. No se tocó la base.")

    res = _resumen(filas, periodos, sin_fecha)
    res.update({"archivo": archivo, "dry_run": dry_run, "borradas": 0})
    if dry_run:
        return res

    ahora = datetime.now(UTC)
    actor_norm = (actor or "").lower().strip() or None
    for f in filas:
        f["archivo"] = archivo
        f["importado_en"] = ahora
        f["importado_por"] = actor_norm

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {_TABLA} WHERE periodo = ANY(%s)", (periodos,))
        res["borradas"] = cur.rowcount
        cur.executemany(_INSERT, filas)
        conn.commit()

    # Trazabilidad: la importación ES una acción de la vista Mesa de Dinero, así
    # que va al MISMO libro que las altas de operaciones y los TC. Una segunda
    # tabla de audit para esto obligaría a mirar dos lugares para reconstruir
    # quién tocó qué en la vista.
    from api.services.mesa_dinero import _audit
    _audit(actor, "import_retorno", ",".join(periodos),
           {"archivo": archivo, "filas": len(filas), "borradas": res["borradas"],
            "total_cash": res["total_cash"]})
    return res
