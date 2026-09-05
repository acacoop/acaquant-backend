"""CONTABILIDAD de cuentas propias (Back Office → CONTABILIDAD) — SQL-only.

Resultado MENSUAL por título de una cuenta propia. DOS canales, cada uno
CALCULADO de verdad, y el total es su SUMA:

    resultado_total = TENENCIA + INTERMEDIACIÓN

  · TENENCIA (RxT)   — lo que rindió la posición que estuvo presente en LOS DOS
                       cierres, valuada al precio implícito de cada uno. Es la
                       cadena de la planilla del back office (ver abajo) y viaja
                       ENTERA en la respuesta para que sea auditable paso a paso.
  · INTERMEDIACIÓN   — la SUMATORIA de los boletos del mes: compra NEGATIVA,
                       venta POSITIVA, ir sumando los importes. O sea
                       `ventas − compras`, el neto de caja del período.

⚠️ NO HAY RENTAS. Cupones, dividendos y amortizaciones NO entran al informe: el
proceso del back office no los cuenta acá (regla del user, 2026-09-02). Existió
un tercer canal `RENTAS` leyendo la categoría `acreencia` de
`operaciones.negocio_movimientos`; se sacó ENTERO — no se lee esa tabla, no hay
columna y no suma al total. Media medida sería peor: un canal escondido que
igual mueve el número del mes.

⚠️ LO QUE SE COMPRA Y NO SE VENDE **NO ES RESULTADO DEL MES** (regla del back
office, 2026-09-01). Su valuación final no entra en ningún canal: es el saldo
inicial del mes que viene, y recién ahí genera tenencia.

Hasta 2026-09-01 el total lo definía la IDENTIDAD `ΔValuación + ventas − compras
+ rentas` y los canales se repartían ESE número, con la intermediación como
RESIDUO. Dos fallas, las dos medidas sobre AO29 08/26:

  1. **La ganancia NO REALIZADA de lo comprado en el mes entraba como resultado.**
     La cuenta arrancó sin AO29, compró por 1.788.643.523 y vendió por
     1.789.469.905 (neto **826.382**) y quedó con 955.084 nominales valuados
     en 1.324.988.033 → el informe cantaba
     **1.325.814.415 de intermediación**: la valuación de una posición nueva,
     sumada como si fuera plata ganada.
  2. **Ser el residuo hacía que la intermediación absorbiera cualquier error de
     datos.** Esos 955.084 nominales no los explica ningún boleto; el cuadre lo
     marcaba con un ⚠ y la fila igual sumaba el número al total del mes.

Y el resumen se contradecía con su propio modal: la fila derivaba la
intermediación de la identidad y el modal la costeaba por FIFO — 1.325.814.415
contra 1.171.799, sin árbitro (REGLA #9). Hoy hay **un solo motor**: `libro()`
arma la posición inicial + los boletos y `ledger()` los suma UNA vez; la fila
del resumen y su modal leen de ahí. Congelado por
`tests/unit/test_contabilidad.py`.

LA CADENA DEL RxT ES LA DE LA PLANILLA, columna por columna. Con C/D =
nominales y monto del cierre ANTERIOR y E/F = los del cierre ACTUAL:

    G  no entran en RxT        =  E − C
    H  misma tenencia mantenida=  la posición presente en LOS DOS cierres
    I  monto mes anterior      =  H × (D / C)
    J  monto mes actual        =  H × (F / E)
    K  RxT                     =  J − I
    L  variación del período   =  K / I

H es `min(C, E)`, y los cinco campos viajan en la fila: sin los pasos
intermedios el RxT es un número que hay que creer. Verificado contra la
planilla real (Toronto Trust Balanceado, jun→jul): H 61.481.010,022326 ·
I 241.799.955,418016 · J 246.452.715,294486 · K 4.652.759,8764696 · L 1,92%.

Se intentó afinar H con los SOBREVIVIENTES del lote inicial según FIFO y NO
sirve: si se vende toda la posición inicial y se recompra más (1.000 → 1.200),
del lote inicial no sobrevive nada y la tenencia daba **0** teniendo más
nominales al cierre que al principio. Regla del back office: si hay nominales
al inicio Y al cierre, sí o sí hay resultado por tenencia — y eso no impide que
además haya intermediación. El precio que paga `min`: de los nominales que
cuenta, los comprados a mitad de mes devengan rendimiento de mes completo — la
imprecisión que la planilla acepta a cambio de no tener ceros absurdos.

Fuentes (todo existe, nada nuevo se persiste):
  · Valuaciones  → `portafolio.tenencia` al último día hábil de cada mes (la misma
    foto diaria que usa Tenencia Valorizada). Cash (cartera MONEDAS) queda afuera:
    no es un título. Si el último hábil no tiene snapshot se usa el último día CON
    datos del mes y la respuesta lo dice (`fecha_usada` ≠ `fecha_objetivo`).
  · Compras/ventas → **`operaciones.movimientos_propias`** (cambio de fuente
    2026-09-05, regla del user; antes era `operaciones.operaciones`). Es el feed
    de la cartera PROPIA de la casa, a grano LÍNEA y sin filtros.
    **Una fila del informe = una línea de TÍTULO**: se descartan las líneas cuya
    `unidad` es una moneda, que son la pata de DINERO del mismo comprobante.
    La PLATA se toma de esa pata hermana (el `total` de una línea de título son
    NOMINALES, no pesos — así lo trata `aunesa_negocio.agrupar_boletos`), lo que
    además trae la MONEDA de verdad y evita tener que adivinar el divisor por
    cartera de un bono que cotiza en paridad. La dirección la da la `categoria`
    del feed, con las MISMAS constantes de siempre; las solicitudes de FCI no
    entran a ninguna (ahí muere el doble conteo provisional/final). Lo que no
    mueve posición NO se suma y se CUENTA en `ignorados`.
  · **AJUSTES administrativos** — línea de título que mueve CANTIDAD y no lleva
    plata (canje, split, amortización, rebautizo de especie). Son VÁLIDOS
    (regla del user) y son lo que la fuente anterior NO traía: sin ellos el
    cuadre de nominales fallaba y el título se iba a «sin conciliar» teniendo la
    tenencia toda la razón. Van como dirección PROPIA, jamás como una compra de
    importe 0 — eso ensuciaría el precio promedio con el que se costea lo que
    quedó en cartera.
  · **EXCLUIDOS a mano** — el back office puede sacar un movimiento del
    resultado del mes (`operaciones.contabilidad_excluidos`). Saca la PLATA, no
    el HECHO: sus nominales siguen contando para el cuadre, porque si no,
    tildar una casilla rompería el cuadre del título y la fila entera se caería
    a «sin conciliar» — o sea que sacar un movimiento borraría el título del
    informe, que no es lo que nadie quiere al tildar. Se declara en los totales
    (`excluidos`, `excluido_total`), nunca en silencio.

Boletos en USD se pesifican con el `mep` snapshot del propio boleto (fallback
`get_mep_for_date`), igual que el motor de PnL. Magnitudes en valor absoluto —
los signos crudos de Aunesa no son confiables; la dirección la da la categoría.

Cruce título↔boleto: `operaciones.instrumento` ES la `unidad` de la tenencia
(medido en `operaciones_sql`: 99% del volumen), así que ambos lados pasan por el
MISMO mapping `unidad → clave` del motor de PnL (`pnl_sql._mapas_assets`, vía
`portafolio.assets`: CAFCI para FCI, ticker para el resto) — REGLA #9.

CUADRE por fila: `cuadre_nominales` = nominales_fin − nominales_ini − Δ nominales
de los boletos. Si no da ~0, faltan boletos o hubo un evento corporativo — la
vista lo marca en la fila en vez de mostrar un número sano que no lo es.
"""
from __future__ import annotations

