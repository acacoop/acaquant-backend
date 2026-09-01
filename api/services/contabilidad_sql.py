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
  · Compras/ventas/rentas → `operaciones.negocio_movimientos` (los boletos con
    precio, la MISMA fuente del cost-basis del motor de PnL).

Convención de signos — VERIFICADA contra el motor (`pnl.py`), no asumida: los
signos crudos de Aunesa no son confiables, la dirección la da la CATEGORÍA y las
magnitudes van en valor absoluto (compra/suscripción = plata que sale, venta/
rescate = plata que entra). Boletos en USD se pesifican con el `mep` snapshot del
propio boleto (fallback `get_mep_for_date`), igual que el motor.

Puente título↔boleto: la tenencia habla por `unidad`, los boletos por `ticker`.
El puente es el MISMO mapping del motor de PnL (`pnl_sql._mapas_assets`, vía
`portafolio.assets`: CAFCI para FCI, ticker para el resto) — REGLA #9: no se
inventa otro emparejamiento.

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


def _boletos_mes(id_cuenta: str, mes_str: str) -> list[dict]:
    return negocio_movimientos_rows(
        fields=_CAMPOS_BOLETO, id_cuenta=id_cuenta, fecha_prefix=mes_str,
        categorias=sorted(_CATS_TODAS), ticker_not_null=True, order=True)


@cached(ttl=300)
def resumen(*, id_cuenta: str, mes: str) -> dict:
    """El informe del mes: una fila por título + totales (sumados ACÁ, no en el
    navegador). `mes` = "YYYY-MM"."""
    from api.services.pnl_sql import _mapas_assets
    anio, m = int(mes[:4]), int(mes[5:7])
    a0, m0 = _mes_anterior(anio, m)
    ini = _cierre_mes(id_cuenta, a0, m0)
    fin = _cierre_mes(id_cuenta, anio, m)
    boletos = _boletos_mes(id_cuenta, mes)
    mapas = _mapas_assets()
    titulos = calcular_titulos(
        filas_ini=ini["filas"], filas_fin=fin["filas"], boletos=boletos,
        unidad_to_match=mapas["unidad_to_match"],
        match_to_display=mapas["match_to_display"])
    tot = {k: sum(t[k] for t in titulos)
           for k in ("v_ini", "v_fin", "compras", "ventas", "rentas",
                     "rxt", "intermediacion", "total")}
    tot["descuadres"] = sum(1 for t in titulos if not t["cuadra"])
    tot["mep_faltantes"] = sum(t["mep_faltantes"] for t in titulos)
    return {"id_cuenta": id_cuenta, "mes": mes,
            "cierre_ini": {"fecha_objetivo": ini["fecha_objetivo"],
                           "fecha_usada": ini["fecha_usada"]},
            "cierre_fin": {"fecha_objetivo": fin["fecha_objetivo"],
                           "fecha_usada": fin["fecha_usada"]},
            "titulos": titulos, "totales": tot, "n_boletos": len(boletos)}


def detalle(*, id_cuenta: str, mes: str, key: str) -> dict:
    """Los boletos del mes que componen la fila `key` (el drill-down auditable:
    el mismo insumo del resumen, no otra query con otro criterio)."""
    boletos = [b for b in _boletos_mes(id_cuenta, mes)
               if (b.get("ticker") or "").strip() == key]
    for b in boletos:
        b["importe_ars"], b["sin_mep"] = _pesificar(b)
        b["direccion"] = ("compra" if b["categoria"] in _CATS_COMPRA else
                          "venta" if b["categoria"] in _CATS_VENTA else "renta")
    return {"id_cuenta": id_cuenta, "mes": mes, "key": key, "boletos": boletos}


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
