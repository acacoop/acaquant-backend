"""Agente RENTA FIJA (familia mercado): un bono o una curva. Cuánto rinde, qué
hay en una curva, qué es, cuándo paga. Sus herramientas y su agente, en un solo
archivo. Doc: docs/AvAgentAI.md."""
from __future__ import annotations

from datetime import date
from typing import Literal, get_args

from asistente import pantalla
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
        # Sin la curva no se sabe contra QUÉ compara una TEA: la de un CER y la
        # de un hard dollar no son el mismo número aunque se llamen igual.
        "curva": b.get("pill"),
        # `emisor` es el NOMBRE (YPF S.A.); `emisor_tipo`, la categoría
        # (soberano · provincial · corporativo · bcra). Son dos datos distintos.
        "emisor": b.get("emisor"),
        "emisor_tipo": b.get("emisor_tipo"),
        "vencimiento": str(vto)[:10] if vto else None,
        "meses_al_vencimiento": meses,
        "precio": _num(m.get("last_price"), 4),
        "tea_pct": _pct(m.get("TEA")),
        "tem_pct": _pct(m.get("TEM")),
        # La TNA que publicó el backend, por sus DOS vías (`curvas_vista`):
        # `_tna_de` la calcula para `tasa_fija` (convención 1816 plazo-rem,
        # medida), y la rama `manda_1816` la copia de 1816 para CUALQUIER
        # curva cuando el proveedor manda. Donde ninguna aplica queda None, y
        # se queda así A PROPÓSITO: la pantalla ahí deriva TEM×12
        # (`bonos-table.tsx`) y ESA CONVENCIÓN NO ESTÁ MEDIDA para bonos que
        # amortizan. ⚠️ PENDIENTE: medirla contra 1816 antes de copiarla —
        # un número plausible con la fórmula ajena es el peor de los errores.
        "tna_pct": _pct(m.get("TNA")),
        "paridad_pct": _num(m.get("paridad")),
        "duration": _num(m.get("duration")),
        "volumen_dia": _num(m.get("total_nominals"), 0),
        "tasa_ruido": bool(b.get("tasa_ruido")),
    }


def _agg(valores: list[float]) -> dict | None:
    """min · max · promedio · mediana de una lista, o None si está vacía. El
    promedio es SIMPLE (no ponderado por volumen): es lo que se puede decir sin
    inventar un criterio."""
    v = sorted(x for x in valores if x is not None)
    if not v:
        return None
    n = len(v)
    mediana = v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
    return {"min": round(v[0], 2), "max": round(v[-1], 2),
            "promedio": round(sum(v) / n, 2), "mediana": round(mediana, 2)}


def _resumen(filas: list[dict]) -> dict:
    """Los agregados de TODAS las filas, calculados ACÁ. Existen porque el
    modelo tiene prohibido calcular: sin esto, «la duration promedio» se
    contesta a ojo sobre las que entraron en `limit`, que además son una parte.
    Se calculan antes de recortar, así que el límite no los puede falsear.

    Las tasas con `tasa_ruido` quedan afuera de lo que habla de TASAS: son
    bonos que vencen en días y su TEA anualizada es un número enorme que no
    compara con nada. Adentro del promedio se llevarían puesta la curva."""
    con_tasa = [f for f in filas if not f["tasa_ruido"] and f["tea_pct"] is not None]
    por_vto = sorted((f for f in filas if f["vencimiento"]), key=lambda f: f["vencimiento"])
    mejor = max(con_tasa, key=lambda f: f["tea_pct"], default=None)
    corto = lambda f: {"ticker": f["ticker"], "vencimiento": f["vencimiento"]}  # noqa: E731
    return {
        "cuantos": len(filas),
        "con_tasa_comparable": len(con_tasa),
        "tea_pct": _agg([f["tea_pct"] for f in con_tasa]),
        "duration": _agg([f["duration"] for f in con_tasa]),
        "rinde_mas": {"ticker": mejor["ticker"], "tea_pct": mejor["tea_pct"]} if mejor else None,
        "vence_primero": corto(por_vto[0]) if por_vto else None,
        "vence_ultimo": corto(por_vto[-1]) if por_vto else None,
    }