import re
from datetime import date

from api.cache import cached
from api.services._sql import _f, _q

# Dirección por categoría (misma partición que el motor de PnL: _CATS_PAGO /
# _CATS_COBRO_VENTA / _CATS_COBRO_PASIVO).
_CATS_COMPRA = {"compra", "suscripcion_fci"}
_CATS_VENTA = {"venta", "rescate_fci"}
_CATS_TODAS = _CATS_COMPRA | _CATS_VENTA

# ⚠️ Las SOLICITUDES de FCI (`solicitud_suscripcion_fci` / `solicitud_rescate_fci`)
# NO están en ninguno de los dos conjuntos, y es lo que evita el doble conteo:
# Aunesa manda el pedido y la liquidación como movimientos distintos del MISMO
# hecho. La liquidación (`suscripcion_fci` / `rescate_fci`) es la que mueve
# posición; la solicitud se declara en `ignorados` y no suma. Con la fuente
# anterior esto lo resolvía `emparejar_provisional_final` leyendo
# `tipo_operacion` — un campo que `movimientos_propias` no tiene; acá lo resuelve
# la categoría, que es un dato y no una heurística de texto.

# La LÍNEA DE DINERO de un boleto: su `unidad` es una moneda. Todo lo demás es
# una línea de TÍTULO, y esas son las filas del informe (regla del user,
# 2026-09-05). El `total` de una línea de título son NOMINALES, no pesos: la
# plata vive en la línea de dinero del MISMO comprobante.
_MONEDAS = ("ARS", "USD", "USDL", "USDC")

# Movimiento ADMINISTRATIVO: línea de título que mueve CANTIDAD y no tiene plata
# (canje, split, amortización, rebautizo de especie). Es VÁLIDO y es exactamente
# lo que la fuente anterior no traía — por eso el cuadre de nominales fallaba y
# la fila se iba a «sin conciliar» teniendo la tenencia razón. Va como dirección
# propia y NO como una compra de importe 0: si entrara como compra contaminaría
# `px_compra = compras / qty_compras` (plata real dividida por nominales que
# incluyen los ajustes) y el RxT saldría mal sin que nada falle.
_CAT_AJUSTE = "ajuste"

# Redondeo del cuadre de nominales: por debajo de esto es ruido de float, no un
# boleto que falta.
_TOL_NOMINALES = 1e-6


def _mes_anterior(anio: int, mes: int) -> tuple[int, int]:
    return (anio - 1, 12) if mes == 1 else (anio, mes - 1)


def _pesificar(b: dict) -> tuple[float, bool]:
    """(importe_ars, mep_faltante) — regla del motor: ARS crudo; otra moneda ×
    mep snapshot del boleto; sin mep → fallback a la serie por fecha."""
    importe = b.get("importe") or 0.0
    if (b.get("moneda") or "ARS") == "ARS":
        return importe, False
    mep = b.get("mep")
    if not mep:
        from api.services._mep import get_mep_for_date
        mep = get_mep_for_date(b.get("fecha") or "")
    if not mep or mep <= 0:
        return importe, True  # queda en moneda original y la fila se flaguea
    return importe * mep, False


# ── Cálculo PURO (testeable sin base) ────────────────────────────────────────

