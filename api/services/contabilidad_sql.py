"""CONTABILIDAD de cuentas propias (Back Office → CONTABILIDAD) — SQL-only.

Resultado MENSUAL por título de una cuenta propia, partido en tres canales sobre
la identidad contable que rige todo el módulo (definición del back office):

    resultado_total = valuación_final − valuación_inicial + ventas − compras + rentas

  · TENENCIA (RxT)   — la fórmula de la planilla histórica del back office:
                       posición mantenida = min(nominales_ini, nominales_fin),
                       valuada al precio implícito (valuación ÷ nominales) de cada
                       cierre. Sin operaciones en el mes ⇒ RxT = ΔValuación.
  · RENTAS           — cupones / dividendos / amortizaciones (categoría `acreencia`
                       de los boletos), con signo tal cual viene (igual que el
                       `pnl_pasivo` del motor de PnL).
  · INTERMEDIACIÓN   — el residuo: total − RxT − rentas. Así el split JAMÁS puede
                       descuadrar del total, y las ALTAS/BAJAS del período (que la
                       planilla dejaba sin número) quedan valuadas solas.

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
  · Rentas → `operaciones.negocio_movimientos` categoría `acreencia` (cupones /
    dividendos / amortizaciones — en `operaciones.operaciones` no existen). Si esa
    tabla no trae la cuenta, rentas queda en 0 y el hueco se ve en el cuadre.

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
from api.services._negocio_sql_read import negocio_movimientos_rows
from api.services._sql import _f, _q

# Dirección por categoría (misma partición que el motor de PnL: _CATS_PAGO /
# _CATS_COBRO_VENTA / _CATS_COBRO_PASIVO).
_CATS_COMPRA = {"compra", "suscripcion_fci"}
_CATS_VENTA = {"venta", "rescate_fci"}
_CATS_RENTA = {"acreencia"}
_CATS_TODAS = _CATS_COMPRA | _CATS_VENTA | _CATS_RENTA

_CAMPOS_BOLETO = ["fecha", "categoria", "op", "ticker", "cantidad", "precio",
                  "importe", "moneda", "mep", "comprobante"]

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
) -> list[dict]:
    """Una fila por título con los tres canales + cuadre. Entradas:
      filas_ini/filas_fin: filas de `portafolio.tenencia` (unidad, ticker,
        cartera, cantidad, valuacion) SIN cash (el caller ya filtró MONEDAS).
      boletos: dicts shape negocio_movimientos (solo categorías relevantes).
    """
    por_key: dict[str, dict] = {}

    def _fila(key: str) -> dict:
        return por_key.setdefault(key, {
            "titulo": match_to_display.get(key, key), "unidades": [],
            "qty_ini": 0.0, "qty_fin": 0.0, "v_ini": 0.0, "v_fin": 0.0,
            "compras": 0.0, "ventas": 0.0, "rentas": 0.0,
            "qty_compras": 0.0, "qty_ventas": 0.0, "n_boletos": 0,
            "mep_faltantes": 0,
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

    for b in boletos:
        cat = b.get("categoria")
        key = (b.get("ticker") or "").strip()
        if not key or cat not in _CATS_TODAS:
            continue
        d = _fila(key)
        importe_ars, sin_mep = _pesificar(b)
        d["mep_faltantes"] += 1 if sin_mep else 0
        d["n_boletos"] += 1
        cantidad = abs(b.get("cantidad") or 0.0)
        if cat in _CATS_COMPRA:
            d["compras"] += abs(importe_ars)
            d["qty_compras"] += cantidad
        elif cat in _CATS_VENTA:
            d["ventas"] += abs(importe_ars)
            d["qty_ventas"] += cantidad
        else:  # renta: signo tal cual (una corrección puede venir negativa)
            d["rentas"] += importe_ars

    out: list[dict] = []
    for key, d in por_key.items():
        qi, qf, vi, vf = d["qty_ini"], d["qty_fin"], d["v_ini"], d["v_fin"]
        # La identidad — la definición del resultado, pase lo que pase abajo.
        total = (vf - vi) + d["ventas"] - d["compras"] + d["rentas"]
        # RxT de la planilla: posición mantenida × Δ precio implícito.
        px_ini = vi / qi if qi else None
        px_fin = vf / qf if qf else None
        q_min = min(qi, qf)
        rxt = q_min * (px_fin - px_ini) if (q_min > 0 and px_ini is not None
                                            and px_fin is not None) else 0.0
        residual = qf - qi - d["qty_compras"] + d["qty_ventas"]
        cuadra = abs(residual) < _TOL_NOMINALES

        def _r2(x: float) -> float:  # plata a 2 decimales, sin −0.0 de ruido float
            return round(x, 2) + 0.0

        # Redondear ANTES de derivar el residuo: así rxt + intermediación +
        # rentas == total EXACTO también en centavos (el split no puede
        # descuadrar ni por redondeo).
        total, rxt, rentas = _r2(total), _r2(rxt), _r2(d["rentas"])
        intermediacion = _r2(total - rxt - rentas)
        out.append({
            "titulo": d["titulo"], "key": key, "unidades": d["unidades"],
            "qty_ini": qi, "qty_fin": qf, "v_ini": _r2(vi), "v_fin": _r2(vf),
            "px_ini": px_ini, "px_fin": px_fin,
            "compras": _r2(d["compras"]), "ventas": _r2(d["ventas"]),
            "rentas": rentas,
            "rxt": rxt, "intermediacion": intermediacion, "total": total,
            "estado": ("alta" if qi == 0 and qf != 0 else
                       "baja" if qi != 0 and qf == 0 else
                       "sin_operar" if d["n_boletos"] == 0 else "operado"),
            "n_boletos": d["n_boletos"],
            "cuadre_nominales": residual, "cuadra": cuadra,
            "mep_faltantes": d["mep_faltantes"],
        })
    out.sort(key=lambda r: abs(r["total"]), reverse=True)
    return out


def separar_altas(titulos: list[dict]) -> tuple[list[dict], list[dict]]:
    """(con_resultado, altas_puras). ALTA PURA = no había nominales al cierre
    anterior y en el mes solo se COMPRÓ (sin ventas ni rentas): se compró para
    dejar en cartera, y por definición del proceso su resultado recién entra al
    RxT del mes que viene — mostrarla entre los resultados es ruido. Una alta
    que además vendió o cobró renta SÍ tiene resultado del mes y se queda."""
    con_resultado, altas = [], []
    for t in titulos:
        if t["estado"] == "alta" and t["ventas"] == 0 and t["rentas"] == 0:
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
           "WHERE id_cuenta = %(c)s AND fecha = %(f)s AND cartera <> 'MONEDAS'")
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
    if op == "compra" or "suscri" in op:
        return "compra"
    if op == "venta" or "rescate" in op:
        return "venta"
    t = (tipo_operacion or "").strip().lower()
    if "cauci" in t or "futuro" in t or "opcion" in t or "opción" in t:
        return None
    if "suscri" in t or "compra" in t:
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


def _boletos_mes(id_cuenta: str, mes_str: str | None, u2m: dict[str, str]) -> list[dict]:
    """Boletos del MES CONTABLE traducidos al shape que consume
    `calcular_titulos`: compras/ventas desde `operaciones.operaciones`
    (importe = bruto, título por `instrumento` = unidad → clave del mapping)
    + rentas (`acreencia`) desde `negocio_movimientos`. El corte de mes es por
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
        # corte fino por liquidación lo hace `pertenece_al_mes`.
        w_mes = "AND concertacion >= %(desde)s AND to_char(concertacion,'YYYY-MM') <= %(m)s "
        p |= {"desde": borde_prev, "m": mes_str}
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
    rentas = negocio_movimientos_rows(
        fields=_CAMPOS_BOLETO, id_cuenta=id_cuenta, fecha_prefix=mes_str,
        categorias=["acreencia"], ticker_not_null=True, order=True)
    boletos.extend(rentas)
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
        match_to_display=mapas["match_to_display"])
    # Las ALTAS PURAS (comprado para dejar en cartera) no son resultado de ESTE
    # mes: van en su bloque aparte y NO suman a los totales del informe.
    titulos, altas = separar_altas(todos)
    tot = {k: round(sum(t[k] for t in titulos), 2)
           for k in ("v_ini", "v_fin", "compras", "ventas", "rentas",
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


def ledger_fifo(boletos: list[dict]) -> dict:
    """El LIBRO del título: recorre los boletos EN ORDEN y anota en cada fila
    `nominales_acum` (posición corrida) y `pnl_acum` (realizado por costeo FIFO
    + rentas, corrido). Espera filas ya pesificadas (`importe_ars`).

    FIFO: cada compra entra como un lote (cantidad, costo); cada venta consume
    los lotes MÁS VIEJOS primero y realiza `ingreso − costo de esos lotes`.
    La fila sintética `saldo_inicial` (la posición al cierre del mes anterior,
    a su valuación de ese cierre) entra como PRIMER lote sin generar PnL — es el
    reset mensual del módulo: vender el 100% realiza `venta − valuación inicial`.
    Una venta que excede saldo inicial + compras se marca `sin_costo`: solo la
    parte con lote genera PnL — mismo criterio que el motor de PnL con los wash
    trades. Devuelve {"sin_costo": n} para que la vista declare el parcial."""
    lotes: list[list[float]] = []  # [cantidad_restante, costo_restante], viejo primero
    nominales = 0.0
    pnl = 0.0
    sin_costo = 0
    for b in boletos:
        cat = b.get("categoria")
        q = abs(b.get("cantidad") or 0.0)
        imp = abs(b.get("importe_ars") or 0.0)
        if cat in _CATS_COMPRA or cat == "saldo_inicial":
            lotes.append([q, imp])
            nominales += q
        elif cat in _CATS_VENTA:
            restante, costo = q, 0.0
            while restante > _TOL_NOMINALES and lotes:
                lote = lotes[0]
                usa = min(lote[0], restante)
                parte = lote[1] * (usa / lote[0])
                costo += parte
                lote[1] -= parte
                lote[0] -= usa
                restante -= usa
                if lote[0] <= _TOL_NOMINALES:
                    lotes.pop(0)
            cubierta = (q - restante) / q if q else 0.0
            pnl += imp * cubierta - costo
            if restante > _TOL_NOMINALES:
                b["sin_costo"] = True
                sin_costo += 1
            nominales -= q
        elif cat in _CATS_RENTA:
            pnl += b.get("importe_ars") or 0.0  # con signo
        b["nominales_acum"] = round(nominales, 2)
        b["pnl_acum"] = round(pnl, 2)
    return {"sin_costo": sin_costo}


def detalle(*, id_cuenta: str, mes: str, key: str) -> dict:
    """El LIBRO del título en el MES elegido (pedido del user 2026-09-01: los
    boletos son del período, no de todo el año). Arranca con una fila sintética
    de POSICIÓN INICIAL — los nominales y la valuación del cierre del mes
    anterior, que entran como primer lote FIFO — y sigue con los boletos del
    mes, cada uno con nominales acumulados y PnL acumulado (FIFO + rentas).
    Así vender el 100% muestra `venta − valuación inicial` = la intermediación
    de la fila del resumen."""
    from api.services.pnl_sql import _mapas_assets
    u2m = _mapas_assets()["unidad_to_match"]
    anio, m = int(mes[:4]), int(mes[5:7])
    a0, m0 = _mes_anterior(anio, m)
    ini = _cierre_mes(id_cuenta, a0, m0)
    qty_ini = sum((r.get("cantidad") or 0.0) for r in ini["filas"]
                  if u2m.get(r.get("unidad") or "", r.get("unidad")) == key)
    v_ini = sum((r.get("valuacion") or 0.0) for r in ini["filas"]
                if u2m.get(r.get("unidad") or "", r.get("unidad")) == key)
    boletos = [b for b in _boletos_mes(id_cuenta, mes, u2m)
               if (b.get("ticker") or "").strip() == key]
    for b in boletos:
        b["importe_ars"], b["sin_mep"] = _pesificar(b)
        cat = b.get("categoria")
        b["direccion"] = ("compra" if cat in _CATS_COMPRA else
                          "venta" if cat in _CATS_VENTA else
                          "renta" if cat in _CATS_RENTA else "otro")
    if qty_ini or v_ini:
        boletos.insert(0, {
            "fecha": ini["fecha_usada"] or ini["fecha_objetivo"],
            "categoria": "saldo_inicial", "op": "POSICIÓN INICIAL (cierre anterior)",
            "ticker": key, "cantidad": qty_ini, "importe": None, "moneda": None,
            "mep": None, "comprobante": "—", "importe_ars": v_ini, "sin_mep": False,
            "direccion": "otro",
        })
    stats = ledger_fifo(boletos)
    return {"id_cuenta": id_cuenta, "mes": mes, "key": key, "boletos": boletos,
            "sin_costo": stats["sin_costo"]}


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
