---
name: triage-conversacion
description: Diagnosticar por qué una conversación del asistente o copiloto salió mal (respondió cualquier cosa, no encontró algo, se volvió circular, filtró o deformó texto). Trae las trazas reales, clasifica el modo de falla y propone el fix. Aplica cuando el usuario pega un chat malo o dice "el asistente contestó mal".
---

# Triage de una conversación que salió mal

Se aplica cuando el usuario pega una conversación fea, dice "el asistente
respondió cualquier cosa", "no encontró X", "se volvió circular", "mostró un
dato raro". El objetivo NO es adivinar — es traer los turnos REALES, clasificar
el modo de falla contra los que ya conocemos, y proponer UN fix concreto.

**No asumas la causa por lo que se ve en el chat.** El texto que ves puede ser
el modelo tapando un hueco (una tool muda contesta "sin datos" y el modelo
improvisa; una tool que no existe hace que el modelo se disculpe e invente). La
causa real está en las trazas y en el código, no en la prosa.

## 1. Traer las trazas reales (Droplet, read-only)

```bash
python -m scripts.diag_ia_trazas --full --limite 15        # las últimas 15 llamadas ENTERAS
python -m scripts.diag_ia_trazas --buscar "<palabra del chat>"   # localizar la conversación
python -m scripts.diag_ia_trazas --tarea asistente_negocio        # filtrar por tarea
```

`--full` muestra, por llamada: la tarea, el modelo, el texto que SALIÓ al
proveedor (ya tokenizado), la respuesta, el razonamiento, tokens y latencia. Ahí
se ve QUÉ vio el modelo y QUÉ herramienta usó (o no usó).

## 2. Clasificar el modo de falla (los que ya conocemos)

Mirá la traza y ubicá el síntoma en uno de estos — cada uno tiene un fix
distinto y un precedente real:

| Síntoma en la traza | Causa | Fix |
|---|---|---|
| La tool devolvió "sin datos" pero el dato existe | **Tool MUDA**: lee una clave que el service no emite | Verificar el shape real del service; skill `add-tool-ia §1`. (Pasó con `aum_composicion`, `controles_calidad_datos`, `rendimiento_esperado`.) |
| El modelo contestó de compromiso ("lo dejo planteado", "te paso el cuadro") sin llamar a nada | **Falta la tool** o el modelo no sabe que existe | Agregar la tool (`add-tool-ia`) y/o nombrar el caso en el prompt. (Pasó con `registrar_pedido` y con la sensibilidad.) |
| Mandó a la vista/sección equivocada e insistió | **Mapa de la guía stale** (`copiloto/ayuda.py::_MAPA`) | Verificar dónde vive de verdad en el frontend; corregir la entrada + fila de EQUIVALENCIA. (Pasó con "sensibilidad" → mandaba a Renta Fija, vive en Estrategia.) |
| Respondió el dato equivocado (patrimonio cuando pidió volumen) | **El modelo eligió la tool equivocada** | Afilar las `description` (cuándo usar cada una y qué NO es); regla "cada pregunta tiene SU dato" en el prompt. |
| Texto deformado ("el permiso de CLIENTE_1", "AuM CLIENTE_17 X e Y") | **La aduana tachó una etiqueta del sistema** | `diag_pii_matcher --frase` para ver qué token lo causa; skill `tocar-aduana`. |
| Un nombre/cuenta real apareció donde no debía | **LEAK de PII** — prioridad máxima | Skill `tocar-aduana`; `eval_asistente` para confirmar; test de no-leak de esa vía. |
| Se volvió circular / perdió el hilo desde el 2º mensaje | Historial mal armado, o una tool que devuelve ruido que ensucia el contexto | Ver el historial re-inyectado en la traza; revisar qué mete la tool en el contexto. |
| Números que no cuadran entre sí | El modelo aritmetizó (tiene PROHIBIDO) o mezcló datos de tools distintas | El cálculo va en CÓDIGO; el prompt prohíbe sumar/comparar entre tools sin aclarar que miden distinto. |

Si no matchea ninguno, es un modo NUEVO: describilo, y una vez resuelto va a la
tabla de este skill y al eval set (`scripts/eval_asistente.py`).

## 3. Confirmar la causa en el código (no reportar sin verificar)

Antes de proponer el fix, confirmá contra el código que la causa es esa —
`add-tool-ia §1` (shape del service), `ayuda.py::_MAPA` (mapa), `pii_gateway`
(aduana). Un diagnóstico inventado hace perder tiempo (REGLA #2).

## 4. Proponer UN fix + dejar el candado

- Una causa por vez, con su explicación ejecutiva (REGLA #3).
- **Dejá el caso en un eval set** para que no vuelva: `scripts/eval_asistente.py`
  (asistente), `scripts/eval_copiloto.py` (copilotos de vista). Un bug sin candado vuelve.
- Doc [VIVO]: si el fix tocó el copiloto/asistente, changelog en `docs/COPILOTO.md`.

## Criterios de éxito

- ✓ El modo de falla quedó clasificado contra la traza REAL, no adivinado.
- ✓ La causa está confirmada en el código antes de proponer el fix.
- ✓ El caso quedó en una batería (candado anti-regresión).
- ✓ `docs/COPILOTO.md` actualizado si tocó el asistente/copiloto.
