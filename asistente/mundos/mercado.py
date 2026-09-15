"""Mundo MERCADO: qué hay y cuánto rinde, sin importar quién lo tenga. Sus
herramientas y su agente, en un solo archivo. Doc: docs/AvAgentAI.md."""
from __future__ import annotations

from datetime import date
from typing import Literal, get_args

from asistente.agente import COMUN, Agente

MAX_INSTRUMENTOS = 50
MAX_FLUJOS = 24
Curva = Literal["tasa_fija", "cer", "hard_dolar", "dolar_linked", "tamar"]
OrdenCurva = Literal["tea", "vencimiento", "duration", "volumen_dia"]


def _pct(x):
    """TEA y TEM salen del motor como decimal; al modelo van en porcentaje."""
    return round(float(x) * 100, 2) if x is not None else None


def _num(x, dec: int = 2):
    return round(float(x), dec) if x is not None else None


def _ordenar(filas: list[dict], por: str) -> list[dict]:
    if por == "tea":
        return sorted(filas, key=lambda f: (f["tasa_ruido"] or f["tea_pct"] is None,
                                            -(f["tea_pct"] or 0)))
    if por == "volumen_dia":
        return sorted(filas, key=lambda f: (f["volumen_dia"] is None, -(f["volumen_dia"] or 0)))
    return sorted(filas, key=lambda f: (f[por] is None, f[por] or 0))


def _instrumento(b: dict, hoy: date) -> dict:
    m = b.get("metrics") or {}
    vto = b.get("vencimiento")
    try:
        meses = round((date.fromisoformat(str(vto)[:10]) - hoy).days / 30.44, 1) if vto else None
    except ValueError:
        meses = None
    return {
        "ticker": b.get("ticker_corto"),
        "emisor": b.get("emisor"),
        "vencimiento": str(vto)[:10] if vto else None,
        "meses_al_vencimiento": meses,
        "precio": _num(m.get("last_price"), 4),
        "tea_pct": _pct(m.get("TEA")),
        "tem_pct": _pct(m.get("TEM")),
        "paridad_pct": _num(m.get("paridad")),
        "duration": _num(m.get("duration")),
        "volumen_dia": _num(m.get("total_nominals"), 0),
        "tasa_ruido": bool(b.get("tasa_ruido")),
    }


def metricas_por_ticker() -> dict[str, dict]:
    """Lo que el mercado dice hoy de cada bono del master de curvas, por ticker
    corto: la misma foto que `curva`. Es un helper de DATOS, no una herramienta:
    lo usa el mundo cartera para decir cuánto rinde lo que una cuenta tiene, sin
    que ningún modelo cruce nada. Levanta si la vista no contesta."""
    from api.services import curvas_vista as CV

    hoy = date.today()
    return {b["ticker_corto"]: _instrumento(b, hoy)
            for b in CV.get_curvas_vista().get("bonos") or [] if b.get("ticker_corto")}


def curva(curva: Curva, ordenar_por: OrdenCurva = "tea", limit: int = 15) -> dict:
    """Qué instrumentos hay HOY en una curva de renta fija y cuánto rinden.

    Es el MERCADO, no una cuenta: acá no hay nominales de nadie ni plata que
    entra. Para «qué tengo» está `tenencia_actual`; para «qué cobro»,
    `cobros_futuros`. Esto contesta «qué hay», «qué rinde más», «qué vence en
    tal plazo», «cuál conviene».

    Curvas: `cer` (ajustan por inflación), `tasa_fija` (LECAP/BONCAP y tasa
    fija en pesos), `hard_dolar` (bonos en dólares: AL, GD, ONs), `dolar_linked`,
    `tamar`. Si el usuario dice «bonos CER» es `cer`; «en dólares», «soberanos»
    o «hard dollar» es `hard_dolar`; «letras» o «tasa fija» es `tasa_fija`.

    QUÉ DEVUELVE:
      · `instrumentos` — una fila por título: `ticker`, `emisor`, `vencimiento`,
        `meses_al_vencimiento`, `precio`, `tea_pct` y `tem_pct` (YA en
        porcentaje), `paridad_pct`, `duration` (años), `volumen_dia` y
        `tasa_ruido`. Si `tasa_ruido` es true, la tasa NO es comparable (el
        bono vence en días): no la uses para decir cuál rinde más.
      · `cuantos` — cuántos hay en la curva en total; `truncado` si entraron
        menos que eso en `instrumentos`.
      · Todo lo que hay que calcular YA VIENE CALCULADO. No conviertas tasas.

    Args:
        curva: cuál de las curvas mirar.
        ordenar_por: cómo ordenar; con `tea` los que más rinden van primero.
            Si el usuario no dijo, es `tea` y no hace falta preguntar.
        limit: cuántos instrumentos traer, de 1 a 50. Si no dijo, 15.
    """
    from api.services import curvas_vista as CV
    from core import curvas_ejes as ce

    if curva not in ce.pills_disponibles():
        return {"error": f"`curva` tiene que ser una de {list(ce.pills_disponibles())}, "
                         f"llegó {curva!r}"}
    if ordenar_por not in get_args(OrdenCurva):
        return {"error": f"`ordenar_por` tiene que ser uno de {list(get_args(OrdenCurva))}, "
                         f"llegó {ordenar_por!r}"}
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return {"error": f"`limit` tiene que ser un número entero, llegó {limit!r}"}
    n = max(1, min(n, MAX_INSTRUMENTOS))
    try:
        bonos = [b for b in CV.get_curvas_vista().get("bonos") or [] if b.get("pill") == curva]
    except Exception as e:
        return {"error": f"no pude leer la curva: {type(e).__name__}: {e}"}
    hoy = date.today()
    filas = _ordenar([_instrumento(b, hoy) for b in bonos], ordenar_por)
    return {
        "curva": curva,
        "ordenado_por": ordenar_por,
        "instrumentos": filas[:n],
        "cuantos": len(filas),
        "truncado": len(filas) > n,
        "_tabla": {
            "campo": "instrumentos",
            "columnas": ["ticker", "emisor", "vencimiento", "precio", "tea_pct", "duration"],
        },
    }


