# Copiloto de Mesa — doc vivo (QuantAI P3)

> **REGLA DE ESTE DOC:** todo cambio que modifique lo que el asistente VE
> (columnas, bloques, reglas del prompt, vistas) o CÓMO se comporta, se asienta
> en el **Changelog** de abajo CON FECHA, en el MISMO commit del cambio. Si el
> changelog no refleja lo que corre en prod, el cambio está incompleto.
> Roadmap y decisiones de programa: `docs/QUANTAI.md` (P3). Acá vive el detalle
> operativo del asistente.

## Dónde vive el código (mapa)

| Pieza | Archivo | Qué hace |
|---|---|---|
| **El cerebro** | `api/services/copiloto/` | Paquete (modularizado 2026-07-17, antes un `.py` de ~3.400 líneas). `base.py` (helpers puros + system prompt + tono por rol) · `verificacion.py` (guardrails de números/jerga/derrame) · **una `<vista>.py` por vista** (`renta_variable`, `renta_fija`, `trading`, `home`, `agro`, `opciones`, `ons`, `reuters`: fetch + extras + reglas + chips) · `registro.py` (el dict `VISTAS`) · `derivacion.py` (`[[VISTA:x]]` + acceso RBAC) · `motor.py` (`preguntar` + `vigia` + historial + feedback). **Tocar una vista = tocar SOLO su archivo.** `__init__.py` re-exporta la superficie pública. |
| Transporte LLM | `core/ai.py` | Gateway único: tarea `copiloto_vista` (tier flash, max_tokens 2000), presupuesto diario, retry, traza de cada llamada. No sabe nada del copiloto. |
| HTTP | `api/routers/ia.py` | 3 endpoints: `GET /api/ia/copiloto/vistas`, `POST /api/ia/copiloto`, `POST /api/ia/copiloto/feedback`. Solo plumbing; gate `ia` en el montaje. |
| Panel UI | `acaquant-web/src/components/ia-vista-panel.tsx` | Drawer + botón "Consultale a la IA". Historial corto client-side. Se oculta si el backend no habilita la vista. |
| Ubicación del botón | `acaquant-web/src/components/header.tsx` (`VISTA_IA_POR_RUTA`) | Slot derecho del header (ex-TERMINAL) para HOME/RF/RV/Agro/Opciones/ONs. Excepción: /trading monta el suyo in-view (cableado a tarjetas + vigía). |
| Observabilidad | tabla `ia.trazas` + Manager → OBSERVABILIDAD → pill IA | Cada pregunta: tokens, latencia, ok/error, feedback 👍/👎. |
| Tests | `tests/unit/test_copiloto.py` | Congelan contrato: TSV, gates, caps, degradación, detección de tickers, verificador. |
| Diag de contexto | `scripts/diag_contexto.py --vista <v>` | LA LUPA (todas las vistas): imprime el contexto exacto que ve el modelo, sin tokens. Primer comando ante cualquier rareza. |
| Baterías | `scripts/bateria_rf.py`, `scripts/bateria_home.py`, `scripts/bateria_reuters_trading.py` (+ `smoke_copiloto.py`, `eval_copiloto.py`) | Mapeo masivo de preguntas reales contra el copiloto vivo (gasta tokens del email que se pase). |

## Cómo fluye una pregunta

```
Panel (browser) ── {vista, pregunta, historial} ──► POST /api/ia/copiloto
    (los DATOS nunca viajan del browser)                │
                                                        ▼
   copiloto.preguntar():  fetch del service @cached de la vista
                          → enriquecer (columnas derivadas)
                          → TSV + extras (CCL, detalle por ticker mencionado)
                          → core.ai.completar_con_traza("copiloto_vista")
                          → {respuesta, traza_id}  (ok=False si algo falla)
```

## Qué ve el asistente HOY (vista `renta_variable`)

- **Tabla completa** (~187 CEDEARs × 26 columnas): identificación (ticker,
  nombre, underlying, ratio, sector, rubro, país, **es_ia**), precios ARS,
  variaciones (intradía, 1d, 1d USD), liquidez (bid/offer/spread/volumen/
  monto), subyacente USD (precio, variaciones, retornos WTD/7d/MTD/YTD,
  volumen USD) y **zonas de pivots** `piv_anual` / `piv_mensual`.
- **CCL live** (siempre, 1 línea).
- **Detalle por ticker MENCIONADO en la pregunta** (cap 3, detección
  determinista de tokens): pivots 4 marcos con niveles, quant (beta/corr vs
  SPY/QQQ, vol, z-score), últimos 15 retornos diarios, fundamentals Refinitiv
  (si la empresa está en `research.companies`).
- **Conocimiento de mesa en el prompt:** significado de cada columna + lectura
  de pivots (>R3/<S3 = movió muchísimo · R2/S2 = tendencia clara · R1/S1 =
  movió algo · PP = referencia ideal de decisión) + manejo de pedidos de
  recomendación (ranking objetivo con criterio, nunca consejo de inversión).

## Cómo se agrega una vista nueva

1. Módulo nuevo `copiloto/<vista>.py` con su fetch/extras/reglas/chips, y entrada
   en `VISTAS` (`copiloto/registro.py`, importando esos símbolos): `titulo`, `modulo` RBAC, `dominio`
   (una línea de qué se pregunta ahí — alimenta la derivación entre vistas),
   `fetch` (el MISMO service @cached de la vista), `columnas`, `reglas` del
   dominio, y opcionales `enriquecer` / `extras`.
2. `<IaVistaPanel vista="..." />` donde la vista lo quiera (el padre posiciona).
3. Entrada acá en el changelog + estado en QUANTAI.md.

---

## Cómo se evalúa (candado de regresión)

**Decisión del user (2026-07-11): las pruebas las hace ÉL, manualmente, desde
el panel** — preguntas reales + 👍/👎. No pedirle presupuesto para el runner
ni proponer correrlo de rutina.

`evals/copiloto_vista.json` (casos reales del shadow + adversos) + runner
`python -m scripts.eval_copiloto` quedan como herramienta OPCIONAL para
momentos puntuales (ej. antes de abrir el copiloto al resto de la mesa).
Cada fallo real del shadow se sigue agregando como caso — documenta qué NO
puede volver a romperse, se corra o no el runner.

## Plan "prompt engineering para finanzas" (guía AI-in-Finance, 2026-07-11)

Adopciones decididas charlando con el user (TODO se implementa CON su ayuda y
queda asentado acá):
1. **Audiencia por rol RBAC** — **HECHO (2026-07-11, tono definido por el
   user):** trader = seco/numérico, sales = explicado y con frases repetibles
   a un cliente, admin = neutro. `_TONO_POR_ROL` en `copiloto/base.py` vía
   `get_user_role` (best-effort: roles caídos → neutro).
2. **Biblioteca de consultas de mesa** — **HECHO (2026-07-11, chips elegidos
   por el user):** 5 chips en el panel (Papeles de IA · Argentina · En zona
   de decisión · Rezagados repuntando · Voladores del año), prompts curados
   en `_CHIPS_RENTA_VARIABLE` (copiloto.py) y servidos por /copiloto/vistas.
   Sumar/editar un chip = editar esa lista + changelog.
3. **Plantillas de formato por tipo de pregunta** (ranking / estado de papel /
   comparación / screening). Estado: cubierto por el método Minto + ejemplos
   MAL/BIEN + los prompts de los chips (que fijan formato por consulta).
4. **Escenarios deterministas** ("¿qué pasa si el CCL sube 5%?") — código
   computa, el modelo narra. DIFERIDO: antesala del P5.
Ya cubiertos por diseño previo: los 5 componentes (rol/contexto/tarea/
restricciones/formato), verificación de outputs (automatizada, mejor que el
protocolo manual del libro), anti-patrón de datos en tiempo real (el modelo
solo ve lo inyectado).

## Técnicas aplicadas (glosario de referencia)

**Datos (context engineering):** grounding/contexto curado server-side ·
precómputo determinista (pulso, rankings, zonas piv, ruedas — la IA nunca es
fuente de un número) · detección determinista de tickers en la pregunta.
**Prompt:** persona + audiencia por rol RBAC · few-shot con ejemplos MAL/BIEN
salidos del shadow · método Minto/BLUF (conclusión primero, el plazo de la
pregunta manda) · biblioteca de prompts = chips versionados · control del
thinking por tarea (apagado copiloto / prendido triage; razonamiento a trazas).
**Guardrails (código, no prompt):** verificación de grounding de números ·
detector de jerga interna y derrame de razonamiento · reflexion
(auto-corrección con el problema señalado, 1 reintento, antes de mostrar).
**Operación:** shadow + eval set que crece con fallos reales · trazas
completas + 👍/👎 · presupuestos con kill switch editables.
**El patrón rector:** lo que el modelo rompe dos veces deja de ser regla de
prompt y baja a código. Prompt para el estilo, código para la verdad.

## Changelog del asistente (obligatorio, con fecha)

### 2026-07-21 — v1.76 (IA para INVITADOS — decisión user, con condiciones de seguridad)
El user habilitó la IA en el portal público www (pisa el default-deny original
de QUANTAI, asentado allá). El paquete acordado:
- **[RBAC]** `ia` en INVITADO_MODULES: el invitado ve los copilotos de las
  vistas de MERCADO que ya tenía (home/RF/RV/agro/derivados/ONs).
