"""core/postrade_margenes.py — márgenes requeridos y balance de saldos, planos.

Las dos cosas que le faltan a la cabecera del reporte de la mesa:

    Requerimiento de Márgenes  →  PosTrade/MarginRequirementReport
    Activo Integrado           →  PosTrade/AccountBalance, cuenta contable 12

Igual que `core/postrade_posicion`, esto está separado del transporte para que
el aplanado sea **puro**: recibe la respuesta y devuelve filas, sin red y sin
base. Es lo que lo hace testeable de verdad contra el ejemplo del manual.

## La respuesta de márgenes viene en CUATRO niveles

    Value[]                      ← agente (ClearingMember) + fecha [+ ProductGroup]
      └ Accounts[]               ← cuenta de compensación
          └ SubAccounts[]        ← cuenta de neteo  ← ACÁ está el comitente
              └ References[]     ← ACÁ está el importe, con su MONEDA

Se aplana a una fila por hoja porque un blob de cuatro niveles no se puede
sumar, filtrar ni comparar sin volver a parsearlo en cada consulta — la misma
razón por la que se expandió `PositionQty` en la posición.

⚠️ **`viewDetails=true` agrega `ProductGroup`** (DLR, SOJ…) al primer nivel. NO
es "lo mismo con más detalle": con desglose, la MISMA cuenta aparece una vez por
grupo de producto. Sumar las dos respuestas juntas contaría todo dos veces.

⚠️ **Los importes vienen NEGATIVOS** en el ejemplo del manual (`Margin:
-16800000`). Se preservan tal cual: darlos vuelta acá sería una decisión de
presentación metida en la capa que trae el dato, y el día que la cámara mande un
positivo real nadie entendería por qué cambió de signo.

⚠️ **Cada importe trae su `Currency`.** No se suman entre monedas: es la misma
regla que rige toda la vista AP5 (el agro liquida en Dólar MtR y el dólar futuro
en Pesos).
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

METODO_MARGENES = "MarginRequirementReport"
METODO_SALDOS = "AccountBalance"

# Las cuentas contables de `AccountBalance`, tal como las publica el manual.
# Están acá y no sueltas en un script porque son un CATÁLOGO del proveedor: el
# día que haga falta otra (garantías, resultados), el código ya sabe cómo se
# llama en vez de mostrar un número pelado.
CUENTAS_CONTABLES: dict[str, str] = {
    "11": "Cta. Comp. y Liquidación", "12": "Gtía inicial",
    "13": "Integración Márgenes U$S", "14": "Integración Márgenes",
    "21": "Márgenes", "22": "Diferencias", "23": "Primas", "24": "Resultados",
    "26": "Diferencia de cambio por Cta.Registro",
    "31": "Tasas", "32": "IVA Tasas",
    "34": "Retención Ganancias por Resultados", "35": "Derechos a transferir",
    "41": "Márgenes especiales", "43": "Liq. Valores Negociables",
    "44": "Mercadería", "45": "Corretaje", "47": "Liq. Final",
    "53": "IVA Crédito", "54": "Retenciones ACSA IVA/SUSS", "55": "IVA Débito",
    "56": "Ret IVA a Vendedores", "57": "Retención Ganancias por Entrega",
    "58": "Impuesto a los Sellos", "59": "Retención Ingresos Brutos",
    "61": "Retenciones ACSA Gcias.", "62": "Retenciones RFX IVA/SUSS/Gcias",
    "63": "Retenciones PMY IVA/SUSS/Gcias",
    "90": "Cuentas regularizadoras Depositarias",
}

# La que le interesa a la mesa para el ACTIVO INTEGRADO.
CUENTA_GARANTIA_INICIAL = "12"


def _num(v: Any) -> float | None:
    """El número, o None si no vino. Un importe ausente NO es cero: en un
    requerimiento de márgenes, 'no informó' y 'no debe nada' son cosas
    distintas y solo una es afirmable."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _txt(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def aplanar_margenes(crudo: Any) -> tuple[list[dict], dict[str, int]]:
    """`Value` de MarginRequirementReport → (filas planas, contadores).

    PURA: no toca red ni base. Una fila por hoja del árbol
    (agente → cuenta de compensación → cuenta de neteo → referencia).

    Los contadores viajan porque un nivel vacío tiene que ser VISIBLE: si la
    cámara deja de mandar `SubAccounts`, el total daría 0 y sin el conteo se
    leería igual que "hoy no hay márgenes".
    """
    filas: list[dict] = []
    stats = {"agentes": 0, "cuentas": 0, "subcuentas": 0, "referencias": 0,
             "sin_cuentas": 0, "sin_subcuentas": 0, "sin_referencias": 0}

    if not isinstance(crudo, list):
        return filas, stats

    for nivel1 in crudo:
        if not isinstance(nivel1, dict):
            continue
        stats["agentes"] += 1
        cuentas = nivel1.get("Accounts")
        if not isinstance(cuentas, list) or not cuentas:
            stats["sin_cuentas"] += 1
            continue

        for cta in cuentas:
            if not isinstance(cta, dict):
                continue
            stats["cuentas"] += 1
            subs = cta.get("SubAccounts")
            if not isinstance(subs, list) or not subs:
                stats["sin_subcuentas"] += 1
                continue

            for sub in subs:
                if not isinstance(sub, dict):
                    continue
                stats["subcuentas"] += 1
                refs = sub.get("References")
                if not isinstance(refs, list) or not refs:
                    stats["sin_referencias"] += 1
                    continue

                for ref in refs:
                    if not isinstance(ref, dict):
                        continue
                    stats["referencias"] += 1
                    filas.append({
                        "fecha": _txt(nivel1.get("Date"))[:10],
                        "agente": _txt(nivel1.get("ClearingMember")),
                        "agente_codigo": _txt(nivel1.get("ClearingMemberCode")),
                        # Solo con `viewDetails=true`. Vacío = la respuesta SIN
                        # desglose: mezclarlas contaría todo dos veces.
                        "grupo_producto": _txt(nivel1.get("ProductGroup")),
                        "cuenta_compensacion": _txt(cta.get("CompensationAccount")),
                        "cuenta_compensacion_codigo": _txt(cta.get("CompensationAccountCode")),
                        # El comitente: es la clave que une con `ap5.cuentas`.
                        "cuenta": _txt(sub.get("NettingAccountCode")),
                        "cuenta_nombre": _txt(sub.get("NettingAccount")),
                        "referencia": _txt(ref.get("Reference")),
                        "moneda": _txt(ref.get("Currency")),
                        "margen": _num(ref.get("Margin")),
                        "primas": _num(ref.get("OptionAmount")),
                        "inter_temporal": _num(ref.get("InterTempAmount")),
                    })

    return filas, stats