def ficha_bono(ticker: str) -> dict:
    """Qué ES un bono: quién lo emite, en qué moneda paga, cómo ajusta, cuándo
    vence, qué cupón tiene, qué paga en los próximos meses y cómo cotiza hoy.

    Es la ficha del INSTRUMENTO, sin importar quién lo tenga. NO dice cuánto
    cobra una cuenta de ese bono: eso depende de cuántos nominales tiene, y lo
    contesta `cobros_futuros`. Los montos acá son por 100 de valor nominal.

    QUÉ DEVUELVE:
      · `ficha` — emisor, tipo, curva, moneda, ajuste, ley, emisión,
        vencimiento, valor nominal, cupón anual.
      · `proximos_pagos` — un renglón por fecha futura: `fecha`, `interes`,
        `amortizacion`, `monto`, por 100 VN. `cuantos_pagos` y `truncado`
        dicen si entraron todos.
      · `hoy` — precio, `tea_pct` y `tem_pct` (YA en porcentaje), paridad y
        duration de la cotización de hoy, si el bono cotizó.
      · Un bono que no existe devuelve `error`: decilo, no adivines otro.

    Args:
        ticker: el ticker CORTO del bono, en mayúsculas: `AL30`, `TX26`,
            `S31O5`. Sin sufijo de moneda (`AL30D` es la misma especie que
            `AL30`; usá `AL30`).
    """
    from api.services import bono_detalle as BD

    tk = str(ticker or "").strip().upper()
    if not tk:
        return {"error": "`ticker` está vacío"}
    try:
        r = BD.get_bono(tk)
    except Exception as e:
        return {"error": f"no pude leer la ficha: {type(e).__name__}: {e}"}
    if r.get("error"):
        return {"error": r["error"],
                "que_hacer": "Decile al usuario que ese ticker no está en el master de "
                             "curvas. NO pruebes con otro ticker parecido: preguntale."}
    futuros = [f for f in r.get("flujos") or [] if f.get("futuro")]
    patas = r.get("patas") or []
    principal = next((p for p in patas if p.get("pata") == r.get("pata_principal")), None)
    m = (principal or {}).get("metrics") or {}
    return {
        "ticker": r.get("ticker"),
        "ficha": {k: v for k, v in (r.get("ficha") or {}).items()
                  if k in ("emisor", "emisor_tipo", "tipo", "curva", "moneda", "moneda_flujo",
                           "ajuste", "ley", "fecha_emision", "fecha_vencimiento",
                           "valor_nominal", "cupon_anual", "cer_fijado")},
        "unidad": r.get("unidad_flujo"),
        "nota": r.get("nota_flujo"),
        "proximos_pagos": [{
            "fecha": f.get("fecha"),
            "interes": f.get("interes"),
            "amortizacion": f.get("amortizacion"),
            "monto": f.get("monto"),
        } for f in futuros[:MAX_FLUJOS]],
        "cuantos_pagos": len(futuros),
        "truncado": len(futuros) > MAX_FLUJOS,
        "hoy": {
            "precio": _num(m.get("last_price"), 4),
            "tea_pct": _pct(m.get("TEA")),
            "tem_pct": _pct(m.get("TEM")),
            "paridad_pct": _num(m.get("paridad")),
            "duration": _num(m.get("duration")),
        } if m else None,
        "_tabla": {
            "campo": "proximos_pagos",
            "columnas": ["fecha", "interes", "amortizacion", "monto"],
        },
    }


# ── el registro ─────────────────────────────────────────────────────────────

# Las de la CUENTA reciben `cuenta` y pasan por el permiso. Las del MERCADO no
# la reciben. Un test exige las dos cosas.


# ── el agente ───────────────────────────────────────────────────────────────

_INSTRUCCION = """
Hablás del MERCADO: lo que hay y cuánto rinde, sin importar quién lo tenga.
Una tasa marcada `tasa_ruido` no es comparable: no la uses para decir cuál
rinde más. Un ticker que no existe se dice, no se reemplaza por uno parecido.
"""


def _instruccion(_foco: dict) -> str:
    return COMUN + _INSTRUCCION


AGENTE = Agente(
    nombre="mercado",
    tarea="asistente_mercado",
    describe="el mercado, sin importar quién lo tenga: qué bonos hay en una curva, cuánto "
             "rinden, qué es un bono, cuándo vence, cómo cotiza.",
    instruccion=_instruccion,
    herramientas=(curva, ficha_bono),
    senales=("curva", "curvas", "rinde", "rinden", "rendimiento", "rendimientos", "tea",
             "tir", "bono", "bonos", "cotiza", "cotizacion", "paridad", "duration", "ficha",
             "ticker", "cer", "tasa fija", "hard dolar", "dolar linked", "tamar", "letra",
             "letras", "on", "ons", "soberano", "soberanos"),
)
