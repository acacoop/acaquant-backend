"""api/services/bonos_admin.py — LA PUERTA DE ESCRITURA de los bonos NO-ON.

Los bonos soberanos / tasa_fija (Lecaps/Boncaps) / CER viven DIRECTO en
`mercado.curvas` (no hay master intermedio). Las ONs NO: van por `ons.upsert_on`.

⚠️ **EL PANEL QUE LO USABA YA NO EXISTE.** El tab `/manager → TÍTULOS → BONOS`
se borró: lo que hacía lo hace EL AV AGENT. Con él se fueron `list_bonos`,
`delete_bono`, `bonos_sin_tasa` y `parse_flujos_bono` (el "pegar Excel" del form),
que no los llamaba nadie más. **Lo que queda es lo que usa `agente/alta.py`**:
`upsert_bono` (arreglos `alta_bono` / `alta_flujos`) y `curvas_validas`
(paso `rama` del pre-flight). Si volviera a hacer falta una carga manual, el
formulario y el parser están en el historial de git, no reescritos de cero.

Dos formas de flujo:
  - BULLET (Lecap/Boncap tasa_fija): `flujo_vencimiento` (por 100 VN) al `fecha_vencimiento`.
    No lleva array de flujos. El motor de acreencias/valuación lo proyecta como pago único.
  - CRONOGRAMA (cupón / amortización): array `flujos` [{fecha, amortizacion, interes,
    valor_residual}].

Escritura directa a Curvas con upsert por `ticker_corto` (su clave única) → el motor
lo toma en el próximo loop. Puro (sin FastAPI).
"""
from __future__ import annotations

from datetime import UTC, datetime

from api.services.ons import _f, _fecha_flujo_iso, _fecha_iso, _upsert_curva_doc
from core import curvas_ejes as ce

# Curvas que escribe esta puerta (las ONs van por ons.py; mercado las maneja el motor).
# Las curvas que el CÓDIGO conoce de siempre. **No es la lista completa**: usar
# `curvas_validas()`.
CURVAS_BONO = ("tasa_fija", "cer", "soberanos", "dolar_linked", "tamar", "dual")


def curvas_validas() -> tuple[str, ...]:
    """Las curvas escribibles HOY = las del código ∪ **las del catálogo**.

    ⚠️ **Era una tupla a mano y por eso mentía.** El AV Agent puede CREAR curvas
    (`mercado.curvas_catalogo`, E1.h) y de hecho creó `badlar`: la pill existe en
    la vista de renta fija. Pero esta constante no se enteraba, así que el alta de
    un bono BADLAR se rechazaba con «curva inválida» **por una curva que el propio
    sistema ya tiene**.

    Es el mismo patrón que viene apareciendo toda la semana: dos fuentes para la
    misma pregunta —«¿qué curvas existen?»— y solo una se actualiza. Ahora la
    constante es el PISO y el catálogo la amplía, así que crear una curva la deja
    escribible en el mismo acto, sin tocar código.

    Degrada al piso si el catálogo no responde: es exactamente el comportamiento
    anterior, que es el peor caso aceptable.
    """
    try:
        from core import curvas_catalogo
        extra = tuple(a for a in curvas_catalogo.todas() if a not in CURVAS_BONO)
    except Exception:
        extra = ()
    return CURVAS_BONO + extra


# Campos doc nivel-bono que el alta puede setear (según tipo).
_DOC_STR = ("tipo", "moneda_flujo", "tasa_referencia", "emisor")  # strings tal cual
_DOC_NUM = ("cer_emision", "cupon_anual")                  # numéricos
_DOC_FECHA = ("fecha_emision", "fecha_vencimiento")        # fechas ISO
# Campos numéricos de un FLUJO (pass-through; el subset depende del tipo de bono).
_FLUJO_NUM = ("amortizacion", "interes", "valor_residual", "amortizacion_pct",
              "cupon_sobre_residual", "residual_previo_pct", "cupon_anual")


def upsert_bono(payload: dict, actor: str = "") -> dict:
    """Crea/edita un bono directo en Trading.Curvas (upsert por `ticker_corto`).
    Guarda las MISMAS shapes de Trading.Curvas según el tipo (no se inventa nada):
      - lecap/boncap (tasa_fija) → bullet `flujo_vencimiento`.
      - cer  → flujos {fecha, amortizacion_pct, cupon_sobre_residual, residual_previo_pct} + cer_emision/cupon_anual.
      - dual/tamar → flujos {fecha, amortizacion_pct} + tasa_referencia.
      - tasa_fija con cupón → flujos {fecha, amortizacion, interes}.
      - soberanos → flujos {fecha, amortizacion_pct, cupon_sobre_residual}.
    """
    tc = (payload.get("ticker_corto") or "").strip()
    if not tc:
        raise ValueError("falta 'ticker_corto'")
    ticker = (payload.get("ticker") or "").strip()
    if not ticker:
        raise ValueError("falta 'ticker' (completo, ej 'MERV - XMEV - T30J6 - 24hs')")
    curva = (payload.get("curva") or "").strip()
    validas = curvas_validas()
    if curva not in validas:
        raise ValueError(f"curva inválida: {curva!r} (válidas: {', '.join(validas)})")

    doc: dict = {
        "ticker": ticker,
        "ticker_corto": tc,
        "curva": curva,
        "valor_nominal": _f(payload.get("valor_nominal"), 100.0) or 100.0,
        "actualizado_por": actor,
        "actualizado_at": datetime.now(UTC),
    }
    for k in _DOC_STR:
        v = payload.get(k)
        if v not in (None, ""):
            doc[k] = v.upper() if k == "moneda_flujo" else v
    for k in _DOC_NUM:
        if payload.get(k) is not None:
            doc[k] = _f(payload.get(k))
    for k in _DOC_FECHA:
        fi = _fecha_iso(payload.get(k))
        if fi:
            doc[k] = fi

    # Flujo: bullet (Lecap/Boncap) o cronograma (pass-through de los campos del tipo).
    fv = payload.get("flujo_vencimiento")
    flujos_in = payload.get("flujos")
    if flujos_in:
        flujos = []
        for fl in flujos_in:
            fi = _fecha_flujo_iso(fl.get("fecha"))
            if not fi:
                continue
            row: dict = {"fecha": fi}
            for ff in _FLUJO_NUM:
                if fl.get(ff) is not None:
                    row[ff] = _f(fl.get(ff))
            flujos.append(row)
        if not flujos:
            raise ValueError("los flujos no tienen ninguna fecha válida")
        doc["flujos"] = flujos
        doc["flujo_vencimiento"] = None   # cronograma → sin bullet
    elif fv is not None and _f(fv) > 0:
        if not doc.get("fecha_vencimiento"):
            raise ValueError("el bullet (flujo_vencimiento) necesita 'fecha_vencimiento'")
        doc["flujo_vencimiento"] = _f(fv)
    else:
        raise ValueError("falta el flujo: 'flujo_vencimiento' (bullet) o 'flujos' (cronograma)")

    # Los EJES (emisor_tipo/moneda_eje/ajuste/ajuste_alt/ley) van directo a las
    # COLUMNAS, no al doc: `normalizar_ejes` valida el dominio y, sobre todo, que
    # `ajuste_alt != ajuste` — un dual consigo mismo entra sin ruido y se ve bien.
    # Hasta hoy los ejes solo los escribía un script one-shot, así que un bono dado
    # de alta acá nacía SIN clasificar y no aparecía en la vista de renta fija.
    ejes = ce.normalizar_ejes(payload)
    saved = _upsert_curva_doc(doc, ejes)
    return {"bono": saved}
