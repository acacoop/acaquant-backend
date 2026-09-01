"""api/services/bancos.py — lectura de `bancos.*` para la tab INTERBANKING.

Módulo PURO (sin FastAPI). Lee SOLO de Postgres: **nunca** le pega a
Interbanking. El que habla con Interbanking es `jobs/interbanking_sync`, porque
el límite de 100 llamadas/minuto es del ABONADO y no del proceso — una pantalla
que consultara en vivo podría agotar la cuota y romper el job.

## Regla de exposición (SEGURIDAD)

Son los datos bancarios de la casa. La API devuelve **campos explícitos**, nunca
la fila entera ni el `raw` jsonb. Concretamente, esto NO sale nunca:

- `account_cbu` de nuestras cuentas: es lo que permite transferirles plata.
- `account_cuit`.
- (el `account_number` SÍ se publica entero desde el 2026-08-18 — ver
  `_cuenta_publica`. El CBU no: identificar y poder transferir no son lo mismo.)
- el `raw` de cualquier tabla.
- el CUIT de la contraparte, que va enmascarado (son datos personales de
  terceros y vienen en ~3% de los movimientos).

Está congelado por `tests/unit/test_interbanking_seguridad.py`: si alguien
agrega uno de esos campos a la proyección pública, el test falla.
"""
from __future__ import annotations

import json
import re
from datetime import date

from api.services._sql import _f, _q
from core.calendario import restar_habiles
from core.postgres import get_pool
from core.tz import ahora_ar

# ── GASTOS BANCARIOS ─────────────────────────────────────────────────────────
#
# Qué campo del movimiento puede mirar una regla. La clave es lo que se guarda
# en `bancos.gastos_reglas.campo`; el valor, la columna de `bancos.movimientos`.
#
# Se declara acá y NO se arma SQL dinámico con lo que venga de la base: una regla
# la escribe un usuario, y un `campo` que viaje directo a un `WHERE` es una
# inyección esperando. Un campo que no esté en este dict simplemente no matchea.
CAMPOS_REGLA = {
    "codigo_ib": "codigo_operacion_ib",
    "codigo_banco": "codigo_operacion_banco",
    "descripcion_banco": "descripcion_banco",
    "descripcion_ib": "descripcion_ib",
}
OPERADORES_REGLA = ("igual", "contiene")

# ⚠️ **UN CAMPO QUE LA PANTALLA DERIVA, LA REGLA LO TIENE QUE DERIVAR IGUAL.**
#
# La columna DESCRIPCIÓN de la vista NO es la columna `descripcion_banco`: es
# `descripcion_banco` y, **si el banco no mandó la suya, el CONCEPTO**. Lo decide
# `_movimiento_publico`, y existe porque una fila sin ningún texto es ilegible.
#
# Mientras la regla miraba la columna cruda, las dos mitades decían cosas
# distintas **sin fallar**: el operador leía `NOTA DB` bajo DESCRIPCIÓN, cargaba
# esa grafía sobre DESCRIPCIÓN —el campo que además viene elegido por defecto en
# el ABM— y el motor la comparaba contra un string VACÍO. No matcheaba nunca, no
# saltaba ninguna excepción, y el movimiento se quedaba en MOVIMIENTOS RESTANTES
# teniendo su columna cargada. Medido el 2026-09-01 sobre las 3 fechas de la
# base: 2 movimientos `NOTA DB` de Credicoop con `descripcion_banco` vacío y el
# texto en `descripcion_ib`, y **dos grafías cargadas a mano que no agarraban
# absolutamente nada**.
#
# El arreglo es que la regla lea LO MISMO que se dibuja. Es **estrictamente
# aditivo**: donde `descripcion_banco` trae texto, el fallback ni se consulta y
# el comportamiento es idéntico al de antes. Medido sobre esas mismas fechas:
# 1 movimiento cambia de balde (presentación) y **0 cambian de ser o no gasto**
# — el arreglo no mueve un peso del total.
#
# ⚠️ NO hay fallback al revés: la columna CONCEPTO de la pantalla muestra
# `descripcion_ib` crudo y un «—» cuando está vacío. Espejar la pantalla es la
# regla; inventar un segundo derivado que nadie ve sería el mismo error otra vez.
_FALLBACK_CAMPO = {"descripcion_banco": "descripcion_ib"}


def valor_campo(mov: dict, campo: str) -> str:
    """El texto que una regla compara para `campo`. **La única puerta**: la usan
    `_matchea` (¿es gasto?) y `desglosar` (¿en qué balde cae?).

    Que sea una sola función es el punto. Son las dos mitades del mismo criterio
    y la vista ofrece los MISMOS cuatro campos para las dos: si `DESCRIPCIÓN`
    quisiera decir una cosa en las reglas y otra en el desglose, el equipo no
    tendría cómo saber cuál está usando — y el que se equivoca no ve un error,
    ve otro número.

    Devuelve `""` para un campo no declarado: un `campo` lo escribe un usuario y
    nunca viaja a un `WHERE`.
    """
    col = CAMPOS_REGLA.get(campo)
    if not col:
        return ""
    valor = str(mov.get(col) or "").strip()
    if valor:
        return valor
    alt = _FALLBACK_CAMPO.get(campo)
    return str(mov.get(CAMPOS_REGLA[alt]) or "").strip() if alt else ""


# ── DESGLOSE de los gastos ───────────────────────────────────────────────────
#
# El total de gastos bancarios no alcanza: el back office necesita ver CUÁNTO de
# ese total es IVA, cuánto percepción y cuánto comisión. Es una separación de
# PRESENTACIÓN — no cambia ningún número, parte el que ya está.
#
# Cada balde tiene una lista de matchers `(campo, operador, valor)`. Son varios
# porque el mismo concepto se escribe distinto según el banco — Patagonia dice
# `IMP.DB/CR BANCARIOS P/DEB` y BIND dice `LEY25413DB`, y es el mismo impuesto.
#
# ⚠️ El CAMPO va en el matcher y no en el balde: `COM.TRANSF` llega como CONCEPTO
# en unos bancos y escrito en la DESCRIPCIÓN en otros, así que un balde tiene que
# poder mirar los dos lados. Cuando el campo estaba a nivel del balde, esto no se
# podía expresar.
#
# `grupo` decide dónde se muestra:
#   · "concepto" → columna propia en el CONSOLIDADO.
#   · "otros"    → en el consolidado se suman todos en UNA columna, OTROS IMP;
#                  adentro del modal se ven de a uno.
#
# ⚠️ Los tres primeros van por `igual` a propósito: con `contiene`, «IVA» se
# comería «IVAPERCEP» y la columna IVA mostraría de más mientras IVAPERCEP
# quedaría en cero. Cuando un valor es prefijo de otro, `contiene` no sirve.
#
# ⚠️ **Esto es la SEMILLA, no la fuente de verdad.** Hasta el 2026-08-18 era una
# constante y punto, con el argumento de que "qué columnas tiene una tabla no se
# cambia todos los días". Duró un día: el back office encontró un impuesto que no
# entraba en ningún balde y la única forma de sumarlo era que yo tocara código.
# Eso es exactamente lo que NO puede pasar — el que sabe qué escribe cada banco es
# el equipo, no el que programa.
#
# Ahora el catálogo vive en `bancos.gastos_baldes` + `gastos_balde_matchers`, con
# ABM desde la vista, y esta lista solo se usa para SEMBRARLO la primera vez (ver
# `_sembrar_desglose`). Cambiarla acá no cambia nada en una base ya sembrada.
DESGLOSE_SEMILLA: list[dict] = [
    {"clave": "iva", "etiqueta": "IVA", "grupo": "concepto",
     "matchers": [("descripcion_ib", "igual", "IVA")]},
    {"clave": "ivapercep", "etiqueta": "IVAPERCEP", "grupo": "concepto",
     "matchers": [("descripcion_ib", "igual", "IVAPERCEP")]},
    {"clave": "iibbpercep", "etiqueta": "IIBBPERCEP", "grupo": "concepto",
     "matchers": [("descripcion_ib", "igual", "IIBBPERCEP")]},

    # ⚠️ Este balde mira DOS campos, y por eso el campo va en el MATCHER y no en
    # el balde. Es la misma comisión de transferencia y el banco la manda de tres
    # formas: como CONCEPTO abreviado (`COM.TRANSF`) o, en otros bancos, escrita
    # en la DESCRIPCIÓN (`COMISIONES DATANET`, `COMISION ECHEQ CLEA`). Todas
    # suman a la misma columna, que se sigue llamando COM.TRANSF.
    #
    # Van por `contiene` porque el texto real trae cola: `COMISION ECHEQ CLEA` de
    # verdad llega como `N/D - COMISION ECHEQ CLEA…` y truncado a ~25 caracteres.
    {"clave": "comtransf", "etiqueta": "COM.TRANSF", "grupo": "concepto",
     "matchers": [("descripcion_ib", "contiene", "COM.TRANSF"),
                  ("descripcion_banco", "contiene", "COMISIONES DATANET"),
                  ("descripcion_banco", "contiene", "COMISION ECHEQ CLEA")]},

    {"clave": "imp_credito", "etiqueta": "IMP.DB/CR P/CRE", "grupo": "otros",
     "matchers": [("descripcion_banco", "contiene", "IMP.DB/CR BANCARIOS P/CRE")]},
    {"clave": "imp_debito", "etiqueta": "IMP.DB/CR P/DEB", "grupo": "otros",
     "matchers": [("descripcion_banco", "contiene", "IMP.DB/CR BANCARIOS P/DEB"),
                  ("descripcion_banco", "contiene", "LEY25413DB")]},
    {"clave": "sellos", "etiqueta": "IMPUESTO A LOS SELLOS", "grupo": "otros",
     "matchers": [("descripcion_banco", "contiene", "SELLOS")]},
    {"clave": "tasa_liq", "etiqueta": "TASA LIQUIDEZ", "grupo": "otros",
     "matchers": [("descripcion_banco", "contiene", "TASA LIQUIDEZ")]},
]

# Un gasto que no cae en NINGÚN balde. **No se reparte a dedo** y no es una
# columna: se muestra en el modal solo cuando no es cero. Es el mismo criterio
# que `sin_clasificar` en la vista ACA — si el desglose no cubre todo, la
# pantalla lo canta en vez de esconderlo adentro de otra celda.
#
# Que OTROS IMP sean SOLO esas cuatro descripciones (y no "todo el resto") es
# decisión del back office: son los impuestos que les interesa ver juntos. La
# contracara es que las columnas pueden NO sumar el total, y por eso existe esto.
RESTO = "resto"

GRUPOS_BALDE = ("concepto", "otros")


def desglosar(mov: dict, baldes: list[dict]) -> str:
    """A qué balde va este movimiento. PURO. Devuelve la clave, o `RESTO`.

    Gana el PRIMER balde que matchea, **en el orden en que vienen** (que es el
    campo `orden` del catálogo): los conceptos primero, las descripciones
    después. Así un movimiento con concepto IVA y descripción que contiene SELLOS
    cuenta una sola vez y siempre del mismo lado — si sumara en los dos, el
    desglose daría más que el total.

    Por eso el ORDEN es un dato editable y no un detalle: es lo único que decide
    los empates, y es lo que hay que mover cuando dos baldes se pisan.
    """
    for balde in baldes:
        for m in balde.get("matchers") or []:
            dato = valor_campo(mov, m["campo"]).casefold()
            v = str(m["valor"]).strip().casefold()
            if not dato or not v:
                continue
            if (dato == v) if m["operador"] == "igual" else (v in dato):
                return balde["clave"]
    return RESTO


def semilla_catalogo() -> list[dict]:
    """`DESGLOSE_SEMILLA` con la MISMA forma que devuelve `_baldes()`.

    Existe para que la semilla y lo que se lee de la base no tengan dos formas
    distintas: el sembrador y los tests usan esta, y así lo que se prueba es
    exactamente lo que se siembra.
    """
    return [{"clave": b["clave"], "etiqueta": b["etiqueta"], "grupo": b["grupo"],
             "orden": (i + 1) * 10,
             "matchers": [{"id": None, "campo": c, "operador": o, "valor": v}
                          for c, o, v in b["matchers"]]}
            for i, b in enumerate(DESGLOSE_SEMILLA)]


def _baldes_vacios(baldes: list[dict]) -> dict[str, float]:
    return {b["clave"]: 0.0 for b in baldes} | {RESTO: 0.0}


def catalogo_desglose(baldes: list[dict]) -> list[dict]:
    """Lo que el front necesita para dibujar las columnas Y para editarlas. Las
    etiquetas las manda el BACKEND: si el front las copiara, cambiar un balde
    obligaría a tocar dos lados y podrían quedar diciendo cosas distintas.

    Los `matchers` viajan también: son lo que el ABM edita, y ya están leídos.
    """
    return [{"clave": b["clave"], "etiqueta": b["etiqueta"], "grupo": b["grupo"],
             "orden": b["orden"], "matchers": b["matchers"]} for b in baldes]


# Presencia: cuánto vale un heartbeat. El poll de la vista es de 60s, así que el
# TTL tiene que aguantar más de un ciclo o la gente titila al primer poll tarde.
PRESENCIA_TTL_S = 180


def _matchea(mov: dict, regla: dict) -> bool:
    """¿Este movimiento cumple esta regla? PURO — sin base, sin red.

    Es la pieza que decide plata, así que vive sola y con tests: si la
    clasificación se rompe, no falla nada — la columna simplemente muestra otro
    número, que es el peor modo de falla posible.

    Comparación case-insensitive y sin espacios de sobra: la API manda
    `'00108 '` y `'CREDITO POR DATANET'`, y nadie va a escribir una regla
    replicando esos espacios.
    """
    valor = str(regla.get("valor") or "").strip().casefold()
    if not valor:
        return False
    # El texto sale de `valor_campo`, la MISMA puerta que usa el desglose: un
    # campo significa lo mismo en las dos mitades del criterio. Ver su docstring.
    dato = valor_campo(mov, str(regla.get("campo") or "")).casefold()
    if not dato:
        return False
    return dato == valor if regla.get("operador") == "igual" else valor in dato


def clasificar(movs: list[dict], reglas: list[dict],
               overrides: dict[str, bool]) -> dict[str, dict]:
    """{mov_hash: {"es_gasto": bool, "origen": "manual"|"regla"|None, "regla": id}}.

    PURO. El orden de precedencia es lo único que importa acá y es siempre el
    mismo: **la marca manual gana sobre la regla**. Si una persona decidió que
    este movimiento es (o no es) un gasto, ninguna regla se lo da vuelta — mismo
    criterio que `assets.vigencia_motivo='manual'` y que las exclusiones de
    Tesorería.

    `origen` viaja hasta la pantalla a propósito: si el equipo ve que marca lo
    MISMO a mano todos los días, eso no es un override, es una regla que falta.
    """
    activas = [r for r in reglas if r.get("activa", True)]
    out: dict[str, dict] = {}
    for m in movs:
        h = m["mov_hash"]
        if h in overrides:
            out[h] = {"es_gasto": overrides[h], "origen": "manual", "regla": None}
            continue
        regla = next((r for r in activas if _matchea(m, r)), None)
        out[h] = {
            "es_gasto": regla is not None,
            "origen": "regla" if regla else None,
            "regla": regla["id"] if regla else None,
        }
    return out


def fecha_default() -> date:
    """El día que muestra la vista por defecto: **el día HÁBIL ANTERIOR a hoy**.

    No es hoy (user, 2026-08-18: «siempre tiene que ser el día hábil anterior,
    que es la más relevante»). Y tiene sentido: el día hábil anterior está
    CERRADO — el banco ya informó su extracto completo y su saldo final. El día
    de hoy, a media mañana, es una foto a mitad de camino.

    La vista es de **UN día**, no de un rango (user, 2026-08-18: «la fecha es una
    sola, es siempre el mismo día»). Antes eran `desde`/`hasta` y no aportaba: el
    consolidado mostraba la apertura de un día y el cierre de otro, que no es una
    variación de nada.

    El "hoy" sale de la hora ARGENTINA y no del reloj del proceso: el Droplet
    corre en UTC y a partir de las 21 ART ya está en el día siguiente.

    ⚠️ Tiene que caer DENTRO de la ventana que ingesta `jobs/interbanking_sync`
    (`ventana()`: día hábil anterior + hoy). Si la vista arrancara en un día que
    el job no trae, la pantalla mostraría un hueco que no existe en el banco.
    Hay un test que lo fija.
    """
    return restar_habiles(ahora_ar().date(), 1)


def _exec(sql: str, params: tuple) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        n = cur.rowcount
        conn.commit()
        return n or 0


def _movs_para_clasificar(fecha: date, cuenta_id: int | None = None) -> list[dict]:
    """Los campos de los movimientos que una regla puede mirar, de UN día."""
    where = "m.fecha = %s" + (" AND m.cuenta_id = %s" if cuenta_id is not None else "")
    params: tuple = (fecha, cuenta_id) if cuenta_id is not None else (fecha,)
    # ⚠️ El `ignorado` viene por LEFT JOIN y NO por una query aparte. Contra
    # Supabase cada roundtrip cuesta ~8,5ms de peaje FIJO —es distancia, no
    # software—, así que una columna más en una query que ya corre es gratis y
    # una query más no. Hay un test que congela el conteo.
    return _q(
        f"""SELECT m.mov_hash, m.cuenta_id, m.importe, m.tipo, m.codigo_operacion_ib,
                   m.codigo_operacion_banco, m.descripcion_banco, m.descripcion_ib,
                   (i.mov_hash IS NOT NULL) AS ignorado
              FROM bancos.movimientos m
              LEFT JOIN bancos.movimientos_ignorados i ON i.mov_hash = m.mov_hash
             WHERE {where}""",
        params,
    )


def _baldes() -> list[dict]:
    """El catálogo del desglose, de la BASE. **UNA sola query** (baldes + sus
    matchers en un LEFT JOIN) porque contra Supabase cada roundtrip son ~8,5ms de
    peaje fijo, y esto lo pide cada request de las dos vistas.

    Si la tabla está vacía se SIEMBRA con `DESGLOSE_SEMILLA` y se relee. Es la
    única escritura que hace un camino de lectura, y pasa una vez en la vida de
    la base: sin eso, la primera carga tras el deploy mostraría todo en
    MOVIMIENTOS RESTANTES y parecería un bug.
    """
    filas = _q(
        """SELECT b.clave, b.etiqueta, b.grupo, b.orden,
                  m.id AS matcher_id, m.campo, m.operador, m.valor
             FROM bancos.gastos_baldes b
             LEFT JOIN bancos.gastos_balde_matchers m ON m.balde = b.clave
            WHERE b.activo
            ORDER BY b.orden, b.clave, m.id""")
    if not filas and _sembrar_desglose():
        return _baldes()

    out: dict[str, dict] = {}
    for r in filas:
        b = out.setdefault(r["clave"], {
            "clave": r["clave"], "etiqueta": r["etiqueta"],
            "grupo": r["grupo"], "orden": r["orden"], "matchers": [],
        })
        if r.get("matcher_id") is not None:
            b["matchers"].append({"id": r["matcher_id"], "campo": r["campo"],
                                  "operador": r["operador"], "valor": r["valor"]})
    return list(out.values())


