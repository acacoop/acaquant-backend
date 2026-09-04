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
  · Compras/ventas → **`operaciones.operaciones`** (decisión del user 2026-09-01:
    `negocio_movimientos` no traería las cuentas propias, y el precio por boleto
    no hace falta — con los aranceles AFUERA del proceso, el `bruto` ES la plata
    de la pata). Filtros canónicos de esa tabla: sin anulados, `etapa` ≠
    solicitud, sin cierres de caución. La dirección la da el enrich `operacion`
    del catálogo `tipos_operacion` (compra/venta; suscripción/rescate de FCI
    cuentan como compra/venta). Lo que no mueve posición (cauciones, futuros,
    `otro`) NO se suma y se CUENTA en `ignorados` — si el catálogo usara otras
    grafías se vería ahí, no fallaría en silencio.

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

from datetime import date

from api.cache import cached
from api.services._sql import _f, _q

# Dirección por categoría (misma partición que el motor de PnL: _CATS_PAGO /
# _CATS_COBRO_VENTA / _CATS_COBRO_PASIVO).
_CATS_COMPRA = {"compra", "suscripcion_fci"}
_CATS_VENTA = {"venta", "rescate_fci"}
_CATS_TODAS = _CATS_COMPRA | _CATS_VENTA

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
        neto_boletos = ag["qty_compras"] - ag["qty_ventas"]
        entraron = min(max(0.0, qf - qi), max(0.0, neto_boletos))
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
        salieron = min(max(0.0, qi - qf), max(0.0, -neto_boletos))
        costo_salida = salieron * px_ini if px_ini is not None else 0.0
        # INTERMEDIACIÓN: la sumatoria de los boletos, menos el activo que
        # salió, y sin el costo de lo que quedó en cartera (ese se lo llevó la
        # tenencia). Con estos dos términos, TENENCIA + INTERMEDIACIÓN da la
        # plata real del mes (`v_fin − v_ini + ventas − compras`) en TODOS
        # los casos — quieta, intradía, alta, baja, achicó, agrandó, rotó, y
        # vendió-todo-y-recompró. Congelado por tests.
        rxt = _r2(rxt)
        intermediacion = _r2(ag["intermediacion"] - costo_salida + costo_nuevo)
        residual = qf - qi - ag["qty_compras"] + ag["qty_ventas"]
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
        })
    out.sort(key=lambda r: abs(r["total"]), reverse=True)
    return out


def separar_altas(titulos: list[dict]) -> tuple[list[dict], list[dict]]:
    """(con_resultado, altas_puras). ALTA PURA = no había nominales al cierre
    anterior y en el mes solo se COMPRÓ (sin ventas): se compró para
    dejar en cartera, y por definición del proceso su resultado recién entra al
    RxT del mes que viene — mostrarla entre los resultados es ruido. Una alta
    que además vendió SÍ tiene resultado del mes y se queda."""
    con_resultado, altas = [], []
    for t in titulos:
        if t["estado"] == "alta" and t["ventas"] == 0:
            altas.append(t)
        else:
            con_resultado.append(t)
    return con_resultado, altas


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
           "AND cartera IS DISTINCT FROM 'MONEDAS'")
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


def _direccion(operacion: str | None, tipo_operacion: str | None = None) -> str | None:
    """Punta del boleto → compra / venta / None (no mueve posición de títulos).

    Primero manda el enrich `operacion` del catálogo (suscripción/rescate de FCI
    cuentan como compra/venta). Si el catálogo no define la punta (`otro`/vacío),
    RESPALDO: se lee del descriptor de Aunesa (`tipo_operacion`), que la trae en
    el texto («SENEBI Contado - Venta», «Concurrencia Contado - Venta»). Caso
    real 2026-09-01: la venta SENEBI del 100% de TTCBO venía sin punta del
    catálogo → VENTAS daba 0, el cuadre chillaba y la intermediación salía toda
    negativa teniendo el boleto A LA VISTA. Guarda: cauciones/futuros/opciones
    jamás — mueven plata, no nominales de títulos."""
    op = (operacion or "").strip().lower()
    if op == "compra" or "suscri" in op or "licita" in op:
        return "compra"
    if op == "venta" or "rescate" in op:
        return "venta"
    t = (tipo_operacion or "").strip().lower()
    if "cauci" in t or "futuro" in t or "opcion" in t or "opción" in t:
        return None
    # «Licitación» del primario = adjudicación de títulos nuevos = COMPRA
    # (misma regla que el motor de PnL; caso real: «COLP - Licitación»).
    if "suscri" in t or "compra" in t or "licita" in t:
        return "compra"
    if "rescate" in t or "venta" in t:
        return "venta"
    return None


def _es_24hs(condiciones: str | None) -> bool:
    return "24" in (condiciones or "")


