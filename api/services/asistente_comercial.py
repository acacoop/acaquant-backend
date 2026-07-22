"""asistente_comercial — el bloque COMERCIAL del asistente de negocio.

Por qué vive en su propio módulo: `asistente_tools` es el aggregator (schemas
+ dispatcher); cada DOMINIO trae sus tools desde acá. Sumar un dominio nuevo
es un módulo nuevo y dos líneas en el aggregator, no 300 líneas más en un
archivo de mil.

Por qué UNA tool y no cinco: las cinco preguntas comerciales de la auditoría
(#20 estado de cartera, #27 objetivos, #29 ranking de comerciales, #30 cuentas
sin comercial) comparten el MISMO gate, el MISMO tratamiento de identidades y
la MISMA forma de salida. Cinco tools gemelas serían cinco descriptions entre
las que el modelo rutea mal (hallazgo del patrón #4 de `docs/TOOLS_IA.md`).
Acá es un REGISTRO: agregar una lente comercial es una fila.

REGLAS que no se negocian:
- GATE: todo esto lo protege Control Comercial, un permiso POR USUARIO (no es
  el rol). Sin el flag no sale ni un número.
- PII: los COMERCIALES son empleados, y tampoco cruzan el perímetro — salen
  como OPERADOR_n. Los clientes, como CLIENTE_n. Los EMAILS no salen nunca,
  ni siquiera fichados (son una identidad directa y no aportan nada al
  análisis).
- READ-ONLY.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from core import pii_gateway

logger = logging.getLogger(__name__)

# Cuánto detalle vuelve al modelo. Un solo lugar: es una decisión de costo.
_TOP_FILAS = 12


def _hoy() -> date:
    return (datetime.now(UTC) - timedelta(hours=3)).date()


def _rango(args: dict) -> tuple[str, str]:
    """Default: el mes en curso. Las lentes comerciales son de período, no de
    foto — sin rango explícito la pregunta casi siempre es 'este mes'."""
    hoy = _hoy()
    desde = str(args.get("desde") or "").strip() or hoy.replace(day=1).isoformat()
    hasta = str(args.get("hasta") or "").strip() or hoy.isoformat()
    return desde, hasta


def _monto(v: float | None, moneda: str) -> str:
    """Reusa el formateador del aggregator para que TODA la salida del
    asistente hable igual (mismos cortes de millones/miles)."""
    from api.services.asistente_tools import _monto as fmt
    return fmt(v, moneda)


def _pct(v) -> str:
    return f" ({float(v):+.1f}%)" if v is not None else ""


def _ficha_operador(nombre: str | None, mapping: dict) -> str:
    """Un comercial SIEMPRE vuelve como OPERADOR_n. Si la fila no tiene
    nombre, se dice que no está asignado — jamás se cae al email."""
    n = (nombre or "").strip()
    if not n or "@" in n:      # sin nombre real → el email NO sale, ni fichado
        return "(comercial sin nombre cargado)"
    return pii_gateway.asignar_ficha(mapping, "OPERADOR", n)


def _ficha_cliente(denominacion: str | None, mapping: dict) -> str:
    d = (denominacion or "").strip()
    if not d or d in ("—", "-"):
        return "(sin denominación)"
    return pii_gateway.asignar_ficha(mapping, "CLIENTE", d)


# ── LENTES ───────────────────────────────────────────────────────────────────
# Cada lente: (etiqueta, ayuda_para_el_modelo, funcion). La función recibe
# (args, mapping, moneda) y devuelve el texto ya sin identidades.

def _lente_operadores(args: dict, mapping: dict, moneda: str) -> str:
    from api.services import control_comercial_sql as cc

    desde, hasta = _rango(args)
    r = cc.datos_por_operador(desde=desde, hasta=hasta, moneda=moneda) or {}
    filas = r.get("filas") or []
    if not filas:
        return f"sin actividad por comercial entre {desde} y {hasta}"
    lineas = [f"[comerciales — {desde} a {hasta}, {moneda}; los % son contra el "
              f"período anterior de igual largo]"]
    for f in filas[:_TOP_FILAS]:
        lineas.append(
            f"  - {_ficha_operador(f.get('operador_nombre'), mapping)}: "
            f"volumen {_monto(f.get('volumen'), moneda)}{_pct(f.get('volumen_pct'))} · "
            f"comisiones {_monto(f.get('comisiones'), moneda)}"
            f"{_pct(f.get('comisiones_pct'))} · AuM {_monto(f.get('aum'), moneda)}"
            f"{_pct(f.get('aum_pct'))} · {int(f.get('clientes_activos') or 0)} clientes "
            f"activos{_pct(f.get('clientes_activos_pct'))}, "
            f"{int(f.get('clientes_inactivos') or 0)} inactivos")
    if len(filas) > _TOP_FILAS:
        lineas.append(f"  (y {len(filas) - _TOP_FILAS} comerciales más)")
    return "\n".join(lineas)


def _lente_objetivos(args: dict, mapping: dict, moneda: str) -> str:
    from api.services import control_comercial_sql as cc

    desde, hasta = _rango(args)
    r = cc.objetivos_vs_actual(desde=desde, hasta=hasta, moneda=moneda) or {}
    filas = [f for f in (r.get("filas") or [])
             if f.get("volumen_objetivo") or f.get("comisiones_objetivo")]
    if not filas:
        return (f"no hay objetivos cargados para el período {desde} a {hasta} "
                "(se cargan por mes en Manager)")
    filas.sort(key=lambda f: (f.get("pct_alcanzado") is None, f.get("pct_alcanzado") or 0))
    lineas = [f"[objetivos vs real — {desde} a {hasta}, {moneda}; el objetivo es la "
              f"suma de los objetivos mensuales que caen en el rango]"]
    for f in filas[:_TOP_FILAS]:
        pct = f.get("pct_alcanzado")
        lineas.append(
            f"  - {_ficha_operador(f.get('operador_nombre'), mapping)}: "
            f"volumen {_monto(f.get('volumen_actual'), moneda)} de "
            f"{_monto(f.get('volumen_objetivo'), moneda)} · comisiones "
            f"{_monto(f.get('comisiones_actual'), moneda)} de "
            f"{_monto(f.get('comisiones_objetivo'), moneda)}"
            + (f" · alcanzado {float(pct):.0f}%" if pct is not None else " · sin objetivo"))
    lineas.append("Ordenado de MENOS a MÁS alcanzado: los primeros son los que están "
                  "más lejos del objetivo.")
    return "\n".join(lineas)


def _lente_cartera(args: dict, mapping: dict, moneda: str) -> str:
    from api.services import comercial_sql

    ficha_op = str(args.get("ficha_operador") or "").strip()
    operador = "__todos__"
    etiqueta = "toda la mesa"
    if ficha_op:
        operador = pii_gateway.operador_de_ficha(ficha_op, mapping) or ""
        if not operador:
            return (f"no pude resolver {ficha_op} a un comercial — preguntale al "
                    "usuario de qué comercial habla")
        etiqueta = ficha_op
    r = comercial_sql.analisis_comercial(operador=operador, moneda=moneda) or {}
    clientes = r.get("clientes") or []
    if not clientes:
        return f"sin clientes en la cartera de {etiqueta}"
    # El estado comercial lo clasifica el SERVICE (mismos cortes que la vista):
    # acá solo se agrupa, no se re-inventan umbrales.
    por_estado: dict[str, dict] = {}
    for c in clientes:
        e = por_estado.setdefault(str(c.get("estado") or "?"), {"n": 0, "aum": 0.0})
        e["n"] += 1
        e["aum"] += float(c.get("aum") or 0)
    total_aum = sum(e["aum"] for e in por_estado.values())
    lineas = [f"[cartera comercial de {etiqueta} — {len(clientes)} clientes, "
              f"AuM {_monto(total_aum, moneda)} · corte de estado: activa ≤"
              f"{r.get('dias_activa')} días sin operar, dormida >"
              f"{r.get('dias_dormida')}]"]
    for estado, e in sorted(por_estado.items(), key=lambda x: -x[1]["aum"]):
        pct = 100 * e["aum"] / total_aum if total_aum else 0
        lineas.append(f"  - {estado}: {e['n']} clientes · {_monto(e['aum'], moneda)} "
                      f"({pct:.1f}% del AuM de la cartera)")
    # Los dormidos con plata son la acción concreta que sale de esta pregunta.
    dormidos = sorted((c for c in clientes
                       if str(c.get("estado") or "").upper().startswith("DORMIDA")),
                      key=lambda c: -float(c.get("aum") or 0))[:5]
    if dormidos:
        lineas.append("Los DORMIDOS con más AuM (a quién llamar primero):")
        for c in dormidos:
            lineas.append(f"  - {_ficha_cliente(c.get('denominacion'), mapping)}: "
                          f"{_monto(c.get('aum'), moneda)} · última operación "
                          f"{c.get('ultima_op') or 'nunca'}")
    return "\n".join(lineas)


def _lente_sin_operador(args: dict, mapping: dict, moneda: str) -> str:
    from api.services import sin_operador

    r = sin_operador.cuentas_sin_operador() or {}
    clientes = r.get("clientes_sin_operador") or []
    n_no_clientes = int(r.get("n_no_clientes") or 0)
    if not clientes:
        return ("no hay cuentas de clientes operando sin comercial asignado"
                + (f" ({n_no_clientes} cuentas sin comercial son internas/no "
                   "clientes, no hace falta asignarlas)" if n_no_clientes else ""))
    lineas = [f"[cuentas sin comercial asignado — {len(clientes)} son clientes reales"
              + (f"; otras {n_no_clientes} son internas/no clientes" if n_no_clientes else "")
              + "]"]
    # claves VERIFICADAS (sin_operador.cuentas_sin_operador): `vol` (pesificado
    # a ARS por el service) y `denominacion`.
    for c in sorted(clientes, key=lambda x: -float(x.get("vol") or 0))[:_TOP_FILAS]:
        lineas.append(f"  - {_ficha_cliente(c.get('denominacion') or c.get('cuenta'), mapping)}: "
                      f"operó {_monto(c.get('vol'), 'ARS')}")
    lineas.append("Cada una es comisión que no tiene dueño: asignarlas en "
                  "Clientes → OPERADORES.")
    return "\n".join(lineas)


_LENTES: dict[str, dict] = {
    "operadores": {
        "ayuda": "ranking de comerciales: volumen, comisiones, AuM y clientes "
                 "activos/inactivos de cada uno, con su variación contra el "
                 "período anterior",
        "fn": _lente_operadores,
    },
    "objetivos": {
        "ayuda": "cómo viene cada comercial contra su OBJETIVO del período "
                 "(volumen y comisiones), y cuánto lleva alcanzado",
        "fn": _lente_objetivos,
    },
    "cartera": {
        "ayuda": "el estado de la cartera de clientes (activa / enfriándose / "
                 "dormida / nueva): cuántos hay en cada estado, cuánto AuM "
                 "representan y qué clientes dormidos son los más grandes. "
                 "Con ficha_operador, la cartera de ESE comercial",
        "fn": _lente_cartera,
    },
    "sin_operador": {
        "ayuda": "qué cuentas están operando SIN comercial asignado (comisión "
                 "sin dueño)",
        "fn": _lente_sin_operador,
    },
}


def _descripcion() -> str:
    """La description se ARMA del registro: agregar una lente la documenta
    sola, y el modelo nunca ve una opción que el código no sabe ejecutar."""
    return ("El TABLERO COMERCIAL de la mesa: cómo viene cada comercial y en qué "
            "estado está la cartera de clientes. Elegí la lente con `que`:\n"
            + "\n".join(f"- {k}: {v['ayuda']}." for k, v in _LENTES.items())
            + "\nRequiere el permiso de Control Comercial: si el usuario no lo "
              "tiene, la herramienta te lo dice y no hay que mostrar ningún número. "
              "Los comerciales aparecen como OPERADOR_n y los clientes como "
              "CLIENTE_n — usá esas referencias, nunca inventes nombres.")


TOOLS_COMERCIAL: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "tablero_comercial",
            "description": _descripcion(),
            "parameters": {
                "type": "object",
                "properties": {
                    "que": {"type": "string", "enum": sorted(_LENTES),
                            "description": "Qué lente comercial mirar."},
                    "desde": {"type": "string",
                              "description": "YYYY-MM-DD. Default: 1º del mes actual. "
                                             "No aplica a `cartera` ni `sin_operador`, "
                                             "que son la foto de hoy."},
                    "hasta": {"type": "string",
                              "description": "YYYY-MM-DD. Default: hoy."},
                    "moneda": {"type": "string", "enum": ["ARS", "USD"]},
                    "ficha_operador": {
                        "type": "string",
                        "description": "Para `cartera`: mirar SOLO la cartera de ese "
                                       "comercial, por su referencia (OPERADOR_1). "
                                       "Sin esto, toda la mesa.",
                    },
                },
                "required": ["que"],
            },
        },
    },
]


def tablero_comercial(args: dict, *, mapping: dict, usuario: str | None) -> str:
    """Despacha a la lente pedida. GATEADO por Control Comercial (permiso POR
    USUARIO): sin el flag no se consulta nada — ni siquiera se toca la DB."""
    from api.services.asistente_tools import _moneda_ok, puede_control_comercial

    if not puede_control_comercial(usuario):
        return ("el tablero comercial requiere el permiso de Control Comercial, "
                "que este usuario no tiene — decíselo y no muestres ningún número")
    que = str(args.get("que") or "").strip()
    lente = _LENTES.get(que)
    if not lente:
        return (f"lente comercial desconocida: {que or '(vacía)'} — las válidas son "
                + ", ".join(sorted(_LENTES)))
    fn: Callable = lente["fn"]
    return fn(args, mapping, _moneda_ok(args.get("moneda")))


HANDLERS_COMERCIAL: dict[str, Callable] = {
    "tablero_comercial": tablero_comercial,
}
