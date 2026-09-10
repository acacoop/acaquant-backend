"""api/services/bono_detalle.py — LA FICHA DE UN BONO, en un request.

Doc: `docs/RENTA_FIJA.md` §0 (paso 19). Service PURO (sin FastAPI).

**Qué contesta.** Lo que la tabla de la tab CURVAS no puede: por qué ese bono
rinde lo que rinde. La tabla da una fila —precio, TEA, duration— y la pregunta que
sigue siempre es la misma: *¿y cuándo paga?*. Eso es el CRONOGRAMA, que hasta hoy
no estaba en ninguna pantalla de mercado (existía dentro de `/api/titulos/flujos`,
que devuelve los 222 bonos completos — 240 KB para mirar uno).

**La regla que ordena todo este archivo: el cronograma sale de la MISMA función
que usa el motor para calcular la TEA que muestra la tabla.**

`engines.curvas` no tiene UNA fórmula de flujo, tiene TRES, y cuál toca lo decide
`rama_calculo` a partir de los EJES del bono:

    rama `soberanos` / `dolar_linked` → `monto_flujo_soberano`  (amortizacion_pct
                                        + cupon_sobre_residual, los dos ya por 100 VN)
    rama `cer`                        → `monto_flujo_cer`        (porcentual, sin el
                                        ajuste por CER: ver `unidad_flujo`)
    rama `on` / `tasa_fija` / resto   → `monto_flujo`            (amortizacion + interes,
                                        montos absolutos por 100 VN)

Si acá se sumara `amortizacion + interes` para todos —que es lo que uno escribe
sin mirar—, los soberanos y los CER darían CERO (sus campos se llaman distinto) y
el modal mostraría un bono que no paga nada al lado de una TEA del 12%. No falla,
no avisa: dibuja un gráfico vacío que parece un dato. Por eso el despacho se
delega en `rama_calculo` en vez de repetirse acá (REGLA #9: dos copias de la misma
regla siempre terminan eligiendo distinto).

**El BULLET no tiene array de flujos.** Las Lecaps/Boncaps pagan todo junto al
vencimiento y el master lo guarda en `flujo_vencimiento`, no en `flujos`. Sin este
caso, la mitad de la pill TASA FIJA —la que más se mira— abriría el modal con el
cronograma vacío. Mismo tratamiento que ya hace `acreencias.calendario_instrumentos`.

**Lo que NO hace: no calcula tasas.** La TEA, la duration, la paridad y el margen
salen de `curvas_vista`, o sea de la misma consulta que dibuja la fila de la tabla.
Recalcularlas acá sería abrir la puerta a que el modal y la tabla muestren dos
números distintos para el mismo bono, que es exactamente el bug que este repo se
pasó el mes arreglando.
"""
from __future__ import annotations

from datetime import date

from api.cache import cached
from api.services._sql import _f
from core import curvas_sql

# Qué significa "100" en la columna MONTO, por rama. Viaja al front para que el
# eje del gráfico lo diga: un número por 100 VN sin unidad no se puede leer, y
# peor, se lee mal (un CER sin ajustar parece que rinde la mitad).
_UNIDAD = {
    "soberanos":    "USD por 100 VN",
    "dolar_linked": "USD por 100 VN",
    "cer":          "ARS por 100 VN a valores de EMISIÓN",
    "cer_fijado":   "ARS por 100 VN a valores de EMISIÓN",
    "tasa_fija":    "ARS por 100 VN",
    "on":           "por 100 VN",
    "otros":        "por 100 VN",
}

# La nota que acompaña a la unidad cuando el número NO es lo que se cobra.
_NOTA = {
    "cer": (
        "Los montos son contractuales, a valores de emisión: lo que se cobra es "
        "cada uno multiplicado por CER(liquidación) / CER(emisión). Ese "
        "coeficiente todavía no existe para los flujos lejanos, así que ajustar "
        "el cronograma entero obligaría a proyectar inflación — y una proyección "
        "dibujada igual que un dato contractual es la peor forma de mostrarla."
    ),
}


