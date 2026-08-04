"""api/services/anulados.py — anulación de boletos por la marca `(A)` de Aunesa.

Servicio PURO (sin FastAPI). Aunesa marca un boleto anulado agregándole el
sufijo ` (A)` al número: `BOL 2026126329` → `BOL 2026126329 (A)`. Como los dos
jobs de ingesta upsertean por número de boleto, el boleto marcado entra como una
fila NUEVA y convive con el original → el volumen se cuenta dos veces.

Dos caminos, misma regla:

  * `detectar_marca_a()` — barre lo que YA está en la base buscando el sufijo, y
    arrastra el gemelo sin marca (mismo número, misma operación).
  * `anular_lista()` — recibe una lista de boletos (Excel de Aunesa) y busca cada
    uno en las dos tablas probando las DOS variantes: con `(A)` y sin `(A)`.

Ambos marcan `anulado_en` en `operaciones.operaciones` (col. `boleto`) y en
`operaciones.negocio_movimientos` (col. `comprobante`). El mismo número de
boleto es la MISMA identidad en las dos tablas: una anulación es un solo hecho y
se marca en las dos a la vez. Ambos aceptan `commit=False` (previsualiza sin
tocar nada) y son idempotentes.
"""
from __future__ import annotations

import re

from psycopg.rows import dict_row

from api.services._sql import _f, _q
from core.postgres import get_pool

# ' (A)' al final del número, tolerante a espacios y minúscula.
_RE_MARCA_A = re.compile(r"\s*\(\s*A\s*\)\s*$", re.IGNORECASE)

_SEL_OPS = (
    "boleto, concertacion::text AS fecha, id_cuenta, denominacion AS cliente, "
    "tipo_operacion AS operacion, instrumento, bruto AS importe, moneda, anulado_en"
)
_SEL_NM = (
    "comprobante AS boleto, fecha::text AS fecha, id_cuenta, cuenta AS cliente, "
    "categoria AS operacion, ticker AS instrumento, importe, moneda, anulado_en"
)
# El LIKE va SIEMPRE parametrizado: psycopg3 no interpola `%` cuando execute()
# se llama sin params, y un '%%' escrito a mano viajaría literal.
_LIKE_A = "%(A)%"
# (tabla, columna del boleto, SELECT de detalle)
_TABLAS = (
    ("operaciones", "boleto", _SEL_OPS),
    ("negocio_movimientos", "comprobante", _SEL_NM),
)


def base_boleto(s: str) -> str:
    """`'bol  2026126329 (A)'` → `'BOL 2026126329'`. Quita la marca, colapsa
    espacios internos y normaliza a mayúsculas — así el mismo boleto escrito de
    dos formas distintas resuelve a la MISMA clave."""
    return " ".join(_RE_MARCA_A.sub("", str(s or "")).split()).upper()


def tiene_marca_a(s: str) -> bool:
    return bool(_RE_MARCA_A.search(str(s or "")))


def _fila(r: dict, tabla: str) -> dict:
    return {
        "tabla": tabla,
        "boleto": r["boleto"],
        "fecha": r["fecha"],
        "id_cuenta": r["id_cuenta"],
        "cliente": r["cliente"],
        "operacion": r["operacion"],
        "instrumento": r["instrumento"],
        "importe": _f(r["importe"]),
        "moneda": r["moneda"],
        "ya_anulado": r["anulado_en"] is not None,
    }


def _marcar(cur, tabla: str, col: str, boletos: list[str]) -> int:
    """UPDATE idempotente: solo toca las filas que todavía están vivas."""
    if not boletos:
        return 0
    cur.execute(
        f"UPDATE {tabla} SET anulado_en = now() "
        f"WHERE {col} = ANY(%s) AND anulado_en IS NULL", (boletos,))
    return cur.rowcount