def _emisores_de(filas: list[dict]) -> list[dict]:
    """Qué emisores hay en estas filas y cuántos bonos tiene cada uno, en orden
    de cantidad. Va SIEMPRE en la respuesta: sin esto el modelo tiene que
    adivinar el nombre exacto para filtrar, y adivinar un nombre es inventar."""
    cuenta: dict[str, int] = {}
    for f in filas:
        if nombre := (f.get("emisor") or "").strip():
            cuenta[nombre] = cuenta.get(nombre, 0) + 1
    return [{"emisor": n, "bonos": c}
            for n, c in sorted(cuenta.items(), key=lambda kv: (-kv[1], kv[0]))]


def clave_emisor(nombre: str | None) -> str:
    """El nombre de un emisor, normalizado para comparar: sin espacios de más,
    en mayúsculas. Lo usan renta fija y cartera: un solo criterio o el mismo
    emisor se parea distinto según quién pregunte (REGLA #9)."""
    return " ".join(str(nombre or "").split()).upper()


def es_del_emisor(fila: dict, buscado: str) -> bool:
    """¿Esta fila es de ese emisor? Por NOMBRE normalizado y por PEDAZO: el
    usuario escribe «YPF» y en la base dice «YPF S.A.». Al revés no vale (un
    nombre entero no matchea una sigla suelta), así que el que se contiene es
    siempre el buscado."""
    return buscado in clave_emisor(fila.get("emisor"))


def metricas_por_ticker() -> dict[str, dict]:
    """Lo que el mercado dice hoy de cada bono del master de curvas, por ticker
    corto: la misma foto que `curva`. Es un helper de DATOS, no una herramienta:
    lo usa el agente cartera para decir cuánto rinde lo que una cuenta tiene, sin
    que ningún modelo cruce nada. Levanta si la vista no contesta."""
    from api.services import curvas_vista as CV

    hoy = date.today()
    return {b["ticker_corto"]: _instrumento(b, hoy)
            for b in CV.get_curvas_vista().get("bonos") or [] if b.get("ticker_corto")}


