"""api/services/financiamiento_calc.py — CALCULADORA DE DESCUENTO (panel 4 de FINANCIAMIENTO).

QUÉ ES
------
La planilla Excel que la mesa usaba para cotizarle a un cliente cuánto plata
recibe HOY si descuenta un cheque / pagaré, y cuánto le termina costando eso en
tasa anual. Vive en el panel libre de la vista FINANCIAMIENTO (tab de
/operaciones) y tiene DOS tabs:

  - **CALCULADORA** — el usuario pone MONTO A DESCONTAR, TASA, DÍAS y elige un
    AVAL (SGR) + el instrumento (CHEQUE o PAGARÉ). El backend devuelve los tres
    bloques de la planilla: NETO SIN AVAL, NETO CON AVAL y el CFT con sus dos
    flujos de efectivo. **NADA de esto se persiste**: es un simulador, lo corre
    cualquiera que entre a la vista y se lo lleva puesto al salir.
  - **DATOS** — la única parte que SÍ persiste: el catálogo de SGRs con su costo
    de aval por instrumento, el arancel de ACA Valores y el derecho de mercado.
    Son los parámetros que la planilla tenía hardcodeados a un costado.

POR QUÉ EL CÁLCULO ESTÁ ACÁ Y NO EN EL FRONT
--------------------------------------------
Mismo criterio que el detalle por celda de Tesorería: la fórmula vive UNA sola
vez. Si el front replicara las cuentas, el día que cambie un signo habría que
tocar dos lados y la pantalla podría contradecir a la API sin que nadie se
entere. Además los parámetros (arancel, derecho, costo del aval) ya están acá:
mandarlos al navegador para que multiplique allá no ahorra nada y expone la
tabla de costos completa en cada request de la calculadora.

Es matemática pura sobre 4 números — no toca la base salvo para leer los DATOS,
así que el roundtrip es barato y el front lo llama con debounce.

UNIDADES — LEER ANTES DE TOCAR
------------------------------
Todos los porcentajes viajan y se guardan como **PORCENTAJE** (25.0 = 25 %,
0.06 = 0,06 %), NO como fracción. Es lo que el usuario tipea y lo que se lee en
la planilla, así que mirar la tabla en SQL da el mismo número que la pantalla.
La división por 100 se hace UNA vez, acá adentro, en `_frac()`. El único valor
que no es configurable es el IVA (`IVA_PCT`): es una alícuota fiscal, no un
parámetro comercial de la mesa.

LAS FÓRMULAS (tal cual la planilla, verificadas contra el Excel del user)
------------------------------------------------------------------------
NETO SIN AVAL:
    monto_descontado = monto / (1 + tasa · dias/365)
    tasa_directa     = 1 − monto_descontado / monto
    arancel_aca      = monto · arancel_pct / 365 · dias
    derecho_mercado  = monto_descontado · derecho_pct
    iva_derecho      = derecho_mercado · 21 %
    iva_aranceles    = arancel_aca     · 21 %
    a_recibir        = monto_descontado − arancel − derecho − iva_d − iva_a

    ⚠️ El arancel de ACA Valores se prorratea por días (es una tasa anual) y se
    calcula sobre el monto NOMINAL; el derecho de mercado NO se prorratea y se
    calcula sobre el monto DESCONTADO. No es una inconsistencia: son dos cosas
    distintas y así es como se factura.

NETO CON AVAL (la SGR cobra su comisión sobre el nominal, no sobre el neto):
    comision_sgr     = monto · costo_aval / 365 · dias
    monto_descontado = a_recibir − comision_sgr        ← lo que el cliente cobra
    tasa_final       = tasa + costo_aval

COSTO FINANCIERO TOTAL (efectiva anual de la operación completa):
    cft = (1 + (monto − neto_con_aval) / neto_con_aval) ^ (365/dias) − 1

Los flujos de efectivo son la lectura del CFT: entra el neto HOY, sale el
nominal al vencimiento (hoy + días, calendario corrido).

VERIFICACIÓN: `tests/unit/test_financiamiento_calc.py` clava los seis números
del Excel que pasó el user (50.000.000 · 25 % · 127 días · aval 4 %) contra el
resultado del service. Si tocás una fórmula y ese test no falla, no tocaste lo
que creías.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from psycopg.types.json import Jsonb

from core.postgres import get_pool

_log = logging.getLogger("api.financiamiento_calc")

# Alícuota de IVA sobre aranceles y derecho de mercado. Constante a propósito: es
# fiscal, no un parámetro que la mesa negocie. Si algún día cambia por ley, se
# cambia acá y no hay que tocar ninguna fila.
IVA_PCT = 21.0

# Base de días del año. La planilla usa 365 (no 360) en TODOS los prorrateos.
BASE_ANUAL = 365

INSTRUMENTOS = ("cheque", "pagare")

# Semilla del catálogo — los valores de la planilla que pasó el user. Solo se
# insertan si la tabla está VACÍA (primer arranque): a partir de ahí manda lo que
# se cargue desde la tab DATOS y esto no vuelve a opinar nunca.
_SEED_AVALES: tuple[tuple[str, float, float, str], ...] = (
    ("Trend SGR",  5.00, 2.00, ""),
    ("Bind",       5.00, 2.50, ""),
    ("Conaval",    4.00, 3.00, ""),
    ("Acindar",    3.85, 2.00, "más 0,4 directo"),
    ("Garantizar", 3.25, 1.50, ""),
)
_SEED_ARANCEL_ACA = 1.00
_SEED_DERECHO_MERCADO = 0.06

# Ya se intentó sembrar en ESTE proceso (ver `_seed_si_vacio`).
_SEEDED = False


# ──────────────────────────────────────────────────────────────────────────────
# Plumbing SQL (mismo patrón que tesoreria.py: dict rows + commit explícito)
# ──────────────────────────────────────────────────────────────────────────────

def _q(sql: str, params: dict | None = None) -> list[dict]:
    from psycopg.rows import dict_row

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def _exec(sql: str, params: dict) -> int:
    """Escritura. Traduce 'la tabla no existe' a `TablasFaltantes` — el resto de
    los errores suben tal cual (un constraint violado NO es lo mismo que un
    schema sin aplicar y no se puede confundir con eso)."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            n = cur.rowcount
            conn.commit()
            return n
    except Exception as e:
        if _es_tabla_faltante(e):
            raise TablasFaltantes(_MSG_FALTAN_TABLAS) from e
        raise