def pertenece_al_mes(fecha: str, condiciones: str | None, *,
                     mes: str, borde_prev: str, borde_fin: str) -> bool:
    """¿El boleto es de este MES CONTABLE? La tenencia es una foto LIQUIDADA y
    `operaciones.operaciones` registra por CONCERTACIÓN (detección del user,
    2026-09-01): un boleto del último hábil del mes anterior en 24hs liquida el
    1º hábil de este mes — la foto del cierre anterior NO lo tiene, así que
    cuenta ACÁ. Simétrico en la otra punta: el del último hábil de ESTE mes en
    24hs no está en la foto del cierre y pasa al mes siguiente. Contado
    inmediato liquida el mismo día y se queda donde concertó."""
    if fecha < mes:  # mes anterior: entra solo el borde que liquida acá
        return fecha >= borde_prev and _es_24hs(condiciones)
    if fecha >= borde_fin:  # borde final: en 24hs liquida el mes que viene
        return not _es_24hs(condiciones)
    return True


# Días máximos entre el provisional y su final para considerarlos la misma
# operación. Es también cuánto se estira la ventana de lectura a cada lado del
# mes para ver parejas que lo cruzan.
_VENTANA_PAREJA = 20


def _fase_fci(tipo_operacion: str | None) -> str | None:
    t = (tipo_operacion or "").lower()
    if "provisional" in t:
        return "provisional"
    if "final" in t:
        return "final"
    return None


def emparejar_provisional_final(ops: list[dict]) -> list[dict]:
    """Un FCI vía Aunesa entra como DOS boletos del MISMO movimiento: el
    «provisional» (el pedido) y el «final» (el liquidado), con la misma
    cantidad. Contar los dos duplica; borrar uno a ciegas ROMPE cuando el otro
    cayó en otro mes (caso real 2026-09-04: el rescate provisional era el que
    correspondía al mes y se había ido con la exclusión de «provisional»).

    Regla: cada FINAL busca hacia atrás el PROVISIONAL más cercano del mismo
    instrumento, misma punta y misma cantidad, a lo sumo `_VENTANA_PAREJA`
    días antes (o el mismo día). La pareja queda como UNA fila, fechada en el
    PROVISIONAL (el primero de los dos), con `op` que nombra a las dos patas y
    `comprobante` con los dos boletos. Lo que no encuentra pareja viaja tal
    cual: «no pude emparejar» ≠ «no existe» (REGLA #9), y se ve en el modal
    con su nombre original.

    Emparejamiento por FICHA (instrumento + punta + cantidad + cercanía), no
    por texto. Recibe filas crudas de la query (`fecha`, `instrumento`,
    `operacion`, `tipo_operacion`, `cantidad`, `boleto`) y devuelve el mismo
    shape, en el mismo orden."""
    from datetime import timedelta

    def _ficha(r: dict) -> tuple:
        return (r.get("instrumento") or "",
                _direccion(r.get("operacion"), r.get("tipo_operacion")),
                round(_f(r.get("cantidad")) or 0.0, 2))

    usados: set[int] = set()
    salida: list[dict] = []
    provisionales: dict[tuple, list[int]] = {}
    for i, r in enumerate(ops):
        if _fase_fci(r.get("tipo_operacion")) == "provisional":
            provisionales.setdefault(_ficha(r), []).append(i)
    for i, r in enumerate(ops):
        if i in usados:
            continue
        if _fase_fci(r.get("tipo_operacion")) != "final":
            salida.append(r)
            continue
        f_fin = date.fromisoformat(r["fecha"])
        candidato = None
        for j in provisionales.get(_ficha(r), []):
            if j in usados:
                continue
            f_prov = date.fromisoformat(ops[j]["fecha"])
            if f_prov <= f_fin <= f_prov + timedelta(days=_VENTANA_PAREJA):
                if candidato is None or f_prov > date.fromisoformat(ops[candidato]["fecha"]):
                    candidato = j
        if candidato is None:
            salida.append(r)
            continue
        prov = ops[candidato]
        usados.add(candidato)
        pareja = dict(prov)
        base = (r.get("tipo_operacion") or "").lower().replace("final", "").strip().capitalize()
        pareja["tipo_operacion"] = (f"{base} (provisional {prov['fecha'][8:]}/{prov['fecha'][5:7]}"
                                    f" → final {r['fecha'][8:]}/{r['fecha'][5:7]})")
        pareja["boleto"] = f"{prov.get('boleto') or '?'} + {r.get('boleto') or '?'}"
        # el importe LIQUIDADO manda si el provisional no lo traía
        if not _f(prov.get("bruto")) and _f(r.get("bruto")):
            pareja["bruto"] = r.get("bruto")
        salida = [x for x in salida if x is not prov]
        salida.append(pareja)
    # Reordenar por fecha/boleto: la pareja va donde estaba el provisional.
    orden = {id(r): i for i, r in enumerate(ops)}
    salida.sort(key=lambda r: (r["fecha"], orden.get(id(r), 10**9)))
    return salida