def curva(curva: Curva, ordenar_por: OrdenCurva = "tea", limit: int = 15,
          emisor: str | None = None) -> dict:
    """Qué instrumentos hay HOY en una curva de renta fija y cuánto rinden.

    Es el MERCADO, no una cuenta: acá no hay nominales de nadie. Para «qué
    tengo» está `tenencia_actual`; para «qué cobro», `cobros_futuros`.

    Curvas: `cer` (ajustan por inflación), `tasa_fija` (LECAP/BONCAP, y es lo
    que pidan como «letras»), `hard_dolar` (en dólares: AL, GD, ONs; también
    «soberanos» y «hard dollar»), `dolar_linked`, `tamar`.

    QUÉ DEVUELVE:
      · `instrumentos` — una fila por título, con ticker, emisor, vencimiento,
        precio, tasas, paridad, duration y volumen. Tres campos con trampa:
        `tasa_ruido` true = la tasa NO compara (el bono vence en días);
        `tna_pct` puede venir null aunque la pantalla muestre una TNA: no la
        rellenes ni la derives, usá `tea_pct`, que está en todos; `emisor` es
        el NOMBRE («YPF S.A.») y `emisor_tipo` la categoría (soberano · …).
      · `resumen` — los agregados de TODA la curva, no de lo que entró acá:
        `tea_pct` y `duration` con min/max/promedio/mediana, `rinde_mas`,
        `vence_primero`, `vence_ultimo`. «El promedio», «el que más rinde» y
        «el que vence más lejos» salen de acá, ya resueltos.
      · `emisores` — los que hay y con cuántos bonos. Mirá cómo se escribe uno
        antes de filtrar.
      · `cuantos` y `truncado`; si truncó, `aviso` dice qué quedó afuera.

    Args:
        curva: cuál mirar.
        ordenar_por: con `tea` los que más rinden van primero; si no lo dijo,
            es `tea`.
        limit: cuántos traer, 1 a 50. Si no dijo, 15. Para un extremo o un
            promedio no lo subas: está en `resumen`.
        emisor: filtra por nombre, por pedazo («YPF» encuentra «YPF S.A.»). Si
            no existe, la respuesta trae la lista de los que sí.
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
    emisores = _emisores_de(filas)
    if emisor is not None:
        buscado = clave_emisor(emisor)
        if not buscado:
            return {"error": "`emisor` llegó vacío: o mandás un nombre o no mandás el campo"}
        filas = [f for f in filas if es_del_emisor(f, buscado)]
        if not filas:
            return {"error": f"en la curva {curva} no hay ningún bono de un emisor que "
                             f"contenga {emisor!r}",
                    "emisores": emisores,
                    "que_hacer": "Decile al usuario cuáles hay. No busques el mismo emisor "
                                 "en otra curva por tu cuenta."}
    salida = {
        "curva": curva,
        "emisor": emisor,
        "ordenado_por": ordenar_por,
        "instrumentos": filas[:n],
        "resumen": _resumen(filas),
        "emisores": emisores,
        "cuantos": len(filas),
        "truncado": len(filas) > n,
        "_tabla": pantalla.tabla(
            "instrumentos",
            ["ticker", "emisor", "vencimiento", "precio", "tea_pct", "duration"],
            f"Curva {curva}" + (f" · {emisor}" if emisor else "")),
    }
    if len(filas) > n:
        # El corte esconde un extremo: el último de la lista NO es el último de
        # la curva. Sin decirlo, «el que vence más lejos» se contesta con el
        # número 15 — plausible, en el lugar correcto, y mal.
        salida["aviso"] = (f"hay {len(filas)} y estás viendo los primeros {n} ordenados por "
                           f"{ordenar_por}: el último de esta lista NO es el último de la "
                           f"curva. Para un extremo o un promedio, mirá `resumen`, que es de "
                           f"las {len(filas)}.")
    return salida


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
        "_tabla": pantalla.tabla(
            "proximos_pagos", ["fecha", "interes", "amortizacion", "monto"],
            f"Próximos pagos del {tk}"),
    }


# ── el registro ─────────────────────────────────────────────────────────────

# Las de la CUENTA reciben `cuenta` y pasan por el permiso. Las del MERCADO no
# la reciben. Un test exige las dos cosas.


# ── el agente ───────────────────────────────────────────────────────────────

_INSTRUCCION = """
Hablás de RENTA FIJA: bonos, letras y ONs, lo que hay y cuánto rinde, sin
importar quién lo tenga. Una tasa marcada `tasa_ruido` no es comparable: no la
uses para decir cuál rinde más. Un ticker que no existe se dice, no se
reemplaza por uno parecido.
"""


def _instruccion(_foco: dict) -> str:
    return COMUN + _INSTRUCCION


AGENTE = Agente(
    nombre="renta_fija",
    tarea="asistente_renta_fija",
    describe="bonos, letras y ONs: qué hay en una curva, cuánto rinde, qué es un bono, cuándo "
             "vence y qué paga, cómo cotiza hoy.",
    instruccion=_instruccion,
    herramientas=(curva, ficha_bono),
    familia="mercado",
    foco=("ticker",),
    senales=("curva", "curvas", "tea", "tir", "bono", "bonos", "paridad", "duration", "ficha",
             "cer", "tasa fija", "hard dolar", "dolar linked", "letra", "letras", "lecap",
             "boncap", "on", "ons", "soberano", "soberanos", "renta fija"),
)