def _sembrar_desglose() -> bool:
    """Carga la semilla. Idempotente y **solo si la tabla está vacía de verdad**:
    un balde que alguien borró a propósito no puede volver solo."""
    try:
        if _q("SELECT 1 FROM bancos.gastos_baldes LIMIT 1"):
            return False
        for b in semilla_catalogo():
            _exec("""INSERT INTO bancos.gastos_baldes (clave, etiqueta, grupo, orden,
                                                       creado_por)
                     VALUES (%s,%s,%s,%s,'semilla') ON CONFLICT (clave) DO NOTHING""",
                  (b["clave"], b["etiqueta"], b["grupo"], b["orden"]))
            for m in b["matchers"]:
                _exec("""INSERT INTO bancos.gastos_balde_matchers
                           (balde, campo, operador, valor, creado_por)
                         VALUES (%s,%s,%s,%s,'semilla')
                         ON CONFLICT (balde, campo, operador, valor) DO NOTHING""",
                      (b["clave"], m["campo"], m["operador"], m["valor"]))
        return True
    except Exception:
        # Sin catálogo el desglose queda todo en RESTO, que es visible y honesto.
        # Tumbar la vista entera por esto sería peor.
        return False


def listar_reglas() -> list[dict]:
    return _q(
        """SELECT id, campo, operador, valor, nota, activa, creado_por, creado_at
             FROM bancos.gastos_reglas ORDER BY campo, valor"""
    )


def _overrides(fecha: date) -> dict[str, bool]:
    return {r["mov_hash"]: r["es_gasto"] for r in _q(
        """SELECT o.mov_hash, o.es_gasto
             FROM bancos.gastos_overrides o
             JOIN bancos.movimientos m ON m.mov_hash = o.mov_hash
            WHERE m.fecha = %s""", (fecha,))}


def _ajuste_manual(fecha: date) -> dict[int, dict]:
    """{cuenta_id: {"ajuste", "acumulado", "movimientos"}} — los manuales de una
    cuenta vistos desde una fecha. Una sola query.

    `ajuste` es lo cargado **ESE día**. `acumulado` es todo lo cargado hasta ese
    día inclusive. Los dos se usan, pero **NO en la misma cuenta**, y de eso se
    trata todo:

      · **Cuenta que informa Interbanking** → se usa `ajuste`, el DEL DÍA. El
        saldo que el banco manda mañana **ya trae adentro** el movimiento de hoy,
        así que sumarle además el manual viejo lo cuenta dos veces.
      · **Cuenta que Interbanking NO informa** → se usa `acumulado`. Ahí no hay
        ningún saldo del banco que lo absorba: si no se acumula, la cuenta vuelve
        a cero al día siguiente teniendo la plata.

    ⚠️ **Esto ya se rompió por acumular de más (2026-08-27).** Se aplicó el
    acumulado a TODAS las cuentas, y el cierre pasó a incluir *todos los manuales
    cargados desde siempre* arriba del saldo de Interbanking. El síntoma: el
    cierre daba MÁS que Interbanking + los manuales del día, y la diferencia
    entre dos días no se explicaba con nada de lo que la pantalla mostraba.

    Lo que se arrastra de un día al otro NO se arrastra acá: el **saldo inicial**
    de un día es el **cierre del día anterior**, y eso lo resuelve
    `_saldos_banco(día hábil anterior)` leyendo el cierre entero de esa fecha.
    """
    signo = "CASE WHEN tipo = 'C' THEN abs(importe) ELSE -abs(importe) END"
    return {r["cuenta_id"]: {"ajuste": _f(r["ajuste"]) or 0.0,
                             "acumulado": _f(r["acumulado"]) or 0.0,
                             "movimientos": r["n"]}
            for r in _q(
                f"""SELECT cuenta_id,
                           sum(CASE WHEN fecha = %s THEN {signo} ELSE 0 END) AS ajuste,
                           sum({signo})                                      AS acumulado,
                           count(*) FILTER (WHERE fecha = %s)                AS n
                      FROM bancos.movimientos_manuales
                     WHERE fecha <= %s
                     GROUP BY cuenta_id""", (fecha, fecha, fecha))}


def _manuales_de(fecha: date, cuenta_id: int) -> list[dict]:
    """TODOS los movimientos manuales de UNA cuenta hasta esa fecha, en orden.

    ⚠️ Trae el histórico y no solo el día por dos razones:

    1. El ajuste del saldo es ACUMULADO (ver `_ajuste_manual()`), así que la
       vista necesita el total; sacarlo de esta misma lista es una query en vez
       de dos, y el tope de `test_interbanking_queries` no se mueve. `vista()`
       la parte en «los del día» (lo que muestra el modal) y «los anteriores».
    2. **Sin el histórico a la vista, el acumulado no se puede auditar.** La
       forma de sacar un manual que ya entró por el banco es cargar otro EN
       CONTRA, y para eso hay que poder ver cuáles siguen pesando.
    """
    return _q(
        """SELECT id, fecha, descripcion, importe, tipo, creado_por, creado_at
             FROM bancos.movimientos_manuales
            WHERE fecha <= %s AND cuenta_id = %s
            ORDER BY fecha, creado_at""", (fecha, cuenta_id))


def _gastos_bancarios(fecha: date, baldes: list[dict]) -> dict[int, dict]:
    """{cuenta_id: {"total": x, "<balde>": y, ...}}. Se DERIVA, no se persiste.

    Resolver en la lectura y no materializar una columna tiene una consecuencia
    concreta: cambiar una regla se refleja al instante en todos los días que haya
    en la base, sin recomputar nada, y el total de la grilla no puede contradecir
    al catálogo de reglas. Es barato porque la base retiene 3 fechas.

    **Signo**: un débito SUMA (el banco cobró) y un crédito RESTA (lo reintegró).
    Así la columna es "cuánto se llevó el banco ese día, neto", que es la
    pregunta que se está haciendo — y no la suma de valores absolutos, que
    contaría dos veces un cobro mal hecho y su devolución.
    """
    movs = _movs_para_clasificar(fecha)
    if not movs:
        return {}
    reglas, overrides = listar_reglas(), _overrides(fecha)
    # Sin una sola regla ni marca, no hay CRITERIO: devolver ceros diría "el
    # banco no cobró nada", que es una conclusión que nadie sacó. Se devuelve
    # vacío y la vista muestra «—».
    if not [r for r in reglas if r.get("activa", True)] and not overrides:
        return {}

    marcas = clasificar(movs, reglas, overrides)
    # Con criterio cargado, una cuenta que tuvo movimientos y ninguno es gasto
    # vale CERO de verdad — ahí sí lo sabemos.
    out: dict[int, dict] = {
        m["cuenta_id"]: {"total": 0.0, **_baldes_vacios(baldes)} for m in movs}
    for m in movs:
        # Ignorado = no cuenta. Sigue clasificado y sigue visible en la lista
        # (tachado): lo que cambia es que no suma.
        if m.get("ignorado") or not marcas[m["mov_hash"]]["es_gasto"]:
            continue
        # Un gasto es un DÉBITO, así que suma con signo positivo: la columna
        # contesta "cuánto se llevó el banco", no "cuánto se movió el saldo".
        firmado = -_firmado(m)
        celda = out[m["cuenta_id"]]
        celda["total"] += firmado
        celda[desglosar(m, baldes)] += firmado
    return {cid: {k: round(v, 2) for k, v in celda.items()} for cid, celda in out.items()}


def _cuenta_publica(r: dict) -> dict:
    """Lo que la vista puede ver de una cuenta.

    ⚠️ **El NÚMERO DE CUENTA se publica ENTERO desde el 2026-08-18**, por decisión
    del user. Antes salía solo la terminación (`…0488`) — era una decisión mía y
    no de ellos, y sobraba de prudente: son las cuentas **de la casa**, no de un
    tercero, las mira el back office detrás de Cloudflare Access con el módulo
    `back-office`, y el número es justamente lo que después copian y pegan en
    otros sistemas. Esconderlo no protegía nada y volvía la vista inútil para su
    trabajo.

    **El CBU sigue sin salir**, y esa distinción es la que importa: el número de
    cuenta identifica, el **CBU es lo que hace falta para transferirle plata**.
    Son dos niveles de riesgo distintos y no se relajan juntos. Si algún día
    hace falta, es otra decisión explícita — hay un test que lo congela.
    """
    return {
        "id": r["id"],
        "banco": r.get("bank_number"),
        "banco_nombre": (r.get("bank_name") or "").strip(),
        "tipo": r.get("account_type"),
        "moneda": r.get("currency"),
        "etiqueta": (r.get("account_label") or "").strip(),
        "numero": str(r.get("account_number") or "").strip(),
        "activa": r.get("activa"),
        # `manual` = la cargó una persona, no Interbanking. La vista lo marca:
        # una cuenta cuyo saldo no lo informa ningún banco no vale lo mismo que
        # una conciliada contra un extracto.
        "manual": (r.get("origen") or "interbanking") == "manual",
    }


def _cuit_enmascarado(v: str | None) -> str | None:
    """CUIT de terceros: se muestra lo justo para reconocerlo, no para copiarlo."""
    s = "".join(ch for ch in str(v or "") if ch.isdigit())
    return f"{s[:2]}-…-{s[-1:]}" if len(s) >= 8 else None


# ⚠️ **EL SIGNO DEL IMPORTE — la trampa que rompió el control de DIFERENCIAS.**
#
# `bancos.movimientos.importe` viene **YA FIRMADO** de Interbanking: un débito
# llega NEGATIVO. Yo asumí que llegaba en valor absoluto y que el signo lo ponía
# `tipo` (C/D), así que le aplicaba el signo **por segunda vez** — y una suma que
# tenía que dar `−500,53` daba `1.612.340.349,01`, o sea la suma de los VALORES
# ABSOLUTOS. Verificado al centavo contra los movimientos reales de una cuenta.
#
# La regla que queda: **`abs(importe)` con el signo que dice `tipo`.** No es
# "sacar la negación de más" — es la única fórmula que da bien tanto si el banco
# firma el importe como si no, y hay bancos de los dos tipos entre los 9. Nunca
# usar `importe` crudo para sumar.
def _firmado(r: dict) -> float:
    """El importe con el signo correcto: + si el banco acreditó, − si debitó."""
    imp = abs(_f(r.get("importe")) or 0.0)
    return -imp if (r.get("tipo") or "").upper() == "D" else imp


def _movimiento_publico(r: dict) -> dict:
    return {
        # El hash es la IDENTIDAD del movimiento: sin él la pantalla no puede
        # pedir "marcá este". No es un dato sensible — es un sha256 de campos
        # que la propia fila ya muestra.
        "mov_hash": r.get("mov_hash"),
        "fecha": r["fecha"].isoformat() if r.get("fecha") else None,
        "hora": r["fecha_proceso"].strftime("%H:%M:%S") if r.get("fecha_proceso") else None,
        # ⚠️ El importe se publica en VALOR ABSOLUTO y el signo lo dice `tipo`.
        # Crudo viene ya firmado del banco, y la pantalla —que dibuja el signo a
        # partir de `tipo`— mostraba «--86,73» con dos menos. Un solo lugar
        # decide el signo: `_firmado` acá, `tipo` en la pantalla.
        "importe": abs(_f(r.get("importe")) or 0.0) if r.get("importe") is not None else None,
        "tipo": r.get("tipo"),
        "descripcion": (r.get("descripcion_banco") or r.get("descripcion_ib") or "").strip(),
        "concepto": (r.get("descripcion_ib") or "").strip(),
        "codigo": r.get("codigo_operacion_ib"),
        # COD OP BCO y SUCURSAL: se venían guardando desde la primera corrida y
        # no se publicaban. El back office los pidió el 2026-08-18 y no costaron
        # ni una llamada nueva ni un backfill — el dato ya estaba en la base.
        "codigo_banco": r.get("codigo_operacion_banco"),
        "sucursal": (str(r.get("sucursal")).strip() if r.get("sucursal") is not None else None),
        "extracto": r.get("numero_extracto"),
        "correlativo": r.get("correlativo"),
        "comprobante": r.get("comprobante"),
        "contraparte": (r.get("denominacion_contraparte") or "").strip() or None,
        "contraparte_cuit": _cuit_enmascarado(r.get("cuit_contraparte")),
        # IGNORAR — mismo modelo que el destildado de Tesorería: la fila SIGUE
        # visible (tachada), pero no suma. Sin fila en la tabla de ignorados el
        # movimiento cuenta, que es el default sano.
        "ignorado": bool(r.get("ignorado")),
        "ignorado_motivo": r.get("ignorado_motivo"),
        "ignorado_por": r.get("ignorado_por"),
        "ignorado_at": r["ignorado_at"].isoformat() if r.get("ignorado_at") else None,
    }


def _manual_publico(r: dict) -> dict:
    """Un movimiento manual, con las MISMAS claves que uno del banco: así la
    pantalla los dibuja en la misma tabla sin un segundo juego de columnas. Lo
    que los distingue es `manual`, que la fila usa para marcarse y para ofrecer
    borrarla — un movimiento del banco no se borra, este sí."""
    return {
        "id": r["id"],
        "manual": True,
        "fecha": r["fecha"].isoformat() if r.get("fecha") else None,
        "hora": r["creado_at"].strftime("%H:%M") if r.get("creado_at") else None,
        # Misma convención que los del banco: valor absoluto + `tipo`.
        "importe": abs(_f(r.get("importe")) or 0.0),
        "tipo": r.get("tipo"),
        "descripcion": (r.get("descripcion") or "").strip(),
        "por": r.get("creado_por"),
    }


def _dia_publico(r: dict) -> dict:
    return {
        "fecha": r["fecha"].isoformat() if r.get("fecha") else None,
        "saldo_apertura": _f(r.get("saldo_apertura")),
        "saldo_cierre": _f(r.get("saldo_cierre")),
        "creditos": _f(r.get("total_creditos")),
        "debitos": _f(r.get("total_debitos")),
        "movimientos_banco": r.get("total_movimientos"),
        "movimientos_base": r.get("movimientos_base"),
        "cierra": r.get("cierra"),
        "diferencia": _f(r.get("diferencia")),
        "sincronizado_at": (r["sincronizado_at"].isoformat()
                            if r.get("sincronizado_at") else None),
    }


# --------------------------------------------------------------------------- #
# Lecturas
# --------------------------------------------------------------------------- #
def listar_cuentas() -> list[dict]:
    filas = _q(
        """SELECT id, bank_number, bank_name, account_number, account_type,
                  currency, account_label, activa, origen
             FROM bancos.cuentas
            WHERE activa
            ORDER BY bank_name, currency, account_type, account_number"""
    )
    return [_cuenta_publica(r) for r in filas]