- **[seguridad] la GUÍA queda EXCLUIDA** (`solo_internos` en el registro —
  mapea el producto entero, REGLA #8): el acceso del invitado se resuelve
  EXPLÍCITO contra INVITADO_MODULES en `derivacion._acceso()` (listado,
  pregunta, derivación y sugerencias — la identidad compartida no puede pasar
  por roles-por-email, caería en el rol default). Header: sin botón guía para
  guests.
- **[identidad] cada invitado ES su email** (corrección del user sobre la
  primera versión compartida): identidad "guest:<email>" → persiste SU
  conversación (la memoria del panel funciona igual que para la mesa) y tiene
  SU tope diario propio de **100k tokens/día** (default en core/ai, editable
  por email como excepción personal en Manager). El prefijo además marca al
  externo en las trazas de OBSERVABILIDAD.
- Tests de seguridad actualizados: `test_invitado_ia_con_condiciones` congela
  el paquete completo (guía excluida, trading excluido, tope 100k por guest).

### 2026-07-21 — v1.75 (veredicto del piloto de tools: FUNCIONA + fix "herramienta ofrecida")
Piloto corrido en prod por el user (3 preguntas): (1) "¿cómo evolucionó la TEA
de TX26 desde abril?" → pidió la serie solo y respondió con fechas/valores
reales — EL OBJETIVO, cumplido; (3) control sin tools → no las usó y respondió
fino del cuadro. (2) reveló el defecto nuevo: ofreció "profundizar con la
herramienta de búsqueda" al usuario — plomería a la vista y un permiso que
nadie puede dar. Fix: regla "las herramientas son TUYAS e INVISIBLES: jamás
las ofrezcas ni las nombres — usalas y respondé". + señales pro nuevas
(evolución/tendencia: la pregunta 1 corrió en flash y merecía pro).
VEREDICTO: piloto aprobado — extender tools a más vistas queda como próximo
paso del programa (candidatas: renta_fija con series de curvas, RV con series
del subyacente).

### 2026-07-20 — v1.74 (canon aplicado: telemetría de caché + PILOTO function-calling en Research)
Tras revisar las guías oficiales (Anthropic "Building effective agents" +
"Context engineering", OpenAI "Practical guide to building agents", doc
DeepSeek), dos implementaciones:
- **[telemetría] caché del proveedor medido**: `ia.trazas` suma
  `cache_hit_tokens`/`cache_miss_tokens` (el hit cuesta ~10x menos) — mide el
  ahorro real del diseño prefijo-estable. Columnas nuevas → correr
  `apply_schema` al deployar.
- **[gateway +] `core.ai.completar_con_tools`**: loop de function calling
  (máx 4 rondas, presupuesto chequeado por ronda, resultados capados a 4k
  chars, traza por llamada, contrato nunca-levanta). Devuelve también el
  contexto acumulado de las tools → la verificación de números lo cubre.
- **[piloto] vista RESEARCH con 2 tools (JIT retrieval, canon)**:
  `serie_de(ticker, campo, desde, hasta)` (serie 1816 resumida por código:
  stats + ≤24 puntos) y `buscar_en_mails(tema)` (FTS con fechas). El modelo
  PIDE lo que la pregunta necesita ("¿cómo venía la TEA de TX26 en abril?")
  en vez de responder "no lo tengo". Motor: vistas con `tools` en el registro
  usan el loop; el resto sigue igual. Trading queda para el final (latencia).
- Testeado: ejecutor de tools + loop completo del gateway con proveedor fake.

### 2026-07-20 — v1.73 (HANDOFF transparente a la guía: "te tiene que guiar DIRECTO")
Corrección del user sobre v1.72b: derivar a la guía con un botón es un REBOTE
("¿qué FCI se operó más hoy?" desde HOME tiene que devolver la receta, no "no
lo tengo, tocá acá").
- **[motor +] handoff transparente**: cuando un copiloto de DATOS deriva a
  [[VISTA:ayuda]], el motor re-hace la MISMA pregunta a la vista ayuda ahí
  adentro y devuelve SU respuesta (la receta: Operaciones → tipo Suscripción/
  Rescate → por título ves los fondos). Profundidad 1 (ayuda no deriva a
  ayuda); si falla, cae a la respuesta original. Testeado.
- **[mapa ~]** equivalencia FCI + regla de "hoy": las vistas abren por defecto
  en el día en curso — no hay que tocar fechas (dato del user).
- (v1.72b, mismo día: dominio desconocido → prohibido inventar nombres de
  vistas; deriva a la guía — ahora ese camino termina en el handoff.)

### 2026-07-20 — v1.72 (guía: mapa PROFUNDO verificado contra el frontend + EQUIVALENCIAS)
El user señaló el límite estructural: el mapa curado no sabía del filtro SOLO
GAR ("títulos en garantía" SÍ existe: Back Office → Tenencia Valorizada) ni
que "FCI operado" = tipo de operación Suscripción/Rescate — "esto va a pasar
con miles de cosas más". Doble respuesta:
- **[mapa ~] releído del CÓDIGO del frontend**: Back Office con sus 5 pestañas
  reales + el filtro TODOS/SIN GAR/SOLO GAR/SIN ALQUILER · /operaciones con
  sus 4 pestañas reales (OPERACIONES/ARANCELES/AGRO/DEPÓSITOS & EXTRACCIONES —
  la vista NEGOCIO se había mudado a Manager y el mapa tenía info vieja).
- **[mapa +] filas EQUIVALENCIA** (concepto → dónde vive con OTRO nombre):
  garantía→SOLO GAR · FCI operado→Suscripción/Rescate por título · depósitos/
  extracciones · alquiler de títulos · aranceles. Regla nueva: ANTES de decir
  "no existe", revisar equivalencias.
- **[tooling] `scripts/gen_guia_check.py`** (contrato gen_sistema): extrae del
  frontend TODOS los destinos navegables (menú + labels de pestañas) y avisa
  cuáles no están en el mapa (--strict para CI). Corrido hoy: 29/29 cubiertos.
  El mapa ya no puede pudrirse en silencio.

### 2026-07-20 — v1.71 (marcador de derivación: nunca más crudo al usuario + navegación genérica)
Caso real desde HOME ("¿cómo veo qué títulos están en garantía?"): la
respuesta inventó nombres de secciones y mostró el marcador interno CRUDO
("[[VISTA:clave:ayuda]]"). Causa doble: (a) el modelo copió la palabra
"clave" del EJEMPLO de nuestra instrucción (misma lección de v1.67: las
instrucciones se escriben con valores reales, no placeholders); (b) la regex
de limpieza era estricta → el marcador malformado no se borraba.
- **[fix] limpieza a prueba de balas**: TODO residuo `[[VISTA…]]` (bien o mal
  formado) se borra SIEMPRE antes de mostrar; si adentro hay una clave
  registrada, la sugerencia se rescata igual (testeado).
- **[reglas ~] instrucción sin placeholder** ("EXACTAMENTE la palabra entre
  paréntesis de la lista: [[VISTA:renta_fija]], [[VISTA:ayuda]]").
- **[reglas +] navegación GENÉRICA**: "¿cómo/dónde VEO tal cosa?" preguntado
  en cualquier vista de datos → prohibido adivinar secciones; una frase +
  derivación a la Guía ([[VISTA:ayuda]]), que es quien tiene el mapa real.

### 2026-07-20 — v1.70 (la lupa de RKLB reveló 2 datos podridos en el contexto)
El user corrió `diag_contexto --vista renta_variable --pregunta RKLB` y la
foto (a) CONFIRMÓ el fix v1.69 (extremos del año presentes: máx 151 el 27/05 —
el "96.63" era el **R3 semanal** re-etiquetado) y (b) mostró dos datos basura
que se inyectaban al modelo:
- **[fix] pivots con precios NEGATIVOS**: la fórmula con el rango anual ancho
  de RKLB daba "S2 -10.35 · S3 -35.42" — un precio negativo no existe. Los
  niveles ≤0 ya no se muestran.
- **[fix] rankings con empate en cero**: "top semana: ARM +0.00%, NXE +0.00%…"
  un lunes a la mañana (WTD de todos = 0) es ruido que invita a conclusiones
  falsas — un ranking donde todos empataron en ~0 se OMITE entero.
- Nota de límite: los fundamentals traen los años 2021-2025 (balances) → para
  papeles CON fundamentals, "2025" está respaldado en el contexto y el candado
  de períodos fantasma no dispara; un juicio de PRECIO sobre 2025 apoyado en
  años de balance sigue siendo posible — lo contienen la regla de nombres
  exactos y el caso de eval.

### 2026-07-20 — v1.69 (caso RKLB: RE-ETIQUETADO — "máximo del año" respondido con el R3 anual)
Clase de error NUEVA (no la caza el grounding): preguntaron "¿máximo del año?"
y el modelo respondió 96.63 — número que SÍ existe en el contexto… como pivot
R3 ANUAL. No inventó el número: le inventó el SIGNIFICADO (el máximo del año
real no estaba en el contexto; el histórico 151 sí, y lo citó bien al ser
corregido). Doble fix:
- **[contexto +] extremos del AÑO en curso** en el [detalle] por ticker: máx/
  mín desde el 1 de enero CON FECHAS, desde la serie diaria
  (`_extremos_del_anio`, cache 900s). La pregunta ahora tiene el dato real.
- **[reglas +]** "cada número se cita con SU NOMBRE EXACTO": prohibido
  responder un dato pedido con otro número 'parecido' — re-etiquetar es
  INVENTAR aunque el número exista; sin el dato con ese nombre → "no lo tengo".
- Debugging asentado: `diag_contexto --vista renta_variable --pregunta "RKLB"`
  muestra el contexto exacto — así se rastreó que 96.63 era el R3 anual.

### 2026-07-20 — v1.68 (caso EWZ parte 4/final: PERÍODOS FANTASMA — el invento del "2025 flojo")
La falla más grave de la serie: el modelo justificó una recomendación con "un
2025 flojo" — período INVENTADO (los datos arrancan en 2026). Se escapó por la
única ventana sin candado: los AÑOS están excluidos del chequeo numérico a
propósito (fechas) y "flojo" es palabra, no número.
- **[verificación +] `_periodos_sin_respaldo`**: ningún año puede aparecer en
  la respuesta si no existe en el contexto → autocorrección "eliminá toda
  referencia a períodos que no están en los datos (sin reemplazarla por otra
  afirmación inventada)". Testeado.
- **[eval +] caso `ewz_sin_inventos`** en `evals/copiloto_vista.json` (10
  casos): congela TODA la serie EWZ — prohibidos 2025/pivots/equilibrio/beta/
  z-score/correlación/vol anualizada/intradía; exigido el benchmark (S&P).
- Límite honesto asentado: un invento cualitativo SIN número NI año ("viene de
  un trimestre flojo") no es capturable por código — lo contienen las reglas,
  el caso de eval y el 👎 del usuario. Las 4 capas juntas son la defensa.

### 2026-07-20 — v1.67 (caso EWZ parte 3: pivots/equilibrio prohibidos como FAMILIA + sin intradía por defecto)
Tercera vuelta del user sobre EWZ (la conversación ya venía mucho mejor:
benchmark citado, disculpa correcta ante el "2025 flojo" inventado). Dos
residuos, ambos con la MISMA causa raíz: el modelo copiaba vocabulario de
NUESTRAS PROPIAS reglas ("equilibrio" y "¿intradía?" estaban en los textos
de la v1.65/66). Lección asentada: las reglas se escriben en el idioma final.
- **[verificación ~]** la regex de niveles pasa de formas puntuales a la
  FAMILIA entera: `pivot/pivots/pivote/s`, `equilibrio` (exenta "inflación de
  equilibrio" — breakevens de RF) y resistencia/soporte con marco temporal.
  "Zona de pivots anual" y "quedó en equilibrio" ya no se escapan.
- **[reglas ~]** regla 3 reescrita SIN la palabra equilibrio: "al usuario le
  importa UNA cosa: ¿es buen momento para comprar o vender?" + traducciones
  ("no estás comprando caro" / "perseguir la suba" / "cuchillo cayendo").
- **[reglas ~]** regla 9: la repregunta de objetivo ya NO ofrece intradía
  ("¿lo estás pensando para comprar, o querés ver cómo viene?"); fuera de
  trading el day-trading aparece SOLO si el usuario lo pide él mismo.

### 2026-07-20 — v1.66 (caso EWZ parte 2: 3 candados de CÓDIGO contra el tecnicismo)
El user revisó las respuestas reales de EWZ: "extremadamente técnica, no se
entiende nada" (punto pivote/resistencia con precio, beta/vol anualizada/
z-score, 8+ cifras) y un error de RAZONAMIENTO: "+11.68% en el año viene bien"
sin compararlo con el mercado. Lo que el prompt no logró dos veces baja a
código (patrón rector):
- **[verificación +] niveles DELETREADOS**: el filtro solo cazaba PP/R1/S3 y
  el modelo los esquivaba escribiendo "punto pivote semanal", "resistencia
  anual en 36.96" → regex nueva (fuera de trading, y salvo que el usuario
  hable de niveles) dispara la autocorrección.
- **[verificación +] estadística NOMBRADA**: beta/correlación/z-score/vol
  anualizada/"a N ruedas" en la respuesta (sin que el usuario los pida) →
  autocorrección "traducila a lenguaje de mesa".
- **[verificación +] EXCESO DE CIFRAS**: pregunta puntual (no ranking/tabla)
  respondida con >5 números de dato → autocorrección "máximo 3 cifras, el
  resto en palabras".
- **[reglas ~] regla 8 nueva: TODO JUICIO ES RELATIVO AL MERCADO** — prohibido
  "viene bien/flojo" sin el benchmark de la tabla (SPY/QQQ): "+12% con el S&P
  +25% = quedó atrás". Sin benchmark en datos → número sin adjetivo.
- **[reglas ~] niveles traducidos a lectura de ENTRADA** (pedido textual del
  user): equilibrio sin corrida previa = "zona razonable para entrar: no estás
  comprando un techo"; extendido = "entrar acá es perseguir la suba".
- **[ruteo ~] señales pro nuevas**: compro/mantener/fin de año/posición/vender
  ("pensando si compro ahora y mantengo hasta fin de año" ahora escala a pro).

### 2026-07-20 — v1.65 (ruteo flash/pro por pregunta + regla conversacional + research compacta)
Tres pedidos del user en uno (el disparador: un usuario preguntó "¿cómo ves
EWZ?" y el copiloto volcó datos técnicos en vez de preguntar el objetivo):
- **[ruteo] flash vs pro POR PREGUNTA** (`base._es_profunda`, determinista,
  testeada): señales de análisis (tesis/escenario/proyección/recomendación/
  invertir/…), conversación de 3+ turnos, o consigna >220 chars → tier PRO;
  el resto → flash. TRADING sigue clavado en pro (fija su `tarea`). La
  ambigua corta ("¿cómo ves EWZ?") queda en flash A PROPÓSITO → regla 8.
- **[reglas +] regla 8 del system (TODAS las vistas): INVITÁ LA CONVERSACIÓN.**
  Pregunta abierta sin objetivo → estado esencial en 1-2 frases + repregunta
  ("¿lo mirás para el intradía o pensando en invertir?"). El volcado técnico
  ante pregunta ambigua queda prohibido. El follow-up del usuario ("para
  invertir") escala solo a pro por el ruteo.
- **[contexto ~] tabla de RESEARCH COMPACTA** (la balanza midió ~35% de celdas
  vacías en la ancha de 17 columnas): ahora 5 columnas con `dato` denso por
  fila ("TEA 7.31% (7d -0.39pp · 30d +0.59pp) · paridad 97.86% · …"), sin "-",
  sin fecha repetida por fila. `celda_max` 220. Reglas reescritas al formato.
- **[tooling] `diag_ia_trazas --buscar EWZ`**: recupera conversaciones enteras
  de `ia.trazas` por texto (pregunta o respuesta) — para revisar cómo respondió
  el copiloto cuando el panel de OBSERVABILIDAD quedó tapado por una batería.

### 2026-07-20 — v1.64 (guía: filtros de Operaciones EN VIVO + fixes de la batería)
Primera batería del guía (`bateria_guia`, 20/20 respondidas) + pedido del user
(que sepa los filtros de Operaciones y sus valores):
- **[contexto +] `[filtros de Operaciones]`**: los VALORES vigentes de los
  selectores de MOVIMIENTOS (mercado / segmento nivel 1 / nivel 3) leídos EN
  VIVO de los mismos catálogos que usa la vista (`ops_mercados/segmentos/
  niveles3`), cache 1h. Nada hardcodeado → nunca queda stale. + fila del mapa
  de Operaciones reescrita con todos los filtros (rango de fechas, moneda,
  cuenta, operador, solo/sin ACA VALORES).
- **[mapa ~] fixes cazados por la batería**: cauciones (cuánto pagan hoy) y
  futuros de dólar con devaluación implícita → están en HOME, no en Agro/
  Sintéticos (el guía mandaba mal); Agro aclara "cauciones/pases CON COBERTURA
  del agro".
- **[reglas +]** prohibido inventar detalles visuales de la interfaz (la
  batería lo pescó diciendo "primer ícono del menú de la izquierda" — el menú
  es la barra superior y es lo único que afirma del layout).
- **[perf] `jerga_permitida` del guía** (sección/ruta/menú/…): el detector de
  jerga disparaba la autocorrección al pedo en 3 de 20 preguntas (re-llamada
  al LLM por usar la palabra "sección" — que en un guía es idioma nativo).
- **[batería]** +3 preguntas de filtros ("cuánto se operó en BYMA", valores
  por filtro, rango de fechas/segmento) → 23.

### 2026-07-20 — v1.63 (fix research: el mail del día llegaba VACÍO al copiloto)
Cazado por LA BALANZA en su primera corrida en prod (el bloque [research más
reciente] pesaba 19 tokens = sin cuerpo): `_mail_reciente` leía `cuerpo` pero
`listar_research` expone `texto` (el crudo limpio). Fix + test que congela el
campo. Lección asentada: la balanza no solo mide costo — delata bloques rotos
(un bloque sospechosamente liviano es un bloque vacío).

### 2026-07-20 — v1.62 (optimización: TRADING a tier PRO + balanza de tokens + caché)
Plan de optimización acordado con el user (técnicas 1 y 2 + escalado de modelo):
- **[modelo ~] TRADING corre en tier PRO** ("TRADING jamás en flash" — ahí se
  opera plata en vivo). Tarea nueva `copiloto_vista_pro` en el gateway (pro,
  thinking disabled, timeout 90s); la vista la elige vía `tarea` en su entrada
  del registro (default sigue flash). La autocorrección usa el MISMO tier.
  En `ia.trazas` las dos tareas se distinguen → se puede comparar calidad/costo.
- **[medición] LA BALANZA**: `diag_contexto --pesos` estima tokens por pieza
  del prompt (system base, reglas, tabla, cada bloque extra) ordenado desc con
  %. Es la base para recortar contexto CON DATOS (REGLA #2 aplicada a la IA).
  Ya pagó en dev: (1) el system base pesa ~2k tokens él solo; (2) detectó que
  el cap de 60 chars por celda MUTILABA el mapa de la vista ayuda.
- **[fix] `celda_max` por vista** en el TSV (default 60; ayuda 400): el mapa
  del guía ya no se trunca.
- **[caché] timestamp del encabezado a MINUTOS** (era segundos): el proveedor
  cachea el prefijo repetido del prompt (~10x más barato); con segundos, cada
  pregunta rompía el prefijo aunque la tabla no cambiara.

### 2026-07-20 — v1.61 (vista AYUDA: el GUÍA de la plataforma en toda página)
Pedido del user: un asistente estilo DigitalOcean/Supabase que funcione en TODA
la página y NO hable de datos — solo te lleva a donde querés ir.
- **[vista +] `ayuda`** (`copiloto/ayuda.py`, módulo `home` → todos los
  internos con `ia`). La tabla es el **mapa del producto curado a mano**
  (secciones, cómo llegar por menú, qué hay, quién la ve, + RECETAS frecuentes
  tipo "cuánto operó una cuenta" → pasos). Lenguaje de negocio, cero nombres
  internos. **Mantener el mapa al mover/crear vistas es parte de este doc vivo.**
- **[reglas]** PROHIBICIÓN TOTAL de datos/números/consejos: ante "¿cuánto operó
  X?" responde CÓMO verlo (menú → vista → filtro), jamás el dato. Deriva a los
  copilotos de datos con [[VISTA:x]] cuando la pregunta es de análisis. No
  inventa vistas que no están en el mapa.
- **[UI]** `header.tsx`: el slot derecho ahora SIEMPRE tiene un panel — rutas
  con copiloto de datos propio muestran el suyo; el resto (operar, operaciones,
  aum, valuaciones, manager, back-office, …) muestran el GUÍA. El invitado
  jamás lo ve (sin módulo `ia`, default-deny — REGLA #8 intacta).
- Sin extras ni queries: contexto 100% estático → barato y cacheable.

### 2026-07-20 — v1.60 (TRADING: la rueda como PELÍCULA — trayectoria intradía + holding)
Pedido del user: el día no es lineal (perdió plata por leerlo así) — "+1% de
QQQ" no dice nada sin el camino (venía -2, recuperó, se dio vuelta). Todo
derivado por código de series que YA existían:
- **[contexto +] `[rueda X hoy]`**: trayectoria intradía del papel EN FOCO +
  SPY/QQQ (CEDEARs como proxy del índice, si tienen tape) + el dólar financiero
  (`valuaciones.dolar` intradía). Por bloque: apertura/máx/mín CON HORA,
  posición en el rango, distancia desde máx/mín, y el "camino por media hora"
  (var% por tramo de 30' — la FORMA del día). Compresor puro `_trayectoria`
  (testeado); los giros quedan legibles sin adjetivos (máx 11:20 + ahora -1.8%
  desde ahí = se dio vuelta a las 11:20).
- **[contexto ~] `[mis posiciones]`** ahora trae el "cómo venís" calculado:
  last actual + var% vs entrada + a favor/en contra según el lado (cero
  aritmética del modelo).
- **[contexto ~] `[reloj de mercado]`** suma el paso del tiempo: "van Xh de
  rueda; quedan Yh hasta el cierre".
- **[reglas +] "LA RUEDA ES UNA PELÍCULA"**: +1% viniendo de -2 = fuerza vs +1%
  que era +2.5 = apagándose; giros con hora; rueda con tramos alternados =
  volátil (quiebres valen menos); papel vs mercado (debilidad propia vs de
  fondo); un giro a 30' del cierre ≠ a las 11:00.
- **[reglas +] "HOLDING"**: la posición se evalúa contra entrada + nivel de la
  tesis + película (nunca el último tick): retroceso a un nivel respetado con
  mercado intacto = ruido ("el día no es lineal"); nivel perdido + giro del
  mercado confirmado = tesis muerta, se corta (ni pánico ni aguante ciego);
  día extendido + tramo final = asegurar contra el próximo nivel.

### 2026-07-20 — v1.59 (research: el copiloto ve las 4 fuentes JUNTAS, siempre)
Corrección de alcance sobre v1.58, a pedido del user ("el research tiene que
saber de todo"): la tabla ya NO es la de la tab activa — **concatena SIEMPRE
las 4 fuentes** (columna `fuente`: 1816 · bcra · fred · reportes) y
`params.tab` pasa a ser solo señal de prioridad en extras, no filtro. Volumen
total comparable a la tabla de renta_variable (~187 filas, probada) — los watch
son curados y hay caps por fuente (120; reportes 30). Los 3 readers EOD van con
`@cached(ttl=300)` (N preguntas comparten queries). Reglas reescritas: "analista
integral — tu valor máximo es CRUZAR fuentes" + chip nuevo "Cruce local vs
afuera". Test: el fetch concatena las 4 fuentes sea cual sea la tab.

### 2026-07-20 — v1.58 (vista RESEARCH unificada: un copiloto para toda /research)
El "Nivel 2" que VISTA_RESEARCH.md §2.7 dejó anotado ("la IA se consume SOLO
cuando alguien pregunta"). Decisión del user: **UN copiloto para toda la vista**
— el panel manda `params.tab` y el contexto es el de la tab activa (la tab
RV INTERNACIONAL queda afuera: ya tiene su vista `reuters`).
- **[vista +] `research`** (`copiloto/research.py`, módulo RBAC `research`).
  Tabla por tab, columnas genéricas + específicas: `argentina` = watch 1816
  (último TEA%/paridad%/precio/duration por bono + cambios de TEA 7/30d en pp;
  TEA/paridad se pasan de fracción a % en código) · `bcra`/`internacional` =
  watch BCRA/FRED (último valor + cambios 7/30d como DIFERENCIA absoluta en la
  unidad) · `reportes` = ficha de documentos + comentario del equipo (el PDF no
  se lee — declarado en reglas). Derivación pura `_ultimo_y_cambios` (testeada).
- **[contexto +] el research ESCRITO, citable con fecha**: `[research más
  reciente]` (el último mail, recortado) SIEMPRE + `[1816 dijo]` = **FTS
  determinista** sobre `ia.research` con los términos de la pregunta
  (`_terminos_busqueda`, stoplist + cap 3 términos / 4 fragmentos, índice GIN ya
  existente). Regla dura: "1816 dijo X" SOLO citando esos bloques con fecha; sin
  resultados → "no encuentro menciones" (jamás parafrasear de memoria).
- **[UI]** botón del copiloto en la barra de tabs de `/research`
  (`research-view.tsx`, `getParams={() => ({tab})}`), oculto en RV INT.
- **[chips]** El día en pocas líneas · ¿Qué se movió? · Número + narrativa ·
  ¿Qué estoy viendo?

### 2026-07-20 — v1.57 (TRADING: memoria de ruedas + modo propositivo)
Pedido del user: que el copiloto de trading recomiende y guíe ("¿dónde está el
trade?", "¿cómo busco ganar $X?"), detecte patrones entre días, y siga forzando
la disciplina de operar EN los niveles. Dos piezas nuevas, ambas server-side y
deterministas (el modelo narra, el código calcula):
- **[contexto +] `[historia X — últimas 7 ruedas]`** para el papel en foco + los
  mencionados (cap 3, helper compartido `_foco_y_mencionados`). Fuente: las
  tablas YA existentes `mercado.cedears_ohlc_daily` / `bonos_ohlc_daily`
  (ventana 20 ruedas, jobs 20:15 UTC) — sin tabla ni job nuevos. Por rueda:
  cierre, var%, rango, **niveles tocados** (pivots re-calculados con la base que
  regía ESE día) y zona de cierre. Derivación pura `_historia_derivar` (testeada).
- **[contexto +] `[setups ahora]`**: cards a ≤0.50% de un nivel con recorrido a
  los niveles adyacentes YA calculado (% y ARS por nominal). Si no hay, el bloque
  lo dice explícito (el modelo no puede inventar un setup).
- **[reglas ~] MODO PROPOSITIVO**: ante "¿dónde está el trade?" propone SOLO
  desde [setups]/radar con entrada-confirmación-objetivo-riesgo; objetivos en
  plata se traducen con los ARS/nominal precalculados ("~150 nominales SI el
  nivel aguanta"), siempre como recorrido posible, nunca promesa; sin setups la
  respuesta correcta es "hoy no hay trade" (anti-overtrading). Sigue prohibido
  el imperativo ("entrá ya").
- **[refactor]** El radar T2 del vigía se extrajo a `trading._radar_candidatos`
  (motor.py lo consume) — una sola fuente de candidatos para vigía y copiloto.
- **[chips +]** "¿Dónde está el trade?" y "Memoria del papel".

### 2026-07-18 — v1.56 (reuters: columna `rubro` en el contexto)
- **[contexto +]** `tablero_reuters()` ahora trae el `rubro` del catálogo de
  CEDEARs (join a `mercado.cedears`) → la vista `reuters` del copiloto lo ve como
  columna (`registro.py`). Habilita preguntas por rubro en el tablero RV
  Internacional (la UI ganó columna RUBRO + filtro, y el CCL se movió al lado de
  ÚLTIMO).

### 2026-07-18 — v1.55 (la vista reuters se mudó a /research + RE-GATEADA a módulo `research`)
- **[UI]** El tablero REUTERS (y su copiloto in-view, vista `reuters`) ya no vive
  en /trading: ahora es la tab RENTA VARIABLE INTERNACIONAL de **/research**
  (`research-view.tsx`).
- **[gate ~]** Directiva del user: TRADING queda ADMIN-ONLY y RESEARCH se habilita
  a toda la mesa → la vista `reuters` del copiloto pasó de módulo `trading` a
  **`research`** (registro.py). Quien tenga `research` + `ia` puede usarla; ya no
  exige trading. Contexto, reglas y chips sin cambios. Los endpoints HTTP del
  tablero también se mudaron (`/api/trading/reuters*` → `/api/research1816/reuters*`).

### 2026-07-17 — v1.54 (TRADING accede a TODO Reuters: quote US + fundamentals)
Pedido del user: que el copiloto de TRADING tenga acceso a todo lo de Reuters que
hicimos, cotizaciones Y fundamentals. Nota: los fundamentals NUNCA habían estado
en ningún copiloto (ni el de la vista Reuters, cuya tabla es solo quotes) — es lo
primero que los expone.
- **[contexto +]** Dos bloques nuevos en `_extras_trading`, para el papel EN FOCO
  + los MENCIONADOS en la pregunta (cap 3), vía el subyacente US (`_underlying` de
  la card, fallback al ticker):
  - `[reuters X — quote US]`: `core.eikon_live.tablero_reuters` — last/bid/ask,
    pre y after con su variación, retornos EOD 5d→5años, CCL implícito. Los
    retornos son al cierre anterior; MTD/YTD calendario ≠ 1m/1año móvil (se aclara
    en las reglas).
  - `[fundamentals X — Reuters]`: `core.eikon_live.tablero_fundamentals`
    (`mercado.eikon_fundamentals`) — valuación (market cap, P/E y fwd, EV/EBITDA,
    P/VL, div yield), resultados (ingresos, EBITDA, resultado neto, FCF, capex),
    márgenes y solidez (deuda, caja, DN/EBITDA, current ratio), próximo balance.
    El rol de PROFESOR (explicar cada métrica) se sumó a `_REGLAS_TRADING`.
- **[eficiencia]** Dos queries únicas (la plaza entera) filtradas en memoria — no
  N queries por card.
- **[escalas, verificado del frontend — REGLA #2]** `market_cap`/`deuda_total`/
  `caja` vienen en USD ABSOLUTO (→ se pasan a millones ÷1e6) pero
  `revenue`/`ebitda`/`net_income`/`fcf`/`capex` YA vienen en millones; todo se
  presenta en millones (parser-safe, sin separador de miles). Los % del quote y
  los márgenes NO se multiplican ×100 (ya vienen en %). Congelado en
  `test_reuters_fundamentals_escalas_y_pct` para que un cambio de escala no rompa
  el verificador en silencio.
- **[tests]** +2 casos (escalas/% y detección foco+mencionados).

### 2026-07-17 — v1.53 (3 fallos reales de trazas: anti-alucinación global · precio punta a punta RF · cap de tarjetas)
Directiva del user tras leer trazas en vivo: "que NO invente, NO asocie, NO saque
conclusiones — lo justo y necesario; exprimir que tenemos datos posta". Modo
elegido: **describe, no explica** (intermedio — comparar dentro de los datos sí,
causas/conocimiento externo no).
- **[prompt ~, GLOBAL — Bug B: alucinación en HOME]** Traza #330: "Merval cae 3.2%
  liderado por tecnología y papeles de IA" (el MERVAL no tiene tech de EE.UU. — el
  modelo pegó el pulso de CEDEARs al índice ARG) + "cauciones bajan → alivian el
  carry" (asociación inventada). Raíz: `_SYSTEM_BASE` PEDÍA narrar causa (regla 4
  "qué pasó → **por qué** → qué mirar"). Nueva **REGLA DE ORO** en `_SYSTEM_BASE`
  (todas las vistas): describís QUÉ y CUÁNTO, nunca POR QUÉ; prohibido explicar
  causas, asociar movimientos por deducción y usar conocimiento propio de qué
  contiene un mercado/índice. Regla 4 → "hilo DESCRIPTIVO" (sin "por qué"). Ejemplo
  RKLB depurado de su conclusión causal. `_REGLAS_HOME`: el MERVAL (acciones ARG)
  y el [pulso por rubro] (CEDEARs/ADRs = EE.UU.) son DOS mundos — jamás explicar
  uno con el otro ni atribuirle sectores al MERVAL.
- **[contexto +, Bug A — RF inventa retorno punta a punta]** Trazas #329-337
  (javier.curzel): pidió "performance de TZXD6 en junio" / "punta a punta" y ese
  número NO existía en el contexto (solo la descomposición modelada de 30d y el
  precio de hoy) → el modelo lo improvisó y hasta **reusó el precio de un bono
  para otro** (TZXD6→TZXS8). Nuevo bloque `[precio punta a punta TICKER]`
  (`_precio_puntapunta_rf`, bajo demanda al nombrar bonos): retorno de PRECIO real
  cierre-a-cierre 7d/14d/30d con fecha y precio de cada punta, desde
  `snapshots_cierre_hist`. Regla nueva en `_REGLAS_RENTA_FIJA`: precio punta a
  punta (crudo) ≠ descomposición (modelo, puede no coincidir); si piden un MES
  CALENDARIO exacto que no coincide con las ventanas móviles, decirlo y ofrecer la
  más cercana; **JAMÁS reusar las puntas de otro papel** — si no está el bloque de
  ese ticker, decir que no se tiene y PARAR.
- **[bugfix, Bug C — TRADING "ve algunas cards y no todas"]** Trazas #351-354:
  "ASTS está en mis cards" y el modelo juraba que no. VERIFICADO (no hipótesis):
  el frontend `trading-view.tsx` tiene `SLOTS=12` y manda todas las cards, pero el
  backend `_MAX_TARJETAS` estaba en **8** → truncaba en silencio de la 9ª en
  adelante. Fix: cap **8→12** (matchea SLOTS) + `_fetch_trading` GARANTIZA una fila
  por card saneada (las que no resuelven datos entran como "sin datos ahora", nunca
  se dropean) + regla de humildad en `_REGLAS_TRADING` (no tratar de equivocado al
  trader por sus propias cards). Test `test_sanear_params_trading_cap_12` congela
  el cap para que CI cace el drift.
- **[refactor, sin cambio de comportamiento]** `copiloto.py` (~3.400 líneas) →
  paquete `api/services/copiloto/`: `base`, `verificacion`, una `<vista>.py` por
  vista, `registro` (VISTAS), `derivacion` y `motor`. `__init__.py` re-exporta la
  superficie pública histórica → `from api.services import copiloto; copiloto.X`
  sigue igual. Los 3 tests que parcheaban `copiloto.X` de funciones cross-módulo
  ahora parchean el submódulo donde el nombre se resuelve (renta_variable/home/
  motor). 311 tests verdes, ruff limpio, import-chain OK. Ganancia: tocar una
  vista = tocar SOLO su archivo; cada vista se testea aislada.

### 2026-07-17 — v1.52 (reuters: glosario "profesor de la vista")
- **[reglas ~]** `reuters` suma un GLOSARIO (bid/ask, pre/after, market cap, EV,
  P/E y forward, EV/EBITDA, EBITDA, FCF, márgenes, DN/EBITDA, current ratio,
  retornos calendario vs móviles) + rol explícito de explicar qué es / cómo se
  calcula / cómo se lee cualquier métrica, con ejemplo de la tabla si suma.
  Pedido del user: que la IA explique cada cosa de la vista.

### 2026-07-16 — v1.51 (vista nueva: REUTERS)
- **[vista +]** `reuters` (módulo `trading`, tab REUTERS de /trading): el tablero
  live de subyacentes US del feed Eikon de oficina (`docs/INTEGRACION_REUTERS.md`).
  Fetch: `core.eikon_live.tablero_reuters` (sin cache, tabla chica y live) con
  `hora_dato` convertida a ART. Columnas: quote USD completo + pre/after market
  con sus variaciones YA calculadas + retornos EOD 5D→5A. Reglas clave: los
  retornos son al cierre anterior (no mezclar con el día), `ret_mes%` (MTD) ≠
  `ret_1mes_movil%` (30d), CCL pendiente (explicar concepto, no inventar número),
  precios en ARS derivan a Renta Variable/Trading. 3 chips (panorama / ranking
  de retornos / fuera de rueda). Panel: `<IaVistaPanel vista="reuters" />` en
  `reuters-view.tsx`. Pendiente: batería manual del user desde el panel.

### 2026-07-14 — v1.50 (post-batería de las 3 vistas nuevas: 41/45 buenas, 4 fallas → fixes)
Corrida real en el Droplet (`scripts/bateria_nuevas_vistas`, 15×3): agro 14/15 ·
opciones 15/15 · ons 12/15. Lo que se cazó y bajó a código:
- **[bugfix verificador, causa de 3 de las 4 fallas]** La puntuación de FRASE
  pegada al número ("operó 634.100, y…" → token `634.100,`) rompía TODOS los
  parseos → bloqueaba respuestas CORRECTAS. `_candidatos_numericos` ahora
  descarta `.`/`,` finales. Test de regresión con la línea exacta de la batería.
- **[prompt ~, fallo grave]** ONs #12 afirmó "acá todas las ONs son ley
  argentina" — INVENTADO (el campo ley no existe en el master y muchas ONs son
  ley NY). Regla dura en `_REGLAS_ONS`: jamás afirmar la ley de emisión; concepto
  sí, mapeo a papeles no. (Fix de fondo pendiente de decisión del user: agregar
  campo `ley` al master de ONs en Manager.)
- **[prompt ~]** Agro #8 mezcló el costo pase con el spread pizarra−futuro y
  dijo que "la mesa lo carga": definición exacta en `_REGLAS_AGRO` (constante de
  mercado 0,45%, NO incluye el pase bruto).
- **[jerga ~]** "adr" whitelisted en la vista opciones — la vol realizada de
  referencia ES del ADR y la autocorrección lo borraba (3 veces en la batería).
- **[contexto +]** Agro: línea `[estado]` SIEMPRE (live con edad del tick, o
  snapshot) — la #15 no podía confirmar si los futuros operaban en vivo.
- **[verificación ~]** El mensaje de autocorrección ahora enseña el redondeo
  correcto ("-83.56 se escribe -83.6, jamás -83") — el caso del TNAV truncado
  (agro #2, única falla que la reflexion no pudo salvar).
- **[hallazgos de DATOS (no del asistente)]** TMF27 aparece en la curva ONs con
  paridad 4704% y es Tesoro, no corporativa → revisar su fila en el master;
  PNDCO precio 43 con paridad 107,5 (inconsistente). El asistente los manejó
  con cautela pero ensucian los bloques.
- **[datos ~, mismo día]** El tape de CEDEARs (`scanner.get_cedears_trades` /
  `get_cedears_intraday`) emitía timestamps UTC → el copiloto de TRADING le
  contaba al trader horas corridas +3 ("19:52" a las 16:52) y la vista igual.
  Ahora TODO el tape sale en hora ARGENTINA naive (misma convención que los
  bonos). Afecta: bloque [tape] del copiloto, panel Time & Sales, chart LIVE
  y tools MCP (descripciones actualizadas).

### 2026-07-14 — v1.49 (TRES VISTAS NUEVAS: Agro · Opciones · ONs — pedido del user)
- **[vista +]** `agro` (módulo `agro`, página /agro): tabla = el PASE AGRO
  aplanado (pizarra + futuros por commodity; pase en US$/Tn y TNAV ya en %).
  Extras: [pase con cobertura] (cards ON/Pagaré con ganancia por tonelada y el
  pase lleno NETO del costo pase, todo precalculado), [datos de referencia]
  (dólar BNA / Matba / BNA T-1 con su fecha A3500 + costo pase 0,45%), [cámara
  de cereales] y aviso de frescura fuera de rueda. `agro_sql.get_pase_agro`
  ganó `@cached(5s)`: fetch y extras comparten el payload (y el poll del shell
  lo aprovecha). Reglas: los inputs manuales pueden faltar → "sin dato cargado"
  (jamás estimar); semántica del signo del pase; ON vs Pagaré SIEMPRE juntos
  (la elección es del usuario); nada de clima/cosecha/retenciones de memoria.
  Chips: Panorama agro · ¿ON o Pagaré? · Datos de referencia.
- **[vista +]** `derivados` (módulo `derivados`, página /derivados): la chain
  de OPCIONES sobre GGAL — solo contratos CON precio (mismo filtro que la
  vista), IV fracción→%, griegas tal cual las calcula el motor (jamás
  recalcular). Extras: [referencias] (tasa libre de riesgo + vol realizada 40
  ruedas local/ADR) y [resumen por vencimiento] (calls/puts, rango de strikes,
  volumen efectivo, IV del strike más cercano al spot — precalculado). Reglas:
  la prima no es el subyacente; IV vs realizada SOLO con los números dados;
  griegas traducidas a lenguaje de mesa; actividad = vol_efectivo; PROHIBIDA la
  recomendación de operatoria concreta; la acción GGAL se deriva a RV/Trading.
  Chips: Panorama de la chain · ¿La vol está cara? · Calls vs puts.
- **[vista +]** `ons` (módulo `renta-fija` — el mismo gate que la página /ons
  en la nav): la curva de ONs por sector (energía/finanzas/otros), TEA
  fracción→%. Extras: [TEA promedio por sector y moneda] (precalculado — la
  moneda separa rankings SIEMPRE) y [próximos pagos] (calendario 90 días,
  monto por 100 VN). Reglas: LA ILIQUIDEZ ES EL TEMA CENTRAL (nominales_dia
  manda; una TEA altísima = precio viejo O riesgo del emisor, sin inventar
  cuál); riesgo crediticio además de tasa; soberanos/lecaps → Renta Fija.
  Chips: Panorama de ONs · Mejores TEA en USD · Pagos próximos.
- **[UI]** Botón "Consultale a la IA" del header también en /agro, /derivados
  y /ons (mapa `VISTA_IA_POR_RUTA`, header.tsx). La derivación entre vistas
  las incorpora sola (lista RBAC-aware).
- **[tests]** 5 casos nuevos congelan: registro/módulos de las 3 vistas,
  aplanado + tnav% del agro, filtro sin-precio + iv% de opciones, tea% +
  sector de ONs, y promedios por sector-moneda.

### 2026-07-13 — v1.48 (no sustituir lo que no se tiene + NARRÁMELO fuera del briefing)
- **[prompt ~, fallo real]** "¿Qué ONs ley local me recomendás?" en RF → el
  modelo reconocía que no tenía ONs ("solo soberanos y del Tesoro") pero igual
  ofrecía bonares como "lo más parecido" y recomendaba papeles que el usuario NO
  pidió. Regla GLOBAL (`_SYSTEM_BASE`, aplica a todas las vistas): si piden
  específicamente un instrumento/tipo/clase que no está, se dice derecho que acá
  no lo hay (y si vive en otra vista, se deriva) y se PARA — jamás "lo más
  parecido" ni una recomendación de reemplazo no pedida.
- **[producto −]** Narración del briefing ELIMINADA por completo (pedido del
  user: "no sirve, no tiene sentido, no funciona bien"). Se quitó: botón
  🗣 NARRÁMELO + handler + imports del handoff del modal (`briefing-modal.tsx`),
  el chip "Narrame el briefing" del copiloto HOME y el prompt
  `_PREGUNTA_NARRAR_BRIEFING` (copiloto.py). El chip "¿Cómo viene el mercado?"
  (panorama por segmentos) se mantiene — es otra consulta, sí útil.

### 2026-07-13 — v1.47 (TRADING: tarjeta ≠ posición + "¿qué tengo abierto?" directo)
- **[prompt ~, fallo real del shadow]** "¿Qué posiciones tengo abiertas?" (única
  abierta: SHORT 18 SNDK) → el modelo trajo MU (que está en las TARJETAS, no en
  las posiciones) como "tu otra referencia del rubro" e infló la respuesta con el
  desplome del sector. Raíz: `_REGLAS_TRADING` decía "leé desde la posición" pero
  no separaba TARJETAS (watchlist que el usuario mira) de POSICIONES (solo el
  bloque `[mis posiciones abiertas]`). Regla reescrita: un papel de la tabla que
  no esté en ese bloque NO es posición (jamás llamarlo así ni traerlo como "tu
  otro papel", aunque sea del mismo rubro); "¿qué tengo abierto?" se responde
  DIRECTO y solo con el bloque (vacío → "no tenés posiciones abiertas"), corto y
  sobre los pivots de ESE papel, sin arrastrar el resto de la tabla ni el sector.

### 2026-07-13 — Rollout a la mesa + cartel de NOVEDAD + fix color del botón
- **[rollout]** El user sumó el módulo `ia` a **trader y sales** en la matriz de
  roles → el copiloto (y el briefing) dejan el shadow admin-only y quedan
  disponibles para toda la mesa. Canary → producción.
- **[producto +]** Cartel de novedad del copiloto: `acaquant-web/src/components/
  copiloto-noticia.tsx`, montado GLOBAL en `layout.tsx` (todas las páginas).
  Modal centrado tipo NOTICIA, una-sola-vez por usuario (localStorage
  `noticia.copiloto.v1` — subir la versión re-anuncia), **sin horario** (aparece
  apenas carga la app — es anuncio de feature, no data de mercado), con poll de
  60s + visibilitychange para que aparezca también a quien ya tenía la sesión
  abierta. Gate `ia` resuelto en el layout (prop `hasIa`, cero llamadas al
  backend). z-index sobre el briefing (one-time, va arriba). "Probar ahora →"
  lleva a /renta-variable. Copia sin jerga (curvas/bonos/CEDEARs/señal, sin
  "pivots").
- **[UI fix]** Botón "Consultale a la IA" del header: usaba `bg-[var(--t-accent)]`
  → en modo oscuro quedaba una pastilla NARANJA sobre el header azul fijo
  (`#094293`), fuera de tono con la nav blanca. Nueva prop `tone` en
  `IaVistaPanel`: `onDark` (header HOME/RF/RV = blanco/sutil como la nav, en
  ambos temas) vs `accent` (default — /trading, donde el botón se apoya sobre el
  fondo de la página y el acento sí funciona).

### 2026-07-13 — v1.46 (ronda 2 de la batería HOME: DLR con fallback + puntual ≠ panorama)
- **[datos +]** `[futuros DLR ROFEX]` con FALLBACK al último cierre: la lupa
  confirmó que el snapshot live viene vacío fuera de rueda (por eso la #8
  no veía la curva e inventó una "vista de Futuros ROFEX") → si el live no
  trae nada se lee `mercado_hist` FuturosDLR (última fecha, precio_cierre +
  TNA de cierre) y el header declara "cierre del YYYY-MM-DD".
- **[prompt ~]** Puntual ≠ panorama: la estructura por segmentos es SOLO
  para "¿cómo viene el mercado?"/narración — una pregunta puntual (la #12,
  el MERVAL) se responde puntual, sin recorrer segmentos que nadie pidió.
- **[prompt ~]** Un dato faltante de ESTA vista jamás es motivo de
  derivación (la #9 mandaba cauciones a Renta Fija, la #13 dólares a
  Trading): cauciones/DLR/watchlist son de acá — "existe pero sin dato
  ahora"; derivar solo cuando la pregunta cae DE LLENO en otra vista, ante
  la duda no derivar ni inventar vistas.

### 2026-07-12 — v1.45 (post-batería HOME: límites de la vista + redondeo legítimo)

### 2026-07-12 — v1.45 (post-batería HOME: límites de la vista + redondeo legítimo)
- **[verificación ~]** El modelo redondea a 1 decimal ("5,9%" cuando el dato
  es 5.85) y la pregunta 11 de la batería moría bloqueada: el verificador
  ahora acepta MEDIA UNIDAD del último decimal escrito (5,9↔5.85 pasa;
  "4,2" para 4.11 sigue cayendo — redondeo mal hecho es error). Menos
  reintentos de reflexion, menos bloqueos duros.
- **[prompt +]** Límites de la vista HOME (batería 9/10/14): pregunta
  DEDICADA a acciones/CEDEARs → titular en 1-2 frases + derivar a Renta
  Variable, jamás intentar recomendaciones de papeles desde acá (la 14
  respondía por rubro y pedía la lista). Verificado en ronda 2: 10/11/14 OK.
- **[tooling]** `scripts/diag_contexto.py --vista <v>`: la lupa GENERALIZADA
  a las 4 vistas (borra `diag_contexto_rf.py`, REGLA #5) — con ella se
  confirmó el bloque DLR ausente (→ fallback en v1.46).
- **[en observación]** El verificador matchea números por valor ABSOLUTO: la
  pregunta 6 escribió el canje con signo invertido ("-2.97%" cuando el bloque
  dice +2.97%) y pasó. Enforcement de signo pendiente — tiene falsos
  positivos no triviales (rangos "4-6", "cayó 4.7%" en positivo).

### 2026-07-12 — v1.44 (HOME segmentada — el mercado no es uno)
- **[rediseño, feedback del shadow]** "¿Cómo viene el mercado?" mezclaba
  granos con dólar y acciones en una sola conclusión, y el pulso estaba
  CIEGO a la renta fija ("en ARGY es donde más a full está"). La narración
  ahora es POR SEGMENTO en orden fijo: renta fija → acciones → dólares y
  tasas → commodities/índices → cruce final. Prompt de narración, chip y
  copia del modal actualizados en el mismo cambio.
- **[contexto +]** `[renta fija hoy]`: TEA promedio por CURVA y TRAMO
  (corto/medio/largo) con Δ vs el último cierre en bps — responde "¿comprime
  la corta o la larga? ¿los CER o los globales?" con datos (reusa los fetch
  cacheados de la vista RF). `[pulso por rubro]` de CEDEARs sumado como
  segmento ACCIONES (misma función de la vista RV).
- **[prompt +]** PROHIBIDO promediar grupos a mano ("los granos +4%"): para
  hablar de un grupo se nombra el que más se mueve con SU número — hipótesis
  principal del bloqueo de verificación de "Narrame el briefing" del shadow.
- **[tooling]** `scripts/bateria_home.py`: 15 preguntas (narración incluida
  como #1) cubriendo segmentos, honestidad y derivación — para correr en el
  Droplet y cazar los bloqueos con su motivo.

### 2026-07-12 — v1.43 (CUARTA VISTA: Home — panorama + briefing narrado)
- **[vista +]** `home` (módulo `home`, los 3 roles; invitado jamás — el gate
  `ia` es default-deny): la vista del "¿qué está pasando?". Tabla = watchlist
  entera (~40 filas: métricas ARGY con anclas día/7d/MTD/YTD + market_quotes)
  — tan chica que entra completa, sin detección de tickers. La más barata de
  las 4 (~4-5k tokens vs 22k de RV).
- **[contexto]** Bloques: `[briefing de apertura]` = el MISMO payload del
  modal de las 10:00 (`briefing.briefing_hoy`, follow-ups con el número
  exacto de la tabla) · `[futuros DLR ROFEX]` (curva + TNA implícita, ya en
  %) · `[retorno de PRECIO por curva]` y `[carry y canje]` REUSADOS del
  copiloto RF (mismas funciones).
- **[prompt]** Regla de honestidad central: los datos dicen QUÉ se movió,
  nunca POR QUÉ — prohibido inventar causas macro/noticias de memoria
  (decisión del user: noticias NO entran al contexto). Método de narración
  transversal del día (qué manda / dólares y brecha / riesgo país / qué
  mirar). Semántica del canje y del oficial sin histórico en las reglas.
- **[producto +]** 🗣 NARRÁMELO en el modal del briefing: la capa de
  redacción IA que P1 siempre anticipó, A DEMANDA (cero tokens de cron). En
  HOME dispara el panel por evento; desde otra página usa el handoff de
  sessionStorage + navegación. Prompt curado `_PREGUNTA_NARRAR_BRIEFING`
  (copiloto.py = fuente de verdad; briefing-modal.tsx lo copia) — también es
  el chip "Narrame el briefing".
- **[chips]** Narrame el briefing · ¿Cómo viene el mercado? · Dólares y
  brecha (propuestos — editables como siempre).
- **[UI]** Botón "Consultale a la IA" en el header también en `/` (slot
  ex-TERMINAL).

### 2026-07-12 — v1.42 (hilo narrativo — la respuesta cuenta UNA historia)
- **[prompt ~]** Fallo real del shadow ("¿cómo viene RKLB?" → 4 bullets
  sueltos: retornos / pivots / ruedas / fundamentals sin conectar): la regla
  4 del método pedía "texto plano con guiones" — empujaba al inventario.
  Reescrita como HILO NARRATIVO: pregunta por UN papel = párrafo corrido de
  3-5 frases conectadas (qué pasó → por qué → qué mirar), bullets prohibidos
  para enumerar aspectos del mismo papel; solo 2-3 números que sostienen la
  conclusión, el resto en palabras. Ejemplo MAL/BIEN nuevo con el caso RKLB
  real. Bullets quedan solo para listas de papeles sin datos; con datos,
  tabla chica como siempre.
- **[evals]** Caso `hilo_narrativo` (prohíbe 4+ bullets consecutivos y el
  derrame del nombre de screening "zona de decisión mensual").

### 2026-07-12 — v1.41 (handoff automático de la derivación + fix marcador en historial)
- **[bugfix]** El marcador `[[VISTA:x]]` reaparecía LITERAL al restaurar el
  chat tras navegar: `ia.trazas.respuesta` guarda la respuesta CRUDA del
  modelo (pre-limpieza) y `historial_persistido` la servía tal cual. Ahora
  el marcador se limpia también al leer el historial.
- **[producto +]** Handoff de la derivación (pedido del user): el botón
  "Abrir X →" deja `{vista, pregunta original, conv_id}` en sessionStorage;
  el panel de la vista destino lo levanta al montar, retoma la MISMA
  conversación, se abre solo y re-pregunta en segundo plano — cero re-tipeo.
  Costo honesto: la re-pregunta paga UNA llamada (la respuesta real necesita
  el contexto de la vista destino — inevitable); lo que se elimina es el
  re-tipeo manual, no esa llamada.

### 2026-07-12 — v1.40 (derivación entre vistas + presupuesto claro + botón al header)
- **[producto +]** Pregunta de OTRO dominio ("¿qué bono rinde más?" hecho en
  RV) ya no muere en "eso no está en esta tabla": el system prompt recibe la
  lista de las otras vistas con copiloto QUE EL USUARIO PUEDE USAR (RBAC —
  jamás derivar a una puerta cerrada), campo `dominio` en `VISTAS`. El modelo
  deriva en UNA frase y cierra con el marcador `[[VISTA:x]]`, que el CÓDIGO
  valida (existe + acceso) y borra del texto; el panel lo vuelve botón
  "Abrir Renta Fija →" (`vista_sugerida` en la respuesta, mapa
  `RUTA_VISTA` en `ia-vista-panel.tsx`).
- **[UX]** Presupuesto agotado con mensaje accionable: "pedile al
  administrador que te amplíe el cupo" (el texto anterior mandaba a Manager,
  que la mesa no ve). Y el caso confuso REAL: si el tope se cruzaba DURANTE
  la llamada (el pre-chequeo había pasado justo abajo del límite), caía al
  genérico "IA no disponible" — ahora `preguntar()` re-chequea
  `motivo_presupuesto` al recibir vacío y nombra el motivo.
- **[UI]** El botón "Consultale a la IA" de RV y RF se mudó al slot derecho
  del Header (reemplaza el texto decorativo TERMINAL solo en esas rutas —
  pedido del user: usar el lugar que ya existe) — desaparece la franja gris
  propia en /renta-fija. Trading conserva su botón in-view: va cableado a
  las tarjetas y al vigía (`getParams`/`preguntaExterna`).

### 2026-07-12 — v1.39 (cierre de la batería: canje bien definido + chip Panorama)
- **[prompt +]** Definición dura del canje (la 32 la invirtió): canje =
  CCL/MEP − 1; se ABRE cuando el CCL le gana al MEP (tensión), se CIERRA
  cuando convergen.
- **[chip +]** "Panorama de pesos" — la pregunta 33 de la batería promovida a
  chip (curvas + carry + canje + qué mirar en la próxima rueda, 5 líneas).
- **[en observación]** Carry USD 14d (mediana CER +9.5%) luce alto vs el
  retorno de precio del mismo período — verificar la semántica de ventana
  del service `carry_trade` con rueda abierta antes de confiar el bloque.

### 2026-07-12 — v1.38 (el bug de los números fantasma + forwards sin ONs)
- **[bugfix crítico, cazado con diag_contexto_rf]** El verificador NO VEÍA
  los números pegados a letras en el contexto ("156bps", "143d", "90d" — el
  `\b` final del regex los hacía invisibles) → bloqueaba respuestas
  CORRECTAS (26/29/33 de la batería: 156/143/64/60 estaban en los bloques).
  Regex corregida con regresión de las líneas exactas; el sufijo K/M/B ya no
  se come letras de palabras ("1. MU" no es un mega).
- **[contexto ~]** Forwards filtrados a las curvas de ESTA vista — los de
  ONs (on_energia z±3.5) contaminaban el bloque; las ONs tienen su vista.

### 2026-07-12 — v1.37 (carry solo-real + diag de contexto)
- **[filtro +]** |carry USD| >15% en 14d = dato roto (batería ronda 3: PARP
  "+464%", TO26 "−37%" eran precios viejos) → excluido del bloque, misma
  política que los residuos.
- **[tooling]** `scripts/diag_contexto_rf.py`: imprime el contexto EXACTO que
  ve el modelo (tabla + todos los bloques) sin gastar tokens — la lupa para
  cazar bloqueos de verificación (números fantasma 156/143 en 26/29/33).

### 2026-07-12 — v1.36 (el tablero de /retorno mapeado a RF)
- **[contexto +]** Pedido del user (fundamental): `[retorno de PRECIO por
  curva]` mediana 7d/14d/MTD desde cierres históricos (honestidad declarada:
  bullets cortos ≈ retorno total; hard dollar excluye cupones — el exacto
  vive en /retorno, cuyo cálculo es 100% frontend y no se duplica) ·
  `[carry y canje]`: carry USD 14d por curva (mediana + mejor/peor, vía MEP)
  y canje AL30 hoy vs 7d. Los tres tableros de la home de /retorno, ahora
  presentes en el copiloto RF.

### 2026-07-12 — v1.35 (ronda 2 de la batería: dólares live + spread de legislación)
- **[bugfix]** `get_ultimo_mep()` devuelve un dict {mep, ccl, canje, oficial}
  — el bloque ahora muestra [dólares live] MEP·CCL·oficial (antes float(dict)
  reventaba en silencio).
- **[contexto +]** `[spread de legislación]`: TEA AL−GD en bps HOY y su
  promedio de 90 ruedas (de snapshots_cierre_hist, pares AL30D/GD30D y
  AL35D/GD35D) — "¿caro o barato contra lo normal?" ahora se responde con
  datos (la pregunta 19 quedaba bloqueada porque el modelo intentaba el
  histórico de memoria).

### 2026-07-12 — v1.34 (batería de 15: cluster de escalas y contratos)
- **[bugfix ×5, todos cazados por scripts/bateria_rf]** (1) TEA/TEM/tea_fit
  llegan en FRACCIÓN de los services → normalizados a % en el fetch ("lecaps
  al 0.22% TEA" era esto; también cierres, forwards, breakevens y shocks de
  sensibilidad, cada uno en su bloque). (2) `get_ultimo_mep` vive en
  `api.services.macro`, no en `_mep`. (3) `get_breakevens()` devuelve LISTA
  de docs → `[0].pares`. (4) `comparar` es `@cached` → kwargs (2ª vez la
  misma trampa; fake del test ahora kwargs-only). (5) Detección de tickers
  con sufijo de especie: "GD30" matchea GD30D/C.
- **[contexto ~]** Header de [movimientos] con la fecha del cierre comparado
  + instrucción de decir "no hay rueda nueva" en fin de semana.

### 2026-07-12 — v1.33 (shocks de TIR + silencios inteligentes + descomposición natural)
- **[fix]** Sensibilidad en modo RELATIVA (±0.5/1/2pp sobre la TEA actual):
  "¿y si comprime 2 puntos?" se responde directo del bloque — antes los
  escenarios eran TIRs absolutas, el modelo restaba a mano y la verificación
  lo bloqueaba (caso real GD30).
- **[prompt ~]** Lo que no existe para los bonos de la pregunta NO se
  menciona (shadow: "no hay fair value" en una comparación de bonares donde
  el fair value ni aplica) · descomposición narrada en lenguaje de mesa
  (devengo / rodar por la curva / movimiento de tasas / arrastre
  inflacionario, técnico entre paréntesis solo la primera vez).

### 2026-07-12 — v1.32 (las herramientas de ESTRATEGIA, disponibles en RF)
- **[contexto +]** Pedido del user: comparar inversión, sensibilidad y
  descomposición viven en la página Estrategia pero SON análisis de RF — el
  copiloto RF ahora las tiene, BAJO DEMANDA (se activan al nombrar bonos,
  cero costo en el contexto base): [comparar A vs B] (flujos de ARS 1M hoy
  en cada uno + advertencias) al nombrar dos bonos · [sensibilidad TICKER]
  (escenarios de TIR, solo soberanos, upside de precio sin carry) ·
  [descomposición TICKER 30d] (carry + rolldown + Δtasa — el "¿por qué
  subió?") para pesos. Services puros reutilizados tal cual.

### 2026-07-12 — v1.31 (marco de portfolio: ¿tasa fija o CER? ¿corto o largo?)
- **[contexto +]** `[breakevens + señal vs REM]`: cada par lecap-CER con su
  breakeven YA cruzado por código contra el promedio mensual geométrico del
  REM al mismo horizonte (diff en pp + señal "favorece tasa fija/CER si el
  REM acierta") — la regla TIPS clásica, ejecutada determinista. `[curvas
  por tramo]`: TEA promedio corto/medio/largo + empinamiento por curva.
- **[prompt +]** Marco de PM (investigado en fuentes CFA/institucionales):
  tasa fija vs CER = breakeven vs expectativa (con la salvedad de prima de
  riesgo/iliquidez); corto vs largo = carry+rolldown vs duration según
  empinamiento; SIEMPRE trade-off con supuesto explícito, jamás orden.
- **[chip +]** "¿Tasa fija o CER?" — el cuadro completo con los supuestos de
  cada camino.

### 2026-07-12 — v1.30 (solo señales REALES + rankings humanos en RF)
- **[conocimiento de mesa +]** Directiva del user: "no quiero nada de
  sospechoso, quiero cosas REALES" → los residuos >500bps (precio viejo /
  bono ilíquido; shadow: +9337bps presentado como ganga) se EXCLUYEN del
  ranking por código — el modelo nunca los ve como candidatos
  (`_MAX_RESIDUO_REAL_BPS`). Si un residuo enorme aparece en la tabla general
  pero no en el bloque, el prompt le prohíbe presentarlo como oportunidad.
- **[prompt + / chip ~]** Rankings RF humanos: conclusión en una frase, tabla
  top 3 por lado, lectura final que AGREGA (porqué/riesgo) sin repetir la
  tabla. Chip "Baratos vs curva" reescrito en ese molde.

### 2026-07-12 — v1.29.1 (fix: el fair value no llegaba)
- **[bugfix]** "Baratos vs curva" decía "sin precio teórico": llamada
  POSICIONAL a `get_fair_value_live` (service `@cached` → exige kwargs, el
  clásico del repo) → TypeError tragado → residuos vacíos. Fix + fake del
  test kwargs-only para que CI lo cace si reaparece.

### 2026-07-12 — v1.29 (TERCERA VISTA: Renta Fija — curvas, fair value, forwards, breakevens)
- **[vista +]** `renta_fija` (módulo `renta-fija`, 3 roles): el idioma acá es
  TEA/curva/forward/breakeven (jerga_permitida por vista). Tabla = ~4 curvas
  (cer / tasa_fija con CER-fijados / globales+bonares / dolar_linked) con
  precio, tea/tem, paridad, duration, tc_breakeven y el FAIR VALUE mergeado
  (tea_fit + residuo_bps = el "caro o barato" del bono, equivalente de la
  zona de pivots).
- **[contexto]** Precómputos: [movimientos del día] (Δ TEA vs último cierre,
  de snapshots_cierre_hist — pedido del user: la curva se ve, el movimiento
  no) · [baratos y caros vs curva] (residuos + r² del fit) · [forwards
  desarbitrados] (z contra su historia, calculado por código; + forward
  puntual si nombrás dos bonos = comparación bono vs bono) · [breakevens
  lecap-CER + REM] · [MEP live].
- **[chips]** Movimientos del día · Baratos vs curva · Forwards
  desarbitrados (elegidos por el user; "curvas hoy" descartado — se ve en el
  gráfico).
- **[fuera de alcance]** Canje/carry/comparar/sensibilidad = vista ESTRATEGIA;
  MEP/cauciones/DLR = DERIVADOS; ONs = su propia vista. Copilotos futuros.

### 2026-07-12 — v1.28 (EL VIGÍA — reactividad sin LLM)
- **[reactividad +]** `POST /api/ia/copiloto/vigia` (gate ia+trading):
  watchers DETERMINISTAS elegidos por el user, cero tokens — T1: una de sus
  tarjetas toca/roza un nivel (±0.20%, con SUS overrides); T2: un ticker
  fuera de sus tarjetas, del top 15 por volumen y con ±4% en la rueda, se
  acerca a un nivel (±0.35%, vía pivot_radar). Fuera de rueda no dispara;
  en zona muerta dispara CON la advertencia de disciplina en el mensaje.
- **[UI]** Toasts en la vista TRADING (poll 15s): mensaje template + botón
  "¿LO MIRAMOS?" (abre el copiloto con la pregunta armada — recién ahí se
  gasta UNA llamada) + "➕ AGREGAR <ticker>" en las del radar (1-click,
  autonomy slider: sugerir, jamás automático). Descartes por día en
  localStorage.

### 2026-07-12 — v1.27 (desbalance del libro con los dos lados)
- **[bugfix]** El chip "Lectura del libro" quedaba bloqueado por la
  verificación: el contexto daba solo "14% comprador" y el modelo decía
  "86% vendedor" (100−14 = aritmética prohibida → sin respaldo). El bloque
  [libro] ahora entrega ambos porcentajes ya calculados. Patrón general:
  si un derivado obvio se va a citar, va PRECALCULADO en el contexto.

### 2026-07-12 — v1.26 (la doctrina del trader: pivots, tendencia, posiciones)
- **[contexto +]** Tarjetas enriquecidas: cada nivel PP..S3 como "precio
  (dif%)" YA calculado (el toggle DIF% de la vista — cero aritmética del
  modelo), `nivel_cercano` (cuál y a cuánto), `dia%` y `rubro` del papel,
  bloque `[tendencia rubros de tus tarjetas]` (día del rubro ponderado por
  monto).
- **[contexto +]** `[mis posiciones abiertas]`: puente desde el monitor
  INTRADAY (el excel es efímero en el browser → las posiciones LONG/SHORT
  quedan en localStorage al analizar y viajan como parámetro saneado).
- **[prompt +]** LA DOCTRINA (dictada por el user): (1) rebote en nivel =
  contra-tendencia SOLO en pivot con giro en el tape; (2) tendencia del día =
  jamás avalar short contra día/rubro/índices al alza ni long contra día
  rojo; (3) día muy arriba = esperar la toma de ganancias. DISCIPLINA DE
  PIVOTS como mantra: nivel_cercano > ±0.50% = está EN EL MEDIO → el consejo
  default es ESPERAR el nivel. Con posición abierta: todo se lee DESDE la
  posición (nivel a favor = objetivo, en contra = riesgo).

### 2026-07-12 — v1.25 (números es-AR + reloj de mercado y disciplina)
- **[bugfix crítico]** La verificación bloqueaba TODA la vista trading: el
  modelo escribe precios a la argentina ("10.793" = 10793) y el parser los
  leía como decimales → falso "sin respaldo" → respuesta bloqueada. El
  verificador ahora prueba TODAS las interpretaciones (literal, es-AR con
  puntos de miles, coma decimal, K/M/B).
- **[contexto + / prompt +]** `[reloj de mercado]` determinista (hora ART +
  `mercado.dias_habiles`): PRE-APERTURA / RUEDA VIVA (10:30-13) / **ZONA
  MUERTA (13-16)** / ÚLTIMO TRAMO (16-17) / CERRADO. Rol de DISCIPLINA
  (pedido del user: "más que mostrar datos, asesorar"): en zona muerta la
  primera frase SIEMPRE lo recuerda y frena al usuario si insinúa operar
  (anti-overtrading); fuera de rueda aclara que los datos son de la última
  rueda; en rueda viva marca la ansiedad de mirar muchos papeles seguidos.

### 2026-07-12 — v1.24 (SEGUNDA VISTA: Trading — pivots live, libro, tape)
- **[vista +]** `trading` (página /trading, módulo RBAC `trading`, admin-only):
  el copiloto del que TRADEA. Novedad arquitectónica: la vista recibe
  PARÁMETROS del cliente (`params`: los tickers de las 8 tarjetas, el foco y
  los overrides de máx/mín/cierre) — se SANEAN server-side (upper/dedup/
  floats) y el server busca los datos frescos él mismo. La selección viaja;
  los datos jamás.
- **[contexto]** Tabla = las tarjetas (last/vwap, base de pivots CON los
  overrides del usuario re-aplicados por código, PP..S3, zona actual, foco) ·
  [libro CI/24hs] con spread y desbalance calculados · [tape] resumido
  (monto, % agresión compradora, últimos trades) · [movers ±4%] · CCL/SPY/QQQ.
- **[prompt]** Acá la nomenclatura de pivots ES el idioma (cfg
  `permitir_pivots` desactiva ese guardrail por vista). Respuestas 3-6
  líneas, cruzar zona+libro+tape, JAMÁS dar órdenes de compra/venta.
- **[chips]** Mis tarjetas · Lectura del libro · Movers en juego.

### 2026-07-12 — v1.23 (conversaciones separadas — cada chat su mundo)
- **[producto ~]** Pedido del user: como los chatbots serios — cada
  conversación tiene su id (`ia.trazas.conv_id`, requiere apply_schema),
  botón **＋ NUEVA** en el panel arranca un mundo limpio, y al abrir se
  retoma SOLO la última conversación (no una mezcla de todo el historial —
  que además contaminaba follow-ups y detección de tickers). Al modelo le
  siguen viajando como máximo 4 pares: el costo no cambia; cambia la
  higiene semántica.

### 2026-07-12 — v1.22 (memoria persistente + etapas del pensando · streaming DESCARTADO)
- **[producto +]** Memoria persistente del chat SIN tabla nueva: al abrir el
  panel se recuperan los últimos 8 intercambios del usuario, reconstruidos
  desde `ia.trazas` (`GET /api/ia/copiloto/historial`) — con sus 👍/👎. Los
  follow-ups sobreviven al refresh/cambio de día.
- **[UI]** "Pensando…" ahora narra las etapas reales del pipeline: "Leyendo
  los datos… → Redactando… → Verificando números…".
- **[DESCARTADO] Streaming de respuestas** — incompatible con la política
  "verificado o no se muestra" (2026-07-11): no se puede verificar texto que
  no terminó de generarse; streamear mostraría números sin chequear y habría
  que retractarlos en pantalla. Se re-evalúa solo si algún día se relaja la
  política. Las etapas del pensando cubren la percepción de velocidad.

### 2026-07-12 — v1.21 (destacados IA deterministas + chip afinado + cursiva)
- **[contexto +]** Línea "papeles de IA destacados HOY" en los screenings
  (top 5 del día SOLO entre ia=si, por código) — el chip de IA colaba a GGAL
  porque el modelo tomaba el ranking general del día.
- **[chips ~]** "Papeles de IA" acotado: 5 líneas máx, solo cadena IA, cierra
  con los 3 más fuertes del día (la versión anterior mezclaba plazos y rubros
  sin foco).
- **[UI]** El panel renderiza *cursiva* además de **negrita** (los asteriscos
  sueltos se veían crudos).

### 2026-07-11 — v1.20 (máx/mín histórico REAL desde 2005, sin cargar la historia)
- **[datos +]** Diseño del user: la historia profunda NO se carga — se
  DESTILA. `scripts/backfill_extremos_hist.py` pide 2005→2024 a Yahoo por
  papel, extrae máx/mín y guarda SOLO los lados que superan a la serie viva
  (tabla mínima `mercado.precios_extremos_hist`, ≤1 fila por papel; si el
  extremo ya está en 2024+ no se guarda nada, y se borra si dejó de superar).
- **[contexto ~]** `max_hist_usd`/`min_hist_usd` ahora son los históricos
  relevados desde 2005 (merge serie viva + tabla de extremos); el prompt dice
  "máximo histórico (desde 2005)". Requiere apply_schema + una corrida del
  script (~4 min, re-correr al sumar CEDEARs nuevos).

### 2026-07-11 — v1.19 (verificado o no se muestra + máx/mín de serie)
- **[política]** VERIFICADO O NADA (directiva del user): si tras la
  auto-corrección quedan números sin respaldo, la respuesta NO se muestra
  (error `verificacion` con mensaje claro en el panel). Se terminó el
  "tomalo con pinzas".
- **[prompt −]** PROHIBIDA la aritmética propia del modelo (promedios, sumas,
  agrupaciones inventadas — caso real: "Memoria y storage +432%" era un
  sub-rubro que el modelo inventó y promedió). Los únicos agregados válidos
  son los precalculados (pulso/rankings/screenings). Se quitó la fórmula del
  CCL implícito del prompt (producía números incomprobables).
- **[contexto +]** `max_serie_usd` / `min_serie_usd` / `dist_al_max%` por
  papel (pedido del user): extremos del subyacente en NUESTRA serie —
  HONESTIDAD: arranca ene-2024, el prompt obliga a decir "máximo de los
  últimos dos años", jamás "histórico de siempre". Si algún día se extiende
  el backfill (DESDE_BACKFILL), la profundidad mejora sola.

### 2026-07-11 — v1.18 (screenings deterministas + chips limpios + pivots al guardrail)
- **[contexto +]** Bloque `[screenings ya calculados]`: rezagados de verdad
  (año<0, 15r>0, semana>0), rebotes de corto, zona de decisión (por liquidez)
  y techos rotos — FILTRADOS POR CÓDIGO. El modelo filtrando 187 filas × 3
  condiciones respondía distinto en cada corrida y derramaba correcciones
  ("no, son 15 ruedas…"); ahora la pertenencia es determinista e idéntica
  entre corridas.
- **[guardrail +]** Nomenclatura de pivots (PP/R1-R3/S1-S3) detectada por
  código (">R3 anual" seguía saliendo: R1/S3 esquivaban el filtro de longitud
  mínima). Permitida solo si el usuario habla de pivots/niveles.
- **[UI]** Los chips muestran su ETIQUETA en el chat ("Rezagados repuntando");
  el prompt curado viaja por atrás — conversación limpia.
- **[chips ~]** Prompts de Rezagados y Zona de decisión simplificados (la
  definición vive en el screening, no en el texto del chip).

### 2026-07-11 — v1.17 (tablas, ruedas, rankings deterministas, fix del guardrail)
- **[bugfix]** El guardrail de jerga tenía un bug de regex: "ret_año%" no
  matcheaba por el "%" pegado — por eso siguió pasando jerga tras v1.15.
  Corregido + "columna"/"header" al lexicón + el DERRAME de razonamiento
  visible ("Corrijo:", "— no,") también dispara la reescritura.
- **[contexto +]** Retornos por RUEDAS: `ret_15ruedas%` (ya venía del scanner,
  no lo exponíamos) + `ret_30ruedas%`/`ret_45ruedas%` calculados de la serie
  (1 query cacheada). Pedido de la mesa: el lente de trading.
- **[contexto +]** Bloque `[rankings ya calculados]`: top/peores del año, mes,
  semana y día ordenados por CÓDIGO (caso real: el modelo salteó a SNDK +707%
  en el top del año — ordenar 187 filas a ojo es lo que peor hace).
- **[prompt +]** Definición de REPUNTE de la mesa (cae en el tramo largo Y se
  dio vuelta en 15 ruedas + semana; una semana verde sola = "rebote de
  corto") · listas de papeles con datos → TABLA markdown simple (máx 4
  columnas) + una línea de lectura. Chip Rezagados actualizado a este formato.
- **[UI]** El panel renderiza tablas markdown (datos en tabla, lectura abajo).

### 2026-07-11 — v1.16 (chips de mesa + audiencia por rol)
- **[producto +]** Biblioteca de consultas de mesa: 5 chips de un click en el
  panel (Papeles de IA · Argentina · En zona de decisión · Rezagados
  repuntando · Voladores del año — elegidos por el user). Prompts curados y
  versionados en `_CHIPS_RENTA_VARIABLE`; el panel los recibe por
  /copiloto/vistas.
- **[prompt +]** Audiencia por rol RBAC (tono definido por el user): trader
  seco y numérico · sales explicado con frases repetibles al cliente · admin
  neutro.

### 2026-07-11 — v1.15 (guardrail de jerga + el plazo de la pregunta manda)
- **[agente +]** La jerga interna dejó de ser una regla de prompt y pasó a ser
  GUARDRAIL estructural: `_jerga_en_respuesta()` detecta por código headers/
  términos del sistema colados en la respuesta ("ret_7d", "monto_usd_ny"…) y
  dispara la misma auto-corrección que los números sin respaldo (un solo
  reintento cubre ambos). Permitido solo si el usuario usó el término en su
  pregunta. (Shadow: el prompt solo no alcanzaba — el modelo lo rompía cada
  tanto.)
- **[prompt +]** El PLAZO que nombra la pregunta manda la conclusión; los
  demás plazos entran como matiz al final. Caso real como ejemplo MAL/BIEN:
  "¿cómo vienen las del espacio este 2026?" → la respuesta es la del AÑO
  (+11%), no la del día.

### 2026-07-11 — v1.14 (método de respuesta — pirámide de Minto)
- **[prompt ~]** Las reglas de estilo se consolidaron en un MÉTODO general de
  armado de respuesta (pedido del user: general, no guionado; fundado en
  Pyramid Principle/BLUF y en los pilotos de LLM del FCA): (1) conclusión
  primero en una frase simple + máx 3 apoyos; (2) plazos con sentido — corto Y
  largo, y decir si la historia cambia según el plazo; (3) estadística para
  PENSAR pero traducida al hablar (beta/correlación/z-score jamás nombrados
  salvo pedido explícito); (4) una idea por frase. Ejemplo MAL/BIEN nuevo con
  el caso real de META (metralleta de cifras → narrativa de mesa).

### 2026-07-11 — v1.13 (auto-corrección + solo lo pedido)
- **[agente +]** Reflexion: si la verificación encuentra números sin respaldo,
  la respuesta NO se muestra — el modelo recibe su propia respuesta con la
  lista exacta de números que no cierran y la reescribe con datos reales (1
  reintento; se queda la mejor). El costo extra solo se paga cuando falla.
- **[verificación]** La advertencia ahora dice CUÁLES números no verificó
  ("⚠ No pude verificar: 325M, 8.8M"). Y entiende abreviaciones K/M/B
  ("325M" ≈ 325.432.132 → respaldado) — los 2 falsos positivos del shadow
  eran montos abreviados.
- **[prompt +]** Responder SOLO lo pedido — sin métricas de yapa (el shadow
  metía volumen en una pregunta de pivots) + ejemplo MAL/BIEN del caso real.

### 2026-07-11 — v1.12 (números verificados + pivots traducidos)
- **[bug real]** El modelo citaba la COLUMNA equivocada y volteaba signos
  (dijo "MU ytd −5.13" cuando −5.13 es el MES y el año es +243; "TGT −38"
  cuando es +38). La tabla estaba bien; el modelo se perdía entre 26 headers
  crípticos (`adr_ret_mtd_pct` vs `ytd`).
- **[contexto]** Headers del TSV renombrados a lenguaje claro e inconfundible:
  `ret_mes%`, `ret_año%`, `precio_usd_ny`, `var_dia_usd%`, `zona_piv_año`…
- **[verificación +]** Chequeo mecánico anti-alucinación: cada número de la
  respuesta se busca en el contexto enviado (tolerancia de redondeo; ignora
  rankings/años). Números sin respaldo → warning en logs + `⚠ tomalo con
  pinzas` visible en el panel del copiloto (nivel informar, no bloquea).
- **[prompt +]** Ejemplos few-shot de estilo (MAL/BIEN) + pivots como CONTEXTO
  jamás vocabulario: nunca decir PP/R1/S2 al usuario (salvo que él los nombre);
  traducir la zona a lectura de mesa y dar precios concretos, no nomenclatura.
  Semántica del user: R3/S3 extremos, R1/S1 puede seguir o rebotar al PP,
  R2/S2 tendencia clara.
- **[evals]** Caso `ticker_natural` (la respuesta técnica de GGAL, fallo real).

### 2026-07-11 — v1.11 (voz de operador)
- **[prompt +]** Regla de identidad: habla como OPERADOR, no como analista de
  datos — prohibido mencionar columnas/jerga interna ("es_ia",
  "adr_ret_mtd_pct", "de la tabla…"); criterio del ranking en UNA línea de
  lenguaje de mesa; si piden N, exactamente N; sin resumen redundante al
  final. (Shadow: la respuesta recitaba la mecánica y cerraba repitiendo la
  conclusión.)
- **[evals]** Jerga interna prohibida en los 4 casos de ranking/sector.

### 2026-07-11 — v1.10 (thinking bajo control)
- **[bug raíz]** Los v4 traen `thinking` DEFAULT ENABLED (verificado contra la
  doc del proveedor): el copiloto razonaba sin pedirlo — tokens invisibles,
  "respuestas vacías" al ras del techo, y el razonamiento derramado dentro de
  una respuesta del shadow (ranking con autocorrecciones interminables).
- **[gateway]** Switch `thinking` explícito POR TAREA: copiloto/controles/
  smoke → disabled; triage → enabled a propósito (diagnóstico — cierra el
  cabo suelto del P2). `reasoning_content` se captura y guarda en
  `ia.trazas.razonamiento` (cap 2000) → visible en el DETALLE del panel:
  ahora se puede debuggear CÓMO razonó cada llamada. Requiere apply_schema.
- **[prompt +]** Brevedad dura: directo al resultado, sin cálculos intermedios
  ni correcciones, ~12 líneas máx, ranking = 1 línea de criterio + lista.
- **[evals]** Caso nuevo `ranking_breve` (el fallo real) + chequeo `max_chars`
  en el runner (detecta derrames de razonamiento).

### 2026-07-11 — v1.9 (excepciones de límite por usuario)
- **[gateway]** Límite diario PERSONAL por usuario (clave
  `budget_dia_usuario:<email>` en ia.config): pisa el tope general solo para
  ese email — ej. el admin se da más margen que la mesa. Siempre ≤ global
  (techo duro intacto). Editable en el cuadrante PRESUPUESTO (solo admin,
  auditado); `POST /api/ia/presupuesto/usuario` (valor null = borrar).

### 2026-07-11 — v1.8 (saldo real del proveedor)
- **[observabilidad]** `GET /api/ia/saldo` + cuadrante PRESUPUESTO del panel:
  saldo REAL de la cuenta DeepSeek (`GET /user/balance`, verificado contra la
  doc del proveedor 2026-07-11; cache 5 min). Se muestra plata (total/cargado/
  otorgado por moneda) + flag oficial "alcanza para operar". "Tokens
  restantes" NO se muestra a propósito: el proveedor no lo expone y
  convertir plata→tokens sería inferir precios/mix de modelos (REGLA #2).

### 2026-07-11 — v1.7 (errores claros + trazas con contenido)
- **[UX]** El panel dice CUÁL degradación fue: "alcanzaste TU límite diario"
  (presupuesto_usuario) vs "el sistema alcanzó su tope" (presupuesto_global)
  vs "sin datos" — chau "IA no disponible" genérico. El copiloto chequea el
  presupuesto ANTES de armar el contexto (`core.ai.motivo_presupuesto`).
- **[observabilidad]** `ia.trazas` ganó `detalle` (la pregunta) y `respuesta`
  (extracto, cap 1500): cada llamada del copiloto queda auditable con su
  contenido. Requiere apply_schema.
- **[UI Manager]** OBSERVABILIDAD → IA rediseñada en 4 cuadrantes: presupuesto
  (ver/editar + usado hoy) · por tarea/por día (tabs) · últimas llamadas
  (click) · detalle de la llamada elegida (pedido/respuesta/error).

### 2026-07-11 — v1.6 (pulso + evals)
- **[contexto +]** Bloque `[pulso por rubro]`: retornos 1d/WTD/MTD/YTD por
  rubro YA calculados (ponderados por volumen USD, mismo criterio que el PULSO
  de la vista). Regla de oro 1: para preguntas de mercado/sector el modelo
  narra números deterministas en vez de promediar 187 filas a mano.
- **[gateway]** `max_tokens` 2000 → 3000 (trazas mostraron respuestas de 1796
  al ras del techo + una vacía).
- **[evals]** Nace el eval set (Fase 0.4): 6 casos (4 fallos reales del
  shadow + 2 adversos de diseño) + runner `scripts/eval_copiloto.py`.

### 2026-07-11 — v1.5 (gateway)
- **[gateway]** Presupuestos de tokens editables desde Manager →
  OBSERVABILIDAD → IA (tabla `ia.config`, solo admin, auditado). El GLOBAL
  diario es techo duro del sistema; el tope por usuario no puede superarlo.
  Afecta la disponibilidad del asistente: presupuesto agotado = "IA no
  disponible" hasta medianoche UTC o hasta que el admin suba el tope.

### 2026-07-11 — v1.4 (presupuesto)
- **[medición]** Costo real por pregunta: ~22k tokens de input (187 filas ×
  26 columnas), medido en `ia.trazas`. El shadow agotó el tope por usuario
  (200k = ~9 preguntas) → "IA no disponible" a media tarde.
- **[gateway]** Presupuesto diario por usuario: default 200k → **1M** (~45
  preguntas ≈ centavos en flash). Global sigue en 2M. Env:
  `AI_BUDGET_TOKENS_DIA_USUARIO`.
- **[contexto −]** Números sin separador de miles en el TSV ("15234.50", no
  "15,234.50") — tokeniza mejor con ~4900 celdas por pregunta.

### 2026-07-11 — v1.3
- **[fix detección]** Palabras comunes que colisionan con tickers ("de" →
  Deere) solo matchean escritas en MAYÚSCULAS (`_TOKENS_AMBIGUOS`). Caso real
  del shadow: "acciones de IA" inyectaba el detalle de DE.
- **[prompt +]** Pedidos de recomendación: ranking objetivo con criterio
  explícito en vez de "no puedo recomendar". Sin consejo de inversión.

### 2026-07-11 — v1.2 (primer loop del shadow)
- **[contexto +]** Columnas `es_ia` y `rubro` (la pregunta real "¿qué acciones
  hay de IA?" reveló que faltaban — el modelo improvisaba por nombre).
- **[contexto +]** Zonas de pivots `piv_anual` / `piv_mensual` para TODO el
  universo (2 queries agregadas cacheadas 15 min — no 187 llamadas).
- **[prompt +]** Lectura de pivots de la mesa (conocimiento no inferible,
  aportado por el user): >R3/<S3 subió/cayó muchísimo · R2-R3/S3-S2 tendencia
  clara · R1-R2/S2-S1 movió algo · PP zona ideal de decisión. Uso implícito,
  sin listar niveles salvo pedido.
- **[UI −]** Línea de fuente (📊 vista · filas · hora) fuera de la respuesta;
  el disclaimer fijo del panel cubre la marca de origen IA.

### 2026-07-11 — v1.1 (vista completa)
- **[alcance]** De "tabla CEDEARs" a VISTA Renta Variable completa (ambas tabs).
- **[contexto +]** CCL live (siempre) + detalle por ticker mencionado: pivots
  4 marcos, quant, últimos 15 retornos, fundamentals Refinitiv (cap 3 tickers,
  detección determinista sin LLM).
- **[UI]** Botón "Consultale a la IA" (antes "IA"); luego movido a la barra de
  tabs, margen derecho. Negritas renderizadas. Bienvenida "¿En qué puedo
  ayudarte?". Alias `cedears` creado y borrado tras confirmar el deploy.

### 2026-07-11 — v1 (nacimiento)
- Copiloto CONTEXTUAL por vista (decisión del user: NO chatbot global). Una
  llamada flash por pregunta; datos armados server-side desde el service
  @cached; TSV; gate doble `ia` + módulo de la vista; historial corto
  client-side (4 pares); 👍/👎 → `ia.trazas.feedback`; degradación ok=False.
- Colateral: el copiloto expuso series rotas en `mercado.precios_acciones`
  (2025 incompleto 116/187, split CRWD, vela basura HON) → backfill 2024+ +
  auto-reparación de splits en `jobs/precios_acciones_daily`.
