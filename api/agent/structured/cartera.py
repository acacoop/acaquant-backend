"""Flow estructurado: Recomendar cartera.

Patrón "structured intake": en lugar de que el usuario pida en lenguaje
natural ("armame cartera conservadora pesos onda inflación"), el frontend
le muestra un formulario tipado, el backend lo recibe como `CarteraRequest`,
construye un user message determinístico, y el modelo está FORZADO a
responder llamando la tool `responder_cartera` con un schema fijo.

Esto garantiza tres cosas que el chat libre no puede:
  - Input predecible: todos los campos normalizados, el modelo no parsea.
  - Output comparable: dos consultas con mismos params dan respuestas
    estructuralmente idénticas (clave para evals).
  - UI determinística: el frontend siempre renderiza con el mismo componente.

Usado por POST /api/chat/structured (ver api/routers/chat.py — agregado en parte 2).

Próximos flows que adopten este patrón viven al lado en api/agent/structured/.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ─────────────────────────────────────────────────────────────────────────────
# Input schema (lo que envía el frontend)
# ─────────────────────────────────────────────────────────────────────────────


class CarteraRequest(BaseModel):
    """Parámetros del formulario de recomendación de cartera.

    Los enums son los valores tentativos del MVP. Si la mesa cambia la
    nomenclatura interna (ej: añade "transaccional/estructural", o renombra
    "moderado" a "balanced"), actualizar acá y se propaga al frontend
    automáticamente vía OpenAPI.
    """

    perfil: Literal["conservador", "moderado", "agresivo"]
    exposicion: Literal["pesos", "usd", "mixta"]
    plazo: Literal["corto", "medio", "largo"]   # corto <6m / medio 6-18m / largo >18m
    benchmark: Literal["inflacion", "mep", "tasa_caucion", "sin_benchmark"]

    # Opcionales — no bloquean la generación
    monto_estimado_ars: int | None = Field(default=None, ge=0)
    restricciones: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Output tool (forzada via tool_choice)
# ─────────────────────────────────────────────────────────────────────────────

# Esta tool NO se ejecuta — es solo el schema de salida que Anthropic
# valida cuando el modelo la llama. El runner del flow estructurado debe:
#  1. Pasar esta tool en el body del request a Anthropic, junto a las
#     tools de cotización/serie/etc que el modelo puede invocar para razonar.
#  2. Configurar `tool_choice = {"type": "tool", "name": "responder_cartera"}`
#     en el LAST step del loop (no en los intermedios — en los intermedios
#     el modelo necesita llamar tools de data libremente).
#  3. Cuando el modelo llame `responder_cartera`, el runner intercepta y
#     usa los args como respuesta final, sin pasarlos por dispatch().
RESPONDER_CARTERA_TOOL: dict[str, Any] = {
    "name": "responder_cartera",
    "description": (
        "Emite la cartera recomendada. ÚNICA forma de responder en el flow "
        "estructurado de cartera. NO escribir texto libre fuera de esta tool. "
        "La suma de pesos debe dar 100. Entre 3 y 5 instrumentos."
    ),
    "input_schema": {
        "type": "object",
        "required": ["tesis", "cartera", "que_invalida"],
        "properties": {
            "tesis": {
                "type": "string",
                "description": (
                    "Tesis central de la cartera en 1 oración. Máximo 30 "
                    "palabras. Sin headings ni bullets."
                ),
            },
            "cartera": {
                "type": "array",
                "minItems": 3,
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "required": ["ticker", "peso_pct", "metrica_clave", "justificacion"],
                    "properties": {
                        "ticker": {
                            "type": "string",
                            "description": "Ticker corto (ej: TX26, GD30, S30N6).",
                        },
                        "peso_pct": {
                            "type": "number",
                            "minimum": 1,
                            "maximum": 100,
                            "description": (
                                "Peso del instrumento en %. La suma de TODOS los "
                                "pesos de la cartera debe dar exactamente 100."
                            ),
                        },
                        "metrica_clave": {
                            "type": "string",
                            "description": (
                                "UN solo número con su unidad. Ej: 'TEA 27,3%' o "
                                "'breakeven 2,8% mensual' o 'paridad 89,5%'. "
                                "NO 'TEA 27% / duration 1,2 / paridad 95%'."
                            ),
                        },
                        "justificacion": {
                            "type": "string",
                            "description": (
                                "Por qué ESTE instrumento y no otro de la misma "
                                "curva. Máximo 2-3 oraciones. NO repitas el "
                                "contexto macro general (eso ya está en `tesis`)."
                            ),
                        },
                    },
                },
            },
            "que_invalida": {
                "type": "string",
                "description": (
                    "Escenario que rompería la tesis de la cartera, en 1 oración. "
                    "Ej: 'inflación cae a 2% sostenido en 2 datos consecutivos' "
                    "o 'spread soberano se abre +200 bps por shock político'."
                ),
            },
            "alertas_data": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Si alguna tool falló, devolvió data stale o tuvo warnings "
                    "de invariantes durante el razonamiento, listar acá. Vacío "
                    "si todo OK. Ej: ['breakevens_actuales: staleness=very_stale']."
                ),
            },
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Construcción determinística del user message
# ─────────────────────────────────────────────────────────────────────────────

_PLAZO_DESCRIPCION = {
    "corto": "corto (<6 meses)",
    "medio": "medio (6-18 meses)",
    "largo": "largo (>18 meses)",
}


def build_user_message(req: CarteraRequest) -> str:
    """Mapea CarteraRequest → mensaje determinístico que recibe el modelo.

    NO concatena texto libre del usuario — todo se genera 100% por código a
    partir del schema validado. Eso garantiza que dos requests con los
    mismos params produzcan inputs idénticos al modelo (clave para evals
    consistentes).
    """
    parts = [
        "Construir cartera recomendada con los siguientes parámetros:",
        f"- Perfil de riesgo: {req.perfil}",
        f"- Exposición: {req.exposicion}",
        f"- Plazo: {_PLAZO_DESCRIPCION[req.plazo]}",
        f"- Benchmark de referencia: {req.benchmark.replace('_', ' ')}",
    ]
    if req.monto_estimado_ars:
        monto_fmt = f"{req.monto_estimado_ars:,}".replace(",", ".")
        parts.append(f"- Monto estimado: ARS {monto_fmt}")
    if req.restricciones:
        parts.append(f"- Restricciones: {', '.join(req.restricciones)}")
    parts.append(
        "\nRespondé SIEMPRE llamando la tool `responder_cartera` con el "
        "schema completo. NO escribas texto libre como respuesta final."
    )
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# System prompt addendum (solo se inyecta en este flow)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT_ADDENDUM = """# FLOW ESTRUCTURADO DE CARTERA