def _audit(actor: str, action: str, target: str, data: dict | None = None) -> None:
    """Traza de quién cambió un parámetro. Nunca rompe la operación principal:
    perder el audit es malo, perder la carga del usuario es peor."""
    try:
        _exec(
            "INSERT INTO operaciones.financiamiento_datos_audit "
            "(ts, actor, action, target, data) "
            "VALUES (%(ts)s, %(actor)s, %(action)s, %(target)s, %(data)s)",
            {"ts": datetime.now(UTC), "actor": (actor or "").lower() or None,
             "action": action, "target": target, "data": Jsonb(data or {})},
        )
    except Exception:
        _log.exception("financiamiento_calc: audit insert falló")


class TablasFaltantes(RuntimeError):
    """Las tablas de la calculadora no existen todavía en la base.

    Pasa entre que se pushea el schema y que alguien corre `apply_schema` en el
    Droplet. Las LECTURAS degradan a vacío (la vista FINANCIAMIENTO no depende de
    esto y no puede caerse por acá), pero una ESCRITURA no puede degradar: si
    guardar no guardó, hay que decirlo. Existe para que el router devuelva un
    mensaje accionable en vez de un HTTP 500 pelado, que no le dice a nadie que
    lo único que falta es aplicar el schema.
    """


_MSG_FALTAN_TABLAS = (
    "Las tablas de la calculadora todavía no existen en la base. "
    "Correr en el Droplet: python -m scripts.apply_schema + restart de la API."
)