def calcular_titulos(
    *,
    filas_ini: list[dict],
    filas_fin: list[dict],
    boletos: list[dict],
    unidad_to_match: dict[str, str],
    match_to_display: dict[str, str],
    fecha_ini: str = "",
) -> list[dict]:
    """Una fila por título: TENENCIA + INTERMEDIACIÓN + cuadre.

    Los dos canales salen de la MISMA pasada de FIFO que dibuja el modal
    (`libro`), no de una identidad. Entradas:
      filas_ini/filas_fin: filas de `portafolio.tenencia` (unidad, ticker,
        cartera, cantidad, valuacion) SIN cash (el caller ya filtró MONEDAS).
      boletos: dicts shape negocio_movimientos.
    """
    por_key: dict[str, dict] = {}

    def _fila(key: str) -> dict:
        return por_key.setdefault(key, {
            "titulo": match_to_display.get(key, key), "unidades": [],
            "qty_ini": 0.0, "qty_fin": 0.0, "v_ini": 0.0, "v_fin": 0.0,
            "boletos": [],
        })

    for filas, ql, vl in ((filas_ini, "qty_ini", "v_ini"), (filas_fin, "qty_fin", "v_fin")):
        for r in filas:
            unidad = r.get("unidad") or ""
            key = unidad_to_match.get(unidad, unidad)
            d = _fila(key)
            d[ql] += r.get("cantidad") or 0.0
            d[vl] += r.get("valuacion") or 0.0
            if unidad not in d["unidades"]:
                d["unidades"].append(unidad)

    # Los boletos solo se AGRUPAN acá: sumarlos es trabajo del ledger, y de
    # una sola pasada — la fila del resumen y el modal leen de la misma.
    # También viajan los que no mueven posición (caución/futuros): el modal
    # los muestra y el ledger no los toca.
    for b in boletos:
        key = (b.get("ticker") or "").strip()
        if key:
            _fila(key)["boletos"].append(b)

    def _r2(x: float) -> float:  # plata a 2 decimales, sin −0.0 de ruido float
        return round(x, 2) + 0.0

    out: list[dict] = []
    for key, d in por_key.items():
        qi, qf, vi, vf = d["qty_ini"], d["qty_fin"], d["v_ini"], d["v_fin"]
        px_ini = vi / qi if qi else None   # D/C de la planilla
        px_fin = vf / qf if qf else None   # F/E de la planilla
        _, ag = libro(key=key, qty_ini=qi, v_ini=vi, fecha_ini=fecha_ini,
                      boletos=d["boletos"])
        # ── La CADENA del RxT, tal cual la planilla del back office ────────
        # G  no entran en RxT       = nominales mes actual − mes anterior
        # H  misma tenencia mantenida = la posición presente en LOS DOS cierres
        # I  monto mes anterior     = H × (D/C)   ← H al precio implícito viejo
        # J  monto mes actual       = H × (F/E)   ← H al precio implícito nuevo
        # K  RxT                    = J − I
        # L  variación del período  = K / I
        no_entran_rxt = qf - qi
        tenencia_mantenida = min(qi, qf)
        monto_rxt_ini = tenencia_mantenida * px_ini if px_ini is not None else 0.0
        monto_rxt_fin = tenencia_mantenida * px_fin if px_fin is not None else 0.0
        rxt_mantenida = monto_rxt_fin - monto_rxt_ini
        variacion_rxt = (rxt_mantenida / monto_rxt_ini) if monto_rxt_ini else None

        # ── Los nominales que ENTRARON y quedaron en cartera ───────────────
        # Su resultado es de TENENCIA, no de intermediación (regla del back
        # office): el valor al cierre menos lo que costaron. La valuación del
        # cierre es la foto final y no se toca — ya lo tiene todo implícito.
        # Solo se reclasifica lo que los boletos explican EN NETO
        # (`qty_compras − qty_ventas`), no lo que se compró en bruto: AO29
        # 08/26 compró y vendió 1.255.011 nominales —neto CERO— y cerró con
        # 955.084 que ningún boleto explica; topeando contra las compras
        # brutas se le acreditaba esa posición entera como tenencia nueva y
        # el mes volvía a los 1.325 millones de la fórmula vieja. Ante un
        # descuadre, no se valúa: se marca (`cuadra`) y se deja pasar.
        # ⚠️ DOS netos distintos, y confundirlos INVENTA plata.
        #  · `neto_precio` — solo lo que tiene PRECIO (compras y ventas). Es el
        #    tope de lo que se puede reclasificar entre canales, porque valuar
        #    exige un precio y un ajuste no tiene ninguno. Si un ajuste entrara
        #    acá, excluir una venta a mano dejaba el `costo_salida` cobrándose
        #    sin su ingreso y la fila mostraba una pérdida inventada (medido:
        #    −30.000 en una venta de 33.000 excluida).
        #  · `neto_cuadre` — TODO lo que movió posición, ajustes incluidos. Es
        #    lo único que puede decir si la foto de tenencia está explicada, y
        #    es la mejora concreta de la fuente nueva: la anterior no traía los
        #    movimientos administrativos, así que un canje descuadraba la fila y
        #    la mandaba a «sin conciliar» teniendo la tenencia toda la razón.
        neto_precio = ag["qty_compras"] - ag["qty_ventas"]
        neto_cuadre = neto_precio + ag["qty_ajustes"]
        entraron = min(max(0.0, qf - qi), max(0.0, neto_precio))
        px_compra = (ag["compras"] / ag["qty_compras"]) if ag["qty_compras"] else 0.0
        costo_nuevo = entraron * px_compra
        rxt_nueva = (entraron * px_fin if px_fin is not None else 0.0) - costo_nuevo
        rxt = rxt_mantenida + rxt_nueva

        # ── Los nominales que SE FUERON, a su valor del cierre anterior ────
        # La venta entra por su importe, pero el activo que salió también tiene
        # que salir: si no, vender algo que YA se tenía se cuenta como ganancia
        # entera (AO29 inflaba 2.218.620 = 1.545 × 1.436; una venta SENEBI
        # llegó a mostrar 6.465 millones de PnL). Mismo criterio: solo se
        # costea lo que las VENTAS explican en neto.
        salieron = min(max(0.0, qi - qf), max(0.0, -neto_precio))
        costo_salida = salieron * px_ini if px_ini is not None else 0.0
        # INTERMEDIACIÓN: la sumatoria de los boletos, menos el activo que
        # salió, y sin el costo de lo que quedó en cartera (ese se lo llevó la
        # tenencia). Con estos dos términos, TENENCIA + INTERMEDIACIÓN da la
        # plata real del mes (`v_fin − v_ini + ventas − compras`) en TODOS
        # los casos — quieta, intradía, alta, baja, achicó, agrandó, rotó, y
        # vendió-todo-y-recompró. Congelado por tests.
        rxt = _r2(rxt)
        intermediacion = _r2(ag["intermediacion"] - costo_salida + costo_nuevo)
        residual = qf - qi - neto_cuadre
        out.append({
            "titulo": d["titulo"], "key": key, "unidades": d["unidades"],
            "qty_ini": qi, "qty_fin": qf, "v_ini": _r2(vi), "v_fin": _r2(vf),
            "px_ini": px_ini, "px_fin": px_fin,
            "compras": _r2(ag["compras"]), "ventas": _r2(ag["ventas"]),
            "rxt": rxt, "intermediacion": intermediacion,
            "total": _r2(rxt + intermediacion),
            "estado": ("alta" if qi == 0 and qf != 0 else
                       "baja" if qi != 0 and qf == 0 else
                       "sin_operar" if ag["n_boletos"] == 0 else "operado"),
            "n_boletos": ag["n_boletos"],
            "no_entran_rxt": no_entran_rxt,
            "tenencia_mantenida": tenencia_mantenida,
            "rxt_mantenida": _r2(rxt_mantenida), "rxt_nueva": _r2(rxt_nueva),
            "qty_entraron": entraron, "qty_salieron": salieron,
            "costo_nuevo": _r2(costo_nuevo), "costo_salida": _r2(costo_salida),
            # Ya calculados acá para que la vista no derive NADA: el valor al
            # cierre de lo que entró, y el neto crudo de los boletos.
            "valor_nuevo": _r2(entraron * px_fin if px_fin is not None else 0.0),
            "neto_boletos": _r2(ag["intermediacion"]),
            "monto_rxt_ini": _r2(monto_rxt_ini), "monto_rxt_fin": _r2(monto_rxt_fin),
            "variacion_rxt": variacion_rxt,
            "cuadre_nominales": residual,
            "cuadra": abs(residual) < _TOL_NOMINALES,
            "mep_faltantes": ag["mep_faltantes"],
            # Lo administrativo y lo que una persona sacó a mano viajan en la
            # fila: un total que cambió porque alguien tildó una casilla tiene
            # que poder explicarse SIN abrir el modal.
            "n_ajustes": ag["n_ajustes"],
            "qty_ajustes": ag["qty_ajustes"],
            "excluidos": ag["excluidos"],
            "excluido_total": _r2(ag["excluido_total"]),
        })
    out.sort(key=lambda r: abs(r["total"]), reverse=True)
    return out


