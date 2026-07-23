---
name: add-tool-ia
description: Agregar una tool nueva al asistente de negocio o a un copiloto de mercado. El checklist que evita los 3 modos de falla reales (tool MUDA por shape mal leído, PII que se filtra, enum que diverge del SQL). Aplica al tocar api/services/asistente_tools.py, asistente_comercial.py, o copiloto/*.py con function-calling.
---

# Agregar una tool de IA

Se aplica cuando el usuario pide "que el asistente pueda contestar X", "sumale
una herramienta para Y", o cuando hay que exponer un dato nuevo al chat.

**Antes de escribir una línea, leé `docs/TOOLS_IA.md` y `docs/QUANTAI.md`.** El
mapa de tools, los patrones (una serie genérica, no 40 wrappers) y las
decisiones tomadas viven ahí. La regla de oro está en `docs/TOOLS_IA.md §4`:
**no construyas 40 tools, construí los 3 patrones** (serie genérica, dimensión
de whitelist, diccionario de métricas). Si tu tool es "lo mismo que X con otra
ropa", capaz es una fila en un registro que ya existe, no una tool nueva.

## Dónde vive cada cosa

| Destino | Archivo | Registro |
|---|---|---|
| Asistente de negocio (jefes) | `api/services/asistente_tools.py` | `TOOLS` (schemas) + `_HANDLERS` (dispatch) |
| Bloque de un dominio del asistente | módulo propio (ej. `asistente_comercial.py`) | exporta `TOOLS_X` + `HANDLERS_X`, enganchado por `_enganchar_dominios` |
| Copiloto de una vista de mercado | `api/services/copiloto/<vista>.py` | `_TOOLS_<VISTA>` + `_ejecutar_tool_<vista>`, registrado en `copiloto/registro.py` |
| Común a TODAS las vistas | `copiloto/series.py` (serie histórica), etc. | lo suma `copiloto/motor.py` |

El orquestador del asistente es `api/services/asistente.py`; el del copiloto,
`api/services/copiloto/motor.py`. Ninguno de los dos hay que tocarlo para sumar
una tool — solo el registro del destino.

## El checklist (en ESTE orden — el orden importa)

### 1. VERIFICAR EL SHAPE DEL SERVICE — primero, contra el código (REGLA #2)

**Esto es lo que rompió 3 tools esta sesión** (`aum_composicion` leía `rows`,
el service devuelve `docs`; `controles_calidad_datos` iteraba un nivel de más;
`rendimiento_esperado` filtraba por `total`, el campo es `total_esperado`). Una
tool que lee una clave que el service no emite **no falla — enmudece**: devuelve
"sin datos" siempre, el modelo lo tapa improvisando, y ningún test lo ve porque
los tests mockean el service.

- Abrí el service que vas a llamar y leé su `return` real. NO asumas las claves.
- Anotá el shape exacto en un comentario al lado del acceso (`# clave VERIFICADA
  (service.func): 'docs'`).
- Prohibido el patrón `r.get("a") or r.get("b") or r.get("c")`: escrito como
  defensa, funciona como tapadera — si el shape cambia, no falla, enmudece.

### 2. Escribir el handler (función pura que devuelve texto para el modelo)

- Firma `(args: dict, *, mapping: dict[, usuario])`. Devuelve un string ya
  narrado, compacto (el gateway capa a ~4000 chars).
- Números: calculá vos lo que el modelo tiene PROHIBIDO (percentil, z, %, suma).
  El modelo narra, no aritmetiza.
- Series: NO devuelvas los puntos crudos. Contrato de `copiloto/series.py`:
  stats + muestra ralificada (≤24 puntos). Si es una serie, capaz ya la cubre
  `serie_historica` — sumá una fila a su registro en vez de una tool nueva.

### 3. PII — fichar DENTRO del perímetro (si la tool toca identidades)

**El `tokenize` final del dispatcher es red de seguridad, NO diseño.** Si tu
tool devuelve un nombre/cuenta/email de cliente o de operador, tenés que
ficharlo vos, adentro:

- Cliente → `pii_gateway.asignar_ficha(mapping, "CLIENTE", nombre)` → `CLIENTE_n`.
- Operador (empleado) → `asignar_ficha(mapping, "OPERADOR", nombre)` → `OPERADOR_n`.
  **Los emails NO salen nunca, ni fichados** (ver `asistente_comercial._ficha_operador`).
- Ficha → id real para la query: `id_cuenta_de_ficha` / `operador_de_ficha`
  (resuelven SOLO dentro del perímetro).
- Tipos válidos de ficha: `pii_gateway.TIPOS_FICHA`. Si necesitás uno nuevo
  (referido, contraparte), es una decisión — ver `docs/TOOLS_IA.md §deuda`.
- Regla: cada dato con identidad se ficha o NO se devuelve. Nunca "lo tacha el
  dispatcher después".

### 4. Gate de permisos (si el dato lo gatea la web por permiso POR USUARIO)

- Control Comercial NO es un rol, es un permiso por usuario. Si la vista web
  gatea ese dato con él, la tool tiene que chequear `puede_control_comercial(usuario)`
  **como su PRIMER statement, antes de tocar la DB**. Fail-closed: sin usuario o
  ante error → negar. Sin esto, el chat es una puerta trasera (auditoría 2026-07-21).

### 5. Escribir el schema y DERIVAR los enums del dueño del dato

- Schema formato OpenAI (`{"type": "function", "function": {...}}`).
- **Los enums que ve el modelo se DERIVAN del dueño**, no se escriben a mano:
  dimensiones ← `operaciones_sql.dimensiones_consolidado()`, campos 1816 ←
  `research_1816_sql.CAMPOS`, curvas ← `descomposicion_retorno.curvas_soportadas()`.
  Una lista paralela se desincroniza y el modelo pide algo que el SQL rechaza.
- La `description` es la interfaz real con el modelo: decí CUÁNDO usarla y qué
  NO es (el error #1 es que el modelo elige la tool equivocada). Nombrá el caso
  en las palabras del usuario.

### 6. Registrar (schema + handler juntos)

- Asistente: sumá el schema a `TOOLS` y el handler a `_HANDLERS` (o exportá
  `TOOLS_X`/`HANDLERS_X` desde tu módulo de dominio).
- Copiloto: `_TOOLS_<VISTA>` + `_ejecutar_tool_<vista>`, y registralo en
  `copiloto/registro.py` (`tools` + `tools_ejecutar`).

### 7. Sonda en el smoke + test de contrato

- **Sonda**: agregá la tool a `_SONDAS` en `scripts/smoke_asistente.py` (o dejala
  como `None` si escribe / necesita una ficha de un chat real). El test
  `test_toda_tool_tiene_sonda_en_el_smoke` FALLA si no está.
- **Contrato**: el test `set(_HANDLERS) == herramientas_declaradas()` FALLA si
  declaraste el schema y olvidaste el handler (o al revés). No lo desactives.
- Test de la tool con el shape REAL del service (no una clave inventada, o el
  test se vuelve cómplice del bug — ver §1).

### 8. Docs [VIVO] — mismo commit

- `docs/COPILOTO.md` (changelog obligatorio) y `docs/TOOLS_IA.md` (mové el ítem
  de pendiente a hecho). Si el doc no refleja el estado real, el trabajo está
  incompleto.

## Verificación final (antes de pushear)

```bash
.venv/bin/python -m pytest tests/unit -q          # contrato + tu test
.venv/bin/python -m ruff check .
.venv/bin/python -c "from api.main import app"     # import-chain (REGLA #1)
```

Y en el Droplet, contra datos reales (0 tokens):

```bash
python -m scripts.smoke_asistente --tools          # ¿tu tool trae datos? ✓ / ? / ✗
python -m scripts.smoke_copiloto --contexto         # (si tocaste una vista)
```

Un ✓ en el sondeo real es lo ÚNICO que prueba que la tool no quedó muda. Los
unit tests no alcanzan — mockean el service.

## Criterios de éxito

- ✓ `set(_HANDLERS) == herramientas_declaradas()` (contrato verde).
- ✓ La tool sale ✓ en `smoke_asistente --tools` contra la DB real.
- ✓ Ninguna identidad de cliente/operador vuelve al modelo (fichada adentro).
- ✓ Si es gateada, un usuario sin el permiso recibe la negativa, no el dato.
- ✓ El enum sale del dueño del dato, no de una lista a mano.
- ✓ `docs/COPILOTO.md` + `docs/TOOLS_IA.md` actualizados en el mismo commit.