def _es_tabla_faltante(e: Exception) -> bool:
    """¿El error es 'no existe la tabla/esquema'? Se chequea la CLASE de psycopg
    (no el texto del mensaje, que viene traducido según el locale del server)."""
    from psycopg import errors as pg_errors

    return isinstance(e, pg_errors.UndefinedTable | pg_errors.InvalidSchemaName)


def _num(v, campo: str, *, minimo: float | None = None) -> float:
    """Casteo estricto a float con mensaje entendible. La calculadora no puede
    degradar a 0 en silencio: un parámetro mal tipeado tiene que gritar, no
    devolver una cotización plausible y equivocada."""
    if v is None or v == "":
        raise ValueError(f"falta '{campo}'")
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"'{campo}' no es un número") from None
    if x != x or x in (float("inf"), float("-inf")):
        raise ValueError(f"'{campo}' no es un número finito")
    if minimo is not None and x < minimo:
        raise ValueError(f"'{campo}' debe ser mayor o igual a {minimo}")
    return x


def _frac(pct: float) -> float:
    """Porcentaje → fracción. La ÚNICA división por 100 del módulo."""
    return pct / 100.0


# ──────────────────────────────────────────────────────────────────────────────
# DATOS — lo único que persiste (catálogo de avales + aranceles)
# ──────────────────────────────────────────────────────────────────────────────

def _seed_si_vacio() -> None:
    """Carga inicial del catálogo. Idempotente y solo cuando NO hay ni una fila:
    una tab DATOS vacía en el primer arranque parece rota, y estos valores ya
    estaban en la planilla que reemplaza esta pantalla.

    Se intenta UNA vez por proceso (`_SEEDED`). Sin esa guarda, cada tecla de la
    calculadora pagaría dos SELECT extra para preguntar algo que ya se sabe — y
    en este sistema lo que cuesta es la CANTIDAD de roundtrips (~28 ms cada uno),
    no el plan de la query. Si dos workers arrancan a la vez el
    `ON CONFLICT DO NOTHING` los deja empatar sin romper nada.
    """
    global _SEEDED
    if _SEEDED:
        return
    try:
        if not _q("SELECT 1 FROM operaciones.financiamiento_avales LIMIT 1"):
            for i, (nombre, cheque, pagare, nota) in enumerate(_SEED_AVALES):
                _exec(
                    "INSERT INTO operaciones.financiamiento_avales "
                    "(nombre, costo_cheque, costo_pagare, nota, orden) "
                    "VALUES (%(n)s, %(c)s, %(p)s, %(nota)s, %(o)s) "
                    "ON CONFLICT (nombre) DO NOTHING",
                    {"n": nombre, "c": cheque, "p": pagare, "nota": nota, "o": i},
                )
        if not _q("SELECT 1 FROM operaciones.financiamiento_aranceles WHERE id = 1"):
            _exec(
                "INSERT INTO operaciones.financiamiento_aranceles "
                "(id, arancel_aca, derecho_mercado) VALUES (1, %(a)s, %(d)s) "
                "ON CONFLICT (id) DO NOTHING",
                {"a": _SEED_ARANCEL_ACA, "d": _SEED_DERECHO_MERCADO},
            )
        # Solo acá: si la siembra FALLÓ (típicamente porque el schema no está
        # aplicado) se vuelve a intentar en la próxima llamada. Marcarlo antes
        # dejaría el proceso sin sembrar nunca, aun después de crear las tablas.
        _SEEDED = True
    except Exception:
        _log.warning("financiamiento_calc: no pude sembrar el catálogo", exc_info=True)


def _fila_aval(r: dict) -> dict:
    return {
        "nombre":       r["nombre"],
        "costo_cheque": float(r["costo_cheque"]) if r["costo_cheque"] is not None else None,
        "costo_pagare": float(r["costo_pagare"]) if r["costo_pagare"] is not None else None,
        "nota":         r["nota"] or "",
        "orden":        r["orden"],
        "actualizado_por": r.get("actualizado_por"),
        "actualizado_at":  r["actualizado_at"].isoformat() if r.get("actualizado_at") else None,
    }


