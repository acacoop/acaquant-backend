---
description: Review de CALIDAD del programa de IA (asistente + copiloto) — fan-out de analistas sobre prompts, tools, aduana y evals → mejoras concretas priorizadas
---

Review de calidad del agente de IA, con el mismo patrón que `/security-review`
pero apuntado a CALIDAD de diseño, no a vulnerabilidades. Devuelve mejoras
concretas y priorizadas, no un "está todo bien".

Alcance por defecto: **todo el programa de IA**. Si el usuario pasa un argumento
(`negocio`, `copiloto`, `aduana`, `gateway`), acotá a eso.

## Contexto que hay que leer primero

- `docs/QUANTAI.md` — decisiones tomadas, principios de ingeniería, qué está
  diferido/descartado (NO re-proponer sin novedad).
- `docs/TOOLS_IA.md` — mapa de tools, patrones, tandas, deuda conocida.
- `docs/COPILOTO.md` — changelog e historia de bugs (para no re-descubrir).

Superficie: `api/services/asistente.py`, `asistente_tools.py`,
`asistente_comercial.py`, `api/services/copiloto/*`, `core/ai.py`, `core/llm.py`,
`core/pii_gateway.py`.

## Cómo correrlo

1. **Fan-out**: lanzá analistas en paralelo (subagentes `general-purpose`), uno
   por eje. Cada uno lee su parte y devuelve hallazgos concretos con
   `archivo:línea`, NO un resumen. Ejes:

   - **Prompts** (`asistente.py::_SYSTEM`, `copiloto/base.py`, `ayuda.py`):
     ¿instrucciones contradictorias, ambiguas, o que el modelo ignora? ¿casos
     que la traza mostró que salen mal y no están cubiertos? ¿el prompt nombra
     tools que no existen o al revés?
   - **Tools** (`asistente_tools.py`, `copiloto/*`): ¿tools que leen un shape
     que el service no emite (mudas)? ¿enums escritos a mano en vez de derivados
     del dueño? ¿descriptions que se pisan y hacen rutear mal? ¿huecos de
     cobertura contra `docs/TOOLS_IA.md`?
   - **Aduana/PII** (`pii_gateway.py`): ¿alguna vía por la que una identidad
     escape (usuario / tool / historial re-inyectado)? ¿tools que devuelven un
     nombre sin fichar? (cruza con el skill `tocar-aduana`.)
   - **Gateway/ruteo** (`core/ai.py`, `core/llm.py`): ¿tareas ruteadas al
     proveedor equivocado (privacidad)? ¿fail-closed intacto? ¿max_tokens/
     thinking mal seteados para la tarea?
   - **Evals/observabilidad** (`scripts/eval_*`, `diag_ia_trazas`):
     ¿qué NO está cubierto por un candado? ¿casos reales que salieron mal y no
     quedaron en una batería?

   Escalá el fan-out al alcance: `todo` → 5 analistas; un eje → 1-2.

2. **Verificá cada hallazgo VOS** (no en un subagente). Antes de reportarlo,
   confirmá contra el código que es real — un hallazgo inventado es peor que
   ninguno (la lección del mapa de la guía y de las tools mudas). Descartá lo
   que no se sostenga.

3. **Priorizá** por impacto × esfuerzo, y cruzá con `docs/TOOLS_IA.md` /
   `docs/QUANTAI.md`: si algo ya está diferido/descartado ahí, no lo re-propongas
   sin novedad.

## Formato de salida

Un informe corto, conclusión primero. Para cada mejora:
- **Qué** (una línea), **dónde** (`archivo:línea`), **por qué importa** (atado a
  un modo de falla real, no a un "best practice" teórico), **esfuerzo** (chico/
  medio/grande).
- Separado en: **arreglar ya** / **vale la pena** / **no lo haría (y por qué)**.

NO apliques cambios en este comando — es un review. Aplicar es otra decisión, y
va con el OK del usuario. Si un hallazgo es un bug vivo (una tool muda, un leak),
decilo destacado arriba de todo.