def _monto(rama: str, f: dict, vn: float):
    """El monto de UN flujo, con la fórmula de la rama. Delega en el motor."""
    from engines.curvas import (
        monto_flujo,
        monto_flujo_cer,
        monto_flujo_soberano,
    )
    if rama in ("soberanos", "dolar_linked"):
        return monto_flujo_soberano(f, vn)
    if rama == "cer":
        return monto_flujo_cer(f, vn)
    return monto_flujo(f)


def cronograma(doc: dict) -> tuple[str, list[dict]]:
    """`(rama, flujos)` de un doc del master. Montos por 100 VN, ordenado por fecha.

    Cada fila trae sus componentes (`amortizacion` / `interes`) además del total,
    porque son cosas distintas para quien mira: la amortización devuelve capital y
    baja el residual, el interés no. Los nombres de los campos de origen cambian
    según la rama — acá salen normalizados con UN solo nombre.
    """
    from engines.curvas import fecha_flujo, rama_calculo

    rama = rama_calculo(doc)
    vn = float(doc.get("valor_nominal") or 100)
    hoy = date.today()
    out: list[dict] = []

    for f in doc.get("flujos") or []:
        fd = fecha_flujo(f)
        if not fd:
            continue
        monto = _monto(rama, f, vn)
        if monto is None:
            continue
        # Amortización e interés, cada uno con el nombre que usa SU rama. El
        # `or 0` no oculta nada: un flujo que existe y no amortiza amortiza cero.
        if rama in ("soberanos", "dolar_linked", "cer"):
            amort = float(f.get("amortizacion_pct") or 0) / 100 * vn
            interes = float(monto) - amort
        else:
            amort = float(f.get("amortizacion") or 0)
            interes = float(f.get("interes") or 0)
        out.append({
            "fecha": fd.isoformat(),
            "amortizacion": round(amort, 6),
            "interes": round(interes, 6),
            "monto": round(float(monto), 6),
            "residual_previo_pct": _f(f.get("residual_previo_pct")
                                      or f.get("valor_residual")),
            "futuro": fd >= hoy,
        })

    # BULLET (Lecap/Boncap y cualquier papel sin array de flujos): todo el pago
    # vive en `flujo_vencimiento`. Sin esto el modal de media pill TASA FIJA
    # abriría vacío — y "sin cronograma cargado" y "paga todo al final" se verían
    # igual, que es el modo de falla que este repo persigue.
    if not out:
        fv = _f(doc.get("flujo_vencimiento"))
        vto = _fecha(doc.get("fecha_vencimiento"))
        if fv and fv > 0 and vto:
            out.append({
                "fecha": vto.isoformat(),
                "amortizacion": round(vn, 6),
                "interes": round(fv - vn, 6),
                "monto": round(fv, 6),
                "residual_previo_pct": 100.0,
                "futuro": vto >= hoy,
                "bullet": True,
            })

    out.sort(key=lambda x: x["fecha"])
    # ACUMULADO: lo que se lleva cobrado sumando los pagos FUTUROS en orden
    # (la compra es hoy; lo ya pagado no se cobra y va en None). Se calcula acá
    # y no en el front —REGLA de acaquant-web: el front no suma nada.
    acum = 0.0
    for f in out:
        if f["futuro"]:
            acum += f["monto"]
            f["acumulado"] = round(acum, 6)
        else:
            f["acumulado"] = None
    return rama, out


def _fecha(raw) -> date | None:
    if isinstance(raw, date):
        return raw
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except (ValueError, TypeError):
        return None