def get_datos() -> dict:
    """Catálogo completo para la tab DATOS y para poblar el selector de aval.

    Degrada a vacío si las tablas todavía no existen (schema sin aplicar en el
    Droplet): el panel muestra 'sin datos' en vez de tumbar la vista entera de
    FINANCIAMIENTO, que no depende de esto para nada.
    """
    _seed_si_vacio()
    try:
        avales = _q(
            "SELECT nombre, costo_cheque, costo_pagare, nota, orden, "
            "       actualizado_por, actualizado_at "
            "FROM operaciones.financiamiento_avales ORDER BY orden, nombre"
        )
        ar = _q(
            "SELECT arancel_aca, derecho_mercado, actualizado_por, actualizado_at "
            "FROM operaciones.financiamiento_aranceles WHERE id = 1"
        )
    except Exception:
        _log.warning("financiamiento_calc: no pude leer los datos", exc_info=True)
        return {"avales": [], "aranceles": _aranceles_vacios(), "iva_pct": IVA_PCT,
                "base_anual": BASE_ANUAL, "disponible": False}
    return {
        "avales":     [_fila_aval(r) for r in avales],
        "aranceles":  _fila_aranceles(ar[0]) if ar else _aranceles_vacios(),
        "iva_pct":    IVA_PCT,
        "base_anual": BASE_ANUAL,
        "disponible": True,
    }


def _fila_aranceles(r: dict) -> dict:
    return {
        "arancel_aca":     float(r["arancel_aca"]) if r["arancel_aca"] is not None else None,
        "derecho_mercado": (
            float(r["derecho_mercado"]) if r["derecho_mercado"] is not None else None
        ),
        "actualizado_por": r.get("actualizado_por"),
        "actualizado_at":  r["actualizado_at"].isoformat() if r.get("actualizado_at") else None,
    }


def _aranceles_vacios() -> dict:
    return {"arancel_aca": None, "derecho_mercado": None,
            "actualizado_por": None, "actualizado_at": None}


def guardar_aval(
    *,
    nombre: str,
    costo_cheque: float | None,
    costo_pagare: float | None,
    nota: str | None,
    orden: int | None,
    actor: str,
) -> dict:
    """Alta o edición de una SGR (upsert por nombre). El nombre ES la clave: es
    lo que el usuario elige textualmente en la calculadora."""
    n = (nombre or "").strip()
    if not n:
        raise ValueError("falta 'nombre'")
    for campo, v in (("costo_cheque", costo_cheque), ("costo_pagare", costo_pagare)):
        if v is not None and (v < 0 or v > 100):
            raise ValueError(f"'{campo}' debe estar entre 0 y 100 (es un porcentaje)")
    _exec(
        "INSERT INTO operaciones.financiamiento_avales "
        "(nombre, costo_cheque, costo_pagare, nota, orden, actualizado_por, actualizado_at) "
        "VALUES (%(n)s, %(c)s, %(p)s, %(nota)s, COALESCE(%(o)s, 0), %(por)s, %(at)s) "
        "ON CONFLICT (nombre) DO UPDATE SET "
        "  costo_cheque = EXCLUDED.costo_cheque, costo_pagare = EXCLUDED.costo_pagare, "
        "  nota = EXCLUDED.nota, orden = EXCLUDED.orden, "
        "  actualizado_por = EXCLUDED.actualizado_por, actualizado_at = EXCLUDED.actualizado_at",
        {"n": n, "c": costo_cheque, "p": costo_pagare, "nota": (nota or "").strip(),
         "o": orden, "por": (actor or "").lower() or None, "at": datetime.now(UTC)},
    )
    _audit(actor, "set_aval", n,
           {"costo_cheque": costo_cheque, "costo_pagare": costo_pagare, "nota": nota})
    return {"nombre": n}