# Los tres importes que la cámara manda por referencia. Se guardan los tres —
# son el registro de lo que mandó el proveedor y tirarlos sería no poder
# auditarlos nunca— pero **el importe que cuenta es `margen`**.
IMPORTES = ("margen", "primas", "inter_temporal")

# ⚠️ **EL IMPORTE ES `Margin`, y sólo `Margin`** (determinado por el user contra
# el número real de la mesa, 2026-08-25). Hubo una versión que sumaba los tres
# campos: se generalizó desde una fila de `Cauciones $` que traía el número en
# `InterTempAmount`, y fue una invención mía, no una medición. **`Márgenes` trae
# un `InterTempAmount` no nulo que NO se cuenta**, así que sumar los tres inflaba
# el total — y no fallaba: daba un número creíble.
CAMPO_IMPORTE = "margen"


def por_cuenta_de_neteo(filas: list[dict]) -> list[dict]:
    """Una fila por (cuenta de neteo, cuenta de compensación, CONCEPTO, moneda).

    ⚠️ **`Reference` es el nombre del CONCEPTO, no un identificador** (medido
    2026-08-25: `Márgenes` ×28, `Inicial A3`, `Inicial FGIMC`, `Cauciones $`).
    Es parte de la identidad porque las cards suman conceptos DISTINTOS — el
    activo integrado es `Márgenes + Inicial A3` y el requerimiento no. Colapsar
    los conceptos en un total por cuenta haría imposible separarlos después, y
    la única forma de recuperar el número sería volver a pegarle a la cámara.

    ⚠️ **`primas` e `inter_temporal` se GUARDAN pero no se suman.** Están para
    poder mirarlos el día que alguien pregunte; el importe de cada concepto es
    `margen`. Ver `CAMPO_IMPORTE`.

    ⚠️ **El signo se preserva** (vienen negativos). Darlo vuelta es una decisión
    de presentación y vive en la vista, no acá.
    """
    por: dict[tuple[str, str, str, str], dict] = {}
    for f in filas:
        k = (f.get("cuenta") or "", f.get("cuenta_compensacion_codigo") or "",
             f.get("referencia") or "", f.get("moneda") or "")
        d = por.setdefault(k, {
            "cuenta": k[0], "cuenta_compensacion": k[1], "concepto": k[2],
            "moneda": k[3], "referencias": 0,
            "titular": f.get("cuenta_nombre") or "",
            **{c: 0.0 for c in IMPORTES},
        })
        d["referencias"] += 1
        for c in IMPORTES:
            d[c] += f.get(c) or 0.0
        if not d["titular"]:
            d["titular"] = f.get("cuenta_nombre") or ""

    return [{**d, **{c: round(d[c], 2) for c in IMPORTES}}
            for d in sorted(por.values(),
                            key=lambda x: (x["cuenta"], x["cuenta_compensacion"],
                                           x["concepto"], x["moneda"]))]


def totales_por_moneda(filas: list[dict]) -> list[dict]:
    """Σ de cada importe, POR MONEDA. Nunca un total único.

    Es la misma regla de toda la vista AP5: sumar Pesos con Dólar MtR da un
    número, no falla, y no significa nada.
    """
    por: dict[str, dict] = {}
    for f in filas:
        d = por.setdefault(f["moneda"] or "(sin moneda)", {
            "moneda": f["moneda"] or "(sin moneda)",
            "margen": 0.0, "primas": 0.0, "inter_temporal": 0.0, "cuentas": set(),
        })
        for campo in ("margen", "primas", "inter_temporal"):
            d[campo] += f[campo] or 0.0
        d["cuentas"].add(f["cuenta"])
    return [
        {**d, "cuentas": len(d["cuentas"]), **{k: round(d[k], 2)
         for k in ("margen", "primas", "inter_temporal")}}
        for d in sorted(por.values(), key=lambda x: x["moneda"])
    ]