def vista(email: str, cuenta_id: int | None, fecha: date) -> dict:
    """TODO lo que muestra la tab, en UN request.

    Mismo criterio que `senebis.vista` y `aca.vista`: una tab que pollea cada
    sub-panel por separado multiplica los viajes a la base por usuario.

    De UN día, igual que el consolidado: el selector de la vista es una sola
    fecha (user, 2026-08-18).

    Desde que el detalle es un MODAL que se abre desde el consolidado, el
    `cuenta_id` viene SIEMPRE — así que el catálogo de cuentas solo se consulta
    en el caso degenerado de que no venga. Traerlo igual era una query extra por
    cada apertura del modal para una lista que la pantalla ya tiene.
    """
    cuentas: list[dict] = []
    if cuenta_id is None:
        cuentas = listar_cuentas()
        if cuentas:
            cuenta_id = cuentas[0]["id"]

    dias: list[dict] = []
    movimientos: list[dict] = []

    if cuenta_id is not None:
        dias = [_dia_publico(r) for r in _q(
            """SELECT e.fecha, e.saldo_apertura, e.saldo_cierre, e.total_creditos,
                      e.total_debitos, e.total_movimientos, e.cierra, e.diferencia,
                      e.sincronizado_at,
                      (SELECT count(*) FROM bancos.movimientos m
                        WHERE m.cuenta_id = e.cuenta_id AND m.fecha = e.fecha)
                        AS movimientos_base
                 FROM bancos.extracto_dia e
                WHERE e.cuenta_id = %s AND e.fecha = %s""",
            (cuenta_id, fecha))]

        movimientos = [_movimiento_publico(r) for r in _q(
            """SELECT m.mov_hash, m.fecha, m.fecha_proceso, m.importe, m.tipo,
                      m.descripcion_banco, m.descripcion_ib, m.codigo_operacion_ib,
                      m.codigo_operacion_banco, m.sucursal, m.numero_extracto,
                      m.correlativo, m.comprobante, m.cuit_contraparte,
                      m.denominacion_contraparte,
                      (i.mov_hash IS NOT NULL) AS ignorado,
                      i.motivo AS ignorado_motivo, i.por AS ignorado_por,
                      i.at AS ignorado_at
                 FROM bancos.movimientos m
                 LEFT JOIN bancos.movimientos_ignorados i ON i.mov_hash = m.mov_hash
                WHERE m.cuenta_id = %s AND m.fecha = %s
                ORDER BY m.numero_extracto DESC, m.correlativo""",
            (cuenta_id, fecha))]

    # Los MANUALES se suman a la lista con las mismas columnas, marcados. Van
    # después de clasificar a propósito: no son gastos bancarios (no los cobró el
    # banco) y no tienen códigos que una regla pueda mirar, así que meterlos
    # antes solo los haría caer en MOVIMIENTOS RESTANTES y ensuciar el desglose.
    manuales: list[dict] = []

    # La marca de gasto se resuelve acá y no en la pantalla: es la MISMA función
    # que alimenta la columna GASTOS BANCARIOS del consolidado, así que el
    # detalle no puede contradecir al total.
    #
    # ⚠️ Los crudos y las reglas se leen UNA vez y se reusan para la marca Y para
    # el desglose. Hasta el 2026-08-18 se leían dos veces cada uno: 4 queries
    # donde alcanzan 2. Con el peaje medido de ~8.5ms por roundtrip contra
    # Supabase, lo que importa NO es el plan de la query sino cuántas son.
    reglas: list[dict] = []
    crudos: dict[str, dict] = {}
    if cuenta_id is not None and movimientos:
        reglas = listar_reglas()
        crudos = {m["mov_hash"]: m for m in _movs_para_clasificar(fecha, cuenta_id)}
        marcas = clasificar(list(crudos.values()), reglas, _overrides(fecha))
        for m in movimientos:
            marca = marcas.get(m["mov_hash"]) or {}
            m["es_gasto"] = bool(marca.get("es_gasto"))
            m["gasto_origen"] = marca.get("origen")

    # El desglose se arma con la MISMA función que la columna del consolidado,
    # así el detalle no puede contradecir al total de la grilla.
    baldes = _baldes()
    desglose = _baldes_vacios(baldes)
    if crudos:
        for m in movimientos:
            crudo = crudos.get(m["mov_hash"])
            if not m.get("es_gasto") or crudo is None or crudo.get("ignorado"):
                continue
            balde = desglosar(crudo, baldes)
            # Cada movimiento viaja diciendo EN QUÉ BALDE cayó. Sin esto, la
            # pantalla no puede mostrar qué filas componen cada número del
            # desglose — y un total que no se puede abrir es un total en el que
            # hay que creer.
            m["gasto_balde"] = balde
            desglose[balde] += -_firmado(m)
        desglose = {k: round(v, 2) for k, v in desglose.items()}

    previos: list[dict] = []
    if cuenta_id is not None:
        # Una sola lectura para las dos cosas: lo del día (el modal) y lo de
        # antes, que sigue adentro del saldo. Ver `_manuales_de`.
        todos = [_manual_publico(r) for r in _manuales_de(fecha, cuenta_id)]
        iso = fecha.isoformat()
        manuales = [m for m in todos if m["fecha"] == iso]
        previos = [m for m in todos if m["fecha"] != iso]

    # ⚠️ Créditos y débitos siguen siendo la aritmética del EXTRACTO: los
    # manuales no entran. Si entraran, la vista dejaría de poder compararse con
    # lo que informa el banco, que es para lo que existe la conciliación. Lo que
    # los manuales mueven —el saldo— viaja aparte, en `ajuste_manual`.
    creditos = sum(abs(m["importe"] or 0) for m in movimientos if m["tipo"] == "C")
    debitos = sum(abs(m["importe"] or 0) for m in movimientos if m["tipo"] == "D")
    ajuste_manual = round(sum(_firmado(m) for m in manuales), 2)
    # El acumulado se publica (es lo que arrastra una cuenta que Interbanking no
    # informa), pero el saldo de la pantalla usa el DEL DÍA: si el banco manda su
    # saldo, ese saldo ya trae lo viejo adentro. Sale de la lista ya leída.
    acumulado = round(ajuste_manual + sum(_firmado(m) for m in previos), 2)

    # ⚠️ **El saldo que muestra la pantalla es el NUESTRO, no el del extracto**
    # (2026-08-27). Salía crudo de `extracto_dia.saldo_cierre`, así que una cuenta
    # con movimientos manuales mostraba acá un número y otro en el CONSOLIDADO
    # —misma cuenta, mismo día—, y el de acá era el que ignoraba la carga del
    # back office. El del banco viaja aparte: es la evidencia independiente
    # contra la que se concilia, así que no se pierde, se nombra.
    #
    # Sale de la lista que ya se leyó arriba: no cuesta una query.
    saldo_banco = next((d["saldo_cierre"] for d in dias), None)

    resumen = {
        "dias": len(dias),
        "movimientos": len(movimientos),
        "creditos": round(creditos, 2),
        "debitos": round(debitos, 2),
        "neto": round(creditos - debitos, 2),
        # Las dos alertas de conciliación. Se calculan acá, no en el front.
        "dias_que_no_cierran": [d["fecha"] for d in dias if d["cierra"] is False],
        "dias_incompletos": [d["fecha"] for d in dias
                             if d["movimientos_banco"] is not None
                             and d["movimientos_banco"] != d["movimientos_base"]],
        "saldo_final": (None if saldo_banco is None
                        else round(saldo_banco + ajuste_manual, 2)),
        "saldo_final_banco": saldo_banco,
        "ajuste_manual": ajuste_manual,
        "ajuste_manual_acumulado": acumulado,
        "movimientos_manuales": len(manuales),
        "gastos": round(sum(
            -_firmado(m)
            for m in movimientos if m.get("es_gasto") and not m.get("ignorado")), 2),
        "gastos_desglose": desglose,
    }

    _auditar(email, cuenta_id, fecha, fecha, len(movimientos))

    return {
        "cuentas": cuentas,
        "cuenta_id": cuenta_id,
        "puede_escribir": puede_escribir(email),
        # Ya se leyeron arriba para clasificar: leerlas de nuevo para la
        # respuesta era una query entera de regalo.
        "reglas": reglas if reglas else listar_reglas(),
        "desglose": catalogo_desglose(baldes),
        "fecha": fecha.isoformat(),
        "dias": dias,
        "movimientos": movimientos,
        "manuales": manuales,
        # Los de días anteriores que SIGUEN adentro del saldo. Un ajuste que no
        # se puede ver es un ajuste que no se puede corregir.
        "manuales_previos": previos,
        "resumen": resumen,
        "sync": ultima_sync(),
    }


# --------------------------------------------------------------------------- #
# CONCILIAR contra el mayor del sistema contable
# --------------------------------------------------------------------------- #
# Compara UN número contra otro: nuestro saldo al cierre y el ÚLTIMO saldo del
# mayor que el usuario sube como Excel. Si no coinciden, busca qué movimientos
# del día explican la diferencia — porque el caso típico es que al mayor le falte
# registrar algo que el banco sí tiene.
#
# ⚠️ **El navegador no interpreta el archivo**: manda la grilla cruda y todo el
# criterio vive acá, donde se puede testear. Nada se persiste.

def _num_mayor(celda) -> float | None:
    """El número de una celda del mayor. Devuelve `None` si no hay número.

    ⚠️ **Medido sobre el export real** (`mayor_36.xlsx`), porque acá había dos
    hipótesis y las dos importaban:

    1. El valor de la celda **ya viene firmado** (`-499946423.26`). La `D`/`A`
       que se ve en Excel **es formato de celda**, no texto:
       `#,##0.00" D";#,##0.00" A"` — dos secciones, la segunda es la NEGATIVA y
       **no lleva el menos**. O sea: `A` = negativo, y el número al lado sale en
       valor absoluto.
    2. El front lee con `raw: false` justamente para no perder esa letra, así que
       lo que llega acá es el TEXTO formateado (`"499,946,423.26 A"`), no el
       número. Por eso hay que parsear.

    Se aceptan las dos formas igual —número o texto— porque un CSV, otro export
    o un `raw: true` futuro cambiarían eso sin avisar, y un conciliador que se
    rompe en silencio es peor que no tenerlo.

    El separador decimal se decide por POSICIÓN y no por convención: gana el
    último `.` o `,` que aparezca, salvo que le sigan exactamente 3 dígitos y sea
    el único (ahí es de miles). Así `1,234.56`, `1.234,56` y `500.000` se leen
    bien sin preguntarle a nadie de qué país es el archivo.
    """
    if celda is None:
        return None
    if isinstance(celda, (int, float)) and not isinstance(celda, bool):
        return float(celda)

    txt = str(celda).strip()
    if not txt:
        return None

    # La letra del formato: D deudor (+), A acreedor (−).
    letra = ""
    m = re.search(r"([DAdaCc])\s*$", txt)
    if m:
        letra = m.group(1).upper()
        txt = txt[:m.start()].strip()

    negativo = txt.startswith("-") or (txt.startswith("(") and txt.endswith(")"))
    crudo = re.sub(r"[^0-9.,]", "", txt)
    if not re.search(r"\d", crudo):
        return None

    ult_punto, ult_coma = crudo.rfind("."), crudo.rfind(",")
    corte = max(ult_punto, ult_coma)
    if corte == -1:
        entero, dec = crudo, ""
    else:
        decimales = len(crudo) - corte - 1
        solo_uno = crudo.count(crudo[corte]) == 1 and (ult_punto == -1 or ult_coma == -1)
        if decimales == 3 and solo_uno:
            entero, dec = crudo.replace(crudo[corte], ""), ""   # separador de miles
        else:
            entero, dec = crudo[:corte], crudo[corte + 1:]
    valor = float(re.sub(r"[.,]", "", entero) + ("." + dec if dec else "") or 0)

    # La letra MANDA sobre el menos: en el formato del mayor el negativo se
    # imprime sin signo y con la `A`. Si vinieran los dos, decir dos veces lo
    # mismo no puede dar positivo.
    if letra == "A" or (not letra and negativo):
        valor = -abs(valor)
    elif letra == "D":
        valor = abs(valor)
    return valor


def _saldo_del_mayor(filas: list) -> dict:
    """El ÚLTIMO saldo del mayor: qué columna, qué fila y con qué texto.

    Se devuelve la evidencia (`texto`, `fila`) y no solo el número porque el que
    concilia tiene que poder verificar de dónde salió **sin abrir el Excel al
    lado**. Un conciliador que muestra un número sin decir de dónde lo sacó
    obliga a confiar, que es justo lo que no se puede pedir acá.
    """
    if not filas:
        return {"valor": None, "texto": None, "fila": None, "columna": None,
                "avisos": ["El archivo llegó vacío."]}

    # La columna SALDO se busca por ENCABEZADO y no por posición: el export puede
    # traer títulos arriba o columnas de más, y contar desde la izquierda se
    # rompe el día que agreguen una.
    col, encabezado = _columna(filas, "saldo")

    avisos: list[str] = []
    if col is None:
        # Último recurso: la última columna que tenga números. Se avisa, porque
        # adivinar y no decirlo es la forma de que un número equivocado pase por
        # bueno.
        anchos = max((len(f or []) for f in filas), default=0)
        for j in range(anchos - 1, -1, -1):
            if any(_num_mayor(f[j]) is not None for f in filas if len(f or []) > j):
                col = j
                break
        if col is None:
            return {"valor": None, "texto": None, "fila": None, "columna": None,
                    "avisos": ["No encontré ninguna columna con números en el archivo."]}
        avisos.append(
            f"El archivo no tiene una columna llamada «Saldo»: se usó la columna "
            f"{col + 1}, que es la última con números. Verificá que sea la correcta.")

    for i in range(len(filas) - 1, (encabezado or -1), -1):
        fila = filas[i] or []
        if len(fila) <= col:
            continue
        valor = _num_mayor(fila[col])
        if valor is not None:
            return {"valor": valor, "texto": str(fila[col]).strip(), "fila": i + 1,
                    "columna": col, "avisos": avisos}
    return {"valor": None, "texto": None, "fila": None, "columna": col,
            "avisos": [*avisos, "La columna de saldo no tiene ningún número."]}


def _columna(filas: list, *nombres: str) -> tuple[int | None, int | None]:
    """Dónde está la columna `nombre` y en qué fila el encabezado. Por NOMBRE y
    no por posición: el export puede traer títulos arriba o columnas de más."""
    buscados = {n.strip().lower() for n in nombres}
    for i, fila in enumerate(filas[:10]):
        for j, celda in enumerate(fila or []):
            if str(celda or "").strip().lower() in buscados:
                return j, i
    return None, None


# El `[Op. NNNN]` con que el mayor prefija TODOS sus conceptos. Es el número de
# asiento: cambia en cada fila y es justo lo que impide agruparlos.
_RE_OP = re.compile(r"^\[\s*op\.?\s*\d+\s*\]\s*", re.I)


def _grupo_mayor(concepto: str) -> str:
    """El TIPO de movimiento del mayor, para poder consolidarlo.

    ⚠️ El concepto crudo es **único fila por fila** y por eso no sirve como clave:
    `[Op. 1133481] Extracción - Extracción CE 2026005064- Cta. 1687/GRAL` lleva el
    número de asiento Y el del comprobante. Agrupar por él daría tantos grupos
    como movimientos, o sea la misma lista otra vez.

    Dos cortes, los mínimos, medidos sobre exports reales de Valores y Credicoop:
    se saca el `[Op. NNNN]` y se corta en el primer ` - `. Las tres extracciones
    del ejemplo colapsan en `Extracción`; en el mayor de Credicoop no hay ` - `
    y `bco a bco` / `Recibir fondos` quedan enteros.
    """
    txt = _RE_OP.sub("", (concepto or "").strip()).split(" - ", 1)[0]
    return " ".join(txt.split()) or "(sin concepto)"


def _movimientos_del_mayor(filas: list) -> dict:
    """Los movimientos del mayor: concepto e importe FIRMADO.

    **`importe = Debe − Haber`**, medido y verificado sobre el export real.

    ⚠️ **Una fila es un movimiento si tiene FECHA.** Nada más. La versión anterior
    trataba de identificar el «saldo inicial» y de auto-verificar el parseo
    (inicial + movimientos = saldo final), y el chequeo **se sacó**: el formato
    del mayor admite hasta 7 decimales (`#,##0.00#####`), así que un
    `1.515.504,677` es genuinamente ambiguo contra un separador de miles y el
    saldo inicial se leyó **mil veces más grande**. El aviso salió gritando en un
    archivo que estaba perfecto.

    Y eso es peor que no tener chequeo: **un aviso que grita cuando no pasa nada
    entrena a ignorar todos los avisos**, incluidos los que sí importan. Si el
    dato que lo alimenta no es confiable, el chequeo no es "una ayuda extra": es
    ruido con cara de hallazgo.

    La fecha, en cambio, no es ambigua — y es la definición de movimiento.
    """
    col_debe, enc = _columna(filas, "debe")
    col_haber, _ = _columna(filas, "haber")
    col_concepto, _ = _columna(filas, "concepto", "descripcion", "descripción", "detalle")
    col_fecha, _ = _columna(filas, "fecha")

    if col_debe is None and col_haber is None:
        return {"movimientos": [], "suma": 0.0,
                "avisos": ["El archivo no tiene columnas «Debe» y «Haber»: se "
                           "compara solo el saldo, sin detalle."]}

    def _celda(fila, col):
        return fila[col] if col is not None and len(fila) > col else None

    movimientos: list[dict] = []
    for i, fila in enumerate(filas):
        fila = fila or []
        if enc is not None and i <= enc:
            continue
        debe = _num_mayor(_celda(fila, col_debe)) or 0.0
        haber = _num_mayor(_celda(fila, col_haber)) or 0.0
        if not debe and not haber:
            continue
        concepto = str(_celda(fila, col_concepto) or "").strip()
        # Sin fecha no es un movimiento: es el saldo inicial, un subtotal o un
        # pie del reporte. Si el archivo no trae fecha, se cae al concepto.
        if col_fecha is not None:
            if not str(_celda(fila, col_fecha) or "").strip():
                continue
        elif "saldo" in concepto.lower():
            continue
        movimientos.append({"concepto": concepto or "(sin concepto)",
                            "grupo": _grupo_mayor(concepto),
                            "importe": round(debe - haber, 2), "fila": i + 1})

    return {"movimientos": movimientos,
            "suma": round(sum(m["importe"] for m in movimientos), 2),
            "avisos": []}


# Cuántos movimientos se combinan buscando la explicación.
#
# ⚠️ Era 3 y con `combinations()`. Se subió a 8 junto con el cambio de algoritmo
# (`_combinaciones`, DFS con poda) porque el caso real que no encontraba —una
# diferencia de 4.256.787,71 que salía de sumar VARIOS movimientos de
# Interbanking— nunca podía aparecer: la explicación tenía más de tres partes y
# el buscador ni la generaba. Enumerar C(40,8) son 76 millones de combinaciones
# y no termina nunca; podando por la suma que queda disponible, el árbol se
# corta apenas el resto no alcanza.
#
# `TOPE_NODOS` es el techo de trabajo: si se llega, la respuesta lo DICE
# (`candidatos_truncados`). «No encontré» y «no busqué todo» son cosas distintas.
MAX_COMBINAR = 8
MAX_MOVS_COMBINAR = 40
TOPE_NODOS = 150_000

# Diferencia por debajo de la cual **no hay diferencia**.
#
# ⚠️ Es NOMINAL y por moneda (1 peso, 1 dólar), no un porcentaje. Nace de un caso
# real: 590.708,27 contra 590.708,12 — **15 centavos** que la pantalla mostraba
# como hallazgo y mandaban al back office a buscar un movimiento inexistente.
# Debajo de un peso no hay ningún movimiento que pueda explicar la diferencia:
# es redondeo del sistema contable. Mostrarlo no es ser prolijo, es gastar
# atención en algo que no se puede resolver.
SIN_DIFERENCIA = 1.0

# Margen para encontrar el movimiento cuando la diferencia SÍ existe. Proporcional
# con piso: sobre 500 millones un peso es tan estricto como la igualdad exacta y
# vuelve a esconder el movimiento; sobre mil pesos un porcentaje solo tampoco
# alcanzaría para un centavo.
TOLERANCIA_PISO = 1.0
TOLERANCIA_PCT = 0.00001


def tolerancia(diferencia: float) -> float:
    """El margen para esta diferencia. Se publica en la respuesta: un criterio
    que decide qué se muestra tiene que poder leerse en la pantalla."""
    return round(max(TOLERANCIA_PISO, abs(diferencia) * TOLERANCIA_PCT), 2)


# Margen del ÚLTIMO recurso: «esto no da, pero le pega cerca».
#
# ⚠️ Existe por un caso concreto: una diferencia de 4.256.787,71 que sumando
# varios movimientos del banco quedaba «prácticamente en el mismo importe», y la
# pantalla contestaba «ningún movimiento llega a esa diferencia» — o sea,
# escondía la única pista que había. Media hora de buscar a mano para llegar a lo
# mismo.
#
# Dos candados para que esto NO se convierta en «encontrar cualquier cosa»:
#   1. **Solo COMBINACIONES** (2 movimientos o más). Un movimiento suelto que
#      «casi» da es OTRO movimiento, no el que falta — para el redondeo ya está
#      `tolerancia()`. Sin este candado, 999.950 pasaría por una diferencia de
#      1.000.000, que es exactamente lo que se decidió no hacer.
#   2. **Solo si no hubo ninguna explicación exacta.** Se busca de lo más
#      estricto a lo más laxo y se corta en la primera pasada que encuentra algo.
# Y siempre viaja marcado (`aproximado`) y con el `resto` a la vista.
APROX_PISO = 100.0
APROX_PCT = 0.005


