---
name: tocar-aduana
description: Candado OBLIGATORIO antes de cambiar core/pii_gateway.py (la aduana de PII del asistente). Cualquier cambio al tokenizador puede abrir un leak de identidades de clientes al proveedor LLM. Define qué verificar y qué correr, siempre. Aplica al tocar core/pii_gateway.py o su calibración.
---

# Tocar la aduana (core/pii_gateway.py)

**Bloqueante.** `core/pii_gateway.py` es la pieza de SEGURIDAD del asistente: tacha
las identidades de clientes (nombres, cuentas, documentos) y de empleados
(operadores) ANTES de que el texto viaje al proveedor LLM externo. La garantía,
en el docstring del propio módulo: *las identidades JAMÁS salen del perímetro.*

Un cambio a este archivo que parece cosmético puede abrir un leak en una vía que
no estás mirando. **Ya pasó dos veces esta semana:**
- v1.94 (2026-07-22): apagué el match por palabra suelta en texto generado para
  que dejara de romper las etiquetas del sistema ("entre"/"pico"/"control").
- v1.96 (2026-07-23): ese mismo cambio abrió un leak por el HISTORIAL
  re-inyectado — un apellido suelto en la respuesta previa (texto generado)
  volvía a viajar al proveedor. Lo cazó el security review, no yo.

La moraleja: **un arreglo en la aduana puede crear un problema peor en otra
vía.** Este skill hace ejecutable la regla "cuando toco la aduana, corro X".

## El mapa mental (las capas, y por dónde entra el texto)

`tokenize(texto, mapping, texto_generado=?, protegidos=?)` corre estas capas:
1. **Números** (`_spans_numeros`): CUIT/DNI, `cuenta N`, ids del catálogo. En
   texto generado, los números pelados se respetan (son agregados, no cuentas).
2. **Catálogo** (`_spans_catalogo`): nombres completos (n-gramas) + palabra
   suelta (apellido). **La palabra suelta se apaga en texto generado** — esta es
   la asimetría que abrió el leak.
3. **Defensiva** (`_spans_defensivos`): pares Capitalizados que parecen nombre,
   aunque no estén en el catálogo. Pide 2+ palabras.
4. **Red final** (`_spans_conocidos`, agregada en v1.96): re-enmascara las
   identidades YA fichadas de ESTE chat, siempre, sin depender de detección.

**Las dos vías de entrada, y su asimetría deliberada:**
- Texto del USUARIO (`texto_generado=False`): agresivo. Puede venir un apellido
  suelto mal escrito → la palabra suelta va ON.
- Texto NUESTRO (`texto_generado=True`, respuestas/tools/historial): menos
  agresivo con las etiquetas del sistema, PERO la red final (`_spans_conocidos`)
  garantiza que ninguna identidad ya conocida escape. Equivocarse **de menos**
  (dejar pasar un nombre) es MUCHO peor que de más.

## Qué verificar ANTES de tocar (pensá las 3 vías)

1. **El turno del usuario.** ¿Tu cambio deja pasar un nombre/cuenta/documento que
   el usuario escriba? Probá apellido solo, mayúsculas, acento, con inicial.
2. **El resultado de una tool.** ¿Una tool podría emitir una identidad que la
   aduana ya no tache? (Ojo: las tools deben fichar adentro — ver skill
   `add-tool-ia` — pero la aduana es el cinturón.)
3. **El HISTORIAL re-inyectado.** Esta es la vía traicionera. La respuesta previa
   se persiste con el nombre REAL y se re-tokeniza como `texto_generado=True` en
   el turno siguiente (`asistente.py`). ¿Tu cambio deja escapar algo por ahí?
   Todo lo que ya se fichó tiene que seguir tachándose acá.

## Qué correr, SIEMPRE (no negociable)

### Tests locales
```bash
.venv/bin/python -m pytest tests/unit/test_pii_gateway.py -q
.venv/bin/python -m pytest tests/unit/test_asistente_tools.py -q
```
`test_pii_gateway.py` tiene la clase NO-LEAK (nombre completo, apellido solo,
CUIT, cuenta, historial re-inyectado). Si tu cambio afloja algo, revienta acá.
**Si agregás una vía nueva, agregá su test de no-leak** — no la dejes sin cubrir.

### Calibración contra el catálogo real (Droplet, 0 tokens, read-only)
```bash
python -m scripts.diag_pii_matcher                        # tamaño del catálogo + tokens repetidos + sensibilidad del fuzzy
python -m scripts.diag_pii_matcher --frase "cómo viene Perez este mes"   # QUÉ tacha y POR QUÉ (token exacto / fuzzy / de qué cliente)
```
El `--frase` es la herramienta para decidir la stoplist con datos medidos, no a
ojo (REGLA #2). Si una palabra común se tacha, `--frase` te dice qué token del
catálogo la causa → va a `ASISTENTE_STOPLIST_EXTRA` en el `.env` (se lee en
runtime, sin deploy).

### El candado de no-leak end-to-end (Droplet, gasta pocos tokens)
```bash
python -m scripts.eval_asistente
```
Manda una pregunta con un nombre de cliente REAL y verifica que ninguna
identidad salga del perímetro. **Correlo después de CUALQUIER cambio a la
aduana**, no solo los tests: quiero el candado verde en datos reales.

## Palancas de calibración (env, runtime, sin deploy)

- `ASISTENTE_FUZZY_UMBRAL` (default 0.95, medido) — umbral del fuzzy.
- `ASISTENTE_STOPLIST_EXTRA` — palabras que NUNCA son candidato (falsos positivos
  medidos con `diag_pii_matcher --frase`).
- `ASISTENTE_TOKEN_MAX_CLIENTES` (default 5) — corte por frecuencia (un token en
  más de N clientes no identifica a nadie).

Preferí calibrar por env antes que tocar el código: es reversible y no necesita
deploy.

## Criterios de éxito

- ✓ `test_pii_gateway.py` verde, con test nuevo si abriste una vía nueva.
- ✓ `eval_asistente` verde en el Droplet (no-leak con un cliente real).
- ✓ Pensaste explícitamente las 3 vías (usuario / tool / historial re-inyectado)
  y podés decir por qué ninguna filtra.
- ✓ Si tocaste la agresividad, `diag_pii_matcher --frase` confirma que no
  rompiste etiquetas del sistema NI aflojaste una identidad.
- ✓ `docs/COPILOTO.md` con la entrada del cambio (es doc [VIVO]).