@cached(ttl=30)
def get_bono(ticker: str) -> dict:
    """La ficha completa de UN bono: identidad, cronograma y sus métricas live.

    `ticker` es el CORTO (`AL30`), el mismo que muestra la tabla y el que es PK de
    `mercado.curvas`. Devuelve `{"error": ...}` en vez de tirar: el modal tiene que
    poder decir "no encontré este bono" sin romper la pantalla que lo abrió.
    """
    tk = (ticker or "").strip().upper()
    if not tk:
        return {"error": "ticker vacío"}

    doc = curvas_sql.find_one(tk)
    if not doc:
        return {"error": f"{tk} no está en el master de curvas"}

    rama, flujos = cronograma(doc)
    futuros = [f for f in flujos if f["futuro"]]

    # Las MÉTRICAS salen de la vista, no de una segunda consulta al snapshot: así
    # el modal no puede mostrar una TEA distinta de la fila que lo abrió. Se busca
    # por `ticker_corto` y se toma la fila de la pill PRINCIPAL — un dual sale dos
    # veces (una por pata) y las dos viajan en `patas`.
    from api.services.curvas_vista import get_curvas_vista
    filas = [b for b in get_curvas_vista().get("bonos", [])
             if b.get("ticker_corto") == tk]
    principal = next((b for b in filas if b.get("pata") == doc.get("ajuste")),
                     filas[0] if filas else None)

    unidad = _UNIDAD.get(rama, "por 100 VN")
    # Un CER YA FIJADO no tiene el problema del CER: su coeficiente está
    # publicado, así que la nota sobraría y sería confusa.
    fijado = bool(principal and principal.get("cer_fijado"))
    nota = None if fijado else _NOTA.get(rama)

    return {
        "ticker": tk,
        "instrumento": doc.get("ticker"),          # símbolo de mercado
        "ficha": {
            "emisor":       doc.get("emisor"),
            "emisor_tipo":  doc.get("emisor_tipo"),
            "industria":    principal.get("industria") if principal else None,
            "tipo":         doc.get("tipo"),
            "curva":        doc.get("curva"),
            "moneda":       doc.get("moneda_eje"),
            "moneda_flujo": doc.get("moneda_flujo"),
            "ajuste":       doc.get("ajuste"),
            "ajuste_alt":   doc.get("ajuste_alt"),
            "ley":          doc.get("ley"),
            "fecha_emision":     str(doc.get("fecha_emision") or "")[:10] or None,
            "fecha_vencimiento": str(doc.get("fecha_vencimiento") or "")[:10] or None,
            "valor_nominal":     _f(doc.get("valor_nominal")),
            "cupon_anual":       _f(doc.get("cupon_anual")),
            "cer_emision":       _f(doc.get("cer_emision")),
            "flujo_vencimiento": _f(doc.get("flujo_vencimiento")),
            "cer_fijado":        fijado,
        },
        # La RAMA viaja a propósito: es la que decide la fórmula del cronograma Y
        # la de la TEA. Verla en el modal es cómo se descubre que un bono está mal
        # clasificado sin tener que abrir el motor.
        "rama": rama,
        "unidad_flujo": unidad,
        "nota_flujo": nota,
        "flujos": flujos,
        "resumen": {
            "n_pagos_futuros": len(futuros),
            "proximo_pago": futuros[0] if futuros else None,
            "total_futuro": round(sum(f["monto"] for f in futuros), 6) if futuros else 0.0,
            "ultimo_pago": futuros[-1] if futuros else None,
        },
        # Las patas: un dual tiene dos, y cada una su tasa y su procedencia.
        "patas": [
            {
                "pill": b.get("pill"), "lado": b.get("lado"), "pata": b.get("pata"),
                "metrics": b.get("metrics") or {},
                "tea_fuente": b.get("tea_fuente"), "tea_fecha": b.get("tea_fecha"),
                "margen": b.get("margen"), "tasa_ruido": b.get("tasa_ruido"),
                "tc_breakeven": b.get("tc_breakeven"),
            }
            for b in filas
        ],
    }