Estás en el flow `recomendar_cartera`. Reglas que aplican SOLO acá (se
suman a las del system prompt base):

1. **Tu única forma de responder es llamar la tool `responder_cartera`**.
   NO escribas texto libre como respuesta final. Si llegaste a la conclusión,
   llamá la tool con el schema completo.
2. Máximo 5 instrumentos, idealmente 3-4. Más de 5 es ruido.
3. Máximo 2 instrumentos por curva (CER, tasa fija, HD, DL, TAMAR, liquidez).
   Si hay 3 candidatos en la misma curva, elegí el mejor y mencioná uno
   alternativo dentro de `justificacion` ("alternativa si X cambia").
4. NO incluyas el framework de 4 capas como secciones. El framework es
   razonamiento INTERNO. El output es operable, no research paper.
5. La suma de `peso_pct` debe dar exactamente 100. Verificá antes de llamar.
6. Si fallaron tools críticas (curva relevante para el perfil pedido) y no
   tenés data fresca: NO armes la cartera con datos viejos. Llamá
   `responder_cartera` con `cartera: []`, `tesis: "Data insuficiente"`,
   y listá las tools que fallaron en `alertas_data`.
7. `metrica_clave` por instrumento: UN número con su unidad. Ej: "TEA 27,3%".
   NO tres métricas separadas por barras.
8. `justificacion` por instrumento: 2-3 oraciones. Por qué ese instrumento
   y no otro de la misma curva. NO repitas contexto macro general (eso ya
   está en `tesis`).
"""


# ─────────────────────────────────────────────────────────────────────────────
# Validaciones del output (corren después de que el modelo llame la tool)
# ─────────────────────────────────────────────────────────────────────────────


def validar_pesos_suman_100(cartera: list[dict[str, Any]]) -> tuple[bool, float]:
    """Devuelve (ok, suma_actual). Tolerancia ±0.5 por redondeos del modelo."""
    suma = sum(float(item.get("peso_pct", 0) or 0) for item in cartera)
    return abs(suma - 100.0) <= 0.5, round(suma, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Runner del flow (orquesta: build_message + extra_tools + addendum + validación)
# ─────────────────────────────────────────────────────────────────────────────


def run_cartera_flow(req: CarteraRequest) -> dict[str, Any]:
    """Orquesta el flow estructurado de cartera de punta a punta.

    Pasos:
      1. Construye user message determinístico desde el schema.
      2. Llama al runner genérico con la tool de output forzada vía tool_choice
         en el último step y el addendum del flow.
      3. Valida que la suma de pesos sea 100; si no, marca alert.
      4. Devuelve un dict con structured_output + metadata + alertas.

    Forza Sonnet (las decisiones de cartera son análisis estratégico,
    Haiku no aporta acá).
    """
    # Import local para evitar ciclo (runner importa cosas que pueden
    # importar este módulo en el futuro).
    from api.agent.runner import run_conversation

    user_msg = build_user_message(req)
    addendum_block = {"type": "text", "text": SYSTEM_PROMPT_ADDENDUM}

    result = run_conversation(
        user_message=user_msg,
        history=None,  # los flows estructurados son siempre one-shot
        force_model="sonnet",
        extra_tools=[RESPONDER_CARTERA_TOOL],
        force_tool_name_on_last="responder_cartera",
        extra_system_blocks=[addendum_block],
    )

    structured = result.get("structured_output")
    if structured and structured.get("name") == "responder_cartera":
        args = structured.get("args", {}) or {}
        cartera = args.get("cartera", []) or []
        ok_pesos, suma = validar_pesos_suman_100(cartera)
        if not ok_pesos and cartera:
            alertas = list(args.get("alertas_data", []) or [])
            alertas.append(
                f"Suma de pesos = {suma}% (esperado 100). El modelo no balanceó "
                "correctamente; revisar antes de operar."
            )
            args["alertas_data"] = alertas
        result["structured_output"]["args"] = args
        result["pesos_ok"] = ok_pesos
        result["pesos_suma"] = suma

    return result