def borrar_aval(*, nombre: str, actor: str) -> dict:
    """Baja FÍSICA. No hay histórico que huerfanar: la calculadora no persiste
    ninguna cotización, así que nadie referencia estas filas."""
    n = (nombre or "").strip()
    if not n:
        raise ValueError("falta 'nombre'")
    borrado = _exec("DELETE FROM operaciones.financiamiento_avales WHERE nombre = %(n)s",
                    {"n": n})
    _audit(actor, "del_aval", n)
    return {"borrado": borrado}


def guardar_aranceles(
    *, arancel_aca: float | None, derecho_mercado: float | None, actor: str,
) -> dict:
    """Los dos parámetros de ACA/mercado. Fila única (`id = 1`) — no son por
    cliente ni por operación, son la tarifa vigente."""
    for campo, v in (("arancel_aca", arancel_aca), ("derecho_mercado", derecho_mercado)):
        if v is not None and (v < 0 or v > 100):
            raise ValueError(f"'{campo}' debe estar entre 0 y 100 (es un porcentaje)")
    _exec(
        "INSERT INTO operaciones.financiamiento_aranceles "
        "(id, arancel_aca, derecho_mercado, actualizado_por, actualizado_at) "
        "VALUES (1, %(a)s, %(d)s, %(por)s, %(at)s) "
        "ON CONFLICT (id) DO UPDATE SET "
        "  arancel_aca = EXCLUDED.arancel_aca, derecho_mercado = EXCLUDED.derecho_mercado, "
        "  actualizado_por = EXCLUDED.actualizado_por, actualizado_at = EXCLUDED.actualizado_at",
        {"a": arancel_aca, "d": derecho_mercado,
         "por": (actor or "").lower() or None, "at": datetime.now(UTC)},
    )
    _audit(actor, "set_aranceles", "1",
           {"arancel_aca": arancel_aca, "derecho_mercado": derecho_mercado})
    return {"arancel_aca": arancel_aca, "derecho_mercado": derecho_mercado}


# ──────────────────────────────────────────────────────────────────────────────
# CALCULADORA — matemática pura, no persiste NADA
# ──────────────────────────────────────────────────────────────────────────────

def calcular_puro(
    *,
    monto: float,
    tasa_pct: float,
    dias: int,
    arancel_aca_pct: float,
    derecho_mercado_pct: float,
    costo_aval_pct: float | None,
    hoy: date | None = None,
) -> dict:
    """El cálculo, sin base de datos ni permisos: entran números, salen números.

    Está separado de `calcular()` a propósito — así el test puede clavar los
    valores del Excel sin levantar Postgres, y el día que haga falta reusar la
    cuenta desde un job o el copiloto no hay que arrastrar la lectura del
    catálogo.

    `costo_aval_pct = None` → se devuelve solo el bloque SIN AVAL (`con_aval` y
    `cft` en null). Es el caso 'todavía no elegí SGR', no un error.
    """
    if dias <= 0:
        raise ValueError("'dias' debe ser mayor a 0")
    if monto <= 0:
        raise ValueError("'monto' debe ser mayor a 0")

    prorrateo = dias / BASE_ANUAL

    # ── NETO SIN AVAL ────────────────────────────────────────────────────────
    monto_descontado = monto / (1 + _frac(tasa_pct) * prorrateo)
    tasa_directa_pct = (1 - monto_descontado / monto) * 100
    arancel = monto * _frac(arancel_aca_pct) * prorrateo
    derecho = monto_descontado * _frac(derecho_mercado_pct)
    iva_derecho = derecho * _frac(IVA_PCT)
    iva_arancel = arancel * _frac(IVA_PCT)
    a_recibir = monto_descontado - arancel - derecho - iva_derecho - iva_arancel

    sin_aval = {
        "monto_descontado":  monto_descontado,
        "tasa_directa_pct":  tasa_directa_pct,
        "arancel_aca":       arancel,
        "derecho_mercado":   derecho,
        "iva_derecho":       iva_derecho,
        "iva_aranceles":     iva_arancel,
        "a_recibir_cliente": a_recibir,
    }

    if costo_aval_pct is None:
        return {"sin_aval": sin_aval, "con_aval": None, "cft_pct": None, "flujos": []}

    # ── NETO CON AVAL ────────────────────────────────────────────────────────
    # La SGR cobra sobre el NOMINAL (no sobre lo que el cliente termina cobrando).
    comision = monto * _frac(costo_aval_pct) * prorrateo
    neto = a_recibir - comision
    con_aval = {
        "costo_aval_pct":   costo_aval_pct,
        "comision_sgr":     comision,
        "tasa_final_pct":   tasa_pct + costo_aval_pct,
        "monto_descontado": neto,
    }

    # ── CFT + flujos ─────────────────────────────────────────────────────────
    # Si el costo del aval se come todo el neto (parámetros absurdos), el CFT no
    # existe: elevar un negativo a 365/dias da un complejo. Se devuelve null en
    # vez de un número inventado — la pantalla muestra '—' y el usuario ve que
    # los parámetros no cierran.
    cft_pct = None
    if neto > 0:
        cft_pct = ((1 + (monto - neto) / neto) ** (BASE_ANUAL / dias) - 1) * 100

    d0 = hoy or _hoy_art()
    flujos = [
        {"fecha": d0.isoformat(),                          "importe": neto},
        {"fecha": (d0 + timedelta(days=dias)).isoformat(), "importe": -monto},
    ]
    return {"sin_aval": sin_aval, "con_aval": con_aval, "cft_pct": cft_pct, "flujos": flujos}