def separar(titulos: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """(con_resultado, altas_puras, sin_conciliar).

    SIN CONCILIAR = la fila no cuadra: los nominales del cierre no son los del
    cierre anterior más lo que explican los boletos. **La TENENCIA manda**
    (regla del user, 2026-09-04): un número que sale de boletos que la foto
    no respalda no es resultado, es una PARTIDA SIN CONCILIAR — se muestra
    aparte, con su ⚠ y su residuo, y NO suma al total del mes. Medido antes
    de decidirlo: en 08/26 el residuo lo dominaban el doble conteo del FCI,
    el plazo mal leído y el rebautizo de unidades — ponerle precio (ajuste a
    la foto) habría convertido errores de datos en resultado contable.

    ALTA PURA = no había nominales al cierre anterior y en el mes solo se
    COMPRÓ (sin ventas): su resultado recién entra al RxT del mes que viene.
    Una alta que no cuadra va a sin conciliar, no a altas."""
    con_resultado, altas, sin_conciliar = [], [], []
    for t in titulos:
        if not t["cuadra"]:
            sin_conciliar.append(t)
        elif t["estado"] == "alta" and t["ventas"] == 0:
            altas.append(t)
        else:
            con_resultado.append(t)
    return con_resultado, altas, sin_conciliar


def separar_altas(titulos: list[dict]) -> tuple[list[dict], list[dict]]:
    """Compat: (con_resultado + sin_conciliar, altas). Usar `separar`."""
    con, altas, sin = separar(titulos)
    return con + sin, altas


# ── Lectura SQL ──────────────────────────────────────────────────────────────

def _cierre_mes(id_cuenta: str, anio: int, mes: int) -> dict:
    """Foto de la cuenta al último hábil del mes (sin cash). Si ese día exacto
    no tiene filas, usa el último día CON datos dentro del mes y lo declara."""
    from core.calendario import ultimo_habil_del_mes
    objetivo = ultimo_habil_del_mes(anio, mes)
    sel = ("SELECT unidad, ticker, cartera, cantidad, valuacion "
           "FROM portafolio.tenencia "
           "WHERE id_cuenta = %(c)s AND fecha = %(f)s "
           # `cartera` es NULLABLE y `NULL <> 'MONEDAS'` NO es TRUE: es NULL.
           # Con `<>` toda fila sin cartera se caía en SILENCIO — la tenencia
           # del 31/07 existía y el informe la leía vacía, así que el mes
           # arrancaba en 0 y todo el resultado salía mal sin un solo error.
           # DERIVADOS (futuros) son CONTRATOS, no nominales de títulos: sus
           # boletos no tienen punta y jamás cuadrarían (medido 2026-09-04).
           "AND cartera IS DISTINCT FROM 'MONEDAS' "
           "AND cartera IS DISTINCT FROM 'DERIVADOS'")
    filas = _q(sel, {"c": id_cuenta, "f": objetivo})
    usada = objetivo
    if not filas:
        alt = _q("SELECT max(fecha) AS f FROM portafolio.tenencia "
                 "WHERE id_cuenta = %(c)s AND fecha >= %(ini)s AND fecha <= %(fin)s",
                 {"c": id_cuenta, "ini": date(anio, mes, 1), "fin": objetivo})[0]["f"]
        if alt:
            usada = alt
            filas = _q(sel, {"c": id_cuenta, "f": alt})
    for r in filas:
        r["cantidad"], r["valuacion"] = _f(r["cantidad"]), _f(r["valuacion"])
    return {"fecha_objetivo": objetivo.isoformat(),
            "fecha_usada": usada.isoformat() if filas else None,
            "filas": filas}


_RE_HORAS = re.compile(r"(\d+)\s*h")
_RE_DIAS = re.compile(r"(\d+)\s*d")


def plazo_habiles(condiciones: str | None) -> int:
    """Días HÁBILES entre concertación y liquidación, leídos de `condiciones`.
    Aunesa lo trae en el texto: «24hs» → 1, «48hs» → 2, «3 días» → 3,
    «Contado Inmediato» / «ARS Inm» → 0. Hasta 2026-09-04 solo se entendía
    «24» y todo lo demás caía en contado inmediato: medido en las cuentas
    propias, ~7% de los boletos traen 1/3/4/5/7 días o 48hs. Lo que no se
    entiende se trata como contado (el comportamiento de siempre) y el diag
    de conciliación lista las grafías para verlo."""
    t = (condiciones or "").lower()
    if (m := _RE_HORAS.search(t)):
        return max(1, round(int(m.group(1)) / 24))
    if (m := _RE_DIAS.search(t)):
        return int(m.group(1))
    return 0


def sumar_habiles(d: date, n: int) -> date:
    from core.calendario import proximo_habil
    for _ in range(max(0, n)):
        d = proximo_habil(d)
    return d


def fecha_liquidacion(fecha: str, condiciones: str | None) -> date:
    """Concertación + plazo en hábiles = el día que la foto de tenencia lo ve."""
    return sumar_habiles(date.fromisoformat(fecha), plazo_habiles(condiciones))


def pertenece_al_mes(fecha: str, condiciones: str | None, *, mes: str) -> bool:
    """¿El boleto es de este MES CONTABLE? La tenencia es una foto LIQUIDADA y
    `operaciones.operaciones` registra por CONCERTACIÓN (detección del user,
    2026-09-01): un boleto del último hábil del mes anterior en 24hs liquida el
    1º hábil de este mes — la foto del cierre anterior NO lo tiene, así que
    cuenta ACÁ. Simétrico en la otra punta. Regla única: el boleto pertenece al
    mes en que LIQUIDA (concertación + plazo real de `condiciones`)."""
    return fecha_liquidacion(fecha, condiciones).strftime("%Y-%m") == mes


def _clave(unidad: str, ticker: str | None, u2m: dict[str, str]) -> str:
    """Título de la fila. DOS claves con fallback, a propósito.

    `unidad_to_match` es el mapping del catálogo (`portafolio.assets`) y es el
    que usa el resto del sistema, así que manda. Pero el motor de PnL joinea
    ESTOS MISMOS boletos por el `ticker` parseado del texto
    (`pnl_sql._SEL_BOLETOS`: `WHERE ticker IS NOT NULL`), que vive en el MISMO
    espacio de claves. Usar los dos hace que una unidad que el catálogo todavía
    no conoce —un rebautizo de especie, un asset recién dado de alta— caiga en
    su ticker en vez de quedar huérfana: una fila que no cruza no falla, se va a
    «sin conciliar» y el mes da de menos con la pantalla en verde."""
    return u2m.get(unidad) or (ticker or "").strip() or unidad


def _es_dinero(unidad: str | None) -> bool:
    return (unidad or "").strip().upper() in _MONEDAS


def _clasificar(cat: str, cantidad: float | None, importe: float | None) -> str | None:
    """Qué hace esta línea de título con la posición y con la plata.

    El orden importa y cada rama es una decisión de negocio, no un caso borde:

    1. **compra / venta** — la categoría del feed lo dice. Manda sobre todo.
    2. **sin cantidad → nada.** No mueve posición: se cuenta en `ignorados`.
    3. **sin plata → AJUSTE.** Mueve nominales y no hay importe: canje, split,
       amortización, rebautizo de especie. Declarado VÁLIDO por el user, y es lo
       que la fuente anterior no traía — sin estas filas el cuadre fallaba y el
       título se iba a «sin conciliar» teniendo la tenencia razón.
    4. **categoría desconocida con las dos cosas → por el SIGNO.** Un `TRD` es
       una operación de trading genérica cuyo texto no dice «Compra» ni «Venta»;
       con el importe ya en signo cliente, negativo (pagamos) es compra y
       positivo (cobramos) es venta. Es LA MISMA regla que aplica
       `aunesa_negocio.agrupar_boletos` para el mismo caso — sin esto un TRD
       caía en `ignorados` y el motor de PnL tuvo ese bug con YFCOO.
    5. **categoría conocida que NO es compra/venta, pero mueve nominales →
       AJUSTE.** Una amortización (`acreencia`) baja los nominales de verdad: la
       foto de tenencia lo va a mostrar. Sus nominales tienen que contar para el
       cuadre y su plata NO tiene que entrar al informe — que es exactamente lo
       que hace un ajuste, y lo que dice la regla «no hay rentas».
    """
    if cat in _CATS_TODAS:
        return cat
    if not cantidad:
        return None
    if not importe:
        return _CAT_AJUSTE
    if cat in ("", "otro"):
        return "compra" if importe < 0 else "venta"
    return _CAT_AJUSTE


def _boletos_mes(id_cuenta: str, mes_str: str | None, u2m: dict[str, str]) -> list[dict]:
    """Boletos del MES CONTABLE desde **`operaciones.movimientos_propias`**.

    Cambio de fuente 2026-09-05 (regla del user): las compras/ventas ya no salen
    de `operaciones.operaciones` sino de los movimientos de la cartera PROPIA,
    que es el feed de la casa y trae cosas que la otra tabla no tiene.

    UNA FILA DEL INFORME = UNA LÍNEA DE TÍTULO. Se descartan las líneas cuya
    `unidad` es una moneda (ARS/USD/USDL/USDC): esas son la PATA DE DINERO del
    mismo comprobante, no un movimiento de posición. Contarlas sería sumar dos
    veces el mismo hecho.

    ⚠️ **LA PLATA NO ESTÁ EN LA LÍNEA DE TÍTULO** — o al menos no se puede
    depender de eso. En el consolidador de comitentes (`aunesa_negocio.
    agrupar_boletos`) el `total` de una línea de título es la CANTIDAD y el de
    la línea de dinero es el IMPORTE. Por eso el importe se toma de la HERMANA
    de dinero del mismo comprobante y solo se cae al de la propia línea si no
    hay hermana. Sale igual esté como esté el dato: si la línea de título ya
    trae plata, la hermana trae la misma; si trae nominales, la hermana es la
    única que tiene la plata. Además la hermana trae la MONEDA de verdad, que
    `cantidad × precio` no puede decir (y que decidiría si hay que dividir por
    100 en un bono que cotiza en paridad — el divisor por cartera, que así no
    hace falta adivinar).

    TRES DIRECCIONES, no dos:
      · compra / venta  → `categoria` del catálogo del feed, las MISMAS
        constantes que ya usaba el informe.
      · **ajuste**      → línea de título que mueve CANTIDAD y no tiene plata
        (canje, split, amortización, rebautizo). Declarado VÁLIDO por el user, y
        es lo que la fuente anterior no traía: sin estos movimientos el cuadre
        de nominales fallaba y la fila se iba a «sin conciliar» teniendo la
        tenencia razón.
      · sin dirección   → categoría que no mueve posición (caución, `otro`):
        viaja con `categoria=None`, no suma, y se cuenta en `ignorados`.

    Las SOLICITUDES de FCI no entran a ningún conjunto: ver el comentario de
    `_CATS_COMPRA`. Ahí muere el doble conteo que antes resolvía el emparejador.

    El corte de mes sigue siendo por LIQUIDACIÓN (`pertenece_al_mes`), leyendo
    el plazo de la columna `plazo` del propio movimiento.
    """
    p: dict = {"c": id_cuenta, "mon": list(_MONEDAS)}
    if mes_str:
        from datetime import timedelta

        from core.calendario import ultimo_habil_del_mes
        anio, m = int(mes_str[:4]), int(mes_str[5:7])
        a0, m0 = _mes_anterior(anio, m)
        # Ventana ampliada hacia atrás: un boleto concertado el último hábil del
        # mes anterior en 24hs LIQUIDA acá y tiene que entrar. El corte fino lo
        # hace `pertenece_al_mes`.
        desde = (ultimo_habil_del_mes(a0, m0) - timedelta(days=10)).isoformat()
        hasta = (ultimo_habil_del_mes(anio, m) + timedelta(days=10)).isoformat()
        w_mes = "AND t.fecha >= %(desde)s AND t.fecha <= %(hasta)s "
        p |= {"desde": desde, "hasta": hasta}
    else:
        w_mes = ""
    filas = _q(
        "SELECT to_char(t.fecha,'YYYY-MM-DD') AS fecha, t.id_linea, t.ocurrencia, "
        "       t.unidad, t.ticker, t.categoria, t.op, t.cantidad, t.precio, "
        "       t.importe, t.moneda, t.mep, t.comprobante, t.plazo, t.informacion, "
        # La PLATA y su MONEDA: de la línea de dinero del mismo comprobante.
        "       d.importe AS importe_dinero, d.moneda AS moneda_dinero, d.mep AS mep_dinero, "
        "       (x.id_linea IS NOT NULL) AS excluido, x.motivo AS excluido_motivo "
        "  FROM operaciones.movimientos_propias t "
        "  LEFT JOIN LATERAL ("
        "        SELECT sum(m.importe) AS importe, max(m.moneda) AS moneda, max(m.mep) AS mep "
        "          FROM operaciones.movimientos_propias m "
        "         WHERE m.fecha = t.fecha AND m.comprobante = t.comprobante "
        "           AND m.anulado_en IS NULL "
        "           AND upper(btrim(COALESCE(m.unidad,''))) = ANY(%(mon)s)"
        "       ) d ON true "
        "  LEFT JOIN operaciones.contabilidad_excluidos x "
        "         ON x.fecha = t.fecha AND x.id_linea = t.id_linea "
        "        AND x.ocurrencia = t.ocurrencia "
        f" WHERE t.id_cuenta = %(c)s {w_mes}"
        "   AND t.anulado_en IS NULL "
        "   AND upper(btrim(COALESCE(t.unidad,''))) <> ALL(%(mon)s) "
        " ORDER BY t.fecha, t.comprobante, t.ocurrencia", p)
    if mes_str:
        filas = [r for r in filas
                 if pertenece_al_mes(r["fecha"], r.get("plazo"), mes=mes_str)]
    boletos: list[dict] = []
    for r in filas:
        cat = (r.get("categoria") or "").strip()
        importe = _f(r.get("importe_dinero"))
        moneda = r.get("moneda_dinero") or r.get("moneda")
        mep = _f(r.get("mep_dinero")) or _f(r.get("mep"))
        if importe is None:
            # Sin hermana de dinero: o es administrativo (no hay plata que
            # buscar) o el feed la trae en la propia línea. Se usa la que haya.
            importe, moneda, mep = _f(r.get("importe")), r.get("moneda"), _f(r.get("mep"))
        cantidad = _f(r.get("cantidad"))
        if cantidad is None:
            # El parser saca `cantidad` del TEXTO del boleto y no siempre puede.
            # Los NOMINALES de la línea de título son su propio `importe`
            # (= −total, signo cliente) — así lo trata `agrupar_boletos`.
            cantidad = _f(r.get("importe"))
        categoria = _clasificar(cat, cantidad, importe)
        boletos.append({
            "fecha": r["fecha"], "categoria": categoria,
            "op": r.get("op") or r.get("informacion"),
            "ticker": _clave(r.get("unidad") or "", r.get("ticker"), u2m),
            "unidad": r.get("unidad"),
            "cantidad": cantidad, "importe": importe,
            "moneda": moneda, "mep": mep,
            "comprobante": r.get("comprobante"), "condiciones": r.get("plazo"),
            # La identidad de la LÍNEA: es lo que el front manda para excluirla.
            "id_linea": r.get("id_linea"), "ocurrencia": r.get("ocurrencia"),
            "excluido": bool(r.get("excluido")),
            "excluido_motivo": r.get("excluido_motivo"),
            "informacion": r.get("informacion"),
        })
    return boletos


@cached(ttl=300)
def resumen(*, id_cuenta: str, mes: str) -> dict:
    """El informe del mes: una fila por título + totales (sumados ACÁ, no en el
    navegador). `mes` = "YYYY-MM"."""
    from api.services.pnl_sql import _mapas_assets
    anio, m = int(mes[:4]), int(mes[5:7])
    a0, m0 = _mes_anterior(anio, m)
    ini = _cierre_mes(id_cuenta, a0, m0)
    fin = _cierre_mes(id_cuenta, anio, m)
    mapas = _mapas_assets()
    boletos = _boletos_mes(id_cuenta, mes, mapas["unidad_to_match"])
    todos = calcular_titulos(
        filas_ini=ini["filas"], filas_fin=fin["filas"], boletos=boletos,
        unidad_to_match=mapas["unidad_to_match"],
        match_to_display=mapas["match_to_display"],
        fecha_ini=ini["fecha_usada"] or ini["fecha_objetivo"])
    # Las ALTAS PURAS (comprado para dejar en cartera) no son resultado de ESTE
    # mes: van en su bloque aparte y NO suman a los totales del informe.
    titulos, altas, sin_conciliar = separar(todos)
    tot = {k: round(sum(t[k] for t in titulos), 2)
           for k in ("v_ini", "v_fin", "compras", "ventas",
                     "rxt", "intermediacion", "total")}
    # Lo que la tenencia no respalda se declara, no se suma.
    tot["descuadres"] = len(sin_conciliar)
    tot["sin_conciliar_total"] = round(sum(t["total"] for t in sin_conciliar), 2)
    tot["mep_faltantes"] = sum(t["mep_faltantes"] for t in todos)
    # Lo que una PERSONA sacó del informe y lo que movió sin plata: se DECLARA.
    # Un total que cambió porque alguien tildó una casilla tiene que poder
    # explicarse desde la barra, sin abrir un modal ni comparar con el mes pasado.
    tot["excluidos"] = sum(t["excluidos"] for t in todos)
    tot["excluido_total"] = round(sum(t["excluido_total"] for t in todos), 2)
    tot["ajustes"] = sum(t["n_ajustes"] for t in todos)
    # Exclusiones que apuntan a una línea que ya no existe (Aunesa la corrigió y
    # cambió su hash). No aplican, y en vez de desaparecer se cuentan.
    tot["excluidos_huerfanos"] = _excluidos_huerfanos(id_cuenta, mes)
    # Boletos que NO mueven posición (cauciones, futuros, `otro` del catálogo):
    # se declaran en vez de desaparecer — si el enrich usara otra grafía para
    # compra/venta, TODO caería acá y se vería en la pantalla.
    ignorados: dict[str, int] = {}
    for b in boletos:
        if b.get("categoria") is None:
            et = (b.get("op") or "sin tipo").strip()
            ignorados[et] = ignorados.get(et, 0) + 1
    return {"id_cuenta": id_cuenta, "mes": mes, "ignorados": ignorados,
            "cierre_ini": {"fecha_objetivo": ini["fecha_objetivo"],
                           "fecha_usada": ini["fecha_usada"]},
            "cierre_fin": {"fecha_objetivo": fin["fecha_objetivo"],
                           "fecha_usada": fin["fecha_usada"]},
            "titulos": titulos, "altas": altas, "sin_conciliar": sin_conciliar,
            "totales": tot,
            "n_boletos": sum(1 for b in boletos if b.get("categoria"))}


def ledger(boletos: list[dict]) -> dict:
    """Recorre los boletos EN ORDEN y anota en cada fila `nominales_acum` (la
    posición corrida) y `pnl_acum` (la PLATA corrida).

    LA PLATA ES UNA SUMATORIA SIMPLE — definición del back office 2026-09-01:
    «compra negativo, venta positivo, ir sumando los importes». La fila
    sintética `saldo_inicial` mueve la POSICIÓN y no la plata: no es un boleto,
    no hay importe que sumar.

    Antes acá vivía un costeo FIFO por lotes (venta − costo del lote más viejo).
    Se sacó porque no es lo que el back office llama intermediación: la
    intermediación es el neto de caja de los boletos del mes, y punto.

    Devuelve TODOS los agregados que necesita la fila del resumen, así el
    número de la fila y el que muestra el modal salen de LA MISMA pasada
    (REGLA #9) — no de dos cuentas parecidas que nadie compara.
    """
    nominales = acum = 0.0
    ag = {"compras": 0.0, "ventas": 0.0,
          "qty_compras": 0.0, "qty_ventas": 0.0,
          "qty_ajustes": 0.0, "n_ajustes": 0,
          "n_boletos": 0, "mep_faltantes": 0,
          "excluidos": 0, "excluido_total": 0.0}
    for b in boletos:
        cat = b.get("categoria")
        q = abs(b.get("cantidad") or 0.0)
        imp = abs(b.get("importe_ars") or 0.0)
        if b.get("excluido") and cat in _CATS_TODAS:
            # EXCLUIDO A MANO: saca la PLATA, no el HECHO. Se comporta como un
            # ajuste — mueve la posición (así el cuadre del título sigue dando
            # y la fila NO se cae a «sin conciliar» por tildar una casilla) y no
            # suma un peso. Tampoco entra en compras/ventas: si entrara, sus
            # nominales diluirían `px_compra` con plata que ya no está.
            nominales += q if cat in _CATS_COMPRA else -q
            ag["qty_ajustes"] += q if cat in _CATS_COMPRA else -q
            ag["excluidos"] += 1
            ag["excluido_total"] += imp if cat in _CATS_VENTA else -imp
            b["nominales_acum"] = round(nominales, 2)
            b["pnl_acum"] = round(acum, 2)
            continue
        if cat == "saldo_inicial":
            nominales += q                      # posición, NO plata
        elif cat == _CAT_AJUSTE:
            # ADMINISTRATIVO: mueve la posición y no hay plata que sumar. El
            # signo lo trae la cantidad (Aunesa lo manda con signo cliente), así
            # que acá NO se toma valor absoluto — un canje que resta nominales
            # tiene que restar. Tampoco entra en compras/ventas: si entrara,
            # ensuciaría el precio promedio con el que se costea lo que quedó.
            nominales += b.get("cantidad") or 0.0
            ag["qty_ajustes"] += b.get("cantidad") or 0.0
            ag["n_ajustes"] += 1
        elif cat in _CATS_COMPRA:
            acum -= imp                         # la compra RESTA
            nominales += q
            ag["compras"] += imp
            ag["qty_compras"] += q
        elif cat in _CATS_VENTA:
            acum += imp                         # la venta SUMA
            nominales -= q
            ag["ventas"] += imp
            ag["qty_ventas"] += q
        if cat in _CATS_TODAS:
            ag["n_boletos"] += 1
            ag["mep_faltantes"] += 1 if b.get("sin_mep") else 0
        b["nominales_acum"] = round(nominales, 2)
        b["pnl_acum"] = round(acum, 2)
    # INTERMEDIACIÓN = ventas − compras. El neto de caja de los boletos.
    ag["intermediacion"] = ag["ventas"] - ag["compras"]
    return ag


def libro(*, key: str, qty_ini: float, v_ini: float, fecha_ini: str,
          boletos: list[dict]) -> tuple[list[dict], dict]:
    """EL LIBRO del título en el mes: la POSICIÓN INICIAL como primer lote FIFO
    + los boletos del período en orden de fecha, cada fila con sus acumulados.

    **Es el único motor del módulo.** El resumen saca de acá sus dos canales y
    el modal dibuja estas mismas filas, así la fila y su detalle no pueden dar
    números distintos (REGLA #9) — que es exactamente lo que pasaba hasta
    2026-09-01: 1.325.814.415 en la fila contra 1.171.799 en el modal.
    """
    filas = sorted(boletos, key=lambda b: b.get("fecha") or "")
    for b in filas:
        if "importe_ars" not in b:
            b["importe_ars"], b["sin_mep"] = _pesificar(b)
        cat = b.get("categoria")
        b["direccion"] = ("compra" if cat in _CATS_COMPRA else
                          "venta" if cat in _CATS_VENTA else
                          "otro")
    if qty_ini or v_ini:
        filas.insert(0, {
            "fecha": fecha_ini, "categoria": "saldo_inicial",
            "op": "POSICIÓN INICIAL (cierre anterior)", "ticker": key,
            "cantidad": qty_ini, "importe": None, "moneda": None, "mep": None,
            "comprobante": "—", "importe_ars": v_ini, "sin_mep": False,
            "direccion": "otro",
        })
    return filas, ledger(filas)


def detalle(*, id_cuenta: str, mes: str, key: str) -> dict:
    """El LIBRO del título en el MES elegido: POSICIÓN INICIAL (nominales y
    valuación del cierre anterior, primer lote FIFO) + los boletos del mes, con
    nominales y PnL acumulados. Es la MISMA pasada que calcula la fila del
    resumen — `intermediacion` de acá == la columna de allá, por construcción."""
    from api.services.pnl_sql import _mapas_assets
    u2m = _mapas_assets()["unidad_to_match"]
    anio, m = int(mes[:4]), int(mes[5:7])
    a0, m0 = _mes_anterior(anio, m)
    ini = _cierre_mes(id_cuenta, a0, m0)

    def _key(r: dict) -> str:
        return u2m.get(r.get("unidad") or "", r.get("unidad"))

    propias = [r for r in ini["filas"] if _key(r) == key]
    qty_ini = sum((r.get("cantidad") or 0.0) for r in propias)
    v_ini = sum((r.get("valuacion") or 0.0) for r in propias)
    boletos = [b for b in _boletos_mes(id_cuenta, mes, u2m)
               if (b.get("ticker") or "").strip() == key]
    filas, ag = libro(key=key, qty_ini=qty_ini, v_ini=v_ini,
                      fecha_ini=ini["fecha_usada"] or ini["fecha_objetivo"],
                      boletos=boletos)
    return {"id_cuenta": id_cuenta, "mes": mes, "key": key, "boletos": filas,
            "intermediacion": round(ag["intermediacion"], 2),
            # Para que el modal pueda tildar/destildar sin derivar nada: cada
            # boleto ya viaja con su `id_linea`/`ocurrencia` y su estado.
            "excluidos": ag["excluidos"], "n_ajustes": ag["n_ajustes"]}


# ── Las CUENTAS del proceso (ABM acotado a movimientos_propias) ─────────────

def cuentas() -> list[dict]:
    """Las cuentas que el equipo eligió para el proceso. Sigue siendo un ABM: la
    LISTA la elige la mesa, no se deriva sola — hay cuentas con movimientos
    propios que no son de este informe."""
    filas = _q("SELECT id_cuenta, etiqueta, agregada_por, agregada_en "
               "FROM operaciones.contabilidad_cuentas ORDER BY id_cuenta")
    for r in filas:
        r["agregada_en"] = r["agregada_en"].isoformat() if r["agregada_en"] else None
    return filas


def _tiene_movimientos(id_cuenta: str) -> bool:
    r = _q("SELECT 1 FROM operaciones.movimientos_propias "
           " WHERE id_cuenta = %(c)s AND anulado_en IS NULL LIMIT 1", {"c": id_cuenta})
    return bool(r)


def cuentas_elegibles() -> list[dict]:
    """El universo del que se puede elegir: las que TIENEN movimientos propios,
    con cuántos y desde cuándo. Es lo que el ABM ofrece para no tener que
    tipear un id a ciegas."""
    return _q(
        "SELECT id_cuenta, max(cuenta) AS cuenta, count(*) AS movimientos, "
        "       to_char(min(fecha),'YYYY-MM-DD') AS desde, "
        "       to_char(max(fecha),'YYYY-MM-DD') AS hasta "
        "  FROM operaciones.movimientos_propias "
        " WHERE anulado_en IS NULL AND id_cuenta IS NOT NULL "
        " GROUP BY id_cuenta ORDER BY count(*) DESC")


def agregar_cuenta(actor: str, id_cuenta: str, etiqueta: str | None) -> dict:
    """Suma una cuenta al proceso (o le cambia la etiqueta si ya estaba).

    ⚠️ **ACOTADO A `operaciones.movimientos_propias` (2026-09-05, regla del
    user).** Antes esto era texto libre: aceptaba cualquier string sin validar
    contra nada, así que se podía sumar una cuenta que no tiene un solo
    movimiento propio y el informe salía VACÍO sin decir por qué — un error de
    tipeo se veía igual que un mes sin actividad. Ahora el id tiene que existir
    en el feed de la cartera propia, que es de donde sale el informe: si no
    está, no se puede elegir, y el mensaje dice exactamente eso."""
    id_cuenta = (id_cuenta or "").strip()
    if not id_cuenta:
        return {"ok": False, "error": "id_cuenta vacío"}
    if not _tiene_movimientos(id_cuenta):
        return {"ok": False, "error": (
            f"La cuenta [{id_cuenta}] no tiene movimientos en operaciones.movimientos_propias. "
            "El informe sale de ahí, así que agregarla mostraría un informe vacío. "
            "Solo se pueden elegir cuentas con movimientos propios.")}
    if not etiqueta:
        # El nombre visible sale del MISMO feed que el informe. Respaldo: la
        # tenencia (que es de donde salía antes y puede tener la cuenta igual).
        f = _q("SELECT max(cuenta) AS c FROM operaciones.movimientos_propias "
               " WHERE id_cuenta = %(c)s AND anulado_en IS NULL", {"c": id_cuenta})
        etiqueta = (f[0]["c"] if f else None) or None
    if not etiqueta:
        f = _q("SELECT max(cuenta) AS c FROM portafolio.tenencia "
               "WHERE id_cuenta = %(c)s", {"c": id_cuenta})
        etiqueta = (f[0]["c"] if f else None) or id_cuenta
    _q("INSERT INTO operaciones.contabilidad_cuentas "
       "(id_cuenta, etiqueta, agregada_por) VALUES (%(c)s, %(e)s, %(a)s) "
       "ON CONFLICT (id_cuenta) DO UPDATE SET etiqueta = EXCLUDED.etiqueta "
       "RETURNING id_cuenta", {"c": id_cuenta, "e": etiqueta, "a": actor})
    return {"ok": True, "id_cuenta": id_cuenta, "etiqueta": etiqueta}


def borrar_cuenta(actor: str, id_cuenta: str) -> dict:
    filas = _q("DELETE FROM operaciones.contabilidad_cuentas "
               "WHERE id_cuenta = %(c)s RETURNING id_cuenta", {"c": id_cuenta})
    return {"ok": bool(filas)}


# ── Movimientos EXCLUIDOS a mano ────────────────────────────────────────────

def _excluidos_huerfanos(id_cuenta: str, mes: str) -> int:
    """Exclusiones que ya no apuntan a ninguna línea viva. `movimientos_propias`
    se reconcilia cada media hora y una línea corregida cambia de `id_linea`: la
    exclusión queda apuntando a algo que no existe y deja de aplicar. Se CUENTA
    para que no desaparezca en silencio — si alguien sacó un movimiento y el
    movimiento volvió con otro hash, el informe tiene que poder decirlo."""
    r = _q(
        "SELECT count(*) AS n FROM operaciones.contabilidad_excluidos x "
        " WHERE x.id_cuenta = %(c)s AND to_char(x.fecha,'YYYY-MM') = %(mes)s "
        "   AND NOT EXISTS (SELECT 1 FROM operaciones.movimientos_propias m "
        "                    WHERE m.fecha = x.fecha AND m.id_linea = x.id_linea "
        "                      AND m.ocurrencia = x.ocurrencia AND m.anulado_en IS NULL)",
        {"c": id_cuenta, "mes": mes})
    return int(r[0]["n"]) if r else 0


def excluir(actor: str, *, id_cuenta: str, fecha: str, id_linea: str,
            ocurrencia: int = 1, motivo: str | None = None) -> dict:
    """Saca un movimiento del resultado del mes. **La plata, no el hecho**: sus
    nominales siguen contando para el cuadre (ver el comentario de la tabla en
    `sql/schema.sql`). Idempotente: volver a excluir lo mismo actualiza el
    motivo."""
    filas = _q(
        "INSERT INTO operaciones.contabilidad_excluidos "
        "(fecha, id_linea, ocurrencia, id_cuenta, motivo, excluido_por) "
        "VALUES (%(f)s, %(l)s, %(o)s, %(c)s, %(m)s, %(a)s) "
        "ON CONFLICT (fecha, id_linea, ocurrencia) DO UPDATE SET "
        "motivo = EXCLUDED.motivo, excluido_por = EXCLUDED.excluido_por, "
        "excluido_en = now() RETURNING id_linea",
        {"f": fecha, "l": id_linea, "o": int(ocurrencia), "c": id_cuenta,
         "m": (motivo or "").strip() or None, "a": actor})
    # El informe está cacheado 300s: sin invalidar, tildar la casilla no movía
    # el número hasta cinco minutos después y la pantalla se veía rota.
    from api.cache import invalidate
    invalidate("resumen")
    return {"ok": bool(filas), "id_linea": id_linea, "excluido": True}


def incluir(actor: str, *, fecha: str, id_linea: str, ocurrencia: int = 1) -> dict:
    """Vuelve a contabilizar un movimiento excluido."""
    filas = _q(
        "DELETE FROM operaciones.contabilidad_excluidos "
        " WHERE fecha = %(f)s AND id_linea = %(l)s AND ocurrencia = %(o)s "
        " RETURNING id_linea", {"f": fecha, "l": id_linea, "o": int(ocurrencia)})
    # El informe está cacheado 300s: sin invalidar, tildar la casilla no movía
    # el número hasta cinco minutos después y la pantalla se veía rota.
    from api.cache import invalidate
    invalidate("resumen")
    return {"ok": bool(filas), "id_linea": id_linea, "excluido": False}


def excluidos(id_cuenta: str, mes: str) -> list[dict]:
    """Lo que hay sacado del mes, con quién y cuándo. Es el libro de la decisión:
    sin esto, el mes que viene nadie puede explicar por qué el informe no da lo
    mismo que los boletos."""
    filas = _q(
        "SELECT to_char(x.fecha,'YYYY-MM-DD') AS fecha, x.id_linea, x.ocurrencia, "
        "       x.motivo, x.excluido_por, x.excluido_en, "
        "       m.informacion, m.comprobante, m.unidad, m.importe, m.cantidad "
        "  FROM operaciones.contabilidad_excluidos x "
        "  LEFT JOIN operaciones.movimientos_propias m "
        "         ON m.fecha = x.fecha AND m.id_linea = x.id_linea "
        "        AND m.ocurrencia = x.ocurrencia "
        " WHERE x.id_cuenta = %(c)s AND to_char(x.fecha,'YYYY-MM') = %(mes)s "
        " ORDER BY x.fecha, x.excluido_en", {"c": id_cuenta, "mes": mes})
    for r in filas:
        r["excluido_en"] = r["excluido_en"].isoformat() if r["excluido_en"] else None
        r["importe"] = _f(r["importe"])
        r["cantidad"] = _f(r["cantidad"])
        # Sin fila viva, la exclusión no aplica — y la vista tiene que decirlo.
        r["vive"] = r["comprobante"] is not None
    return filas
