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
            col = CAMPOS_REGLA.get(m["campo"])
            if not col:
                continue
            dato = str(mov.get(col) or "").strip().casefold()
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
    col = CAMPOS_REGLA.get(str(regla.get("campo") or ""))
    if not col:
        return False
    valor = str(regla.get("valor") or "").strip().casefold()
    if not valor:
        return False
    dato = str(mov.get(col) or "").strip().casefold()
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
    """{cuenta_id: {"ajuste": x, "movimientos": n}} de UN día. Una sola query.

    El ajuste es **crédito menos débito**: lo que los movimientos manuales le
    suman o le restan al saldo de esa cuenta ese día.
    """
    return {r["cuenta_id"]: {"ajuste": _f(r["ajuste"]) or 0.0, "movimientos": r["n"]}
            for r in _q(
                """SELECT cuenta_id,
                          sum(CASE WHEN tipo = 'C' THEN importe ELSE -importe END) AS ajuste,
                          count(*) AS n
                     FROM bancos.movimientos_manuales
                    WHERE fecha = %s
                    GROUP BY cuenta_id""", (fecha,))}


def _manuales_de(fecha: date, cuenta_id: int) -> list[dict]:
    """Los movimientos manuales de UNA cuenta en UN día, para el modal."""
    return _q(
        """SELECT id, fecha, descripcion, importe, tipo, creado_por, creado_at
             FROM bancos.movimientos_manuales
            WHERE fecha = %s AND cuenta_id = %s
            ORDER BY creado_at""", (fecha, cuenta_id))


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
        imp = _f(m.get("importe")) or 0.0
        firmado = imp if m.get("tipo") == "D" else -imp
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


def _movimiento_publico(r: dict) -> dict:
    return {
        # El hash es la IDENTIDAD del movimiento: sin él la pantalla no puede
        # pedir "marcá este". No es un dato sensible — es un sha256 de campos
        # que la propia fila ya muestra.
        "mov_hash": r.get("mov_hash"),
        "fecha": r["fecha"].isoformat() if r.get("fecha") else None,
        "hora": r["fecha_proceso"].strftime("%H:%M:%S") if r.get("fecha_proceso") else None,
        "importe": _f(r.get("importe")),
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
        "importe": _f(r.get("importe")),
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
            imp = m["importe"] or 0
            desglose[balde] += imp if m["tipo"] == "D" else -imp
        desglose = {k: round(v, 2) for k, v in desglose.items()}

    if cuenta_id is not None:
        manuales = [_manual_publico(r) for r in _manuales_de(fecha, cuenta_id)]

    # ⚠️ Créditos y débitos siguen siendo la aritmética del EXTRACTO: los
    # manuales no entran. Si entraran, la vista dejaría de poder compararse con
    # lo que informa el banco, que es para lo que existe la conciliación. Lo que
    # los manuales mueven —el saldo— viaja aparte, en `ajuste_manual`.
    creditos = sum(m["importe"] or 0 for m in movimientos if m["tipo"] == "C")
    debitos = sum(m["importe"] or 0 for m in movimientos if m["tipo"] == "D")
    ajuste_manual = round(sum((m["importe"] or 0) * (1 if m["tipo"] == "C" else -1)
                              for m in manuales), 2)

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
        "saldo_final": next((d["saldo_cierre"] for d in dias), None),
        "ajuste_manual": ajuste_manual,
        "movimientos_manuales": len(manuales),
        "gastos": round(sum(
            (m["importe"] or 0) * (1 if m["tipo"] == "D" else -1)
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
        "resumen": resumen,
        "sync": ultima_sync(),
    }


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
        """SELECT cuenta_id, fecha,
                  coalesce(saldo_operativo, saldo_dia) AS saldo
             FROM bancos.saldos WHERE fecha IN (%s, %s)""", (fecha, previa))}

    movs = {r["cuenta_id"]: (_f(r["neto"]) or 0.0, r["n"]) for r in _q(
        """SELECT cuenta_id,
                  sum(CASE WHEN tipo = 'C' THEN importe ELSE -importe END) AS neto,
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
        """SELECT c.id, c.bank_number, c.bank_name, c.account_number,
                  c.account_type, c.currency, c.account_label, c.activa, c.origen,
                  e.saldo_apertura, e.saldo_cierre, e.total_movimientos,
                  coalesce(s.saldo_operativo, s.saldo_dia) AS saldo_banco
             FROM bancos.cuentas c
             LEFT JOIN bancos.extracto_dia e ON e.cuenta_id = c.id AND e.fecha = %s
             LEFT JOIN bancos.saldos       s ON s.cuenta_id = c.id AND s.fecha = %s
            WHERE c.activa
            ORDER BY c.bank_name, c.currency, c.account_type, c.account_number""",
        (fecha, fecha),
    )
    baldes = _baldes()
    gastos = _gastos_bancarios(fecha, baldes)
    manuales = _ajuste_manual(fecha)

    bancos: list[dict] = []
    por_banco: dict[str, dict] = {}
    sin_datos = 0

    for r in filas:
        pub = _cuenta_publica(r)
        ini, fin = _f(r.get("saldo_apertura")), _f(r.get("saldo_cierre"))
        del_banco = _f(r.get("saldo_banco"))

        # Quién manda: el extracto si lo hay, si no el saldo. Nunca los dos.
        man = manuales.get(r["id"]) or {}
        ajuste = man.get("ajuste") or 0.0

        if fin is not None:
            fuente = "extracto"
        elif del_banco is not None:
            fuente, fin = "saldo", del_banco
        elif man:
            # El caso del BANCO MANUAL: no hay extracto ni saldo porque el banco
            # no está en Interbanking. Ahí el saldo ES la suma de lo que se cargó
            # a mano, y arranca de cero.
            fuente, fin = "manual", 0.0
        else:
            fuente = None
            sin_datos += 1

        # ⚠️ El ajuste manual se aplica SIEMPRE, venga el saldo de donde venga.
        # Es lo que el banco no informa: en una cuenta real se suma arriba de su
        # extracto, y en una manual es todo el saldo. Mismo criterio que los
        # REGISTROS MANUALES de Tesorería.
        if fin is not None and ajuste:
            fin = round(fin + ajuste, 2)

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
            "movimientos_manuales": man.get("movimientos") or 0,
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
    """Baja FÍSICA — con sus matchers por CASCADE. No hay histórico que huerfanar:
    el desglose se deriva en la lectura, así que borrar un balde simplemente hace
    que sus movimientos pasen a MOVIMIENTOS RESTANTES. Ningún total cambia."""
    filas = _q("SELECT clave, etiqueta FROM bancos.gastos_baldes WHERE clave = %s",
               (clave,))
    if not filas:
        return False
    _exec("DELETE FROM bancos.gastos_baldes WHERE clave = %s", (clave,))
    _audit(email, "balde_baja", filas[0])
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
        "importe": _f(r.get("importe")),
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
    _audit(email, "movimiento_manual_alta", {**filas[0], "cuenta_id": cuenta_id})
    return _manual_publico(filas[0])


def borrar_movimiento_manual(email: str, mov_id: int) -> bool:
    filas = _q("""SELECT id, cuenta_id, fecha, descripcion, importe, tipo
                    FROM bancos.movimientos_manuales WHERE id = %s""", (mov_id,))
    if not filas:
        return False
    _exec("DELETE FROM bancos.movimientos_manuales WHERE id = %s", (mov_id,))
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