def _hoy_art() -> date:
    """Hoy en ART (UTC-3), sin depender de la tz del server. El flujo de efectivo
    arranca el día que se cotiza y el vencimiento se cuenta desde ahí."""
    return (datetime.now(UTC) - timedelta(hours=3)).date()


def calcular(
    *,
    monto,
    tasa_pct,
    dias,
    aval: str | None = None,
    instrumento: str = "cheque",
) -> dict:
    """Entrada del endpoint: valida, resuelve el costo del aval contra el
    catálogo y delega en `calcular_puro`.

    El costo del aval NO viaja en el request a propósito: si el front lo mandara,
    un usuario podría cotizar con un costo que no es el vigente. Se manda el
    NOMBRE y el backend busca el número — mismo criterio que el arancel.
    """
    inst = (instrumento or "cheque").strip().lower()
    if inst not in INSTRUMENTOS:
        raise ValueError(f"'instrumento' debe ser uno de: {', '.join(INSTRUMENTOS)}")

    m = _num(monto, "monto", minimo=0)
    t = _num(tasa_pct, "tasa_pct")
    d = int(_num(dias, "dias"))

    datos = get_datos()
    ar = datos["aranceles"]
    arancel_pct = ar["arancel_aca"] or 0.0
    derecho_pct = ar["derecho_mercado"] or 0.0

    costo = None
    aval_nombre = (aval or "").strip() or None
    fila = None
    if aval_nombre:
        fila = next((a for a in datos["avales"] if a["nombre"] == aval_nombre), None)
        if fila is None:
            raise ValueError(f"aval desconocido: {aval_nombre!r}")
        costo = fila["costo_cheque"] if inst == "cheque" else fila["costo_pagare"]
        if costo is None:
            raise ValueError(
                f"{aval_nombre} no tiene costo cargado para {inst.upper()} — "
                "completalo en la tab DATOS"
            )

    res = calcular_puro(
        monto=m, tasa_pct=t, dias=d,
        arancel_aca_pct=arancel_pct, derecho_mercado_pct=derecho_pct,
        costo_aval_pct=costo,
    )
    # Los parámetros vigentes viajan de vuelta para que la pantalla pueda mostrar
    # CON QUÉ se calculó. Un número sin sus supuestos no se puede auditar.
    res["params"] = {
        "monto":               m,
        "tasa_pct":            t,
        "dias":                d,
        "aval":                aval_nombre,
        "instrumento":         inst,
        "nota_aval":           (fila or {}).get("nota") or "",
        "arancel_aca_pct":     arancel_pct,
        "derecho_mercado_pct": derecho_pct,
        "iva_pct":             IVA_PCT,
        "base_anual":          BASE_ANUAL,
    }
    return res