# ── barrido: qué hay HOY en la base con la marca (A) ─────────────────────────
def detectar_marca_a(commit: bool = False) -> dict:
    """Busca el sufijo `(A)` y anula ese número en las DOS tablas.

    Dos pasadas, y el orden importa: primero se junta el universo de números
    anulados mirando las DOS tablas, y recién después se marca. Aunesa manda la
    marca `(A)` por `/informes` (→ `operaciones`) pero NO por
    `/consolidadosGenerales` (→ `negocio_movimientos`): si cada tabla se
    resolviera sola, el negocio nunca se enteraría de la anulación.

    Se anula el número completo: la fila con `(A)` y su gemelo sin marca (la que
    se ingestó ANTES de que Aunesa anulara — misma operación, contada dos veces).
    """
    bases: set[str] = set()
    con_marca: dict[str, list[dict]] = {}
    res: dict = {"commit": commit, "tablas": {}, "marcadas": 0}

    with get_pool().connection() as conn:
        for tabla, col, sel in _TABLAS:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"SELECT {sel} FROM {tabla} WHERE {col} LIKE %s ORDER BY 2, 1",
                    (_LIKE_A,))
                con_marca[tabla] = [_fila(r, tabla) for r in cur.fetchall()]
            bases |= {base_boleto(f["boleto"]) for f in con_marca[tabla]}

        variantes = [v for b in sorted(bases) for v in (b, f"{b} (A)")]
        for tabla, col, sel in _TABLAS:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(f"SELECT {sel} FROM {tabla} WHERE {col} = ANY(%s) "
                            f"ORDER BY 2, 1", (variantes,))
                todas = [_fila(r, tabla) for r in cur.fetchall()]
                n = _marcar(cur, tabla, col, [f["boleto"] for f in todas]) if commit else 0
                res["marcadas"] += n

            marcados = {f["boleto"] for f in con_marca[tabla]}
            res["tablas"][tabla] = {
                "con_marca_a": con_marca[tabla],
                "gemelos_sin_marca": [f for f in todas if f["boleto"] not in marcados],
                "n_con_marca": len(con_marca[tabla]),
                "n_gemelos": len(todas) - len(marcados),
                "n_ya_anuladas": sum(1 for f in todas if f["ya_anulado"]),
                "marcadas": n,
            }
        if commit:
            conn.commit()
    return res



# ── carga manual: lista de boletos anulados (Excel de Aunesa) ────────────────
def anular_lista(boletos: list[str], commit: bool = False) -> dict:
    """Anula los boletos de una lista, probando las variantes CON y SIN `(A)`.

    Devuelve, boleto por boleto, qué se encontró en cada tabla — el detalle es
    lo que permite auditar la carga antes de aplicarla (`commit=False`).
    """
    # base normalizada → texto tal cual lo subió el usuario (para el reporte).
    entrada: dict[str, str] = {}
    sin_prefijo: list[str] = []
    for b in boletos:
        base = base_boleto(b)
        if not base:
            continue
        if " " not in base:  # 'BOL 2026...' → ok; '2026...' pelado → no resoluble
            sin_prefijo.append(base)
            continue
        entrada.setdefault(base, str(b).strip())

    bases = sorted(entrada)
    variantes = [v for b in bases for v in (b, f"{b} (A)")]
    encontrados: dict[str, list[dict]] = {b: [] for b in bases}
    res: dict = {"commit": commit, "pedidos": len(bases), "marcadas": 0,
                 "sin_prefijo": sorted(set(sin_prefijo)), "tablas": {}}
    if not bases:
        res["no_encontrados"] = []
        res["detalle"] = []
        return res

    with get_pool().connection() as conn:
        for tabla, col, sel in _TABLAS:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(f"SELECT {sel} FROM {tabla} WHERE {col} = ANY(%s) "
                            f"ORDER BY 2, 1", (variantes,))
                filas = [_fila(r, tabla) for r in cur.fetchall()]
                for f in filas:
                    encontrados[base_boleto(f["boleto"])].append(f)

                n = 0
                if commit:
                    n = _marcar(cur, tabla, col, [f["boleto"] for f in filas])
                    res["marcadas"] += n
            res["tablas"][tabla] = {"encontradas": len(filas), "marcadas": n}
        if commit:
            conn.commit()

    res["no_encontrados"] = [entrada[b] for b in bases if not encontrados[b]]
    res["detalle"] = [f for b in bases for f in encontrados[b]]
    return res


def resumen_marca_a() -> dict:
    """Contadores rápidos para la UI (sin traer el detalle)."""
    out: dict = {}
    for tabla, col, _ in _TABLAS:
        r = _q(f"SELECT count(*) AS n, "
               f"count(*) FILTER (WHERE anulado_en IS NOT NULL) AS anuladas "
               f"FROM {tabla} WHERE {col} LIKE %s", (_LIKE_A,))[0]
        out[tabla] = {"con_marca_a": int(r["n"]), "ya_anuladas": int(r["anuladas"])}
    return out