def _boletos_mes(id_cuenta: str, mes_str: str | None, u2m: dict[str, str]) -> list[dict]:
    """Boletos del MES CONTABLE traducidos al shape que consume
    `calcular_titulos`: compras/ventas desde `operaciones.operaciones`
    (importe = bruto, título por `instrumento` = unidad → clave del mapping)
    El corte de mes es por
    LIQUIDACIÓN, no por concertación — ver `pertenece_al_mes`. Los boletos sin
    dirección viajan con categoria=None: no suman, pero el detalle y el
    contador `ignorados` los muestran."""
    p: dict = {"c": id_cuenta}
    if mes_str:
        from core.calendario import ultimo_habil_del_mes
        anio, m = int(mes_str[:4]), int(mes_str[5:7])
        a0, m0 = _mes_anterior(anio, m)
        borde_prev = ultimo_habil_del_mes(a0, m0).isoformat()
        borde_fin = ultimo_habil_del_mes(anio, m).isoformat()
        # Rango ampliado: desde el borde del mes anterior hasta fin de mes; el
        # corte fino por liquidación lo hace `pertenece_al_mes`. Los dos bordes
        # se estiran `_VENTANA_PAREJA` días más para que una pareja
        # provisional/final que cruza el mes se vea ENTERA y se junte antes
        # del corte.
        from datetime import timedelta
        desde = (date.fromisoformat(borde_prev) - timedelta(days=_VENTANA_PAREJA)).isoformat()
        hasta = (ultimo_habil_del_mes(anio, m) + timedelta(days=_VENTANA_PAREJA)).isoformat()
        w_mes = "AND concertacion >= %(desde)s AND concertacion <= %(hasta)s "
        p |= {"desde": desde, "hasta": hasta}
    else:
        w_mes, borde_prev, borde_fin = "", "", ""
    ops = _q(
        "SELECT to_char(concertacion,'YYYY-MM-DD') AS fecha, instrumento, operacion, "
        "tipo_operacion, condiciones, cantidad, bruto, moneda, mep, boleto "
        "FROM operaciones.operaciones "
        f"WHERE id_cuenta = %(c)s {w_mes}"
        "AND anulado_en IS NULL AND etapa IS DISTINCT FROM 'solicitud' "
        "AND COALESCE(es_cierre, false) = false "
        "ORDER BY concertacion, boleto", p)
    # FCI vía Aunesa: cada suscripción/rescate son DOS boletos (provisional +
    # final) del MISMO movimiento. Se juntan ANTES del corte de mes, sobre la
    # ventana ampliada, así una pareja que cruza el mes cuenta UNA vez y en UN
    # solo mes. Ver `emparejar_provisional_final`.
    ops = emparejar_provisional_final(ops)
    if mes_str:
        ops = [r for r in ops
               if pertenece_al_mes(r["fecha"], r.get("condiciones"),
                                   mes=mes_str, borde_prev=borde_prev, borde_fin=borde_fin)]
    boletos: list[dict] = []
    for r in ops:
        unidad = r.get("instrumento") or ""
        dir_ = _direccion(r.get("operacion"), r.get("tipo_operacion"))
        cat = {"compra": "compra", "venta": "venta"}.get(dir_ or "")
        boletos.append({
            "fecha": r["fecha"], "categoria": cat, "op": r.get("tipo_operacion"),
            "ticker": u2m.get(unidad, unidad), "unidad": unidad,
            "cantidad": _f(r.get("cantidad")), "importe": _f(r.get("bruto")),
            "moneda": r.get("moneda"), "mep": _f(r.get("mep")),
            "comprobante": r.get("boleto"), "condiciones": r.get("condiciones"),
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
    titulos, altas = separar_altas(todos)
    tot = {k: round(sum(t[k] for t in titulos), 2)
           for k in ("v_ini", "v_fin", "compras", "ventas",
                     "rxt", "intermediacion", "total")}
    tot["descuadres"] = sum(1 for t in todos if not t["cuadra"])
    tot["mep_faltantes"] = sum(t["mep_faltantes"] for t in todos)
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
            "titulos": titulos, "altas": altas, "totales": tot,
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
          "n_boletos": 0, "mep_faltantes": 0}
    for b in boletos:
        cat = b.get("categoria")
        q = abs(b.get("cantidad") or 0.0)
        imp = abs(b.get("importe_ars") or 0.0)
        if cat == "saldo_inicial":
            nominales += q                      # posición, NO plata
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
            "intermediacion": round(ag["intermediacion"], 2)}


# ── ABM de cuentas del proceso ───────────────────────────────────────────────

def cuentas() -> list[dict]:
    filas = _q("SELECT id_cuenta, etiqueta, agregada_por, agregada_en "
               "FROM operaciones.contabilidad_cuentas ORDER BY id_cuenta")
    for r in filas:
        r["agregada_en"] = r["agregada_en"].isoformat() if r["agregada_en"] else None
    return filas


def agregar_cuenta(actor: str, id_cuenta: str, etiqueta: str | None) -> dict:
    id_cuenta = (id_cuenta or "").strip()
    if not id_cuenta:
        return {"ok": False, "error": "id_cuenta vacío"}
    if not etiqueta:
        # Nombre visible desde la tenencia (si la cuenta existe ahí).
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
