"""Agente RENTA FIJA (familia mercado): un bono o una curva. Cuánto rinde, qué
hay en una curva, qué es, cuándo paga. Sus herramientas y su agente, en un solo
archivo. Doc: docs/AvAgentAI.md."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, get_args

from pydantic import Field

from asistente import pantalla
from asistente.agente import COMUN, Agente

# Una fecha, DECLARADA como tal en el esquema que ve el modelo (`format: date`)
# en vez de un string libre. El tipo en runtime sigue siendo `str` —el grafo
# llama a la función cruda, sin pasar por pydantic—, así que el que valida de
# verdad es `fecha_o_error`. Lo que cambia es que el modelo ya no tiene que
# adivinar el formato: mandó «2029» una vez y la llamada se perdió.
Fecha = Annotated[str, Field(json_schema_extra={"format": "date"})]

MAX_INSTRUMENTOS = 50
MAX_FLUJOS = 24
Curva = Literal["tasa_fija", "cer", "hard_dolar", "dolar_linked", "tamar"]
# La lista sale de `core.curvas_ejes.EMISORES`; el `Literal` la espeja para que
# viaje como `enum` en la ficha. Un test los compara.
EmisorTipo = Literal["soberano", "provincial", "corporativo", "bcra"]
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


def fecha_o_error(valor, campo: str) -> tuple[str | None, dict | None]:
    """Una fecha `YYYY-MM-DD` validada, o un error COMO DATO. Se devuelve como
    string porque una fecha ISO compara en orden tal cual, que es lo que hace
    falta para una ventana: parsear cada fila sería trabajo para nada."""
    s = str(valor or "").strip()[:10]
    try:
        return date.fromisoformat(s).isoformat(), None
    except ValueError:
        return None, {"error": f"`{campo}` tiene que ser una fecha YYYY-MM-DD, llegó {valor!r}"}


def en_ventana(vto: str | None, desde: str | None, hasta: str | None) -> bool:
    """¿Este vencimiento cae en la ventana? Los bordes ENTRAN. Sin fecha de
    vencimiento queda AFUERA: no se puede probar que esté adentro, y colar un
    título en una ventana que el operador acotó es peor que no traerlo."""
    if not vto:
        return False
    return (desde is None or vto >= desde) and (hasta is None or vto <= hasta)


def texto_ventana(desde: str | None, hasta: str | None) -> str:
    """Cómo se nombra una ventana en el título de una tabla. Vacío si no hay."""
    if desde and hasta:
        return f"vence {desde} → {hasta}"
    if desde:
        return f"vence desde {desde}"
    return f"vence hasta {hasta}" if hasta else ""


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


def curva(curva: Curva, ordenar_por: OrdenCurva = "tea",
          limit: Annotated[int, Field(ge=1, le=MAX_INSTRUMENTOS)] = 15,
          emisor: str | None = None, emisor_tipo: EmisorTipo | None = None,
          vence_desde: Fecha | None = None, vence_hasta: Fecha | None = None,
          con_emisores: bool = False) -> dict:
    """Qué instrumentos hay HOY en una curva de renta fija y cuánto rinden.

    Es el MERCADO, no una cuenta: «qué tengo» es `tenencia_actual`, y rotar un
    título de una cuenta es `opciones_para_rotar` — que ya trae sus alternativas
    comparadas, así que NO la completes con ésta: sería otra tabla de lo mismo.

    `cer` ajusta por inflación · `tasa_fija` = LECAP/BONCAP y «letras» ·
    `hard_dolar` = los que pagan en dólares (AL, GD, ONs), «soberanos» y «hard
    dollar» · `dolar_linked` · `tamar`.

    QUÉ DEVUELVE:
      · `instrumentos` — por bono: ticker, emisor, vencimiento, precio,
        tasas, paridad, duration, volumen. Dos trampas: `tasa_ruido` true =
        esa tasa NO compara (vence en días); `tna_pct` puede venir null aunque
        la pantalla muestre una — no la derives, usá `tea_pct`.
      · `resumen` — los agregados de TODO lo pedido, no de lo que entró acá:
        `tea_pct` y `duration` (min/max/promedio/mediana), `rinde_mas`,
        `vence_primero`, `vence_ultimo`.
      · `emisores` SOLO con `con_emisores` (son ~47 y pesan).
      · `truncado` true = esto es una MUESTRA y los extremos salen de
        `resumen`. Es cocina: contestá con el dato, no con cómo lo conseguiste.

    Args:
        ordenar_por: con `tea` los que más rinden primero.
        limit: si no dijo, 15. Si pidió UNO, pedí pocos.
        emisor: por nombre y por pedazo («YPF» encuentra «YPF S.A.»).
        emisor_tipo: **usalo cuando lo pidan**, no filtres leyendo lo que volvió.
        vence_desde: de ahí en adelante, ese día incluido.
        vence_hasta: hasta ahí, incluido. Con `vence_desde` son la VENTANA:
            «hasta 2029» es `vence_hasta="2029-12-31"`; «más largo que el
            AL30» es pedir su ficha y poner su vencimiento en `vence_desde`.
            ⚠️ ORDENAR NO ES FILTRAR: `ordenar_por="vencimiento"` arranca por
            el más CORTO; para un plazo pedido va la ventana, nunca el orden.
        con_emisores: true si vas a filtrar por uno y no sabés cómo se escribe.
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
    desde = hasta = None
    if vence_desde is not None:
        desde, err = fecha_o_error(vence_desde, "vence_desde")
        if err:
            return err
    if vence_hasta is not None:
        hasta, err = fecha_o_error(vence_hasta, "vence_hasta")
        if err:
            return err
    if desde and hasta and desde > hasta:
        return {"error": f"la ventana está al revés: `vence_desde` ({desde}) es posterior a "
                         f"`vence_hasta` ({hasta})"}
    try:
        bonos = [b for b in CV.get_curvas_vista().get("bonos") or [] if b.get("pill") == curva]
    except Exception as e:
        return {"error": f"no pude leer la curva: {type(e).__name__}: {e}"}
    hoy = date.today()
    filas = _ordenar([_instrumento(b, hoy) for b in bonos], ordenar_por)
    emisores = _emisores_de(filas)
    # ⚠️ EL ORDEN IMPORTA: los filtros van ANTES del recorte. Al revés —ordenar
    # la curva entera, cortar en `limit` y recién ahí filtrar— la selección sale
    # sesgada y la respuesta suena fundamentada igual. Medido el 2026-09-16:
    # «un corporativo HD para rotar» ordenó 125 por TEA, cortó en 15, y de esos
    # solo 4 eran corporativos: se recomendó entre 4 de ~110, y los mejores
    # podían estar en el puesto 40 sin que nadie los mirara.
    if emisor_tipo is not None:
        pedido = str(emisor_tipo).strip().lower()
        if pedido not in get_args(EmisorTipo):
            return {"error": f"`emisor_tipo` tiene que ser uno de {list(get_args(EmisorTipo))}, "
                             f"llegó {emisor_tipo!r}"}
        filas = [f for f in filas if (f.get("emisor_tipo") or "").strip().lower() == pedido]
        if not filas:
            return {"error": f"en la curva {curva} no hay ningún bono {pedido}",
                    "emisores": emisores}
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
    # La ventana va ÚLTIMA a propósito: si además se filtró por emisor, el
    # «no hay ninguno» sale con los vencimientos DE ESE EMISOR, que es lo que
    # hay que contestar, y no con los de la curva entera.
    if desde or hasta:
        previas = filas
        filas = [f for f in filas if en_ventana(f.get("vencimiento"), desde, hasta)]
        if not filas:
            r = _resumen(previas)
            return {"error": f"no hay ningún bono que venza {texto_ventana(desde, hasta)} "
                             f"entre los {len(previas)} pedidos",
                    "vence_primero": r["vence_primero"], "vence_ultimo": r["vence_ultimo"]}
    salida = {
        "curva": curva,
        "emisor": emisor,
        "ordenado_por": ordenar_por,
        "instrumentos": filas[:n],
        "resumen": _resumen(filas),
        "cuantos": len(filas),
        "truncado": len(filas) > n,
        "_tabla": pantalla.tabla(
            "instrumentos",
            ["ticker", "emisor", "vencimiento", "precio", "tea_pct", "duration"],
            " · ".join(x for x in (f"Curva {curva}", emisor, emisor_tipo,
                                   texto_ventana(desde, hasta)) if x)),
    }
    if desde or hasta:
        salida["ventana"] = {"desde": desde, "hasta": hasta}
    if con_emisores:
        salida["emisores"] = emisores
    return salida