def tolerancia_aproximada(diferencia: float) -> float:
    """El margen del último recurso. También se publica: es el que más fácil
    puede hacer pasar una coincidencia por un hallazgo."""
    return round(max(APROX_PISO, abs(diferencia) * APROX_PCT), 2)


def _armar(combo: list[tuple[dict, float]], suma: float, objetivo: float,
           aproximado: bool = False) -> dict:
    return {"movimientos": [m for m, _ in combo], "suma": round(suma, 2),
            "cantidad": len(combo), "resto": round(objetivo - suma, 2),
            "signo_invertido": suma != 0 and round(suma, 2) == round(-objetivo, 2),
            "aproximado": aproximado, "motivo": None}


def _combinaciones(lado: list[tuple[dict, float]], meta: float, margen: float,
                   objetivo: float, aproximado: bool) -> tuple[list[dict], bool]:
    """Subconjuntos de UN lado (un solo signo) que suman `meta` ± `margen`.

    DFS con poda en vez de `combinations()`. No es una optimización: es lo que
    hace posible buscar combinaciones LARGAS, que es donde estaban las
    explicaciones que el buscador viejo no encontraba. Enumerar de a 8 sobre 40
    movimientos son 76 millones de combinaciones; ordenando de mayor a menor y
    podando por la suma que QUEDA disponible (`suf`), el árbol se corta apenas
    el resto no alcanza o ya se pasó.

    Devuelve `(candidatos, se_agotó_el_presupuesto)`. Lo segundo es la diferencia
    entre «no hay» y «no terminé de mirar», y sube a la pantalla.
    """
    if not lado:
        return [], False
    # El lado tiene un solo signo (los separa `_buscar`), así que se trabaja en
    # ABSOLUTO y toda la poda queda en una sola dirección.
    signo = 1.0 if lado[0][1] > 0 else -1.0
    destino = round(meta * signo, 2)
    if destino <= 0:
        # Sumar egresos nunca va a dar un ingreso. Ni se empieza.
        return [], False

    vals = sorted(((m, abs(v)) for m, v in lado), key=lambda t: -t[1])
    n = len(vals)
    suf = [0.0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suf[i] = round(suf[i + 1] + vals[i][1], 2)

    salida: list[dict] = []
    nodos = 0

    def dfs(i: int, elegidos: list, suma: float) -> None:
        nonlocal nodos
        if len(salida) >= 10 or nodos >= TOPE_NODOS:
            return
        if len(elegidos) >= 2 and abs(round(suma - destino, 2)) <= margen:
            salida.append(_armar([(m, v * signo) for m, v in elegidos],
                                 suma * signo, objetivo, aproximado))
            # No se siguen explorando los superconjuntos: con todos los valores
            # del mismo signo, agregar uno más solo puede alejarse.
            return
        if i >= n or len(elegidos) >= MAX_COMBINAR:
            return
        if suma - destino > margen:                 # ya se pasó
            return
        if suma + suf[i] < destino - margen:        # ni sumando todo lo que queda
            return
        nodos += 1
        elegidos.append(vals[i])
        dfs(i + 1, elegidos, round(suma + vals[i][1], 2))
        elegidos.pop()
        dfs(i + 1, elegidos, suma)

    dfs(0, [], 0.0)
    return salida, nodos >= TOPE_NODOS


def _buscar(items: list[tuple[dict, float]], objetivo: float) -> tuple[list[dict], bool]:
    """Subconjuntos de UN lado cuya suma llega al objetivo.

    ⚠️ **De un solo lado, nunca cruzando.** Una explicación que mezcla un
    movimiento del banco con uno del mayor no es una explicación: es una
    coincidencia aritmética. Lo que se busca es concreto —«a este mayor le falta
    ESTE movimiento»— y eso vive entero de un lado.

    ⚠️ **Y de un solo SIGNO.** Una combinación no puede mezclar un egreso con un
    ingreso. No es una restricción de prolijidad: `−999.999,99 + 1.100.000,00 =
    100.000,01` no describe ningún error contable, describe dos movimientos que
    no tienen nada que ver y que por casualidad restan parecido a la diferencia.
    Los errores del mayor son «faltan estos movimientos» o «sobran estos», y en
    los dos casos van todos para el mismo lado. Está resuelto SEPARANDO los
    items por signo antes de combinar —o sea que las combinaciones mixtas ni
    siquiera se generan— y no filtrándolas después: una regla que decide qué es
    una explicación tiene que estar en la estructura, no en un `if` al final.

    Cuatro pasadas, de la más estricta a la más laxa, y se corta en la primera
    que encuentra algo. El orden ES el criterio: buscando con margen desde el
    principio, «esto es» y «esto se le parece» valdrían lo mismo.

      1. exacto
      2. el mismo importe con el signo al revés
      3. con el margen de redondeo (`tolerancia`)
      4. aproximado (`tolerancia_aproximada`) y **solo combinaciones**: ver el
         comentario de `APROX_PCT`.
    """
    obj = round(objetivo, 2)
    truncado = len(items) > MAX_MOVS_COMBINAR
    usables = items[:MAX_MOVS_COMBINAR]
    # Los dos universos que SÍ se pueden combinar. El cero no entra en ninguno:
    # no cambia ninguna suma y solo agrandaría el espacio de búsqueda.
    lados = ([m for m in usables if m[1] > 0], [m for m in usables if m[1] < 0])

    pasadas = ((obj, 0.0, False), (-obj, 0.0, False),
               (obj, tolerancia(obj), False),
               (obj, tolerancia_aproximada(obj), True))

    for meta, margen, aprox in pasadas:
        # De a UNO primero, y sobre TODOS los items (no hay nada que combinar,
        # así que el corte por signo no aplica). En la pasada aproximada no se
        # buscan sueltos a propósito: un movimiento que «casi» da es otro
        # movimiento.
        if not aprox:
            sueltos = [_armar([(m, v)], v, obj) for m, v in items
                       if abs(round(v - meta, 2)) <= margen]
            if sueltos:
                return sueltos[:10], False

        salida: list[dict] = []
        agotado = False
        for lado in lados:
            hallados, corte = _combinaciones(lado, meta, margen, obj, aprox)
            salida += hallados
            agotado = agotado or corte
        if salida:
            # Lo que menos deja sin explicar primero, y a igualdad de resto la
            # explicación más corta: dos movimientos son más creíbles que ocho.
            salida.sort(key=lambda c: (abs(c["resto"]), c["cantidad"]))
            return salida[:10], truncado or agotado
        truncado = truncado or agotado

    return [], truncado


# --------------------------------------------------------------------------- #
# CALZAR POR IMPORTE — sacar del medio lo que ya coincide de los dos lados
# --------------------------------------------------------------------------- #
def _calzar_por_importe(izq: list[float], der: list[float]) -> tuple[list, list]:
    """Empareja los movimientos de los dos lados que tienen el MISMO importe.

    ⚠️ **Por importe y nada más.** Las leyendas de los dos lados no se parecen y
    cambian todo el tiempo (`TRANSFERENCIA ENTRE CUENT` contra `[Op. 1136612]
    bco a bco`), así que cruzarlas por texto es imposible. Lo único que significa
    lo mismo de los dos lados es el número — y el signo, que está alineado:
    plata que entra al banco es Debe en el mayor.

    ⚠️ **Uno a uno.** Si el mismo importe aparece 3 veces del lado del banco y 2
    del lado del mayor, se calzan 2 pares y queda 1 suelto. Calzar «el grupo
    contra el grupo» taparía justo el movimiento que falta.

    No decide nada ni cambia ningún total: solo marca. Para qué sirve: con lo
    coincidente fuera de la vista, lo que queda es la lista corta de lo que hay
    que mirar de verdad.
    """
    pendientes: dict[float, list[int]] = {}
    for j, v in enumerate(der):
        pendientes.setdefault(round(v, 2), []).append(j)

    ci: list = [None] * len(izq)
    cd: list = [None] * len(der)
    pares = 0
    for i, v in enumerate(izq):
        cola = pendientes.get(round(v, 2))
        if not cola:
            continue
        pares += 1
        j = cola.pop(0)
        ci[i] = cd[j] = f"c{pares}"
    return ci, cd


def _gastos_de_movimientos(fecha: date, cuenta_id: int,
                           baldes: list[dict]) -> dict[str, str]:
    """{mov_hash: balde} — qué movimientos del día de ESA cuenta son gasto.

    Es el MISMO criterio que la columna GASTOS (`_gastos_bancarios`): las mismas
    reglas, los mismos overrides y el mismo desglose. Acá se necesita movimiento
    por movimiento y no el total, para poder marcarlos en la lista: un descalce
    en un impuesto es esperable —el banco lo cobra hoy y contabilidad lo carga
    después— y no tiene el mismo valor que un descalce en una transferencia.
    """
    movs = _movs_para_clasificar(fecha, cuenta_id)
    if not movs:
        return {}
    reglas, overrides = listar_reglas(), _overrides(fecha)
    if not [r for r in reglas if r.get("activa", True)] and not overrides:
        return {}
    marcas = clasificar(movs, reglas, overrides)
    return {m["mov_hash"]: desglosar(m, baldes) for m in movs
            if not m.get("ignorado") and marcas[m["mov_hash"]]["es_gasto"]}



# ─────────────────────────────────────────────────────────────
# TABLERO DE CONCILIACIÓN — el mayor que trae `jobs/mayor_sync`, sin archivo
# ─────────────────────────────────────────────────────────────
# Una fila por cuenta, como la vista principal. Reemplaza al .xlsx que había que
# exportar de HYGIRUS y subir cuenta por cuenta; el archivo sigue existiendo como
# camino alternativo (`conciliar()`), que además sirve para una cuenta que todavía
# no esté mapeada.

# ⚠️ **CUÁL DE LOS DOS SALDOS DE LA API DE SALDOS ES «EL SALDO DEL DÍA».**
#
# `bancos.saldos` guarda dos números que el banco manda por bloques distintos y
# que NO son lo mismo:
#
#   · `saldo_dia`       ← `historical_balances[].day_balance`. Una fila POR DÍA:
#                          cuánto quedó ese día. Es lo homogéneo con un cierre.
#   · `saldo_operativo` ← `balances.current_operating_balance`. La foto de HOY,
#                          estampada solo en la fila del `row_date` (`es_foto`).
#                          Es el saldo OPERATIVO —lo disponible ahora—, no el
#                          cierre contable de una fecha.
#
# **Manda `saldo_dia`** (decisión del back office, 2026-09-01). Hasta esa fecha el
# orden estaba al revés y el operativo le ganaba cuando existía, o sea justo en el
# día que la pantalla muestra. Consecuencia: el badge ≠ del CONSOLIDADO comparaba
# el cierre del extracto contra el saldo OPERATIVO y cantaba como contradicción
# del banco lo que era una diferencia de definición.
#
# El `coalesce` se conserva —al revés— y no es un detalle: una cuenta QUIETA no
# tiene fila en `historical_balances`, así que su único saldo es el operativo de
# la foto. Sin el fallback esa cuenta volvería a mostrar «—», que es exactamente
# el agujero que esta API vino a tapar.
#
# ⚠️ Se declara ACÁ y lo usan las TRES pantallas que lo necesitan (el cierre, el
# consolidado y DIFERENCIAS). Estaba copiado en las tres, y tres copias de una
# regla sin árbitro es la REGLA #9: el día que alguien corrija una, las otras dos
# siguen diciendo lo de antes y ninguna falla — muestran otro número.
# Requiere que la tabla `bancos.saldos` venga aliaseada como `s`.
_SALDO_INFORMADO = "coalesce(s.saldo_dia, s.saldo_operativo)"


def _saldos_banco(fecha: date, cuenta_id: int | None = None) -> dict[int, dict]:
    """{cuenta_id: {"valor", "fuente", "ajuste"}} — **EL SALDO AL CIERRE**.

    ⚠️⚠️ **ESTE es el número que muestra la pantalla principal en la columna
    SALDO AL CIERRE, y es el MISMO que se sella y que al día siguiente se lee
    como SALDO INICIO.** Un solo dato, una sola fórmula, un solo lugar:

        saldo al cierre(F)  =  saldo del banco(F)  +  movimientos manuales de F

    ⚠️ **EXCEPCIÓN: las cuentas con `bancos.cuentas.origen = 'manual'`.** Esas no
    las informa Interbanking —su saldo del banco sería siempre 0—, así que ahí el
    ajuste es **ACUMULADO**: el saldo ES la suma de todo lo cargado a mano hasta
    esa fecha. Un +1000 hoy deja el saldo en 1000 hoy y todos los días
    siguientes; si después entra un −900, pasa a 100 y sigue así.

    En las demás cuentas el ajuste es **DEL DÍA**, porque el saldo que informa el
    banco ya trae adentro los movimientos de días anteriores y sumarlos otra vez
    los contaría dos veces.

    El saldo del banco es el del extracto si lo hay, si no el que informa
    `bancos.saldos`, y si la cuenta no la informa Interbanking es 0 (su saldo son
    sus manuales).

    **`consolidado()` llama a esta misma función para dibujar la columna**, así
    que lo que se ve en pantalla y lo que se sella no pueden ser distintos. Eso
    fue un bug real: el consolidado calculaba una fórmula y el sellado otra, y el
    SALDO INICIO del día siguiente salía de la segunda.

    `fuente` usa las etiquetas que espera el frontend: `extracto` | `saldo` |
    `manual`.

    `cuenta_id` acota a UNA cuenta —y entonces NO filtra por `activa`, porque el
    drill-down puede pedir una cuenta dada de baja que el tablero ya no lista.
    """
    signo = "CASE WHEN tipo = 'C' THEN abs(importe) ELSE -abs(importe) END"
    sql = f"""
        SELECT c.id AS cuenta_id, c.origen, e.saldo_cierre,
               {_SALDO_INFORMADO} AS informado,
               coalesce(m.ajuste, 0) AS ajuste,
               coalesce(m.acumulado, 0) AS acumulado
          FROM bancos.cuentas c
          LEFT JOIN bancos.extracto_dia e ON e.cuenta_id = c.id AND e.fecha = %s
          LEFT JOIN bancos.saldos       s ON s.cuenta_id = c.id AND s.fecha = %s
          -- El del DÍA (cuentas de Interbanking) y el ACUMULADO (cuentas
          -- `origen='manual'`, que no tienen saldo del banco). Ver el bucle.
          LEFT JOIN (SELECT cuenta_id,
                            sum(CASE WHEN fecha = %s THEN {signo} ELSE 0 END)
                              AS ajuste,
                            sum({signo}) AS acumulado
                       FROM bancos.movimientos_manuales
                      WHERE fecha <= %s
                      GROUP BY cuenta_id) m ON m.cuenta_id = c.id
         WHERE """
    args = (fecha, fecha, fecha, fecha)
    if cuenta_id is None:
        filas = _q(sql + "c.activa", args)
    else:
        filas = _q(sql + "c.id = %s", (*args, cuenta_id))

    out: dict[int, dict] = {}
    for r in filas:
        if (r["origen"] or "interbanking") == "manual":
            # ⚠️ **LA CUENTA QUE NO VIENE DE INTERBANKING** (`origen='manual'`).
            # Acá el ajuste es ACUMULADO y no del día, porque **no hay ningún
            # saldo del banco que lo absorba**: Interbanking no la informa, su
            # saldo siempre sería 0. Registro +1000 hoy → el saldo es 1000 hoy,
            # mañana y siempre; si después entra un −900, pasa a 100 y sigue
            # así. El saldo ES el acumulado de lo cargado a mano.
            acumulado = _f(r["acumulado"]) or 0.0
            if not acumulado and not _f(r["ajuste"]):
                continue
            out[r["cuenta_id"]] = {"valor": round(acumulado, 2),
                                   "fuente": "manual", "ajuste": acumulado}
            continue

        # El resto: el saldo lo informa Interbanking y ya trae adentro los
        # movimientos de días anteriores, así que solo se suma el manual DEL DÍA.
        ajuste = _f(r["ajuste"]) or 0.0
        if r["saldo_cierre"] is not None:
            base, fuente = _f(r["saldo_cierre"]), "extracto"
        elif r["informado"] is not None:
            base, fuente = _f(r["informado"]), "saldo"
        elif ajuste:
            base, fuente = 0.0, "manual"
        else:
            # Sin extracto, sin saldo y sin manuales no sabemos nada. «No
            # sabemos» no es «cero».
            continue
        out[r["cuenta_id"]] = {"valor": round((base or 0.0) + ajuste, 2),
                               "fuente": fuente, "ajuste": ajuste}
    return out


def sellar_cierre(fecha: date) -> dict[int, dict]:
    """Guarda el saldo al cierre de TODAS las cuentas para esa fecha. Idempotente.

    ⚠️ **Pedido del back office (2026-08-27)**: *«el saldo al cierre tiene que
    quedar como un valor con fecha y banco y usarse al otro día, no hay que hacer
    cálculos raros»*. Esto es ese sellado.

    Se llama cada vez que el cierre de un día puede haber cambiado: cuando la
    ingesta de Interbanking trae datos y cuando alguien carga o borra un
    movimiento manual. Volver a sellar el mismo día pisa el valor anterior, así
    que correrlo de más no rompe nada.
    """
    saldos = _saldos_banco(fecha)
    filas = [(cid, fecha, v["valor"], v["fuente"], v["ajuste"])
             for cid, v in saldos.items()]
    if not filas:
        return {}
    valores = ",".join(["(%s,%s,%s,%s,%s)"] * len(filas))
    _exec(
        f"""INSERT INTO bancos.cierres_diarios
              (cuenta_id, fecha, saldo, fuente, ajuste_manual)
            VALUES {valores}
            ON CONFLICT (cuenta_id, fecha) DO UPDATE SET
              saldo = EXCLUDED.saldo, fuente = EXCLUDED.fuente,
              ajuste_manual = EXCLUDED.ajuste_manual, sellado_at = now()""",
        tuple(x for f in filas for x in f))
    # Devuelve lo que acaba de sellar, con la MISMA forma que `_cierre_sellado`:
    # así el que lo llama para tapar un hueco no tiene que volver a leerlo.
    return saldos


def _cierre_sellado(fecha: date) -> dict[int, dict]:
    """{cuenta_id: {"valor", "fuente", "ajuste"}} — **LEÍDO**, no calculado.

    Esta es la apertura de un día: el cierre sellado del día hábil anterior. No
    recalcula nada — si el valor está guardado, es ese y punto.

    ⚠️ **Si el día no está sellado, lo sella al vuelo.** Hace falta para que la
    pantalla no muestre «—» el primer día después del deploy, ni cuando un día
    viejo nunca se selló. Es un `INSERT ... ON CONFLICT`, así que dos usuarios
    mirando la misma fecha a la vez no se pisan.
    """
    filas = _q(
        """SELECT cuenta_id, saldo, fuente, ajuste_manual
             FROM bancos.cierres_diarios WHERE fecha = %s""", (fecha,))
    if not filas:
        return sellar_cierre(fecha)
    return {r["cuenta_id"]: {"valor": _f(r["saldo"]),
                             "fuente": r["fuente"],
                             "ajuste": _f(r["ajuste_manual"]) or 0.0}
            for r in filas}


def _mayor_del_dia(fecha: date) -> dict[int, dict]:
    """{cuenta_id: {"debe", "haber", "movimientos"}} de `bancos.mayor_movimientos`.

    `haber` va NEGATIVO (el `importe` guardado ya viene firmado, = Debe − Haber),
    así el saldo final es una suma y no hay que acordarse de restar en el lugar
    correcto — que es donde se cuelan los errores de signo.
    """
    return {r["cuenta_id"]: {"debe": _f(r["debe"]) or 0.0,
                             "haber": _f(r["haber"]) or 0.0,
                             "movimientos": r["movimientos"]}
            for r in _q(
                """SELECT cuenta_id,
                          sum(CASE WHEN importe > 0 THEN importe ELSE 0 END) AS debe,
                          sum(CASE WHEN importe < 0 THEN importe ELSE 0 END) AS haber,
                          count(*) AS movimientos
                     FROM bancos.mayor_movimientos
                    WHERE fecha_conciliacion = %s
                    GROUP BY cuenta_id""", (fecha,))}


def tablero(email: str, fecha: date) -> dict:
    """El tablero de conciliación del día: una fila por cuenta.

    Columnas, y de dónde sale cada una:

      saldo_inicio  nuestro cierre del día hábil ANTERIOR — el del banco **más
                    los movimientos manuales de ese día**, o sea exactamente el
                    mismo número que esa fecha mostró como `cierre_banco`. El
                    mayor no informa saldos —`registrosContables` devuelve
                    movimientos— así que la apertura sale de nuestro lado.
                    Consecuencia asumida: **cada día se juzga aislado y un
                    descuadre arrastrado no se ve**. Es deliberado; contesta
                    «¿qué pasó ayer?», no «¿está bien el saldo absoluto?».
      debe/haber    movimientos del MAYOR de ese día.
      saldo_final   saldo_inicio + debe + haber (el haber ya es negativo).
      diferencia    **cierre del BANCO del día − saldo final del mayor.** Los dos
                    arrastres se cancelan, así que equivale a «movimientos del
                    banco − movimientos del mayor»: qué le falta cargar al mayor.

                    ⚠️ NO es `saldo_final − saldo_inicio`: eso se simplifica a
                    `debe + haber` y nunca miraría al banco, así que jamás podría
                    mostrar un descuadre.

                    ⚠️ El ORDEN de la resta es el de `conciliar()` —banco menos
                    mayor— y no al revés: la misma cuenta se ve acá y en el
                    drill-down, y con el signo dado vuelta el mismo descuadre
                    aparecería como `+50.000` en la grilla y `−50.000` al hacer
                    click. Además es el signo con el que ya están guardados los
                    pendientes y el que decide `falta_en_el_mayor` /
                    `sobra_en_el_mayor`.
      gastos        de la vista principal (`_gastos_bancarios`), INFORMATIVO: no
                    entra en ningún total. Se calcula de los movimientos del
                    BANCO, así que existe esté o no cargado en el mayor.
      dif_sin_gastos  lo que queda de la diferencia DESPUÉS de los gastos, para
                    ver si además de ellos hay otra cosa. **`None` cuando no hay
                    diferencia**: una vez que el equipo carga el gasto en el mayor
                    la diferencia se va a cero, y seguir aplicándole los gastos
                    publicaría un número que no existe. Si no hay diferencia, no
                    hay nada que explicar.

    Sin saldo del banco (feriado, extracto que no llegó) la fila va con
    `diferencia: null` y el motivo: **no se cae a cero**, porque «no sé» y «cero»
    son cosas distintas y confundirlas acá inventa un descuadre.

    ⚠️ Una cuenta de `origen='manual'` cae siempre en ese caso, y el motivo lo
    dice con todas las letras en vez de culpar a un extracto que nunca va a
    llegar: Interbanking no la informa y lo que se carga a mano es el MOVIMIENTO
    del día, no un saldo acumulado, así que no hay apertura que publicar. Ver
    `_saldos_banco`.
    """
    dia_previo = restar_habiles(fecha, 1)
    cuentas = _q(
        """SELECT id, bank_number, bank_name, account_number, account_type,
                  currency, account_label, activa, origen, codigo_contable
             FROM bancos.cuentas WHERE activa
            ORDER BY bank_name, currency, account_number""")

    # ⚠️ La APERTURA se LEE del cierre sellado de ayer; el CIERRE de hoy se
    # calcula (hoy todavía se está moviendo). Esa asimetría es a propósito: el
    # saldo inicial dejó de ser un cálculo y pasó a ser un dato.
    previos, hoy = _cierre_sellado(dia_previo), _saldos_banco(fecha)
    mayor = _mayor_del_dia(fecha)
    gastos = _gastos_bancarios(fecha, _baldes())

    filas = []
    for c in cuentas:
        cid = c["id"]
        pub = _cuenta_publica(c)
        ini = previos.get(cid)
        cierre = hoy.get(cid)
        mov = mayor.get(cid)
        g = (gastos.get(cid) or {}).get("total")

        saldo_inicio = ini["valor"] if ini else None
        debe = (mov or {}).get("debe", 0.0)
        haber = (mov or {}).get("haber", 0.0)

        # El ajuste manual ya viene DENTRO del valor, de los dos lados: es plata
        # que el banco no informó y alguien cargó a mano, y va del lado del BANCO
        # (sumarlo del lado del mayor lo contaría al revés). Se lee acá solo para
        # publicarlo aparte, que es lo que le deja decir a la pantalla cuánto de
        # ese saldo lo puso una persona.
        ajuste = (cierre or {}).get("ajuste") or 0.0
        cierre_banco = None if cierre is None else cierre["valor"]

        saldo_final = None if saldo_inicio is None else round(saldo_inicio + debe + haber, 2)
        diferencia = motivo = None
        manual = (c.get("origen") or "interbanking") == "manual"
        if saldo_final is None:
            motivo = ("Interbanking no informa esta cuenta: no hay saldo de apertura"
                      if manual else
                      f"no hay saldo del banco al {dia_previo.isoformat()} (la apertura)")
        elif cierre_banco is None:
            motivo = f"no hay saldo del banco al {fecha.isoformat()}"
        else:
            diferencia = round(cierre_banco - saldo_final, 2)

        concilia = None if diferencia is None else abs(diferencia) < SIN_DIFERENCIA
        # Ver el docstring: sin diferencia no hay nada que explicar, y aplicarle
        # los gastos igual publicaría un número que no existe. El signo: los
        # gastos son lo que se llevó el banco (positivo), y la diferencia va
        # banco−mayor, así que un gasto no registrado en el mayor la deja
        # NEGATIVA — sumarlos es lo que la lleva a cero cuando eso es todo.
        dif_sin_gastos = (None if diferencia is None or concilia or g is None
                          else round(diferencia + g, 2))

        filas.append({
            **pub,
            "tiene_mayor": bool(c["codigo_contable"]),
            "codigo_contable": c["codigo_contable"],
            "saldo_inicio": saldo_inicio,
            "saldo_inicio_fuente": ini["fuente"] if ini else None,
            "gastos": g,
            "debe": round(debe, 2),
            "haber": round(haber, 2),
            "movimientos_mayor": (mov or {}).get("movimientos", 0),
            "saldo_final": saldo_final,
            "cierre_banco": cierre_banco,
            "ajuste_manual": round(ajuste, 2) if ajuste else 0.0,
            "diferencia": diferencia,
            "concilia": concilia,
            "dif_sin_gastos": dif_sin_gastos,
            "motivo": motivo,
        })

    # Cuándo se trajo el mayor. NO es un detalle: entre dos corridas del mismo día
    # una cuenta se movió 2.008 millones (medido el 2026-08-20), así que una
    # diferencia enorme puede ser simplemente que Contabilidad todavía no terminó
    # de cargar. Sin esta fecha en pantalla, alguien sale a buscar un descuadre
    # que no existe.
    log = _q("""SELECT corrida_at, movimientos_banco, ok
                  FROM bancos.mayor_sync_log
                 WHERE fecha_conciliacion = %s
                 ORDER BY id DESC LIMIT 1""", (fecha,))
    return {
        "fecha": fecha.isoformat(),
        "fecha_apertura": dia_previo.isoformat(),
        "filas": filas,
        "mayor_sync": (log or [None])[0],
        "sin_mayor": sum(1 for f in filas if not f["tiene_mayor"]),
    }


def _mayor_de_base(cuenta_id: int, fecha: date) -> tuple[dict, dict]:
    """El mayor de UNA cuenta, con la MISMA forma que sale del .xlsx.

    Devuelve `(saldo, detalle)` idénticos a lo que producen `_saldo_del_mayor` y
    `_movimientos_del_mayor` sobre el archivo, así `conciliar()` no distingue de
    dónde vino y toda la comparación —candidatos, gastos, avisos— se reusa tal
    cual, con los tests que ya tiene.

    ⚠️ **Una diferencia real contra el archivo, y hay que saberla**: el Excel de
    HYGIRUS trae el saldo de cierre ESCRITO por el sistema contable, que es
    evidencia independiente. Acá el endpoint solo devuelve MOVIMIENTOS, así que
    el cierre se DERIVA: apertura del banco + movimientos del mayor. Consecuencia:
    por este camino un descuadre ARRASTRADO de días anteriores no se puede
    detectar, porque la apertura se toma por buena. Cada día se juzga aislado.
    Para el saldo absoluto sigue estando la pestaña POR ARCHIVO.

    `fila` lleva el `movimiento_id` de Aunesa y no un número de renglón: es lo que
    termina en `mov_ref` (`mayor:<id>`) al confirmar un pendiente, y un id estable
    identifica el movimiento aunque el día se vuelva a traer y cambie de orden.
    """
    movs = _q(
        """SELECT movimiento_id, concepto, importe
             FROM bancos.mayor_movimientos
            WHERE cuenta_id = %s AND fecha_conciliacion = %s
            ORDER BY asiento_numero, movimiento_id""", (cuenta_id, fecha))
    movimientos = [{"concepto": (m["concepto"] or "").strip() or "(sin concepto)",
                    "grupo": _grupo_mayor(m["concepto"] or ""),
                    "importe": round(_f(m["importe"]) or 0.0, 2),
                    "fila": m["movimiento_id"]} for m in movs]
    detalle = {"movimientos": movimientos,
               "suma": round(sum(m["importe"] for m in movimientos), 2),
               "avisos": []}

    # La apertura sale de la MISMA función que la del tablero —manuales de ese
    # día incluidos—, así que el cierre que se calcula acá no puede contradecir
    # al de la grilla. `cuenta_id` acota la query a esta cuenta.
    previo = restar_habiles(fecha, 1)
    apertura = _cierre_sellado(previo).get(cuenta_id)
    if apertura is None:
        # Sin apertura no hay cierre que calcular. Se devuelve `None` y NO cero:
        # un cero acá se compararía contra el saldo real y fabricaría un
        # descuadre del tamaño de la cuenta entera.
        return ({"valor": None, "texto": None, "fila": None, "columna": None,
                 "avisos": [f"No hay saldo del banco al {previo.isoformat()}, que es "
                            "la apertura del mayor: sin eso no se puede calcular su "
                            "cierre. Se puede conciliar por archivo."]}, detalle)
    return ({"valor": round(apertura["valor"] + detalle["suma"], 2),
             "texto": None, "fila": None, "columna": None, "avisos": []}, detalle)


def conciliar(email: str, cuenta_id: int, fecha: date,
              filas: list | None = None) -> dict:
    """Nuestro saldo al cierre contra el último saldo del mayor.

    Qué se compara y por qué así:

    · **Nuestro saldo es el MISMO que muestra el consolidado**, ajuste manual
      incluido. Si acá se usara el saldo pelado del banco, dos pantallas dirían
      dos números para la misma cuenta y el mismo día — y el que concilia no
      tendría forma de saber cuál creer. El ajuste viaja aparte para que se vea
      cuánto de ese saldo lo puso una persona.
    · **La diferencia se busca entre los MOVIMIENTOS del día**, porque el caso
      típico es al revés de lo que parece: no es que el banco tenga de más, es
      que al mayor le falta registrar algo que el banco sí informó.
    """
    cuentas = _q(
        """SELECT id, bank_number, bank_name, account_number, account_type,
                  currency, account_label, activa, origen
             FROM bancos.cuentas WHERE id = %s""", (cuenta_id,))
    if not cuentas:
        raise ValueError("Esa cuenta no existe.")
    cuenta = _cuenta_publica(cuentas[0])

    # ⚠️ El saldo sale de `_saldos_banco`, la MISMA función que usan el tablero y
    # `_mayor_de_base`. Acá había una segunda copia de la precedencia
    # (extracto → saldo informado → ajuste manual): mientras las dos dijeran lo
    # mismo no pasaba nada, y por eso el día que `_saldos_banco` se olvidó de los
    # manuales nadie lo vio. Una sola implementación, un solo lugar donde
    # equivocarse.
    saldo = _saldos_banco(fecha, cuenta_id).get(cuenta_id)
    nuestro = saldo["valor"] if saldo else None
    fuente = saldo["fuente"] if saldo else None
    ajuste = (saldo or {}).get("ajuste") or 0.0

    # Sin `filas` el mayor sale de la base (lo trae `jobs/mayor_sync`); con
    # `filas`, del .xlsx que subieron. De acá para abajo NO se distingue: la
    # comparación es una sola y por eso vale para los dos caminos.
    if filas is None:
        mayor, detalle = _mayor_de_base(cuenta_id, fecha)
    else:
        mayor = _saldo_del_mayor(filas)
        detalle = _movimientos_del_mayor(filas)
    avisos = [*mayor["avisos"], *detalle["avisos"]]
    excel = mayor["valor"]

    # ⚠️ La DIFERENCIA es siempre **nuestro saldo menos el del mayor**, y su
    # SIGNO dice qué hacer:
    #   · positiva → el banco tiene más: **falta un movimiento en el mayor**;
    #   · negativa → el mayor tiene más: **sobra un movimiento en el mayor**.
    # Por eso se busca de los DOS lados y cada explicación dice de cuál salió: no
    # es lo mismo «cargá esto en HYGIRUS» que «sacá esto de HYGIRUS».
    diferencia = None if nuestro is None or excel is None else round(nuestro - excel, 2)
    # Debajo de un peso NOMINAL no hay diferencia: es redondeo del sistema
    # contable, y ningún movimiento puede explicar 15 centavos.
    concilia = None if diferencia is None else abs(diferencia) < SIN_DIFERENCIA

    movs = _q(
        """SELECT mov_hash, fecha, fecha_proceso, importe, tipo, descripcion_banco,
                  descripcion_ib, codigo_operacion_ib, comprobante
             FROM bancos.movimientos
            WHERE cuenta_id = %s AND fecha = %s
            ORDER BY numero_extracto, correlativo""", (cuenta_id, fecha))

    # Los gastos bancarios del día, con la MISMA función que la columna del
    # consolidado y el modal de MOVIMIENTOS. Están acá porque son el caso típico
    # de «falta en el mayor»: el banco cobra la comisión y el IVA el mismo día y
    # el sistema contable los registra después, o no los registra.
    #
    # ⚠️ El signo es POSITIVO = lo que se llevó el banco, al revés que los
    # movimientos de la lista (donde un débito va en negativo). Se deja así a
    # propósito: el número tiene que poder compararse de un vistazo con el de las
    # otras pantallas, y ahí la pregunta es «cuánto cobró», no «cuánto se movió
    # el saldo». Sin una sola regla cargada viene vacío y la vista dice «—»: «no
    # sabemos» y «no hubo gastos» son cosas distintas.
    baldes = _baldes()
    gastos = _gastos_bancarios(fecha, baldes).get(cuenta_id) or {}
    # El mismo criterio, pero movimiento por movimiento: la lista los marca.
    gasto_de = _gastos_de_movimientos(fecha, cuenta_id, baldes)

    # ── CALCE POR IMPORTE ────────────────────────────────────────────────────
    # Qué movimiento de cada lado tiene su igual del otro. Ver `_calzar_por_importe`.
    imp_banco = [round(_firmado(m), 2) for m in movs]
    imp_mayor = [m["importe"] for m in detalle["movimientos"]]
    calce_banco, calce_mayor = _calzar_por_importe(imp_banco, imp_mayor)

    candidatos: list[dict] = []
    truncados = False
    sin_calzar_banco = [(m, v) for m, v, c in zip(movs, imp_banco, calce_banco)
                        if c is None]
    sin_calzar_mayor = [(m, m["importe"])
                        for m, c in zip(detalle["movimientos"], calce_mayor)
                        if c is None]
    if diferencia is not None and not concilia:
        # ⚠️ **Se busca SOLO entre los que NO calzaron.** Un movimiento que tiene
        # su igual del otro lado ya está registrado en los dos sistemas: no puede
        # ser el que falta. Sacarlos no es una optimización cosmética — es lo que
        # deja el universo chico y hace que aparezcan las combinaciones largas,
        # que antes quedaban tapadas por movimientos que no tenían nada que
        # explicar.
        del_banco, t1 = _buscar(sin_calzar_banco, diferencia)
        # Del lado del MAYOR: un movimiento cargado de más hace que el mayor se
        # aleje en sentido contrario, así que se busca por el OPUESTO.
        del_mayor, t2 = _buscar(sin_calzar_mayor, -diferencia)
        truncados = t1 or t2

        # ── EXPLICACIONES POR CONSTRUCCIÓN ───────────────────────────────────
        # ⚠️ **No son búsquedas, y por eso SÍ pueden mezclar signos.** Parece
        # contradecir la regla de `_buscar` y no la contradice: allá el problema
        # es ELEGIR un subconjunto que casualmente sume parecido —eso es una
        # coincidencia aritmética, no una explicación—. Acá no se elige nada: es
        # el conjunto ENTERO de un lado. Que su suma dé la diferencia no es un
        # hallazgo, es una IDENTIDAD — si al mayor no le quedó nada sin calzar,
        # todo lo que le falta es exactamente todo lo que al banco le sobró.
        #
        # Esto es lo que el buscador NO podía encontrar nunca: el caso real del
        # 19/08 tenía 4 créditos y 7 débitos sin calzar (los 7 eran los gastos
        # bancarios), o sea signos mezclados y 11 movimientos contra un tope de
        # 8. La pantalla decía «ningún movimiento llega a esa diferencia» con la
        # respuesta entera a la vista.
        extras: list[tuple[dict, str]] = []
        for lado, items, meta in (("banco", sin_calzar_banco, diferencia),
                                  ("mayor", sin_calzar_mayor, -diferencia)):
            suma = round(sum(v for _, v in items), 2)
            if len(items) < 2 or abs(round(suma - meta, 2)) > tolerancia(meta):
                continue
            c = _armar(items, suma, meta)
            gastos_n = sum(1 for m, _ in items if gasto_de.get(m.get("mov_hash")))
            c["motivo"] = (
                f"todo lo que no calzó del {lado}: {len(items)} movimientos"
                + (f", {gastos_n} de ellos gastos bancarios" if gastos_n else ""))
            extras.append((c, lado))

        # Los GASTOS del día, TODOS JUNTOS. Es el caso más común de «falta en el
        # mayor»: el banco cobra comisión, IVA y ley 25.413 el mismo día y el
        # sistema contable los registra al mes, o no los registra. Solo si nada
        # más cerró exacto — si ya hay una explicación al centavo, agregar
        # «además, mirá los gastos» es ruido.
        if not extras and not any(c["resto"] == 0 for c in (*del_banco, *del_mayor)):
            solo_gastos = [(m, v) for m, v in sin_calzar_banco
                           if gasto_de.get(m["mov_hash"])]
            suma_g = round(sum(v for _, v in solo_gastos), 2)
            if len(solo_gastos) >= 2 and abs(round(suma_g - diferencia, 2)) \
                    <= tolerancia_aproximada(diferencia):
                cand = _armar(solo_gastos, suma_g, diferencia,
                              aproximado=suma_g != round(diferencia, 2))
                cand["motivo"] = "todos los gastos bancarios del día sin calzar"
                del_banco = [cand, *del_banco]

        def _pub(c, lado):
            movimientos = [
                {**_movimiento_publico(m), "importe_firmado": _firmado(m),
                 "concepto": (m.get("descripcion_ib") or "").strip(),
                 # Qué impuesto es, si lo es: adentro de una explicación de 11
                 # movimientos, saber cuáles son gastos es lo que la vuelve
                 # accionable («estos entran solos, estos hay que cargarlos»).
                 "balde": gasto_de.get(m["mov_hash"])}
                if lado == "banco" else
                {"mov_hash": f"mayor:{m['fila']}", "descripcion": m["concepto"],
                 "concepto": "", "hora": None, "codigo": None, "comprobante": None,
                 "importe": abs(m["importe"]), "tipo": "C" if m["importe"] >= 0 else "D",
                 "importe_firmado": m["importe"], "balde": None}
                for m in c["movimientos"]]
            return {"lado": lado,
                    # Qué hay que HACER con esto, dicho en una acción y no en un
                    # diagnóstico: el que lo lee tiene que saber qué toca sin
                    # traducir nada.
                    "accion": "falta_en_el_mayor" if lado == "banco" else "sobra_en_el_mayor",
                    "suma": c["suma"], "cantidad": c["cantidad"], "resto": c["resto"],
                    "signo_invertido": c["signo_invertido"],
                    # `aproximado` = no da exacto ni dentro del margen de
                    # redondeo, pero le pega cerca. Viaja marcado porque una
                    # explicación aproximada NO se puede confundir con una que
                    # cierra; el `resto` dice cuánto falta.
                    "aproximado": c.get("aproximado", False),
                    "motivo": c.get("motivo"),
                    "movimientos": movimientos}

        # El signo decide cuál se muestra PRIMERO: es la explicación más probable
        # según de qué lado sobra la plata. Las dos se muestran igual, porque
        # matemáticamente las dos pueden ser ciertas y el que decide es el que
        # conoce el circuito.
        #
        # Antes que todas van las POR CONSTRUCCIÓN: no compiten con las buscadas,
        # están en otra categoría. Una dice «estos son TODOS los que faltan», las
        # otras dicen «con estos también daría».
        primero = del_banco if diferencia > 0 else del_mayor
        segundo = del_mayor if diferencia > 0 else del_banco
        lado1 = "banco" if diferencia > 0 else "mayor"
        lado2 = "mayor" if diferencia > 0 else "banco"
        candidatos = ([_pub(c, l) for c, l in extras]
                      + [_pub(c, lado1) for c in primero]
                      + [_pub(c, lado2) for c in segundo])[:10]

        # ⚠️ La pista que ahorra una hora: si los dos saldos coinciden al dar
        # vuelta el signo del mayor, no falta ni sobra ningún movimiento — es una
        # convención contable (la cuenta quedó del otro lado). NO se corrige
        # solo: invertir un signo por nuestra cuenta es exactamente cómo se
        # fabrica una conciliación que miente.
        if excel and abs(round(nuestro + excel, 2)) < abs(diferencia) / 100:
            avisos.append(
                "Los dos saldos coinciden si se invierte el signo del mayor: no "
                "falta ni sobra ningún movimiento, la cuenta está con saldo del "
                "otro lado (acreedor/deudor). Revisá de qué lado la lleva el "
                "sistema contable.")

        if not candidatos:
            # ⚠️ Corto y accionable. El texto anterior explicaba el algoritmo
            # («ni combinación de hasta 8 del MISMO lado…»): al que concilia no
            # le sirve saber cómo buscamos, le sirve saber qué hacer ahora.
            avisos.append("No encontré movimientos que expliquen la diferencia: "
                          "hay que revisarlo a mano.")
        # ⚠️ El signo invertido y el resto NO llevan aviso de texto: cada
        # candidato ya los publica en su propio campo (`signo_invertido`,
        # `resto`) y la pantalla los muestra como marca al lado del movimiento
        # EXACTO al que le pasan. Repetirlo arriba en un párrafo agregaba un
        # cartel genérico sobre TODAS las opciones para algo que le pasa a UNA,
        # que es justo lo que hace dudar de si el problema es este o el otro.

    if ajuste:
        avisos.append(
            f"Nuestro saldo incluye {ajuste:,.2f} de movimientos cargados a mano, "
            "que el banco no informa.")

    _auditar(email, cuenta_id, fecha, fecha, len(movs))
    # Los dos detalles, uno de cada lado, para poder mirarlos juntos. Sin fechas
    # ni comprobantes: los del banco y los del mayor no tienen NADA en común en
    # esos campos (`[Op. 1130699] bco a bco` contra `TRANSF.O/BANCOS MISMO TIT`),
    # así que ponerlos al lado invitaría a cruzarlos por donde no se puede. Lo
    # único que se compara de verdad es el IMPORTE.
    # ⚠️ El `grupo` es la clave del CONSOLIDADO y viaja calculada desde acá, no
    # desde el navegador: qué se considera «el mismo movimiento» es un criterio
    # de negocio, y si vive en el front no hay una sola prueba que lo respalde.
    # Del lado del banco la descripción YA agrupa (viene truncada a ~25 chars y
    # se repite literal); el `N/D -` / `N/C -` se deja a propósito, es lo que
    # separa débitos de créditos y sacarlo los netearía dentro de un mismo grupo.
    nuestros = [{"descripcion": (d := (m.get("descripcion_banco")
                                       or m.get("descripcion_ib") or "").strip()
                                 or "(sin descripción)"),
                 "grupo": d,
                 "concepto": (m.get("descripcion_ib") or "").strip(),
                 # `calce` = con qué movimiento del mayor coincide el importe;
                 # `balde` = qué impuesto es, si es un gasto. Los dos existen
                 # para el MISMO fin: poder sacar de la vista lo que ya está
                 # explicado y lo que descalza por una razón conocida.
                 "calce": calce_banco[i],
                 "balde": gasto_de.get(m["mov_hash"]),
                 "importe": round(_firmado(m), 2)} for i, m in enumerate(movs)]
    for i, m in enumerate(detalle["movimientos"]):
        m["calce"] = calce_mayor[i]
    return {
        "fecha": fecha.isoformat(),
        "banco_movimientos": nuestros,
        "banco_suma": round(sum(m["importe"] for m in nuestros), 2),
        "mayor_movimientos": detalle["movimientos"],
        "mayor_suma": detalle["suma"],
        "cuenta": {"id": cuenta["id"], "banco": cuenta["banco_nombre"],
                   "numero": cuenta["numero"], "tipo": cuenta["tipo"],
                   "moneda": cuenta["moneda"], "etiqueta": cuenta["etiqueta"]},
        "saldo_nuestro": nuestro,
        "saldo_nuestro_fuente": fuente,
        "ajuste_manual": round(ajuste, 2) or None,
        "saldo_excel": excel,
        # De dónde salió ese saldo. NO son equivalentes y la pantalla tiene que
        # poder decirlo: del archivo es el cierre que ESCRIBIÓ el sistema contable
        # (evidencia independiente); de la base está DERIVADO como apertura del
        # banco + movimientos, así que no puede delatar un arrastre viejo.
        "mayor_origen": "archivo" if filas is not None else "base",
        "saldo_excel_texto": mayor["texto"],
        "saldo_excel_letra": (mayor["texto"] or "")[-1:].upper()
                             if (mayor["texto"] or "")[-1:].upper() in ("D", "A") else None,
        "saldo_excel_fila": mayor["fila"],
        "diferencia": diferencia,
        "concilia": concilia,
        "movimientos_dia": len(movs),
        "gastos": gastos.get("total"),
        "gastos_desglose": gastos or None,
        "candidatos": candidatos,
        "candidatos_truncados": truncados,
        # El resumen del CALCE POR IMPORTE. Los dos números que importan son los
        # `_sin_calzar`: su RESTA es la misma diferencia de arriba (los pares se
        # cancelan entre sí), así que la vista filtrada muestra exactamente los
        # movimientos que la producen y ninguno más.
        "calce": {
            "pares": sum(1 for c in calce_banco if c is not None),
            "banco_sin_calzar": sum(1 for c in calce_banco if c is None),
            "mayor_sin_calzar": sum(1 for c in calce_mayor if c is None),
            "banco_suma_sin_calzar": round(
                sum(v for v, c in zip(imp_banco, calce_banco) if c is None), 2),
            "mayor_suma_sin_calzar": round(
                sum(v for v, c in zip(imp_mayor, calce_mayor) if c is None), 2),
            # Cuánto de lo que quedó sin calzar son GASTOS. Es la partición que
            # importa para trabajar: los gastos suelen entrar solos y el resto
            # hay que cargarlo a mano, así que son dos tareas distintas dentro
            # de la misma diferencia.
            "banco_gastos_sin_calzar": round(
                sum(v for m, v in sin_calzar_banco if gasto_de.get(m["mov_hash"])), 2),
            "banco_gastos_movimientos": sum(
                1 for m, _ in sin_calzar_banco if gasto_de.get(m["mov_hash"])),
        },
        # El margen con que se buscó. Un criterio que decide qué se muestra tiene
        # que poder leerse en la pantalla, no vivir escondido en el código.
        "tolerancia": tolerancia(diferencia) if diferencia is not None else None,
        # Y el del último recurso, que es el que más fácil puede hacer pasar una
        # coincidencia por un hallazgo: con más razón tiene que estar a la vista.
        "tolerancia_aproximada": (tolerancia_aproximada(diferencia)
                                  if diferencia is not None else None),
        "avisos": avisos,
    }


# --------------------------------------------------------------------------- #
# MOVIMIENTOS A CONCILIAR — lo confirmado, que hay que arreglar en el otro sistema
# --------------------------------------------------------------------------- #
ACCIONES = ("falta_en_el_mayor", "sobra_en_el_mayor")


def confirmar_pendiente(email: str, cuenta_id: int, fecha: date, accion: str,
                        descripcion: str, importe: float,
                        diferencia: float | None = None, nota: str = "",
                        mov_ref: str = "") -> dict:
    """Anota un movimiento que hay que arreglar en el sistema contable.

    ⚠️ Encontrar el movimiento no alcanza: **el arreglo se hace en OTRO sistema y
    en otro momento**. Sin anotarlo, la próxima conciliación vuelve a encontrar
    lo mismo y nadie sabe si ya se corrigió — que es exactamente cómo un hallazgo
    se convierte en trabajo repetido.

    ⚠️ **`mov_ref` es lo que identifica al movimiento**, y sin él esta función
    perdía filas en silencio: dos `N/C - CRED REVERSO PASE E` de $50.000 el mismo
    día se pisaban entre sí y quedaba UN pendiente de 50.000 para una diferencia
    de 100.000,55. Es `mov_hash` del lado del banco y `mayor:<fila>` del lado del
    mayor. Si el que llama no lo manda se deriva del texto —que es la conducta
    vieja, y la única honesta cuando no hay identidad— pero se deriva EXPLÍCITO y
    no por omisión de una constraint.
    """
    accion = (accion or "").strip()
    descripcion = (descripcion or "").strip()
    if accion not in ACCIONES:
        raise ValueError(f"Acción inválida. Opciones: {', '.join(ACCIONES)}")
    if not descripcion:
        raise ValueError("Falta la descripción del movimiento.")
    if not _q("SELECT 1 FROM bancos.cuentas WHERE id = %s", (cuenta_id,)):
        raise ValueError("Esa cuenta no existe.")

    importe = round(float(importe), 2)
    ref = (mov_ref or "").strip() or f"txt:{descripcion}|{importe}"

    filas = _q(
        """INSERT INTO bancos.conciliacion_pendientes
             (cuenta_id, fecha, accion, descripcion, importe, diferencia, nota,
              confirmado_por, mov_ref)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (cuenta_id, fecha, accion, mov_ref)
           DO UPDATE SET confirmado_at = now(), confirmado_por = EXCLUDED.confirmado_por,
                         nota = EXCLUDED.nota, resuelto = false,
                         resuelto_por = NULL, resuelto_at = NULL
           RETURNING id""",
        (cuenta_id, fecha, accion, descripcion, importe,
         diferencia, (nota or "").strip() or None, email, ref))
    _audit(email, "conciliar_confirmar",
           {"cuenta_id": cuenta_id, "fecha": fecha.isoformat(), "accion": accion,
            "descripcion": descripcion, "importe": importe, "mov_ref": ref})
    return {"id": filas[0]["id"], "ok": True}


def listar_pendientes(*, incluir_resueltos: bool = False) -> list[dict]:
    """Lo confirmado y todavía sin arreglar. Sin filtro de fecha: un pendiente
    puede tardar días en resolverse, y esconderlo al día siguiente sería perder
    justo lo que se quiso anotar."""
    where = "" if incluir_resueltos else "WHERE NOT p.resuelto"
    return [{
        "id": r["id"],
        "cuenta_id": r["cuenta_id"],
        "cuenta": f"{r['bank_name']} · {r['account_type']} {r['currency']} "
                  f"{r['account_number']}".strip(),
        "moneda": r["currency"],
        "fecha": r["fecha"].isoformat() if r.get("fecha") else None,
        "accion": r["accion"],
        "descripcion": (r.get("descripcion") or "").strip(),
        "importe": _f(r.get("importe")),
        "diferencia": _f(r.get("diferencia")),
        "nota": r.get("nota"),
        "por": r.get("confirmado_por"),
        "at": r["confirmado_at"].isoformat() if r.get("confirmado_at") else None,
        "resuelto": r.get("resuelto"),
        "resuelto_por": r.get("resuelto_por"),
        "resuelto_at": (r["resuelto_at"].isoformat() if r.get("resuelto_at") else None),
    } for r in _q(
        f"""SELECT p.*, c.bank_name, c.account_type, c.currency, c.account_number
              FROM bancos.conciliacion_pendientes p
              JOIN bancos.cuentas c ON c.id = p.cuenta_id
              {where}
             ORDER BY p.resuelto, p.fecha DESC, p.confirmado_at DESC""")]


def resolver_pendiente(email: str, pendiente_id: int, resuelto: bool = True) -> dict:
    """Marca que el arreglo ya se hizo en el otro sistema. **La fila no se
    borra**: es la traza de qué se corrigió y quién lo corrigió."""
    if not _q("SELECT 1 FROM bancos.conciliacion_pendientes WHERE id = %s",
              (pendiente_id,)):
        raise ValueError("Ese pendiente no existe.")
    _exec("""UPDATE bancos.conciliacion_pendientes
                SET resuelto = %s,
                    resuelto_por = CASE WHEN %s THEN %s ELSE NULL END,
                    resuelto_at  = CASE WHEN %s THEN now() ELSE NULL END
              WHERE id = %s""",
          (resuelto, resuelto, email, resuelto, pendiente_id))
    _audit(email, "conciliar_resolver", {"id": pendiente_id, "resuelto": resuelto})
    return {"id": pendiente_id, "resuelto": resuelto}


def borrar_pendiente(email: str, pendiente_id: int) -> bool:
    """Para el confirmado por error. Lo resuelto se marca, no se borra."""
    filas = _q("""SELECT id, cuenta_id, fecha, accion, descripcion, importe
                    FROM bancos.conciliacion_pendientes WHERE id = %s""", (pendiente_id,))
    if not filas:
        return False
    _exec("DELETE FROM bancos.conciliacion_pendientes WHERE id = %s", (pendiente_id,))
    _audit(email, "conciliar_borrar", filas[0])
    return True


# --------------------------------------------------------------------------- #
# DIFERENCIAS — el saldo se movió más (o menos) de lo que dicen sus movimientos
# --------------------------------------------------------------------------- #
def diferencias(email: str, fecha: date) -> dict:
    """Por cuenta: ¿la variación del saldo está EXPLICADA por los movimientos?

    La cuenta que tiene que dar:

        cierre(hoy) − cierre(día anterior)  ==  Σ movimientos de hoy

    Lo que sobra es la **diferencia sin explicar**, y tiene una causa concreta que
    el back office ya conocía: **el banco a veces registra un movimiento con
    fecha de ANTEAYER que recién impacta en el saldo de AYER**. El movimiento
    queda en un día que ya cerramos y el salto aparece en el otro.

    ⚠️ La consecuencia matemática que hace útil a esta pantalla: como
    `Σ movimientos = cierre(hoy) − apertura(hoy)` cuando el día cierra bien,
    entonces

        sin_explicar  =  apertura(hoy) − cierre(día anterior)

    O sea: **la diferencia ES el salto entre el cierre de un día y la apertura
    del siguiente**, los dos informados por el banco. Por eso se publican las dos
    lecturas: `sin_explicar` (el número) y `salto_apertura` (la evidencia). Si no
    coinciden, el problema no es el asiento retroactivo sino que el día no cierra
    contra sus propios movimientos — que es otro hallazgo, y la vista lo dice.

    ⚠️ **Se reconcilia contra el BANCO, no contra la pantalla.** Los movimientos
    manuales mueven el saldo que mostramos pero no existen para el banco: si
    entraran, cada ajuste nuestro aparecería como una diferencia del banco. Se
    publican aparte (`ajuste_manual`) para que nadie se confunda al comparar con
    el consolidado.

    ⚠️ **Los IGNORADOS sí entran.** Ignorar saca un movimiento de los GASTOS, no
    del extracto: acá se está reconstruyendo la aritmética del banco.
    """
    previas = _q(
        """SELECT max(fecha) AS f FROM (
               SELECT fecha FROM bancos.extracto_dia WHERE fecha < %s
               UNION ALL
               SELECT fecha FROM bancos.saldos       WHERE fecha < %s) t""",
        (fecha, fecha))
    previa = previas[0]["f"] if previas else None
    if previa is None:
        # Sin día anterior no hay nada que comparar. La base retiene 3 fechas, así
        # que esto pasa el primer día o si la ingesta viene fallando: es un dato,
        # no un error.
        return {"fecha": fecha.isoformat(), "fecha_previa": None, "filas": [],
                "sin_previa": True}

    cuentas = _q(
        """SELECT id, bank_number, bank_name, account_number, account_type,
                  currency, account_label, activa, origen
             FROM bancos.cuentas WHERE activa
            ORDER BY bank_name, currency, account_type, account_number""")

    # Los dos días en UNA query, y las dos fuentes de saldo en otra: el cierre
    # sale del extracto si lo hay y si no de `bancos.saldos`, el mismo orden que
    # usa el consolidado. Si acá eligiera distinto, dos pantallas dirían dos
    # saldos para el mismo día.
    ext = {(r["cuenta_id"], r["fecha"]): r for r in _q(
        """SELECT cuenta_id, fecha, saldo_apertura, saldo_cierre, cierra
             FROM bancos.extracto_dia WHERE fecha IN (%s, %s)""", (fecha, previa))}
    sal = {(r["cuenta_id"], r["fecha"]): _f(r["saldo"]) for r in _q(
        f"""SELECT s.cuenta_id, s.fecha, {_SALDO_INFORMADO} AS saldo
             FROM bancos.saldos s WHERE s.fecha IN (%s, %s)""", (fecha, previa))}

    movs = {r["cuenta_id"]: (_f(r["neto"]) or 0.0, r["n"]) for r in _q(
        """SELECT cuenta_id,
                  -- abs() + `tipo`: el importe viene YA FIRMADO del banco, así
                  -- que aplicarle el signo otra vez devolvía la suma de los
                  -- valores absolutos. Ver `_firmado`.
                  sum(CASE WHEN tipo = 'C' THEN abs(importe) ELSE -abs(importe) END)
                    AS neto,
                  count(*) AS n
             FROM bancos.movimientos WHERE fecha = %s GROUP BY cuenta_id""", (fecha,))}
    manuales = _ajuste_manual(fecha)

    def _cierre(cid: int, f: date) -> float | None:
        e = ext.get((cid, f))
        if e is not None and e.get("saldo_cierre") is not None:
            return _f(e["saldo_cierre"])
        return sal.get((cid, f))

    filas: list[dict] = []
    for c in cuentas:
        cid = c["id"]
        hoy, ayer = _cierre(cid, fecha), _cierre(cid, previa)
        neto, n = movs.get(cid, (0.0, 0))
        e = ext.get((cid, fecha)) or {}
        apertura = _f(e.get("saldo_apertura"))

        # Sin alguno de los dos cierres no hay resta posible. No se asume cero:
        # «no sabemos» y «no se movió» son cosas distintas y confundirlas
        # inventaría una diferencia del tamaño del saldo.
        variacion = None if hoy is None or ayer is None else round(hoy - ayer, 2)
        sin_explicar = None if variacion is None else round(variacion - neto, 2)
        salto = (None if apertura is None or ayer is None
                 else round(apertura - ayer, 2))

        filas.append({
            **_cuenta_publica(c),
            "cierre": hoy,
            "cierre_previo": ayer,
            "variacion": variacion,
            "movimientos": round(neto, 2),
            "n_movimientos": n,
            "apertura": apertura,
            # La evidencia: el banco cerró un día en X y abrió el siguiente en Y.
            "salto_apertura": salto,
            "sin_explicar": sin_explicar,
            # `cierra` es el OTRO chequeo, el de adentro del día: los movimientos
            # contra apertura/cierre del MISMO día. Viaja para poder distinguir
            # un asiento retroactivo de un día que directamente no cuadra.
            "cierra": e.get("cierra"),
            "ajuste_manual": (manuales.get(cid) or {}).get("ajuste") or None,
        })

    _auditar(email, None, previa, fecha, len(filas))
    return {
        "fecha": fecha.isoformat(),
        "fecha_previa": previa.isoformat(),
        "sin_previa": False,
        "filas": filas,
    }


def consolidado(email: str, fecha: date) -> dict:
    """CONSOLIDADO BANCOS: una fila por cuenta, agrupadas por banco, de UN día.

    Cambió el 2026-08-18 por pedido del back office. Antes tomaba un rango
    `desde`/`hasta` y mostraba la apertura de un día contra el cierre de otro —
    una "variación" que no era la variación de nada. Ahora es **un día**:
    apertura, cierre y la diferencia entre los dos, que sí es lo que pasó ese día.

    De dónde sale el saldo, por orden y siempre declarado en `fuente`:

    1. **`extracto`** — apertura y cierre del extracto de ESE día. Los dos los
       informa el banco y vienen con el detalle de movimientos que los explica.
    2. **`saldo`** — `bancos.saldos`. Entra cuando no hay extracto, que es el
       caso de la cuenta QUIETA: el extracto solo devuelve los días CON
       movimientos, así que una cuenta que no se movió no tiene ninguna fila.
    3. **`null`** — no sabemos. Se cuenta en `sin_datos` y la vista lo canta.

    Las dos fuentes NO se suman ni se promedian: por cuenta gana una sola y la
    respuesta dice cuál. Si el banco informa un cierre de extracto distinto del
    saldo del día, eso es un HALLAZGO de conciliación (`discrepancia`), no un
    número a elegir por nosotros.

    **Sin subtotales por banco ni totales por moneda** (los sacó el back office:
    no los usaba). Los bancos siguen agrupando las cuentas, que es lo que hace
    navegable la lista, pero cada fila se lee sola.
    """
    filas = _q(
        f"""SELECT c.id, c.bank_number, c.bank_name, c.account_number,
                  c.account_type, c.currency, c.account_label, c.activa, c.origen,
                  e.saldo_apertura, e.saldo_cierre, e.total_movimientos,
                  {_SALDO_INFORMADO} AS saldo_banco
             FROM bancos.cuentas c
             LEFT JOIN bancos.extracto_dia e ON e.cuenta_id = c.id AND e.fecha = %s
             LEFT JOIN bancos.saldos       s ON s.cuenta_id = c.id AND s.fecha = %s
            WHERE c.activa
            ORDER BY c.bank_name, c.currency, c.account_type, c.account_number""",
        (fecha, fecha),
    )
    baldes = _baldes()
    gastos = _gastos_bancarios(fecha, baldes)
    # El cierre de HOY (lo que muestra la columna) y el sellado de AYER (la
    # apertura). Las dos, la misma fuente que usa CONCILIAR.
    cierres = _saldos_banco(fecha)
    previos = _cierre_sellado(restar_habiles(fecha, 1))
    manuales = {r["cuenta_id"]: r["n"] for r in _q(
        """SELECT cuenta_id, count(*) AS n FROM bancos.movimientos_manuales
            WHERE fecha = %s GROUP BY cuenta_id""", (fecha,))}

    bancos: list[dict] = []
    por_banco: dict[str, dict] = {}
    sin_datos = 0

    for r in filas:
        pub = _cuenta_publica(r)
        ini, fin = _f(r.get("saldo_apertura")), _f(r.get("saldo_cierre"))
        del_banco = _f(r.get("saldo_banco"))

        # ⚠️ **EL SALDO AL CIERRE SALE DE `_saldos_banco()`, la MISMA función que
        # sella.** Antes esta pantalla tenía su propia fórmula y el sellado otra,
        # así que el número que el back office miraba y daba por bueno no era el
        # que al día siguiente se leía como SALDO INICIO. Ahora es el mismo dato.
        cierre = cierres.get(r["id"])
        fin = (cierre or {}).get("valor")
        fuente = (cierre or {}).get("fuente")
        ajuste = (cierre or {}).get("ajuste") or 0.0
        if fin is None:
            sin_datos += 1

        # ⚠️ **LA APERTURA ES EL CIERRE SELLADO DE AYER**, leído tal cual: es el
        # mismo dato que ayer se mostró en esta misma columna. Si no hay sellado
        # (el primer día, o un hueco) se cae a la apertura que declara el banco.
        ini_banco = ini
        ayer = (previos.get(r["id"]) or {}).get("valor")
        ini = ini_banco if ayer is None else ayer

        # Si el banco informa las dos cosas y no coinciden, es un hallazgo de
        # conciliación — se publica, no se elige una y se tapa la otra.
        discrepancia = None
        if r.get("saldo_cierre") is not None and del_banco is not None:
            d = round(_f(r["saldo_cierre"]) - del_banco, 2)
            if abs(d) >= 0.01:
                discrepancia = d

        cuenta = {
            **pub,
            "saldo_inicio": ini,
            # Lo que el BANCO declara que abrió. La resta contra `saldo_inicio`
            # es el salto de apertura que analiza DIFERENCIAS.
            "saldo_inicio_banco": ini_banco,
            "saldo_cierre": fin,
            "fuente": fuente,
            "saldo_banco": del_banco,
            "discrepancia": discrepancia,
            # None mientras la regla no esté definida — «no sabemos» no es 0.
            "gastos_bancarios": (gastos.get(r["id"]) or {}).get("total"),
            # El desglose viaja COMPLETO; qué columnas dibujar lo decide la vista
            # (el consolidado agrupa el grupo "otros", el modal los abre).
            "gastos_desglose": gastos.get(r["id"]),
            # Cuánto del cierre lo puso una persona. Se publica aparte para que la
            # vista lo pueda cantar: un saldo con ajuste manual no es lo mismo que
            # uno que informó el banco.
            "ajuste_manual": round(ajuste, 2) if ajuste else None,
            "movimientos_manuales": manuales.get(r["id"], 0),
            "movimientos": r.get("total_movimientos"),
            # La variación solo existe si el cierre sale del EXTRACTO: restar una
            # apertura de extracto contra un saldo de otra fuente mezclaría dos
            # cosas que el banco informa por separado.
            "variacion": (round(fin - ini, 2)
                          if fuente == "extracto" and ini is not None and fin is not None
                          else None),
        }

        clave = f"{r.get('bank_number')}|{(r.get('bank_name') or '').strip()}"
        grupo = por_banco.get(clave)
        if grupo is None:
            grupo = {
                "banco": r.get("bank_number"),
                "banco_nombre": (r.get("bank_name") or "").strip(),
                "cuentas": [],
            }
            por_banco[clave] = grupo
            bancos.append(grupo)
        grupo["cuentas"].append(cuenta)

    _auditar(email, None, fecha, fecha, len(filas))
    marcar_presencia(email)

    return {
        "fecha": fecha.isoformat(),
        "conectados": conectados(),
        "puede_escribir": puede_escribir(email),
        "desglose": catalogo_desglose(baldes),
        "bancos": bancos,
        "cuentas": len(filas),
        "sin_datos": sin_datos,
        "gastos_definidos": bool(gastos),
        "sync": ultima_sync(),
    }


def ultima_sync() -> dict | None:
    """Última corrida del job. Es lo que contesta «¿este dato de cuándo es?»."""
    filas = _q(
        """SELECT max(corrida_at) AS corrida_at,
                  count(*) FILTER (WHERE NOT ok) AS con_error,
                  count(*) AS cuentas
             FROM bancos.sync_log
            WHERE corrida_at >= (SELECT max(corrida_at) - interval '10 minutes'
                                   FROM bancos.sync_log)"""
    )
    if not filas or not filas[0].get("corrida_at"):
        return None
    r = filas[0]
    return {
        "corrida_at": r["corrida_at"].isoformat(),
        "cuentas": r["cuentas"],
        "con_error": r["con_error"],
    }


# --------------------------------------------------------------------------- #
# Escritura: reglas de gasto, marcas por movimiento, presencia
# --------------------------------------------------------------------------- #
def puede_escribir(email: str) -> bool:
    """Quién puede tocar la clasificación de gastos.

    **Reusa la allowlist de Tesorería** (`operaciones.tesoreria_escritores`) + admin,
    en vez de crear una propia. No es pereza: una lista nueva nace VACÍA, así que
    la función quedaría muerta hasta que alguien la cargue a mano — y es
    literalmente el mismo equipo, en la misma pantalla del back office. Separarla
    es cambiar una consulta el día que haga falta.

    ⚠️ Que compartan la allowlist **no acopla los datos**: Interbanking y
    Tesorería siguen sin tener una sola clave en común (ver docs/INTERBANKING.md).
    Un permiso es una política sobre personas, no un join.
    """
    e = (email or "").lower().strip()
    if not e:
        return False
    try:
        from core.roles import get_user_role
        if get_user_role(e) == "admin":
            return True
        return bool(_q("SELECT 1 FROM operaciones.tesoreria_escritores WHERE email = %s", (e,)))
    except Exception:
        return False


def _audit(email: str, accion: str, detalle: dict) -> None:
    try:
        _exec("INSERT INTO bancos.gastos_audit (email, accion, detalle) VALUES (%s,%s,%s::jsonb)",
              (email, accion, json.dumps(detalle, ensure_ascii=False, default=str)))
    except Exception:   # la auditoría no puede tumbar la escritura
        pass


def crear_regla(email: str, campo: str, operador: str, valor: str, nota: str = "") -> dict:
    """Alta de una regla. Valida SERVER-SIDE contra las constantes del módulo.

    `campo` y `operador` se validan contra `CAMPOS_REGLA` / `OPERADORES_REGLA` y
    no contra lo que mande el front: es una regla que decide plata y el que la
    escribe es un usuario.
    """
    campo, operador = (campo or "").strip(), (operador or "").strip()
    valor = (valor or "").strip()
    if campo not in CAMPOS_REGLA:
        raise ValueError(f"Campo inválido. Opciones: {', '.join(CAMPOS_REGLA)}")
    if operador not in OPERADORES_REGLA:
        raise ValueError(f"Operador inválido. Opciones: {', '.join(OPERADORES_REGLA)}")
    if not valor:
        raise ValueError("El valor no puede estar vacío.")

    filas = _q(
        """INSERT INTO bancos.gastos_reglas (campo, operador, valor, nota, creado_por)
           VALUES (%s,%s,%s,%s,%s)
           ON CONFLICT (campo, operador, valor)
           DO UPDATE SET activa = true, nota = EXCLUDED.nota
           RETURNING id, campo, operador, valor, nota, activa""",
        (campo, operador, valor, (nota or "").strip() or None, email),
    )
    _audit(email, "regla_alta", filas[0])
    return filas[0]


def borrar_regla(email: str, regla_id: int) -> bool:
    """Baja FÍSICA. La regla no tiene histórico que huerfanar: la clasificación
    se deriva en la lectura, así que borrarla simplemente deja de aplicar. Las
    marcas MANUALES no se tocan — son de una persona, no de la regla."""
    filas = _q("SELECT id, campo, operador, valor FROM bancos.gastos_reglas WHERE id = %s",
               (regla_id,))
    if not filas:
        return False
    _exec("DELETE FROM bancos.gastos_reglas WHERE id = %s", (regla_id,))
    _audit(email, "regla_baja", filas[0])
    return True


def marcar_gasto(email: str, mov_hash: str, es_gasto: bool | None) -> dict:
    """Marca (o desmarca) UN movimiento. `es_gasto=None` BORRA el override y
    devuelve el movimiento al criterio de las reglas.

    Ese tercer estado importa: sin él, deshacer una marca equivocada obligaría a
    adivinar qué decía la regla y marcar el opuesto a mano — congelando para
    siempre algo que la regla ya resolvía bien.
    """
    if not _q("SELECT 1 FROM bancos.movimientos WHERE mov_hash = %s", (mov_hash,)):
        raise ValueError("Ese movimiento no existe (puede haberlo purgado la retención).")

    if es_gasto is None:
        _exec("DELETE FROM bancos.gastos_overrides WHERE mov_hash = %s", (mov_hash,))
    else:
        _exec("""INSERT INTO bancos.gastos_overrides (mov_hash, es_gasto, por, at)
                 VALUES (%s,%s,%s, now())
                 ON CONFLICT (mov_hash)
                 DO UPDATE SET es_gasto = EXCLUDED.es_gasto, por = EXCLUDED.por, at = now()""",
              (mov_hash, es_gasto, email))
    _audit(email, "marca", {"mov_hash": mov_hash, "es_gasto": es_gasto})
    return {"mov_hash": mov_hash, "es_gasto": es_gasto}


def ignorar_movimiento(email: str, mov_hash: str, ignorar: bool,
                       motivo: str = "") -> dict:
    """IGNORA (o des-ignora) UN movimiento — el equivalente al destildado por
    celda de Tesorería.

    La tabla es de PUROS OVERRIDES: la ausencia de fila significa "cuenta", así
    que des-ignorar es un DELETE y no un flag en false. Eso evita el estado
    ambiguo de una fila que dice `ignorado=false` y compite con el default.

    Qué deja de sumar: los GASTOS y su desglose (acá y en la columna del
    consolidado). Los créditos/débitos del día NO se tocan a propósito — son la
    aritmética del extracto del banco, y restarle una fila haría que la vista
    contradiga al extracto.
    """
    if not _q("SELECT 1 FROM bancos.movimientos WHERE mov_hash = %s", (mov_hash,)):
        raise ValueError("Ese movimiento no existe (puede haberlo purgado la retención).")

    if ignorar:
        _exec("""INSERT INTO bancos.movimientos_ignorados (mov_hash, motivo, por, at)
                 VALUES (%s,%s,%s, now())
                 ON CONFLICT (mov_hash)
                 DO UPDATE SET motivo = EXCLUDED.motivo, por = EXCLUDED.por, at = now()""",
              (mov_hash, (motivo or "").strip() or None, email))
    else:
        _exec("DELETE FROM bancos.movimientos_ignorados WHERE mov_hash = %s", (mov_hash,))
    _audit(email, "ignorar", {"mov_hash": mov_hash, "ignorar": ignorar,
                              "motivo": (motivo or "").strip() or None})
    return {"mov_hash": mov_hash, "ignorado": ignorar}


# --------------------------------------------------------------------------- #
# ABM del DESGLOSE — el catálogo de baldes, editable por el equipo
# --------------------------------------------------------------------------- #
# Por qué existe: el que sabe que el Banco X escribe `LEY25413DB` donde el Y dice
# `IMP.DB/CR BANCARIOS P/DEB` es el back office, no el que programa. Mientras los
# baldes fueron una constante, sumar una grafía nueva era un commit y un deploy —
# o sea, el equipo tenía que pedirlo y esperar. Ahora lo hacen ellos y el número
# cambia en el próximo poll, porque el desglose se DERIVA en la lectura.
def _slug(texto: str) -> str:
    """Clave estable a partir de la etiqueta. La clave es la que viaja en el JSON
    y la que referencian los matchers; que sea derivada evita pedirle al usuario
    un campo técnico que no significa nada para él."""
    limpio = "".join(c if c.isalnum() else "_" for c in (texto or "").strip().lower())
    return "_".join(p for p in limpio.split("_") if p)[:40]


def guardar_balde(email: str, etiqueta: str, grupo: str, orden: int,
                  clave: str = "") -> dict:
    """Alta o edición de un balde. Sin `clave` es alta y la deriva de la etiqueta.

    `grupo` decide DÓNDE se muestra: `concepto` = columna propia en el
    consolidado; `otros` = se suma con el resto adentro de OTROS IMP. Es
    presentación, no plata: mover un balde de grupo no cambia ningún total.
    """
    etiqueta = (etiqueta or "").strip()
    grupo = (grupo or "otros").strip()
    if not etiqueta:
        raise ValueError("La etiqueta no puede estar vacía.")
    if grupo not in GRUPOS_BALDE:
        raise ValueError(f"Grupo inválido. Opciones: {', '.join(GRUPOS_BALDE)}")
    clave = (clave or "").strip() or _slug(etiqueta)
    if not clave:
        raise ValueError("De esa etiqueta no sale ninguna clave; poné letras o números.")
    if clave == RESTO:
        raise ValueError(f"`{RESTO}` está reservado para lo que no cae en ningún balde.")

    filas = _q(
        """INSERT INTO bancos.gastos_baldes (clave, etiqueta, grupo, orden, creado_por)
           VALUES (%s,%s,%s,%s,%s)
           ON CONFLICT (clave) DO UPDATE SET etiqueta = EXCLUDED.etiqueta,
                                             grupo    = EXCLUDED.grupo,
                                             orden    = EXCLUDED.orden,
                                             activo   = true
           RETURNING clave, etiqueta, grupo, orden""",
        (clave, etiqueta, grupo, int(orden or 100), email))
    _audit(email, "balde_alta", filas[0])
    return filas[0]


def reordenar_baldes(email: str, claves: list[str]) -> list[dict]:
    """Fija el ORDEN de las columnas, que es lo único que decide los empates.

    ⚠️ Por qué esto existe y por qué NO es una "excepción". El caso real: un banco
    manda `IVA PERCEPCION RESOL GRAL` con el CONCEPTO en `IVA`, y como IVA se
    evaluaba antes, se la comía. Se puede resolver de dos formas:

      · una excepción hardcodeada para ese texto — que es una regla escondida en
        el código, que solo yo puedo cambiar y que nadie más sabe que existe;
      · **subir IVAPERCEP arriba de IVA** y darle a IVAPERCEP un matcher por
        DESCRIPCIÓN. Un movimiento con esa descripción cae en IVAPERCEP; uno con
        concepto IVA y sin esa descripción sigue cayendo en IVA, porque el
        matcher no lo agarra.

    La segunda no es un parche: es el mecanismo funcionando. Por eso lo que se
    agrega es **poder mover el orden**, no una excepción — el mismo movimiento
    resuelve el próximo caso, que va a ser parecido y distinto.

    Se reasigna la secuencia COMPLETA (10, 20, 30…) en vez de tocar un número:
    así no hay empates ni huecos raros después de mover algo diez veces.
    """
    claves = [str(c).strip() for c in (claves or []) if str(c).strip()]
    if not claves:
        raise ValueError("No mandaste ninguna columna.")
    existentes = {r["clave"] for r in _q("SELECT clave FROM bancos.gastos_baldes")}
    if set(claves) != existentes:
        raise ValueError("La lista tiene que traer TODAS las columnas, una sola vez.")
    for i, clave in enumerate(claves):
        _exec("UPDATE bancos.gastos_baldes SET orden = %s WHERE clave = %s",
              ((i + 1) * 10, clave))
    _audit(email, "desglose_orden", {"claves": claves})
    return _baldes()


def borrar_balde(email: str, clave: str) -> bool:
    """Baja de una columna. **Solo si NO tiene textos cargados.**

    ⚠️ Esta regla nació de un incidente (2026-08-19): alguien borró COM.TRANSF con
    sus tres textos de un clic, y **no se pudieron recuperar** — la baja auditaba
    la clave y el nombre, pero los matchers se iban por CASCADE sin quedar
    registrados en ningún lado. Dos errores en uno: un botón demasiado fácil de
    apretar, y una auditoría que guardaba la mitad.

    Las dos cosas se arreglaron:

    · **No se puede borrar una columna con textos.** Primero hay que sacarlos de
      a uno, y cada uno queda auditado por separado. Así el gesto destructivo se
      vuelve deliberado en vez de instantáneo, y siempre hay de dónde
      reconstruir. Una columna VACÍA sí se borra: no hay conocimiento que perder,
      y es el caso real de «me equivoqué al crearla».
    · **La auditoría guarda el balde COMPLETO**, con sus textos. Aunque hoy no se
      pueda borrar uno cargado, si mañana alguien afloja la regla el rastro ya
      está — el costo es una línea y el beneficio es que el dato no se evapora.
    """
    filas = _q("SELECT clave, etiqueta, grupo, orden FROM bancos.gastos_baldes WHERE clave = %s",
               (clave,))
    if not filas:
        return False
    textos = _q("""SELECT id, campo, operador, valor FROM bancos.gastos_balde_matchers
                    WHERE balde = %s ORDER BY id""", (clave,))
    if textos:
        raise ValueError(
            f"«{filas[0]['etiqueta']}» tiene {len(textos)} texto(s) cargado(s). "
            "Sacalos primero, de a uno: así no se pierde de un clic lo que costó "
            "descubrir banco por banco.")
    _exec("DELETE FROM bancos.gastos_baldes WHERE clave = %s", (clave,))
    _audit(email, "balde_baja", {**filas[0], "textos": textos})
    return True


def agregar_matcher(email: str, balde: str, campo: str, operador: str,
                    valor: str) -> dict:
    """Suma una grafía a un balde. Esto es el 90% del uso: el mismo impuesto
    escrito distinto según el banco.

    ⚠️ `igual` vs `contiene` no es un detalle de estilo. Cuando un valor es
    PREFIJO de otro —`IVA` de `IVAPERCEP`— `contiene` se come al otro: la columna
    IVA mostraría de más y IVAPERCEP quedaría en cero, y el total seguiría dando
    bien. Por eso los baldes de concepto van por `igual`. Para texto que llega
    truncado o con cola, `contiene`.
    """
    campo, operador = (campo or "").strip(), (operador or "").strip()
    valor = (valor or "").strip()
    if campo not in CAMPOS_REGLA:
        raise ValueError(f"Campo inválido. Opciones: {', '.join(CAMPOS_REGLA)}")
    if operador not in OPERADORES_REGLA:
        raise ValueError(f"Operador inválido. Opciones: {', '.join(OPERADORES_REGLA)}")
    if not valor:
        raise ValueError("El valor no puede estar vacío.")
    if not _q("SELECT 1 FROM bancos.gastos_baldes WHERE clave = %s", (balde,)):
        raise ValueError("Ese balde no existe.")

    filas = _q(
        """INSERT INTO bancos.gastos_balde_matchers (balde, campo, operador, valor,
                                                     creado_por)
           VALUES (%s,%s,%s,%s,%s)
           ON CONFLICT (balde, campo, operador, valor) DO UPDATE SET balde = EXCLUDED.balde
           RETURNING id, balde, campo, operador, valor""",
        (balde, campo, operador, valor, email))
    _audit(email, "matcher_alta", filas[0])
    return filas[0]


def borrar_matcher(email: str, matcher_id: int) -> bool:
    filas = _q("""SELECT id, balde, campo, operador, valor
                    FROM bancos.gastos_balde_matchers WHERE id = %s""", (matcher_id,))
    if not filas:
        return False
    _exec("DELETE FROM bancos.gastos_balde_matchers WHERE id = %s", (matcher_id,))
    _audit(email, "matcher_baja", filas[0])
    return True


# --------------------------------------------------------------------------- #
# BANCOS Y MOVIMIENTOS MANUALES — lo que Interbanking no tiene
# --------------------------------------------------------------------------- #
TIPOS_MOV = ("C", "D")
TIPOS_CUENTA = ("CC", "CA")


def listar_manuales(fecha: date) -> list[dict]:
    """Los movimientos manuales de UN día, de todas las cuentas, con a qué cuenta
    pertenece cada uno. Es lo que alimenta el panel de carga."""
    return [{
        "id": r["id"],
        "cuenta_id": r["cuenta_id"],
        "cuenta": f"{r['bank_name']} · {r['account_type']} {r['currency']} "
                  f"{r['account_number']}".strip(),
        "fecha": r["fecha"].isoformat() if r.get("fecha") else None,
        "descripcion": (r.get("descripcion") or "").strip(),
        # Valor absoluto + `tipo`, la misma convención que los del banco.
        "importe": abs(_f(r.get("importe")) or 0.0),
        "tipo": r.get("tipo"),
        "por": r.get("creado_por"),
        "hora": r["creado_at"].strftime("%H:%M") if r.get("creado_at") else None,
    } for r in _q(
        """SELECT m.id, m.cuenta_id, m.fecha, m.descripcion, m.importe, m.tipo,
                  m.creado_por, m.creado_at,
                  c.bank_name, c.account_type, c.currency, c.account_number
             FROM bancos.movimientos_manuales m
             JOIN bancos.cuentas c ON c.id = m.cuenta_id
            WHERE m.fecha = %s
            ORDER BY c.bank_name, c.account_number, m.creado_at""", (fecha,))]


def crear_cuenta_manual(email: str, banco: str, numero: str, tipo: str,
                        moneda: str, etiqueta: str = "") -> dict:
    """Da de alta una cuenta que Interbanking no informa.

    ⚠️ **El banco se resuelve por NOMBRE**: si ya existe uno con ese nombre, la
    cuenta nueva hereda su `bank_number` y queda agrupada abajo de él en la
    pantalla; si no existe, se le genera un código propio. Es lo que permite las
    dos cosas que hacen falta con un solo formulario — sumarle una cuenta a un
    banco que ya está, o dar de alta un banco entero que no está en Interbanking.
    Pedirle al usuario un "código de banco" habría sido pedirle un dato que no
    tiene.

    El código generado arranca con `M` para que no pueda chocar nunca con un
    código del BCRA, que son tres dígitos.
    """
    banco = (banco or "").strip()
    numero = (numero or "").strip()
    tipo = (tipo or "CC").strip().upper()
    moneda = (moneda or "ARS").strip().upper()
    if not banco:
        raise ValueError("Poné el nombre del banco.")
    if not numero:
        raise ValueError("Poné el número de cuenta.")
    if tipo not in TIPOS_CUENTA:
        raise ValueError(f"Tipo inválido. Opciones: {', '.join(TIPOS_CUENTA)}")
    if not moneda:
        raise ValueError("Poné la moneda.")

    existente = _q(
        """SELECT bank_number, bank_name FROM bancos.cuentas
            WHERE lower(trim(bank_name)) = lower(%s) LIMIT 1""", (banco,))
    if existente:
        bank_number, banco = existente[0]["bank_number"], existente[0]["bank_name"]
    else:
        usados = [r["bank_number"] for r in _q(
            "SELECT bank_number FROM bancos.cuentas WHERE bank_number LIKE 'M%%'")]
        n = 1
        while f"M{n:02d}" in usados:
            n += 1
        bank_number = f"M{n:02d}"

    filas = _q(
        """INSERT INTO bancos.cuentas
             (bank_number, bank_name, account_number, account_type, currency,
              account_label, origen, creado_por, actualizado_at)
           VALUES (%s,%s,%s,%s,%s,%s,'manual',%s, now())
           ON CONFLICT (bank_number, account_number, account_type, currency)
           DO UPDATE SET account_label = EXCLUDED.account_label, activa = true
           RETURNING id, bank_number, bank_name, account_number, account_type,
                     currency, account_label, activa, origen""",
        (bank_number, banco, numero, tipo, moneda, (etiqueta or "").strip() or None, email))
    _audit(email, "cuenta_manual_alta", filas[0])
    return _cuenta_publica(filas[0])


def borrar_cuenta_manual(email: str, cuenta_id: int) -> bool:
    """Baja de una cuenta MANUAL. Las de Interbanking no se tocan desde acá: las
    da de alta el job y borrarlas sería pelearse con él todos los días.

    Se borra de verdad (con sus movimientos manuales por CASCADE) solo porque no
    hay nada que huerfanar: una cuenta manual no tiene extractos ni saldos del
    banco. Igual se avisa cuántos movimientos se lleva puestos.
    """
    filas = _q("""SELECT id, bank_name, account_number, origen FROM bancos.cuentas
                   WHERE id = %s""", (cuenta_id,))
    if not filas:
        return False
    if (filas[0].get("origen") or "interbanking") != "manual":
        raise ValueError("Esa cuenta la informa Interbanking: no se borra a mano.")
    _exec("DELETE FROM bancos.cuentas WHERE id = %s", (cuenta_id,))
    _audit(email, "cuenta_manual_baja", filas[0])
    return True


def crear_movimiento_manual(email: str, cuenta_id: int, fecha: date, descripcion: str,
                            importe: float, tipo: str) -> dict:
    """Registra un movimiento que el banco no informa.

    **Impacta SIEMPRE el saldo al cierre** del día que se le cargue: en una
    cuenta real se suma arriba de su extracto y en una manual es todo el saldo.
    No hay moneda que elegir — la cuenta ya es de una moneda.
    """
    descripcion = (descripcion or "").strip()
    tipo = (tipo or "").strip().upper()
    if not descripcion:
        raise ValueError("Poné una descripción: dentro de un mes nadie se acuerda.")
    if tipo not in TIPOS_MOV:
        raise ValueError("El movimiento tiene que ser C (suma) o D (resta).")
    try:
        monto = abs(float(importe))
    except (TypeError, ValueError) as e:
        raise ValueError("El importe tiene que ser un número.") from e
    if not monto:
        raise ValueError("El importe no puede ser cero.")
    if not _q("SELECT 1 FROM bancos.cuentas WHERE id = %s AND activa", (cuenta_id,)):
        raise ValueError("Esa cuenta no existe.")

    filas = _q(
        """INSERT INTO bancos.movimientos_manuales
             (cuenta_id, fecha, descripcion, importe, tipo, creado_por)
           VALUES (%s,%s,%s,%s,%s,%s)
           RETURNING id, fecha, descripcion, importe, tipo, creado_por, creado_at""",
        (cuenta_id, fecha, descripcion, monto, tipo, email))
    # El cierre de ese día cambió → se vuelve a sellar, para que mañana la
    # apertura lea el número correcto sin recalcular nada.
    sellar_cierre(fecha)
    _audit(email, "movimiento_manual_alta", {**filas[0], "cuenta_id": cuenta_id})
    return _manual_publico(filas[0])


def borrar_movimiento_manual(email: str, mov_id: int) -> bool:
    filas = _q("""SELECT id, cuenta_id, fecha, descripcion, importe, tipo
                    FROM bancos.movimientos_manuales WHERE id = %s""", (mov_id,))
    if not filas:
        return False
    _exec("DELETE FROM bancos.movimientos_manuales WHERE id = %s", (mov_id,))
    sellar_cierre(filas[0]["fecha"])   # el cierre de ese día cambió
    _audit(email, "movimiento_manual_baja", filas[0])
    return True


def marcar_presencia(email: str) -> None:
    """Heartbeat. El poll de la vista ES el latido: no hay endpoint aparte que
    golpear, igual que en Tesorería."""
    e = (email or "").lower().strip()
    if not e:
        return
    try:
        _exec("""INSERT INTO bancos.presencia (email, visto_at) VALUES (%s, now())
                 ON CONFLICT (email) DO UPDATE SET visto_at = now()""", (e,))
    except Exception:   # que nadie se quede sin ver la vista por el heartbeat
        pass


def conectados() -> list[dict]:
    """Quiénes tienen la vista abierta ahora (últimos PRESENCIA_TTL_S segundos)."""
    try:
        return [{"email": r["email"], "visto_at": r["visto_at"].isoformat()} for r in _q(
            """SELECT email, visto_at FROM bancos.presencia
                WHERE visto_at >= now() - make_interval(secs => %s) ORDER BY email""",
            (PRESENCIA_TTL_S,))]
    except Exception:
        return []


def _auditar(email: str, cuenta_id: int | None, desde: date, hasta: date, filas: int) -> None:
    """Quién miró qué banco y cuándo. Nunca rompe la lectura."""
    try:
        _exec(
            """INSERT INTO bancos.audit_lecturas (email, cuenta_id, fecha_desde,
                                                  fecha_hasta, filas)
               VALUES (%s,%s,%s,%s,%s)""",
            (email, cuenta_id, desde, hasta, filas),
        )
    except Exception:  # la auditoría no puede tumbar la vista
        pass