def ficha_bono(ticker: str, con_pagos: bool = False) -> dict:
    """Qué ES un bono: quién lo emite, en qué moneda paga, cómo ajusta, cuándo
    vence, qué cupón tiene y cómo cotiza hoy.

    Es la ficha del INSTRUMENTO, sin importar quién lo tenga. NO dice cuánto
    cobra una cuenta de ese bono: eso depende de cuántos nominales tiene, y lo
    contesta `cobros_futuros`. Los montos acá son por 100 de valor nominal.

    QUÉ DEVUELVE:
      · `ficha` — emisor, tipo, curva, moneda, ajuste, ley, emisión,
        vencimiento, valor nominal, cupón anual.
      · `hoy` — precio, `tea_pct` y `tem_pct` (YA en porcentaje), paridad y
        duration de la cotización de hoy, si el bono cotizó.
      · `proximos_pagos` SOLO con `con_pagos`: un renglón por fecha futura,
        por 100 VN. Dibuja una tabla en la pantalla del usuario, así que no lo
        pidas «por las dudas».
      · Un bono que no existe devuelve `error`: decilo, no adivines otro.

    Args:
        ticker: el ticker CORTO del bono, en mayúsculas: `AL30`, `TX26`,
            `S31O5`. Sin sufijo de moneda (`AL30D` es la misma especie que
            `AL30`; usá `AL30`).
        con_pagos: true SOLO si preguntan qué paga, cuándo, o por su
            calendario. Para «qué es», «cuánto rinde» o «cuándo vence», no.
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
    salida = {
        "ticker": r.get("ticker"),
        "ficha": {k: v for k, v in (r.get("ficha") or {}).items()
                  if k in ("emisor", "emisor_tipo", "tipo", "curva", "moneda", "moneda_flujo",
                           "ajuste", "ley", "fecha_emision", "fecha_vencimiento",
                           "valor_nominal", "cupon_anual", "cer_fijado")},
        "unidad": r.get("unidad_flujo"),
        "nota": r.get("nota_flujo"),
        "cuantos_pagos": len(futuros),
        "hoy": {
            "precio": _num(m.get("last_price"), 4),
            "tea_pct": _pct(m.get("TEA")),
            "tem_pct": _pct(m.get("TEM")),
            "paridad_pct": _num(m.get("paridad")),
            "duration": _num(m.get("duration")),
        } if m else None,
    }
    # El calendario de pagos SOLO si lo pidieron. No es un extra gratis: son
    # hasta 24 renglones al contexto y, sobre todo, DIBUJA UNA TABLA en la
    # pantalla del usuario (la tabla es consecuencia del payload — sin el campo
    # no hay tabla). Se veían los flujos del YFCOO en una pregunta sobre rotar,
    # porque el modelo llamó la ficha para saber el emisor y le vino todo.
    if con_pagos:
        salida["proximos_pagos"] = [{
            "fecha": f.get("fecha"),
            "interes": f.get("interes"),
            "amortizacion": f.get("amortizacion"),
            "monto": f.get("monto"),
        } for f in futuros[:MAX_FLUJOS]]
        salida["truncado"] = len(futuros) > MAX_FLUJOS
        salida["_tabla"] = pantalla.tabla(
            "proximos_pagos", ["fecha", "interes", "amortizacion", "monto"],
            f"Próximos pagos del {tk}")
    return salida


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
