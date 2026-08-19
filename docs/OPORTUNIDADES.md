# OPORTUNIDADES — backlog priorizado de ACAQUANT

> ⚠️ **2026-08-19 — TODA propuesta de este doc que dependa del COPILOTO está
> MUERTA.** El copiloto de mesa, el asistente de negocio, la guía y el vigía se
> dieron de baja (motivo y qué se rescató: `docs/AV_AGENT.md` §0.k), junto con
> `COPILOTO.md`, `TOOLS_IA.md` y `QUANTAI.md` — las referencias a esos archivos
> que quedan acá abajo son históricas y **no apuntan a nada**. Lo mismo con los
> "huecos de tools": no hay tools que llenar. **Antes de tomar una propuesta de
> IA de este doc, chequear que no sea del copiloto.** Lo que sigue vivo es el AV
> AGENT, y su backlog vive en `AV_AGENT.md`, no acá.

**Generado el 2026-08-09.** Este documento es el resultado consolidado de un relevamiento
adversarial de la plataforma, hecho en dos etapas:

1. **8 lentes** de análisis independientes sobre el código y la base: frescura de datos
   (`frescura.md`), datos que se escriben y nadie lee (`datos-ociosos.md`), los tres roles
   operativos (`rol-mesa.md`, `rol-comercial.md`, `rol-backoffice.md`), estándar de industria
   para una ALyC (`industria.md`), KPIs de gestión (`kpis.md`) y piezas de IA/endpoints
   huérfanos (`ia-huerfanos.md`). Total: **~90 oportunidades crudas**.
2. **Crítica adversarial de 3 agentes** que verificaron cada afirmación contra el código
   (`archivo:línea`), el `sql/schema.sql` y `deploy/crontab.txt`, y emitieron veredicto
   **PASA / ACHICAR / MATAR** con justificación. Las ACHICAR entraron acá **con su núcleo de
   valor, no con la versión inflada**.

**Resultado: 58 oportunidades sobrevivieron · 24 se descartaron** (sección 7: no re-proponerlas).

---

## ⚠️ Advertencia obligatoria antes de usar este documento

- Todo lo marcado **SIN VERIFICAR** es una hipótesis que **NO está confirmada contra la base de
  producción**. Ninguna línea de código puede depender de eso sin correr antes un diag read-only
  (`scripts/diag_*.py`, REGLA #0 + REGLA #2). En varios casos el resultado del diag **cambia la
  forma del proyecto entero o lo cancela**.
- Lo que sí está verificado son las citas `archivo:línea` sobre el repo — los tres críticos las
  chequearon una por una y corrigieron varias afirmaciones falsas del relevamiento original
  (están anotadas como *corrección*).
- **Los tres críticos se contradicen en 7 puntos.** Están todos listados explícitos en la
  sección 8. Uno de ellos (la retención de `mercado.timesales`) es **load-bearing**: decide si
  el proyecto de calidad de ejecución sobre renta fija es posible o no.
- Las estimaciones de esfuerzo son de **desarrollo**. Cuando el cuello de botella es carga de
  datos o una decisión de negocio (tarifario, umbrales de compliance, mapeo de familias de
  producto), está dicho — y ahí el calendario real lo fija otra persona, no el código.

---

## 1. TOP 10 — las que yo haría primero

**Criterio de orden (valor / esfuerzo, NO valor solo):** primero lo que **destraba una
limitación** y habilita otras cosas, por encima de lo que agrega una pantalla más. Segundo,
lo que **no necesita ningún dato que no exista** — si hay que relevar, cargar o negociar algo,
baja. Tercero, lo que **decide** algo (apagar un motor, elegir el camino de un proyecto grande)
por encima de lo que solo informa: una decisión desbloqueada vale más que un KPI nuevo.

| # | Oportunidad | Para quién | Esfuerzo | Qué desbloquea (una línea) |
|---|---|---|---|---|
| 1 | **[M1] Vista de la Estrategia Quant** (3 endpoints construidos, 0 consumidores) | trader, admin | chico (1-3 d, frontend puro) | Decidir si el modelo tiene edge: se apaga un servicio + un cron, o se habilita la Fase 4 |
| 2 | **[C1] Penetración = AuM / cupo transaccional** | comerciales, jefatura | chico (<1 d, **sin backend**) | Convierte el libro existente en pipeline: crecer sin salir a buscar clientes |
| 3 | **[C2] Arancel efectivo en bps** (escalones 1 y 2) | jefatura, dirección | chico (<1 d) | El ranking comercial pasa a premiar margen y no volumen barato |
| 4 | **[D2] Diag: ¿Aunesa acepta `desde` = próximo hábil?** | quien decide el T0 | **<1 hora** | Decide la forma de [D1] Tenencia T0 con un hecho en vez de una hipótesis |
| 5 | **[T1] Serie de saldos de Tesorería + arrastre del inicial** | back office, tesorería | chico (<1 d) | La caja deja de ser foto y pasa a serie: primer control de conciliación banco↔sistema |
| 6 | **[M2] Resolver el ADR con el feed Eikon** (COALESCE) | mesa, trading | chico (<1 d) | Arbitraje CEDEAR/ADR en tiempo real, hoy imposible; y paga un feed licenciado ocioso |
| 7 | **[T11] Lectura de los 3 rastros de auditoría** | administración, jefatura | chico (<1 d) | Control de cuatro ojos real: hoy se paga auditar y no se cobra el beneficio |
| 8 | **[I1] Prender la aduana de PII en las 9 vistas de mercado** | toda la mesa, admin | chico (<1 d) | Cierra el perímetro y habilita preguntas mixtas papel+cliente sin cambiar de proveedor |
| 9 | **[C6] P&L por cooperativa referidora** | dirección, jefatura | chico (<1 d) | Negociar el fee split con evidencia; detectar la coop de margen negativo |
| 10 | **[D3] Bandeja de NOVEDADES + calibrar guardrails** | admin, back office, mesa | medio (2-5 d) | Es la superficie de entrega que no existe: sin ella todo detector nuevo nace ciego |

---

## 2. QUICK WINS — se pueden empezar mañana

Esfuerzo chico, datos completos, sin dependencia previa. Los **fixes** son deuda verificada,
no oportunidades — van igual porque el ratio es imbatible.

| ID | Qué | Esfuerzo |
|---|---|---|
| M1 | Vista LIVE + TRACK-RECORD de la Estrategia Quant (frontend puro) | 1-3 días |
| M2 | ADR desde `eikon_snapshot` con fallback a Finnhub | <1 día |
| M3 | Barrido del book ("si doy 50.000 nominales, ¿a qué precio salgo?") | <1 día |
| M18 | **FIX**: `@cached(ttl=3)` en `risk.py` — el docstring promete un cache que no existe | **<1 hora** |
| M20 | TC de Mesa de Dinero = `COALESCE(manual, MEP del día)` con `origen` visible | <1 día |
| M19 | Z-score de la TNA sintética **o** apagar el job (decisión, ver contradicción B) | <1 día |
| C1 | Columna PENETRACIÓN + bloque OPORTUNIDAD (aritmética en el browser) | <1 día |
| C2 | `arancel_bps` en el rollup + outliers contra la mediana de la cohorte | <1 día |
| C4 | Serie MAC desde `actividad_mensual` + recalibrar `estado_comercial` por segmento | 1-2 días |
| C6 | Vista CANALES (una fila por coop) | <1 día |
| T1 | Encadenar el saldo inicial desde la foto de ayer + tabla de serie sin TTL | <1 día |
| T3 | `fecha_acreditacion` en cheques recibidos | <1 día |
| T5 | **FIX**: el renombre de banco no arrastra a 3 tablas → plata en banco fantasma | **2 horas** |
| T4 | Detectar escrituras posteriores a la foto (recomputar y diffear) | <1 día |
| T13 | Retrabajo de SENEBIS: qué campo se corrige más (`unnest` sobre `campos_editados`) | 1-2 días |
| T14 | Actor + auditoría + allowlist en el override de precio del libro propio | <1 día |
| D2 | Diag Aunesa `desde` = próximo hábil | <1 hora |
| D6 | Exponer `computed_at` de `valuaciones.consolidado` (la vista aparenta dato vivo) | <1 día |
| D7 | Commit de limpieza de endpoints huérfanos + corregir 2 líneas mentirosas del MAPA | <1 día |
| D8 | `cuentas_visibles` fail-closed por clase de identidad + test que congele la invariante | <1 día |
| I1 | `aduana: True` en las 9 vistas de mercado + `detalle` de trazas siempre tokenizado | <1 día |
| M5a | Check **post-trade** de precio fuera de banda como `Control` del registry existente | <1 día |

---

## 3. APUESTAS GRANDES — las que cambian el producto

Cinco proyectos de semanas. Para cada uno, **qué hay que destrabar antes** — ninguno arranca en frío.

### A. Tenencia T0 ([D1]) — el ejemplo canónico
Hoy TODO lo que cuelga de `portafolio.tenencia` es T-1 (AuM, valuaciones, PnL, acreencias,
Tablero Comercial). Con T0 estimado: la ficha del cliente deja de mentir un día entero, el
comercial abre la cartera con la operación de la mañana adentro, y aparece la **conciliación
T0-estimado vs T-1-real** — un control de calidad de la ingesta que hoy no existe.
**Destrabar primero:** (1) el diag [D2] (<1h) — si Aunesa acepta una fecha futura, el camino es
exacto y no hay que estimar nada; (2) [D3] NOVEDADES como watchdog (un T0 estimado sin vigilancia
es más peligroso que un T-1 confiable); (3) [T10] CON-3, que es lo único que le pone **número** al
costo del T-1 (cuántos quiebres de settlement no se detectan por mes).

### B. P&L del día de la mesa, calculado ([M7])
Las DOS superficies de P&L propio son manuales: el cuaderno tipeado de PNL HISTÓRICO y el CSV que
se sube a INTRADAY (que no persiste nada). Con el P&L calculado y persistido con desglose:
P&L por trader, por activo, por estrategia, Sharpe y drawdown de la mesa, atribución MEP vs
títulos vs FCI, y el briefing puede abrir con "ayer la mesa hizo X".
**Destrabar primero:** un diag que mida qué proporción de los boletos de las cuentas propias
(100/255/256) cae fuera del mapping `unidad↔ticker` de `portafolio.assets` — ese número decide si
el cálculo cierra. Y verificar si el JSON de Aunesa trae **hora de trade** (hoy `_item_to_row`
persiste solo `concertacion`; el CSV sí la trae). Sin hora, el FIFO ordena por número de boleto.

### C. Proyección de caja T+1…T+5 ([T6])
Tesorería es 100% retrovisor. No hay ningún endpoint que conteste "¿cuánta plata entra y sale en
los próximos 5 días hábiles?" — y el sistema ya sabe casi todo (liquidación de boletos por
`plazo`, senebis por `liquidacion`, VEPs, cheques emitidos, mercados/FCI pendientes).
**Destrabar primero:** (1) [T7] el libro de cauciones — los vencimientos de caución son **el
movimiento de caja más grande del día**, sin eso la proyección proyecta migajas; (2) [T1] la serie
de saldos, única forma de **medir el error del forecast** y calibrarlo; (3) una decisión de
negocio, no de código: **acreencias es plata de TERCEROS en tránsito**, no caja propia — si se
mezclan, el forecast miente hacia arriba de forma estructural.

### D. Calidad de ejecución / best-execution ([M8])
No se mide **nada** de la ejecución en todo el producto (el único número parecido, el
`slippage_pct` de MEP, se calcula, se muestra en un drawer y se tira). Habilita: responder por
escrito a un reclamo, elegir MARKET vs LIMIT con datos propios, medir si el bilateral de SENEBIS
nos dio mejor precio que la rueda, y **calibrar los umbrales de [M5]** (sin saber cuánto cuesta
hoy una mala ejecución no hay argumento para elegir el valor de un límite).
**Destrabar primero:** resolver la **contradicción A** (¿`mercado.timesales` tiene 7 días o
historia completa?). Si son 7 días, hay que hacer [M9] **ya** — cada día que pasa sin el job de
archivo es un día que no se puede reconstruir jamás. Y decidir el alcance: órdenes propias con
arrival price (necesita el tape del mismo día) vs boletos de clientes contra el rango del día
(no necesita hora, pero es un proxy y hay que rotularlo).

### E. Guardas de riesgo pre-trade ([M5])
Hoy no hay **ningún** control cuantitativo antes de mandar una orden real: las únicas validaciones
son `size > 0` y "si es LIMIT, que haya precio". Sin tope de nominales, sin notional, sin banda de
precio, sin chequeo de saldo. El `confirm()` del navegador existe solo en MEP.
**Destrabar primero:** (1) **fase shadow no negociable** — 2-3 semanas logueando la violación sin
bloquear, para medir cuántas órdenes legítimas habría frenado (y ponerle fecha de cierre, o queda
de adorno como los umbrales `None` de guardrails); (2) [M4] el blotter, porque no se puede limitar
exposición agregada si no existe un lugar donde esté toda la exposición junta; (3) [M8] para
calibrar. **Lo que abre:** recién con límites server-side se puede **sacar `operar` de admin-only**
— el control no frena la operativa, la habilita.

---

## 4. Oportunidades por dominio — MESA

### [M1] Prender la vista de la ESTRATEGIA QUANT
- **Para quién**: trader (módulo `trading`); jefatura, que hoy no puede saber si la señal sirve.
- **Problema hoy**: `api/routers/estrategia.py` expone 4 rutas (`/live:17`, `/track-record:25`,
  `/senales:33`, `/contexto:40`) y el front consume **una**
  (`trading-estrategia-radar.tsx:51` → `/contexto`); `grep -rn "track-record|estrategia/live|estrategia/senales" src/` → 0.
  `src/components/estrategia-view.tsx` **no existe**. Detrás corre un servicio systemd
  (13:20-20:05 UTC) + un cron `*/5` sobre 4 tablas, y el service **ya calcula** hit rate,
  expectativa, MFE/MAE, equity, `edge_factores` y `muestra_suficiente`. Y
  `docs/ESTRATEGIA_QUANT.md:64,192` declara la Fase 3 **entregada**: el doc [VIVO] miente.
- **Qué proponemos**: sub-tab en `/trading` (no vista nueva) con LIVE (`eval_live` por |score|,
  4 factores abiertos) + TRACK-RECORD (por horizonte 15/30/60') + LEDGER, y arriba el contador
  que nadie tiene: **señales resueltas acumuladas** (gate declarado de la Fase 4: N≥200).
  **Backend cero**, el proxy Next ya existe.
- **Datos**: `estrategia.senales`, `resultados`, `eval_live`, `modelo_pesos`. **No falta nada.**
- **Qué DESBLOQUEA**: (a) la decisión hoy imposible — **si no hay edge, se apaga** un servicio, un
  cron, 4 tablas y la Fase 4; (b) matar factores que no predicen (`edge_factores` se calcula y no
  se lee: hoy se ponderan factores por fe); (c) mete la señal en el contexto del copiloto de
  `trading`, la vista más cara del sistema; (d) es el **primer artefacto que mide si la mesa le
  gana al mercado**, y el precedente que hace exigible lo mismo de los pivots, del VIGÍA y del lab.
- **Esfuerzo**: chico (1-3 días), frontend puro.
- **Riesgo**: el resultado esperable es "no hay edge" y hay que estar dispuesto a apagar. Con
  `muestra_suficiente=false` (<30 señales) la pantalla muestra ruido que alguien va a leer como
  señal: el aviso tiene que **bloquear la lectura**, no ser nota al pie.

### [M2] Resolver el ADR con el feed Eikon en vez de Finnhub
- **Para quién**: mesa (`/trading`, `/renta-variable`), traders de CEDEARs.
- **Problema hoy**: `scanner_sql.py:177-181,423` lee **solo** `mercado.adr_snapshot`; `jobs/adr_live.py`
  corre `*/15 13-20` y su docstring admite ~5 min de barrido por rate limit → hasta ~20 min de
  atraso contra un CEDEAR en ARS que es live de 1s. Y `core/eikon_live.py:8-9` dice textual:
  *"de momento nadie lee esta tabla"* — cubre el MISMO universo y ya calcula el **CCL implícito por
  papel** (`_ccl_implicito:68`).
- **Qué proponemos**: `COALESCE(eikon_snapshot fresco, adr_snapshot)` en `scanner_sql`, devolviendo
  `adr_source` + `adr_edad_s`. Finnhub queda de fallback (el patrón de heartbeat ya existe).
- **Datos**: `mercado.eikon_snapshot`, `adr_snapshot`, `cedears` (`ratio`,`underlying`,`ric`),
  `cedears_snapshot`. **No falta nada.**
- **Qué DESBLOQUEA**: el **arbitraje CEDEAR/ADR en tiempo real**, hoy imposible por construcción;
  ranking de CEDEARs caros/baratos; alerta de desarbitraje a N desvíos (una condición de [M14] que
  ningún proveedor externo arma porque necesita el ratio local). Corrige el **PULSO por rubro**,
  que hoy pondera retornos del ADR con datos de hace 15 minutos → el ranking sectorial está sesgado.
  Y **paga la PC de oficina**: hay un feed licenciado corriendo para llenar una tabla que nadie lee.
- **Esfuerzo**: chico (<1 día). Una query y un COALESCE.
- **Riesgo**: el feed depende de una PC prendida → el fallback no es opcional.
  ⚠️ **SIN VERIFICAR**: la cobertura real de RICs sobre los ~190 underlyings (la carga es MANUAL
  desde Manager → TÍTULOS). Si es baja, el COALESCE mejora 30 papeles y no 190.

### [M3] Barrido del book: "si doy 50.000 nominales, ¿a qué precio salgo?"
- **Para quién**: trader ejecutando, comercial cotizando un tamaño.
- **Problema hoy**: el book se sirve crudo — `api/services/order_book.py:21` define
  `_LOB_METRICS` y no calcula **nada** derivado de las puntas; el endpoint devuelve
  `{bids, offers}` de 5 niveles. El trader suma los niveles a ojo, y con bonos en paridad
  (factor 0,01) esa cuenta mental es especialmente fácil de errar.
- **Qué proponemos**: bloque `barrido` en el mismo endpoint: precio promedio ponderado de barrer,
  niveles consumidos, tamaño que **no** entra en los 5 niveles visibles, costo en bps contra el mid.
  Input de nominales arriba del book. Cálculo puro sobre datos que ya viajan en la respuesta.
- **Datos**: `mercado.market_snapshot.book` (5 niveles con price y size). **Nada falta.**
- **Qué DESBLOQUEA**: (a) input directo de [M12] — comparar rutas MEP por la primera punta es
  engañarse; (b) es la versión **honesta y barata** del fat-finger de [M5]: "tu orden barre 3
  niveles, ~28 bps" no bloquea a nadie y se puede tener esta semana; (c) le da al comercial una
  respuesta cuantificada al *"¿me lo hacés a este precio por este monto?"*.
- **Esfuerzo**: chico (<1 día).
- **Riesgo**: el book se ve a 5 niveles; un barrido grande se pasa de la profundidad visible →
  decir explícito "no entra en las puntas visibles". **Un promedio inventado es peor que ningún
  número**, y para tamaño institucional es el caso normal, no el borde.

### [M4] Blotter unificado de la mesa
- **Para quién**: trader ejecutando, y quien lo respalda cuando se levanta del escritorio.
- **Problema hoy**: **no existe una vista de "qué tiene la mesa vivo en el mercado"**.
  `GET /api/ordenes/dia` es siempre **una cuenta** (`ordenes_sql.py` hace
  `resolver_cuenta_rofex(account or cuenta_default())`); los brackets no tienen tablero
  (`GET /api/operar/brackets/dia` figura en `docs/MAPA_APP.md:2103` como **sin consumidor**,
  confirmado con `grep -rn "brackets" src/` → 0); las operativas MEP viven en su propia tabla.
  Tres superficies, ninguna consolidada.
- **Qué proponemos**: tab BLOTTER con **una fila por intención**, sin filtro de cuenta: órdenes,
  brackets con su estado y la pata que falta, operativas MEP con sus 2 patas. Columnas: hora,
  cuenta, ticker, lado, size, ejecutado/pendiente, precio, estado, **quién la mandó**
  (`ordenes_audit.actor_email`) y semáforo de `motor_heartbeat`. Cancelar desde la fila.
- **Datos**: `ordenes_live` (índice `ix_ordenes_live_account` ya existe), `ordenes_audit`,
  `brackets_live`, `operativas_mep`, `motor_heartbeat`. **Falta**: un `list_orders_dia` sin cuenta
  y decidir el merge al broker (servir SQL-only y mergear solo la cuenta enfocada).
- **Qué DESBLOQUEA**: (a) **prerrequisito de cualquier control de riesgo agregado** — [M5] en su
  versión "tope diario por cuenta" depende de esto; (b) hace visible el `EXIT_REJECTED` de
  brackets, que hoy queda "para intervención manual" y **nadie mira**: es una pata sin salida, o
  sea riesgo abierto silencioso; (c) handoff entre traders sin preguntar por chat; (d) insumo
  natural de [M8].
- **Esfuerzo**: medio (2-5 días).
- **Riesgo**: un blotter SQL-only muestra estado stale si el motor de órdenes se cayó → el
  heartbeat es parte del contrato. Y hoy `operar` es admin-only: si el blotter va en `trading`, un
  usuario con `trading` y sin `operar` vería órdenes de cuentas ajenas — **decidir el gate antes de
  escribir una línea**.

### [M5] Guardas de riesgo: banda de precio pre-trade, notional máximo, límite por cuenta y check post-trade
*(fusión de `[RISK]` + `[PRECIO-BANDA]`; absorbe el check de cupo de `[F3]` y el cableado de brackets)*
- **Para quién**: trader (protege al que ejecuta), jefatura/compliance (protege a la firma),
  back office (el que hoy descubre el error al día siguiente).
- **Problema hoy**: tres huecos que son el mismo. (1) **Pre-trade**: `api/routers/ordenes.py:47-51`
  valida `size: int = Field(..., gt=0)` y nada más; `api/services/ordenes.py:199-201` es literalmente
  `if order_type=="LIMIT" and price is None: raise` + `if size<=0: raise` y de ahí a
  `pyRofex.send_order`. Sin banda de precio, sin notional, sin límite por cuenta, sin saldo. El
  único freno es un rate limit `30/minute` que no distingue 1.000 de 1.000.000 de nominales, y la
  idempotencia degrada hacia "mandar". (2) **Post-trade**: de los 12 checks automáticos, los 8 de
  `jobs/controles_datos.py:249-259` son de MAESTROS y los 4 de `guardrails` de datos de mercado —
  **ninguno mira un boleto**. (3) **In-flight**: los brackets vivos no se ven.
- **Qué proponemos**: (a) banda de precio **server-side** contra `market_snapshot` (rechazar si se
  aparta más de X% del mid; MARKET sin book vivo se rechaza — `_tiene_puntas`/`_segundos_desde` ya
  existen en `operar.py:69,82`) + notional máximo por orden (`PRICE_FACTOR_BONOS = 0.01` ya existe)
  + tope diario por cuenta sumando `ordenes_live` + tabla `operaciones.limites_riesgo` (cuenta o
  `*`) con ABM y allowlist, devolviendo 422 con motivo; (b) **check post-trade** como un `Control`
  más del registry: boleto fuera del rango del día o a más de N% del cierre → fila en [D3];
  (c) cablear `/operar/brackets/dia`.
- **Datos**: `market_snapshot.last_price`/book, `operaciones.operaciones`, `ordenes_live`,
  `ordenes_audit`, `clientes.comitentes.cupo_*`, `cedears_ohlc_daily`, `snapshots_cierre_hist`.
  **Falta**: la tabla `limites_riesgo` y su ABM. Para renta fija el check post-trade arranca contra
  el **cierre** (±N%), no contra el rango del día (`snapshots_cierre` guarda solo `last_price`).
- **Qué DESBLOQUEA**: (a) recién con límites server-side se puede **sacar `operar` de admin-only**
  y dárselo a un trader sin que sea acto de fe — el control **habilita** operativa, no la frena;
  (b) es la **precondición para subir el autonomy slider** en cualquier cosa que toque órdenes: la
  regla de oro de `QUANTAI.md` ("la IA nunca escribe a prod") hoy no tiene ninguna barandilla
  determinista debajo; (c) los rechazos loguean el intento → aparece el dato "cuántas veces por mes
  casi metemos la pata", que es exactamente lo que pregunta un regulador; (d) el motor del check
  post-trade es el MISMO que después detecta el arancel fuera de banda ([C2]).
- **Esfuerzo**: chico (<1 día) el check post-trade y el cableado de brackets; medio (2-5 días) el
  pre-trade — el check es chico, lo caro es el ABM y calibrar sin frenar operativa legítima.
- **Riesgo**: un límite mal calibrado **bloquea una operación real en el peor momento** (bono
  ilíquido, apertura, rueda volátil). **Mitigación no negociable**: nacer en modo shadow/advertencia
  registrada 2-3 semanas y medir. Y fijarse **fecha de cierre de la banda**, o queda de adorno como
  los umbrales `None` de guardrails.

### [M6] MTM estimado de futuros en vivo, contra una liquidación que llega T+1/T+2
- **Para quién**: mesa, riesgo, back office, comerciales de agro y dólar futuro, y la ALyC (integra
  las garantías: es plata propia).
- **Problema hoy**: la tab DIFERENCIAS DIARIAS parsea `informacion ILIKE 'Diferencias diarias%'`
  (`operaciones_sql.py:1050-1066`) y ese dato lo emite Aunesa a T+1/T+2 —
  `jobs/negocio_movimientos.py:62-67` lo dice con fecha (*"el corte del 2026-05-06 fue por ingerir
  SOLO hoy"*, `_LOOKBACK_HABILES=2`). **La mesa se entera uno o dos días después de cuánto perdió o
  ganó un cliente en futuros**, mientras los precios DLR son live de 1s. **No existe ninguna vista
  de garantías ni de margen en toda la app.**
- **Qué proponemos**: MTM estimado por cuenta y contrato = posición (tenencia `DERIVADOS` ajustada
  por boletos DLR de hoy) × (precio live − ajuste del cierre anterior) × tamaño de contrato
  (1 DLR = USD 1.000). Tablero ordenado por diferencia estimada, badge `estimado`, y conciliación
  estimado-vs-real cuando llega Aunesa.
- **Datos**: `portafolio.tenencia` (`cartera='DERIVADOS'`), `mercado.futuros_dlr_snapshot`,
  `operaciones.operaciones`, `negocio_movimientos`. *Corrección al original*: el precio de ajuste
  **no falta** — `engines/futuros_dlr.py:211,229` ya escribe `closing.price` en el `data` jsonb.
  ⚠️ **SIN VERIFICAR**: si ese `closing` es el **ajuste oficial de cámara** o el último precio de la
  rueda. No es lo mismo y la diferencia es exactamente lo que se le cobra al cliente.
- **Qué DESBLOQUEA**: **monitoreo de margen intradía** — hoy la ALyC descubre a T+1 que un cliente
  quedó en descubierto sobre garantías que puso ella. Habilita alerta durante la rueda, llamado de
  garantía el MISMO día (la diferencia entre cobrar y comerse la pérdida) y el primer tablero de
  **exposición agregada en futuros de toda la casa**.
- **Esfuerzo**: medio (2-5 días).
- **Riesgo**: el MTM estimado NO es la liquidación oficial (comisiones, ajustes de cámara,
  vencimientos) → va como **señal de alerta**, nunca como el número que se cobra. Verificar la
  posición `DERIVADOS` contra una cuenta conocida antes de mostrar nada.

### [M7] P&L del día de las cuentas propias, calculado
*(fusión de `[PNL0]` + `[F6]`)*
- **Para quién**: trader (su número del día), jefatura de la mesa.
- **Problema hoy**: las **dos** superficies de P&L de la mesa son manuales.
  (1) `/trading → PNL HISTÓRICO`: `api/services/pnl_historico.py:1-9` — *"El usuario tipea el PnL
  de cada día hábil… NO lo alimenta el motor de PnL"*; dejar la celda vacía **borra la fila**.
  (2) `/trading → INTRADAY`: exige subir a mano el CSV y `api/services/intraday.py:19` dice *"NO
  persiste nada"* (el estado vive en `localStorage`). Mientras tanto el sistema ya tiene posiciones
  live del broker, los boletos del día en `negocio_movimientos` **cada 30'** y la tenencia de cierre
  de ayer de las cuentas 100/255/256.
- **Qué proponemos**: job intradía + panel que calcule `(posición T-1 × Δprecio) + (round trips por
  FIFO) − costos`, **reusando el motor FIFO ya testeado** (`fifo_pnl`/`fifo_detallado` son puros:
  hoy solo se los alimenta con un CSV). Persistir en la MISMA tabla que hoy se tipea
  (`valuaciones.pnl_historico`) con columna `origen` (`manual`|`calculado`): **el cuaderno no se
  tira, se precarga**, y el trader puede pisar el número.
- **Datos**: `portafolio.tenencia`, `negocio_movimientos`, `market_snapshot`/`cedears_snapshot`,
  `valuaciones.dolar`, `pnl_historico`. **Falta**: la columna `origen`, el job, y **la hora del
  trade** — el CSV la trae (`_parse_csv` lee `hora`), el payload de `/informes` no
  (`_item_to_row` guarda solo `concertacion`).
  ⚠️ **SIN VERIFICAR y decisivo**: qué proporción de los boletos de las cuentas propias queda fuera
  del mapping `unidad↔ticker` de `portafolio.assets`.
- **Qué DESBLOQUEA**: es el patrón pedido — destrabar que el número lo tipee un humano. Con P&L
  calculado y con desglose: P&L por trader (cruzando `ordenes_audit.actor_email` o
  `mesa_dinero.trader`), por activo y por estrategia, Sharpe y drawdown de la mesa, atribución MEP
  vs títulos vs FCI, y el briefing puede abrir con "ayer la mesa hizo X". Hoy **un día que nadie
  cargó es un día que no existe** en el tablero.
- **Esfuerzo**: medio-grande (1-2 semanas). El cálculo es lo fácil; lo caro es conciliar contra lo
  que el trader viene tipeando.
- **Riesgo**: **el número calculado va a discrepar del tipeado**, y en esa discrepancia suele tener
  razón el humano (sabe qué boleto es un error, qué es un pase). Si se presenta como "la verdad" en
  vez de "el borrador", se pierde la confianza el primer día.

### [M8] Calidad de ejecución (TCA / best-execution)
*(fusión de `[TCA]`(rol-mesa) + `[TCA]`(datos-ociosos) + `[B4]`)*
- **Para quién**: trader (defender su ejecución), jefatura (medirla), comercial (contárselo al
  cliente), compliance (es obligación de una ALyC, no lujo).
- **Problema hoy**: **no se mide nada**. El único número parecido es el `slippage_pct` de la
  operativa MEP (contra `mep_inicial`), que se calcula, se muestra en un drawer y **se tira**: no
  hay serie, ni promedio, ni comparación. Cero ocurrencias de TWAP/slicing/arrival price en el repo.
  Del lado de clientes: `negocio_movimientos` guarda `precio, cantidad, ticker, plazo, fecha,
  id_cuenta` por comprobante y `mercado.timesales` tiene el tape tick a tick — **y nadie los cruza
  jamás**. El insumo de órdenes propias existe: `ordenes_audit` guarda `SEND_REQUEST` con timestamp,
  `actor_email` y payload (`ordenes.py:214-219`), retención 365 días.
- **Qué proponemos**: **dos alcances, no uno** (los críticos proponen cosas distintas, ver
  contradicción G). (1) **Órdenes propias**: job post-cierre (mismo patrón que `day_trading_stats`
  20:06) que destile por orden arrival price, precio promedio ejecutado, slippage en bps, VWAP en la
  ventana de vida y si se llenó/canceló → `operaciones.ordenes_tca` (PK `cl_ord_id`), columna del
  blotter + resumen semanal, **sumando la serie del `slippage_pct` de MEP que hoy se descarta**.
  (2) **Boletos de clientes (renta fija)**: `jobs/ejecucion_calidad.py` que ubique cada boleto en el
  mercado del día — precio vs mín/máx, vs VWAP, vs cierre, `percentil_en_el_rango` → distribución y
  ranking **por instrumento y por contraparte**, con aviso explícito de que es proxy (sin hora, la
  comparación honesta es contra el día).
- **Datos**: `ordenes_audit`, `ordenes_live`, `operativas_mep`, `negocio_movimientos`,
  `mercado.timesales`, `snapshots_cierre_hist`, `cedears_bars_1m` (60 ruedas).
  **Falta**: la tabla y el job. **El spread al momento del envío NO es reconstruible hacia atrás**
  (`market_snapshot` tiene PK `ticker` y se pisa) → medir contra el mid exige [M13], que es
  prospectivo. *Corrección verificada*: `mercado.cedears_time_sales` **no sirve, se vacía todas las
  noches** (`jobs/cleanup_cedears_timesales.py`, `50 23 * * 1-5`).
  ⚠️ **CONTRADICCIÓN A (load-bearing)**: la retención real de `mercado.timesales`. Ver sección 8.
- **Qué DESBLOQUEA**: (a) responder "¿MARKET o LIMIT en este papel?" con datos propios; (b) elegir
  el bono correcto para el MEP del cliente con evidencia ([M12]); (c) **justificar la ejecución ante
  un cliente grande y ante el regulador**; (d) **es lo único que puede calibrar los umbrales de
  [M5]**; (e) **medir a las contrapartes de SENEBIS: ¿el bilateral nos dio mejor precio que la
  rueda?** — la pregunta económica central del módulo que [T8] está conciliando; (f) valida si un
  algo de ejecución mejora algo, así que va ANTES que cualquier algo.
- **Esfuerzo**: medio (3-5 días) cada alcance. Lo delicado es el matching orden↔trades del tape.
- **Riesgo**: **ventana de datos crítica** — si el tape se poda y el job no corre el mismo día, ese
  día no se reconstruye nunca. Medir la ejecución de una persona se lee como vigilancia →
  encuadrarlo por **instrumento y contraparte**, nunca como nota por operador. Y en instrumentos que
  no operan todos los días (buena parte de las ONs) no hay mercado contra el cual medir: esas filas
  salen como "sin referencia", no como cero.

### [M9] Archivar el tape de bonos antes de que la poda se lo lleve
*(fusión de `[F5]` + lo rescatable de `[TAPE]`)*
- **Para quién**: mesa de renta fija, jefatura, compliance, `/ons`.
- **Problema hoy**: `engines/valores.py:52-56` ejecuta `prune_native("mercado.timesales","ts",7)` en
  **cada arranque de motor** (= `DELETE`, `core/pg_mirror.py:226-233`) con el comentario *"El tape
  muestra solo el día"*. Lo que sí se persiste es incompleto: `jobs/bonos_ohlc_daily.py` guarda solo
  OHLC con ventana móvil de **20 ruedas** (`RUEDAS_KEEP=40`→20, línea 40), **sin vwap y sin
  volumen**; `market_snapshot` **tiene** `vwap` y `total_nominals` pero es un upsert con PK `ticker`
  que se pisa cada tick. Contraste directo: los CEDEARs sí tienen barras de 1 minuto a 60 ruedas
  (`jobs/cedears_bars_1m.py`) — **la renta fija, que es el core del negocio, no tiene nada**.
- **Qué proponemos**: (a) sumar `vwap`, `volume` y `total_nominals` al cierre diario y subir la
  ventana (o mandarlo a `snapshots_cierre_hist`, append-only por fecha); (b) `jobs/bonos_bars_1m.py`
  gemelo del de CEDEARs que resamplee `timesales` a OHLCV por minuto **el mismo día**; (c) del mismo
  job, el perfil de liquidez diario por instrumento (nº de trades, ticket mediano y p90, desbalance
  BUY/SELL con el `side` que el motor ya escribe).
- **Datos**: `market_snapshot` (`vwap`,`total_nominals`,`high`,`low`), `timesales`
  (`ticker,ts,price,size,side,money`), `bonos_ohlc_daily`, `snapshots_cierre_hist`, y del otro lado
  `negocio_movimientos.precio`. **Falta**: las columnas y el job.
- **Qué DESBLOQUEA**: (1) **es el insumo sin el cual [M8] solo puede medir CEDEARs**; (2) **liquidez
  real por bono** (Σ volumen 20/60 ruedas) para dimensionar una punta antes de darla — hoy
  `/renta-fija` solo muestra `VOL NOM` del día y `/ons` **esconde toda ON que no operó hoy**
  (`renta_fija_sql.py:377,403`); (3) reconstruir la curva a cualquier minuto de una rueda pasada;
  (4) alimentar el fair value con la noción de si el precio vino de un bono que opera o de uno que no.
- **Esfuerzo**: chico (a) — una columna más en un job que ya corre. Medio (b)+(c).
- **Riesgo**: el VWAP del feed es el del mercado entero, no el de nuestro flujo: para un bono con 3
  trades no significa nada → suprimir por muestra mínima (criterio ya usado en `forwards_zscore` con
  `n_obs<20` → "n/d"). **Riesgo de oportunidad: cada día sin el job es un día que no se puede
  reconstruir jamás.**

### [M10] Curva de caución (1D a 30D) en vez de un solo plazo
- **Para quién**: trader de tasa corta, mesa de agro, tesorería.
- **Problema hoy**: el motor captura **un solo plazo** (`engines/caucion.py:70-76` arma
  `MERV - XMEV - PESOS - {plazo}D` con plazo = días al próximo hábil) y la tabla **ni siquiera
  admite dos**: `mercado.caucion_snapshot` tiene **PK `moneda`**. Consecuencia verificable: **la
  tasa de caución a 7 días se tipea a mano** en AGRO → DATOS (`camara_cereales.py:394`,
  `_TASAS_FIELDS`) y `agro_cobertura.py:54-63` la usa para **descontar el precio disponible del
  cereal que se le muestra al productor**. La mesa tipea a mano un precio que el mercado publica en
  vivo, y ese tipeo mueve una recomendación comercial.
- **Qué proponemos**: suscribir la grilla (1/7/14/21/30D, ARS y USD), PK
  `(moneda, plazo_dias)`, `GET /api/cotizaciones/caucion/curva`, y un panel **CURVA DE TASA CORTA**
  superponiendo caución por plazo + TNA de LECAPs cortas (`market_snapshot.tea`) + TNA de sintéticos.
- **Datos**: `caucion_snapshot` (PK nueva), `mercado_hist`, `curvas`, `futuros_dlr_snapshot`.
  **Falta**: confirmar contra `manager.pyrofex_instruments` qué plazos existen.
  ⚠️ **SIN VERIFICAR**: qué plazos operan con volumen real.
- **Qué DESBLOQUEA**: (a) **mata un input manual que hoy mueve plata del productor** y de paso lo
  audita (nadie sabe hace cuánto está tipeado ese número); (b) la mesa ve de un vistazo el arbitraje
  que hoy hace con calculadora — caución 7D vs LECAP al mismo plazo vs sintético ROFEX+LECAP: **la
  app calcula las tres piezas y nunca las pone juntas**; (c) es la tasa correcta para descontar
  cualquier flujo a plazo (acreencias, pase agro), que hoy usan tasas manuales; (d) le da a [T1]/[T6]
  el precio de la caja ociosa.
- **Esfuerzo**: chico-medio (2-3 días). El motor ya sabe re-suscribirse en caliente; el cambio
  grande es la PK.
- **Riesgo**: plazos largos con puntas vacías ensucian la curva → mostrar volumen y **apagar el
  punto que no operó, nunca interpolar**. Cambiar la PK obliga a tocar todos los lectores (`/argy`,
  briefing, copiloto) **en el mismo commit**.

### [M11] TIR y spread offshore de los soberanos
- **Para quién**: mesa de renta fija, research, comerciales de clientes grandes.
- **Problema hoy**: `core/eikon_bonos.py:29-45` trae el precio OFFSHORE de 11 soberanos a
  `mercado.eikon_bonos_snapshot` y sus dos únicos consumidores (declarados en el propio docstring)
  son la fila de la watchlist de HOME y el bloque `bonos_off` del briefing. Se muestra el precio y
  **nunca se le aplica el motor de curvas que la app ya tiene** (`mercado.curvas` guarda los flujos
  exactos de esos mismos bonos y `analitica/listar-curva` ya calcula TIR/duration/paridad para la
  pata local).
- **Qué proponemos**: (1) correr los flujos contra el precio offshore → **TEA, duration y paridad
  OFFSHORE**, con las dos curvas superpuestas en el panel CURVAS; (2) serie del **spread
  local−offshore** en bps de TIR y en % de precio, con z-score (molde probado: `forwards_zscore`);
  (3) **canje "cable real"** — hoy `/api/analitica/canje` mide `precio_C/precio_D` con instrumentos
  **locales**.
- **Datos**: `eikon_bonos_snapshot`, `eikon_cierres` (grupo `bonos_off`), `curvas`,
  `market_snapshot`. **Falta**: nada nuevo; sí sondear qué campo publica cada RIC `=1M` (el módulo
  avisa que *"qué campos publican NO está verificado en vivo"*, resuelve con fallback
  `last → primact → mid`).
- **Qué DESBLOQUEA**: el spread local-exterior es **operable** y es argumento comercial de primera
  línea, hoy respondido con opinión. Segundo: **un ancla de valuación independiente** — si el precio
  local se desarbitra, el fair value y los residuos están midiendo contra sí mismos. Tercero: cierra
  la inconsistencia ya documentada (`MAPA_APP` §7.4.40): `argy.py` ancla contra `eikon_cierres` y
  `briefing.py` manda `None` — dos superficies mostrando cosas distintas del mismo bono.
- **Esfuerzo**: chico-medio (2-3 días). Reuso puro de `quant/` + `curvas_sql`.
- **Riesgo**: son páginas de contribuidor (`=1M` = MarketAxess), no de exchange: precios stale
  generan spreads falsos. Exigir heartbeat (`ONLINE_TTL_S=180` ya existe) y marcar `stale` en la UI.

### [M12] Tablero de rutas MEP (no la ejecución multi-bono, todavía) — ACHICADA
- **Para quién**: mesa, comercial que cotiza un MEP a un cliente.
- **Problema hoy**: **todo el MEP de la plataforma es AL30 y nada más** — `engines/dolares.py:39-42`
  define exactamente 3 tickers (`AL30 CI`, `AL30D CI`, `AL30C CI`) y la operativa manda las dos patas
  fijas con el único grado de libertad de la rueda CI/24hs. Si GD30 o AL35 dan mejor TC, o si el book
  de AL30 está flaco para el tamaño del cliente, **la plataforma ni lo muestra**.
- **Qué proponemos (núcleo)**: el **tablero read-only** que compara rutas por TC implícito y por
  profundidad de book. **Lo que sobra por ahora**: parametrizar la ejecución — la operativa MEP no es
  atómica y asume que AL30/AL30D son ultra-líquidos (de ahí el guard BUY-antes-de-SELL y los estados
  `OK_PARCIAL`/`STALE_BUY`); habilitar bonos menos líquidos **sube la probabilidad de quedar con una
  pata colgada**. Va después de [M5] y [M3].
- **Datos**: `market_snapshot` (book), `timesales`, `curvas`. **Falta**: suscribir las patas en
  **pesos** de los otros soberanos (hoy los bonares se indexan solo con sufijo D) — una línea por
  ticker en `config.TICKERS_EXTRA_PRECIOS`.
- **Qué DESBLOQUEA**: (a) mejor precio al cliente sin cambiar el proceso = **margen puro**;
  (b) **redundancia operativa** — hoy, si AL30 queda ilíquido o suspendido, la operativa MEP **se
  cae entera**, no se degrada: es un single point of failure de un producto que se le vende al
  cliente; (c) con [M8] encima se mide cuánto TC se ganó por elegir bien la ruta; (d) le devuelve UI
  a `GET /api/analitica/canje`, que quedó sin consumidor el 2026-07-24.
- **Esfuerzo**: chico-medio (2-3 días) el tablero.
- **Riesgo**: el tablero es inofensivo; la ejecución no. Separarlos explícitamente.

### [M13] Muestreo del book, acotado a los tickers que importan — ACHICADA
- **Para quién**: mesa, y todo lo que necesite medir contra el mid.
- **Problema hoy**: **el book vive un segundo y se pierde** — `market_snapshot` tiene PK `ticker`
  (cada tick pisa al anterior) y el cierre que sí se persiste (`snapshots_cierre_hist`) guarda
  precio, TEA, paridad, duration, nominales **y ninguna punta**. Nadie puede responder "¿cuál es el
  spread típico de este bono a las 11?".
- **Qué proponemos (núcleo)**: **achicar el universo**. La propuesta original eran ~200 tickers por
  minuto (~78k filas/día, ~4,7M a 60 días) en un proyecto que ya tuvo un incidente por falta de TTL.
  Muestrear AL30/AL30D/AL30C + los soberanos de [M12] + los ~30 bonos que operan de verdad, **cada 5
  minutos** → ~2k filas/día. Se amplía después con datos de uso en la mano.
- **Datos**: `market_snapshot.book`. **Falta**: la tabla `mercado.book_muestras`, el job, y **la fila
  en `jobs/cleanup_retencion.py::TABLAS` desde el día uno** (no después).
- **Qué DESBLOQUEA**: (a) [M8] deja de ser "slippage contra el last" y pasa a ser "contra el mid y el
  spread vigente", que es la única medición defendible ante un cliente; (b) [M12] puede responder **a
  qué hora** conviene armar el MEP; (c) el fair value puede **filtrar señales que son ruido de un
  book vacío** — hoy un bono que no opera igual aparece con TEA y esa TEA entra a las curvas.
- **Esfuerzo**: medio (2-4 días); chico si se acota como acá.
- **Riesgo**: ⚠️ **SIN VERIFICAR**: espacio real disponible en la instancia Supabase.

### [M14] Alertas sobre lo que solo esta app sabe calcular — ACHICADA
- **Para quién**: mesa.
- **Problema hoy**: `grep` de "alerta" en `api/`, `core/`, `jobs/`, `engines/` da copiloto,
  guardrails y websocket — **cero alertas de mercado configurables**. Lo único parecido es el
  **VIGÍA**: toasts en `/trading` con poll de 15s desde el browser, que **solo existe con la pestaña
  abierta, solo en `/trading`, solo sobre las 6 cards de pivots y sin nada configurable**.
- **Qué proponemos (núcleo)**: **las condiciones derivadas, no el precio**. "Avisame cuando GGAL
  toque X" lo tiene cualquier broker retail. Lo que ningún proveedor externo puede armar:
  *"el sintético a marzo pasó 3% TNA por encima de la caución"* ([M19]+[M10]), *"el residuo de fair
  value superó 2 desvíos"* (`fair_value_residuos` ya existe), *"el canje AL30 se abrió más de X"*,
  *"el CCL implícito de este CEDEAR se desarbitró"* ([M2]).
  **Lo que sobra / bloquea**: el **canal de entrega es un proyecto aparte y hay que decirlo** —
  Telegram fue decomisado TOTAL el 2026-07-25 (`QUANTAI.md:717-721`, **no re-proponer**) y
  `grep -rlE "smtplib|sendgrid|telegram|slack_sdk" --include=*.py .` → **0 archivos**. Sin canal, la
  alerta solo puede aparecer **en la app** (bloque en [D3]) — ya es mejor que el VIGÍA (sobrevive al
  cierre de la pestaña) pero **no despierta a nadie**.
- **Datos**: `market_snapshot`, `cedears_snapshot`, `futuros_dlr_snapshot`, `caucion_snapshot`,
  `fair_value_residuos`, `snapshots_sinteticos`. **Falta**: la tabla `mercado.alertas`, el evaluador
  (puede vivir en el ciclo del motor, que ya tiene el snapshot en RAM → costo ~0) y **el estado
  server-side del escritorio del trader** (hoy las cards y los overrides llegan como `params` del
  browser, así que sin pestaña no hay contexto — esto era `[WL]`, que muere como oportunidad
  autónoma y entra acá como prerrequisito).
- **Qué DESBLOQUEA**: la app **deja de exigir presencia**. Y es el paso previo a que una alerta pueda
  **proponer una orden precargada**, que solo tiene sentido con [M5] puesto.
- **Esfuerzo**: medio (3-5 días) con entrega in-app. Grande si se abre un canal externo.
- **Riesgo**: una alerta que **no dispara** es peor que no tenerla → heartbeat del evaluador y un
  test de "alerta que debería haber disparado". Sin **cooldown** (el motor de estrategia ya usa
  `COOLDOWN_MIN=15`), el trader las silencia en una semana y el feature muere.

### [M15] Backtestear los pivots y el VIGÍA con `cedears_bars_1m` — ACHICADA
- **Para quién**: mesa, jefatura.
- **Problema hoy**: `jobs/cedears_bars_1m.py` corre 20:20 UTC y archiva **60 ruedas** de OHLCV por
  minuto de todo el universo, y sus dos readers (`core/bars_sql.py:34 closes_1m` y
  `:53 efficiency_ratio_hist`) **no tienen un solo caller**; lo único vivo es
  `efficiency_ratio_live`, que lee el tape y cubre **5 tickers**. Se archiva el universo entero y se
  consume el equivalente a nada. Mientras tanto `/trading` dibuja **7 niveles Floor Trader por card**
  y el VIGÍA tostea sobre ellos, y **nadie sabe cuántas veces el precio rebotó en R1 o S1**.
- **Qué proponemos (núcleo)**: solo **medir si los niveles que la app dibuja predicen algo**.
  **Lo que sobra**: el "perfil intradía por ticker" (a qué hora se hace el volumen) es de manual y no
  cambia ninguna decisión; el VWAP para el monitor FIFO se absorbe en [M8].
- **Datos**: `cedears_bars_1m`, `cedears_ohlc_daily` (ya trae ATR-20). **Falta**: nada.
- **Qué DESBLOQUEA**: aplica a los pivots y al VIGÍA **el mismo estándar de trazabilidad que la
  Estrategia Quant ya se autoimpuso** ([M1]) — hoy hay dos varas en la misma pantalla: una señal con
  ledger y resolver, y siete niveles dibujados por fe.
- **Esfuerzo**: medio. Es investigación, no feature.
- **Riesgo**: honesto — **puede demostrar que los pivots no sirven**. Eso igual es valor, pero hay
  que estar dispuesto a sacar la card.

### [M16] Exponer el day-trading lab (`/scanner/day-trading`, `/companeros`) — ACHICADA
- **Para quién**: trader intradía.
- **Problema hoy**: `api/routers/scanner.py:121,132` existen (admin-only) sobre un service de 369
  líneas que ya calcula vueltas zigzag ≥ objetivo, pata en curso, posición en el rango, momentum 15',
  vs VWAP, spread, flujo comprador, minutos sin operar y la "costumbre" del papel; y
  `jobs/day_trading_stats.py` corre 20:06 UTC **solo para eso**. Grep en el front: **cero
  consumidores**; `src/app/` no tiene carpeta `trade-lab`.
- **⚠️ Verificar antes de construir**: los comentarios del front dicen que la vista **se movió**, no
  que se borró (`retorno/page.tsx:6`). O el checkout está desactualizado, o la vista se eliminó sin
  limpiar el job. **Si existe en el deploy de Vercel, esta oportunidad no existe.** Si el hallazgo
  real es "job huérfano", el resultado correcto puede ser **borrar el job** (REGLA #5).
- **Datos**: `cedears_snapshot`, `cedears_time_sales`, `day_trading_stats`, matriz de correlación de
  `rv_motor`. **Nada falta.**
- **Qué DESBLOQUEA**: es la única superficie que mide **frecuencia de oportunidad** en vez de nivel
  de precio, y habilita la pregunta siguiente — *"¿cuántas de esas vueltas agarré?"* = [M8]. Además
  son el backing exacto de dos tools ya priorizadas (`lab_intradia`, `papeles_correlacionados`):
  **borrarlas antes de cablear las tools destruye trabajo, no deuda**.
- **Esfuerzo**: chico (<1 día), frontend puro; el proxy ya existe.
- **Riesgo**: `idea{lado,motivo}` es una **heurística sin track record**. Si se muestra con el mismo
  peso visual que el ledger auditable de [M1], el trader las confunde → degradarla y rotularla.

### [M17] Riesgo del libro propio: duration agregada y DV01
- **Para quién**: mesa, dirección, riesgo. Cuentas propias (100 ACA VALORES, 255, 256).
- **Problema hoy**: `api/routers/risk.py` **no es riesgo de portafolio**: es un wrapper de
  `pyRofex.get_account_*` (saldos, márgenes, posiciones del broker). No hay duration agregada, ni
  DV01, ni exposición por moneda, ni concentración por emisor. Mientras tanto `engines/curvas.py:350-372`
  **ya calcula `duration`, `mod_duration` y `convexity` por ticker** y `renta_fija_sql.py:277-297`
  los muestra **bono por bono**, nunca agregados sobre una tenencia. `/valuaciones` arranca por
  default en la cuenta 100 y le da al libro propio las mismas herramientas que a un cliente.
- **Qué proponemos**: panel **RIESGO DEL LIBRO**: duration modificada ponderada, **DV01 en pesos**,
  desagregado por curva (CER / tasa fija / hard dollar / DLK), exposición por moneda, concentración
  por emisor y por vencimiento, y shock paralelo con la sensibilidad ya implementada.
- **Datos**: `portafolio.tenencia` (`id_cuenta ∈ {100,255,256}`) × `portafolio.assets` ×
  `market_snapshot` (`duration`,`mod_duration`,`convexity`,`tea`). *Corrección al original*: el join
  `assets → market_snapshot` **no es directo** — `market_snapshot.ticker` es el símbolo full
  (`MERV - XMEV - AL30 - 24hs`); la cadena es `mercado.curvas` (`curva`) → `ticker_corto` →
  `assets.ticker` → `unidad` → `tenencia`. **Sin ese puente el panel no arma una fila.**
  **Falta**: tratamiento aparte de opciones, futuros y CEDEARs (delta/nocional), que no tienen duration.
- **Qué DESBLOQUEA**: (a) **límites de riesgo explícitos** ("el libro no supera X de DV01") en vez de
  criterio — y un límite es lo único que después se puede vigilar solo, con lo cual el DV01 se
  convierte en la primera fila de riesgo de [D3]; (b) explicar el PnL del día **por factor** (tasa vs
  spread vs FX); (c) el mismo panel aplicado a un cliente convierte posiciones en asesoramiento;
  (d) base de cualquier reporte de riesgo al directorio de la cooperativa.
- **Esfuerzo**: medio (4-6 días).
- **Riesgo**: `market_snapshot` es **live y solo se llena con lo que operó**; un bono del libro sin
  trades del día no tendría duration y el DV01 saldría subestimado **en silencio** — el modo de falla
  que arruina una métrica de riesgo. Necesita fallback a `snapshots_cierre` y un indicador visible de
  **% de la cartera con métrica disponible**, sin el cual el número no se publica.

### [M18] FIX — `risk.py` promete un cache que no existe — ACHICADA (es un fix)
- **Para quién**: todo usuario con `/operar` abierta.
- **Problema hoy**: `api/routers/risk.py:41` dice en el docstring *"Cache 3s en el…"* y el router
  **no tiene un solo decorador que no sea `@router.get`** (los 5 endpoints, líneas 32/54/70/85/101).
  Cada usuario pega 2 veces a `get_account_report` cada 8 segundos, contra el broker, por usuario. El
  patrón correcto ya está aplicado dos veces por incidentes reales (`market_sql._quotes_all` con
  `@cached(ttl=3)` tras el `PoolTimeout` del 2026-06-16; `tesoreria.traer_crudas` con `@cached(15)`).
- **Qué proponemos**: `@cached(ttl=3)` keyeado por `(account, settle)` + invalidación explícita al
  mandar/cancelar orden + **corregir el docstring, que hoy afirma algo falso**.
- **Qué DESBLOQUEA**: bajar el poll de 8s a 2s sin multiplicar carga — hoy se manda una orden mirando
  un saldo de hace 8 segundos en **la única pantalla que mueve plata real**. Prerrequisito de
  cualquier check de saldo en [M5].
- **Esfuerzo**: <1 hora. · **Riesgo**: un saldo cacheado 3s justo después de una ejecución muestra
  plata que ya no está → **la invalidación no es opcional, es parte del cambio**.

### [M19] Z-score de la TNA sintética — ⚠️ CONTRADICCIÓN ENTRE CRÍTICOS (ver §8-B)
- **Para quién**: mesa (carry trade), agro (la TNA sintética es input de la cobertura del productor).
- **Problema hoy**: `jobs/snapshot_sinteticos.py` corre 20:40 UTC y escribe
  `mercado.snapshots_sinteticos`; los únicos que tocan la tabla son el propio job,
  `scripts/healthcheck_sql_tablas.py` y `scripts/sql_vacuum_tune.py` — se mantiene y se vacuumea una
  tabla que nadie lee. La vista `/sinteticos` pollea el live cada 5s sin un solo chart histórico. Y
  esa MISMA TNA entra a una decisión comercial: `agro_cobertura.py` toma
  `long_rofex_long_lecap[].tna` para la columna "Sintético" de la cobertura — **un número que mueve
  una recomendación al productor, comparado contra nada**.
- **Qué proponemos**: el patrón que ya funciona con los forwards — serie por par (LECAP↔futuro DLR),
  media y desvío 30 días, **z-score / percentil**, chart de TNA vs plazo con la curva de hace 1
  semana / 1 mes, y la misma referencia en la tarjeta de AGRO.
  **La alternativa honesta (crítico 3)**: si no se hace el chart, **apagar el job y dropear la
  tabla** — hoy se paga escritura, storage, vacuum tuning y healthcheck sin cobrar nada.
- **Datos**: `snapshots_sinteticos`, `forwards_zscore` como molde, `futuros_dlr_snapshot`.
  ⚠️ **SIN VERIFICAR**: cuánta historia hay y si tiene huecos (el par depende de que el futuro DLR
  **y** la LECAP hayan operado ese día) — si está agujereada, la serie no es graficable y gana apagar.
- **Qué DESBLOQUEA**: convierte una tabla descriptiva en **señal** — "TNA 38%" no dice si es caro o
  barato. El comercial de agro puede justificar por qué recomienda cubrirse hoy y no la semana que
  viene. Y es una de las condiciones no-genéricas de [M14].
- **Esfuerzo**: chico (<1 día) — el job de z-score de forwards se copia casi literal.
- **Riesgo**: los tickers LECAP rotan (el match es por año-mes de vencimiento) → graficar **por
  plazo**, no solo por ticker, o las series quedan truncadas.

### [M20] TC de Mesa de Dinero desde el MEP
- **Para quién**: Mesa de Dinero, administración, jefatura.
- **Problema hoy**: `mesa_dinero.py:321,382` hacen `LEFT JOIN operaciones.mesa_dinero_tc` y el
  docstring de `:364` lo dice sin vueltas: *"las ops de días SIN TC no suman USD → `dias_sin_tc`
  avisa que el USD está incompleto"*. **El sistema sabe que el número está mal y lo muestra igual**,
  mientras la casa tiene el tipo de cambio en tres tablas.
- **Qué proponemos**: resolver el TC **en el read** con `COALESCE(tc_manual, mep_del_día)` —más
  barato que un job— exponiendo `origen: 'auto'|'manual'`. El override manual sigue mandando.
- **Datos**: `valuaciones.dolar_snapshot`/`dolar`, `mesa_dinero_tc`. **Falta**: columna `origen`.
- **Qué DESBLOQUEA**: la serie en USD de Mesa **se completa hacia atrás** (backfilleable con el MEP
  histórico) → el resultado mensual en dólares pasa de parcial a KPI comparable, se puede comparar
  contra un benchmark, y es **prerrequisito de [M22]** (atribuir resultado a un cliente en una moneda
  con agujeros no sirve para rankear nada).
- **Esfuerzo**: chico (<1 día).
- **Riesgo**: la mesa puede usar deliberadamente un TC distinto al MEP de cierre → **sugerencia con
  origen visible, nunca reemplazo silencioso**, y el backfill toca **solo los días hoy vacíos**.

### [M21] Sugerir las filas de MESA DE DINERO desde los boletos — ACHICADA
- **Para quién**: mesa (los que cargan), jefatura (los que leen el tablero).
- **Problema hoy**: `/mesa-dinero` es 100% manual (`api/services/mesa_dinero.py`: *"Registro MANUAL
  de las operaciones de la mesa"*): cada fila pide `fecha, trader, activo, vn_compra, px_compra,
  vn_venta, px_venta, cliente, observacion`. Mientras tanto `negocio_movimientos` ya tiene cada
  boleto estructurado, ingerido cada 30' con reconciliación de anulados.
- **Qué proponemos (núcleo)**: **el diag primero, la feature después.** ⚠️ **SIN VERIFICAR y
  decisivo**: qué proporción de las filas actuales tiene un `activo` que matchea un `ticker` real.
  Si es 30%, esto ahorra poco y ensucia mucho. **Lo que sobra**: comprometerse al alcance completo
  antes del diag, y el TC automático — **ya fue decidido que no** el 2026-07-29 (no re-proponer; lo
  que sí entra es [M20], que es el TC del día, no el de la operación).
- **Datos**: `negocio_movimientos` (índice `ix_nm_id_cuenta` ya existe), `mesa_dinero`. **Falta**: el
  endpoint de sugerencias y una columna `origen`/`comprobante` para **idempotencia** (mismo patrón
  que `senebis.registro_id`).
- **Qué DESBLOQUEA**: (a) hoy **un día que nadie cargó es un día que no existe** en el tablero que
  lee la jefatura; (b) auditar la Mesa de Dinero contra los boletos — hoy son dos universos paralelos
  que nadie concilia y uno es la base del reparto de resultado; (c) **la regla 50/50 se vuelve
  verificable** contra el boleto real en vez de contra lo que se tipeó en OBS, que es plata que se le
  atribuye a personas; (d) mismo insumo que [M7].
- **Esfuerzo**: medio (3-5 días); el apareo FIFO ya está resuelto, lo caro es el matching de texto libre.
- **Riesgo**: la Mesa de Dinero **no es un espejo contable**, es el criterio de la mesa sobre qué
  cuenta como trade suyo (hay filas "Pase OPS" o "Dolar mep" que no son un ticker) → **sugerencia con
  confirmación explícita, nunca escritura automática**.

### [M22] Conectar el resultado de Mesa de Dinero con el cliente — ACHICADA
- **Para quién**: jefatura, dirección, pricing.
- **Problema hoy**: `operaciones.mesa_dinero.cliente` es **texto libre** (`mesa_dinero.py:123` arma
  el autocomplete con `SELECT DISTINCT cliente`, "sugerencias, no restrictivo") → el margen del
  negocio de bonos está estructuralmente desconectado del CRM. Y la app llama "comisiones" al arancel
  y nada más (`control_comercial_sql.py:14`).
- **Qué proponemos (núcleo)**: (1) diag de cobertura de `mesa_dinero.cliente` — **paso obligatorio,
  si la cobertura es baja esto muere acá**; (2) columna `id_cuenta` + combobox contra
  `clientes.cuentas` (el mismo `buscar_comitentes` que ya usan SENEBIS y Tesorería), texto libre como
  fallback; (3) **una sola lectura**: el cuadrante **volumen vs resultado de mesa** por cliente.
  **Lo que sobra**: la fórmula de tres patas con la comisión de referido — ese es un costo del
  **CANAL** ([C6]), no del cliente.
- **Datos**: `mesa_dinero`, `operaciones.operaciones.arancel` (índices parciales ya creados),
  `clientes.cuentas`. **Falta**: la columna + backfill manual del histórico (trabajo humano).
- **Qué DESBLOQUEA**: "¿a quién le bonificamos arancel?" pasa a tener respuesta (si deja spread, el
  arancel bajo es rentable; si no, es regalo) · le da a la regla 50/50 una **contraparte
  verificable** · y es la única forma de que [C2] no mienta: un cliente barato en bps puede ser el
  más rentable de la casa por el otro lado.
- **Esfuerzo**: chico-medio (1-3 días) el código; el backfill es de negocio.
- **Riesgo**: si el trader no elige del combobox la cobertura no mejora — el ABM tiene que hacer el
  matching **fácil, no obligatorio**, o se degrada la carga que hoy funciona.

---

## 5. Oportunidades por dominio — COMERCIAL

### [C1] Penetración: AuM sobre cupo transaccional
- **Para quién**: cada comercial (lista de llamados), jefatura (potencial del libro).
- **Problema hoy**: `analisis_comercial` (`comercial_sql.py:500-512`) devuelve `aum`,
  `cupo_transaccional_usd` y `cupo_usado_usd` **en el mismo objeto por cliente**; el front los muestra
  como tres columnas independientes y **`grep -riE "penetracion|share.of.wallet" src/` → 0**. El ratio
  nunca se calcula, en ningún lado. La mesa sabe cuánta plata tiene de cada cliente y no cuánta plata
  TIENE el cliente — teniendo el dato (`cupo_transaccional_ars` es tan sólido que
  `docs/SEGMENTACION_PATRIMONIAL.md` lo usa para derivar `nivel_3`).
- **Qué proponemos**: columna **PENETRACIÓN = AuM_USD / cupo_USD** en ESTADO COMERCIAL, ordenable, +
  bloque **OPORTUNIDAD** con los top-N por `cupo × (1 − penetración)`, agregable por segmento y
  operador. En el mismo bloque, la worklist que `[ONB]` proponía como iniciativa propia: **legajos
  sin operar nunca, ordenados por cupo × antigüedad** (sumando `fecha_alta_legajo` a `_FICHA`,
  `comercial_sql.py:30`, que hoy no la incluye + `_ULT_OP_WHERE`).
- **Datos**: **nada nuevo — todo ya viaja en el payload de `/api/operaciones/comercial/analisis`**.
  La v1 es aritmética en el browser, sin deploy de backend.
- **Qué DESBLOQUEA**: convierte el libro existente en **pipeline** (crecer sin salir a buscar
  clientes es 5-10x más barato) · da un denominador de trabajo comercial que no sea el mercado ·
  habilita un objetivo de "penetración del segmento", categoría que hoy no existe · hace útil el
  `cupo`, que se carga a mano y solo sirve para clasificar · **y es la misma cuenta que la regla de
  cupo excedido de [C14] leída al revés**: penetración >100% ES la alerta.
- **Esfuerzo**: **chico (<1 día)**.
- **Riesgo**: el cupo **no es histórico** (en modo foto todo se recalcula al corte **salvo el cupo**)
  → el ratio contra un AuM pasado mezcla temporalidades y hay que decirlo en la UI.
  ⚠️ **SIN VERIFICAR**: qué % de comitentes activas tiene cupo cargado. **Mostrar la cobertura al
  lado del KPI** — sin eso el número miente por omisión.

### [C2] Arancel efectivo en bps — el arancel se reporta contra nada
*(fusión de `[PRICING]` + `[TAKE-RATE]` + `[ROAUM]` + `[CONC]` + `[ARANCEL]`; absorbe `[TARIFA]` como escalón 3)*
- **Para quién**: jefatura comercial, dirección, back office, el comercial cuando negocia.
- **Problema hoy**: `bruto` y `arancel` conviven en la misma fila de `operaciones.operaciones` desde
  siempre y **en ningún lado se divide uno por el otro**: `_rollup_por_cuenta` (`comercial_sql.py:760-776`)
  devuelve `vol_total` **y** `ar_total` por cuenta **en el mismo dict**, y el ranking de comerciales
  ordena por `vol_total` (`:846`) → **un comercial que hace $10.000 MM a 0,5 bps rankea arriba de uno
  que hace $2.000 MM a 8 bps**. `grep -riE "tarifario|alicuota|comision_pactada|arancel_bps|take_rate"`
  sobre todo el repo → **0**. Y un cliente con $500 MM de AuM que deja $0 de arancel no aparece en
  ninguna pantalla — que es exactamente el cliente que hay que llamar.
- **Qué proponemos**, en tres escalones cortables:
  1. **`arancel_bps` como columna de primera clase** (`arancel/bruto × 10.000`) en el rollup, el
     ranking y `/ops/aranceles`. Es sumar una división a queries existentes.
  2. **Outliers contra la mediana de la cohorte** (mismo `operacion` × `mercado` × `nivel_1`) → fila
     en [D3]. **No requiere el tarifario.**
  3. **Maestro `operaciones.tarifario`** (`id_cuenta|segmento|tipo_operacion|mercado|bps_pactados|
     vigencia_desde`) con ABM y herencia segmento→cuenta, cargado **solo para los 20 clientes que
     concentran el volumen**, con el bps efectivo mediano como valor sugerido (el relevamiento no
     arranca de cero, arranca de lo que ya pasa). El control diario se engancha al registry
     `jobs/controles_datos.py::CONTROLES`, que ya diffea nuevo/vigente/resuelto y ya tiene badge.
  Gratis en el mismo panel: **ROA sobre AuM** (`arancel_12m / aum_promedio × 10.000`) y
  **concentración** (top-10 y HHI, que son un `row_number()`).
- **Datos**: `operaciones.operaciones` (`arancel, bruto, mep, id_cuenta, operacion, mercado, nivel_3,
  es_cierre, etapa, anulado_en`) con los índices parciales ya creados, `clientes.comitentes`,
  `portafolio.tenencia` para el ROA. **Falta**: solo la tabla `tarifario` (escalón 3), carga manual.
- **Trampas que un cálculo ingenuo rompe** (no inferibles, ya documentadas):
  volumen y arancel **no comparten filtro de cierre** (el arancel de caución vive SOLO en el cierre) →
  numerador y denominador con predicados distintos, se dividen al final o el take rate de caución da
  infinito · el arancel se guarda **siempre en ARS** (`operaciones_sql.py:386`) y el bruto puede ser
  USD → dolarizar con el `mep` del boleto y **descartar** los que no lo tienen, no contarlos como 0 ·
  `_rollup_por_cuenta` toma volumen de `negocio_movimientos` y arancel de `operaciones`: **dos tablas
  distintas**, su ratio es aproximado; el exacto sale de `operaciones` contra sí misma · hay negocio
  **sin arancel** en esta tabla (FCI, Mesa de Dinero) → esto mide comisión transaccional, no ingreso
  total, y hay que rotularlo.
- **Qué DESBLOQUEA**: convierte el arancel de métrica **descriptiva** en **controlable**: (a) el
  ranking premia margen y no volumen barato, lo que cambia qué se le pide al equipo; (b) con el
  pactado en base se puede simular "¿qué pasa si le bajo 2 bps?" y medir **elasticidad**; (c)
  objetivos de **margen** en `objetivos_comerciales`, que hoy solo admite volumen y comisiones; (d)
  el asistente puede contestar "¿cuánto dejamos de cobrar este mes?" con un número; (e) la regla 50/50
  de Mesa podría repartir **margen** en vez de resultado; (f) **convierte a [TARIFA] de "relevar
  1.836 clientes" en "confirmar 20 valores sugeridos"**.
- **Esfuerzo**: **chico (<1 día)** los escalones 1 y 2; medio (2-5 días) el maestro. La carga inicial
  del tarifario **no es esfuerzo de desarrollo** y es el cuello de botella real.
- **Riesgo**: comparabilidad — los bps varían legítimamente por producto y plazo: **bucketizar o la
  métrica se desacredita en la primera reunión**. Si el tarifario real vive en la cabeza del
  comercial, el maestro nace desactualizado: por eso el escalón 2 va primero. Y el corte por operador
  expone socialmente al equipo → soltarlo primero solo con `control_comercial`.
  **Pregunta previa al escalón 3: ¿existe un tarifario escrito?** Si no, ese escalón no se hace.

### [C3] Atribución del AuM: "subió 8%, ¿es mercado o es plata nueva?"
*(fusión de `[NNM]`(kpis) + `[NNM]`(rol-comercial) + `[ATRIB-AUM]`)*
- **Para quién**: dirección, jefatura comercial, comercial individual, y el asistente de negocio.
- **Problema hoy**: `/aum` grafica la evolución del total y nada más. **Nadie puede decir si la mesa
  captó clientes este trimestre o si subió el mercado** — la pregunta #1 del negocio.
  `valuaciones_sql.py:157::variacion_titulos` **ya descompone** `delta_mercado = (pe_act − pe_prev) ×
  cant_prev` vs `delta_operado = (cant_act − cant_prev) × pe_act`, pero **scopeada a una `id_cuenta`
  contra el cierre del mes anterior**; `portfolio_sql.py:276::total_diff` es una resta de saldos sin
  noción de flujos; `cashflow_sql.py::listar_flujos:54` **no acepta ni `operador` ni `nivel_1`**.
  `grep -rniE "atribucion|attribution|brinson" --include=*.py .` → **0**.
  `docs/TOOLS_IA.md` §6 lo marca como riesgo de alucinación explícito.
- **Qué proponemos**: service `atribucion_aum(desde, hasta, scope)` que devuelva, para el total y por
  cartera/segmento/operador: **efecto precio · efecto cantidad · altas y bajas de cuentas · efecto
  tipo de cambio** (separable: `total_diff` ya divide por MEP), con **`Σ efectos = ΔAuM` por
  construcción** y un residuo explícito como fila "otros". Superficie: fila "por qué se movió" en
  `/aum`, puente de AuM apilado por mes en `/operadores`, y la tool homónima del asistente.
  El ranking de comerciales pasa a ordenarse por **captación neta**, no por volumen.
- **Datos**: `portafolio.tenencia` (`aum='si'`), `valuaciones.dolar`, `portafolio.assets`,
  `clientes.comitentes`. **Falta**: decidir el grano (la tenencia se escribe diaria) y una decisión de
  negocio: un traspaso de títulos desde otro agente entra como cambio de `cantidad` (efecto operado)
  y no como movimiento de efectivo — comercialmente **es** plata nueva y hay que decirlo explícito.
  ⚠️ **CONTRADICCIÓN E**: si hace falta [D4] (normalizar `movimientos`) o no. Ver §8.
- **Qué DESBLOQUEA**: convierte el AuM de **foto en explicación**. (a) se mide al comercial por plata
  captada y no por AuM heredado, y habilita **objetivos de NNM** en `objetivos_comerciales` (hoy solo
  `volumen_objetivo` y `comisiones_objetivo`); (b) **AuM en riesgo**: la serie de retiros netos por
  cliente es un predictor de churn mucho más temprano que "días sin operar" — es la señal que le falta
  a [C9]; (c) el asistente deja de tener prohibido restar en la pregunta #1; (d) reporte trimestral a
  la cooperativa sin trabajo manual; (e) comisión variable atada a captación y no a volumen (que se
  infla rolleando caución).
- **Esfuerzo**: medio (2-5 días) la descomposición sobre `tenencia`. +[D4] si se quiere partir el
  efecto cantidad en "aporte de efectivo" vs "traspaso".
- **Riesgo**: el cuadre exacto es más difícil de lo que parece — altas/bajas de instrumentos, cambios
  de `cartera`, splits y canjes producen residuos; **si el residuo no se muestra, la descomposición
  miente con cara de precisión**. Las transferencias internas entre cuentas de la MISMA casa se
  cuentan dos veces e inflan la captación — y para arriba es justo la dirección que nadie cuestiona.
  Y el AuM es T-1: si falta un snapshot, el waterfall de ese mes miente → necesita un guardrail de
  completitud antes de publicarse (una fila más en [D3]).

### [C4] `clientes.actividad_mensual`: serie MAC y recalibración del semáforo comercial — ACHICADA
*(fusión de `[ACTMES]` + `[COHORTES]`)*
- **Para quién**: jefatura comercial, comerciales, dirección.
- **Problema hoy**: la tabla es un panel mensual por cliente (`year_month, id_cuenta, operador_email,
  nivel_1, n_ops, volumen_ars`, `sql/schema.sql:183-193`) con índice `ix_am_operador`, se reescribe
  todas las noches (`30 22 * * 1-5`) y admite `--backfill` de toda la historia. **Cero lecturas desde
  `api/`**: los únicos consumidores son el propio job, `diagnostico_registry.py:251` y
  `healthcheck_sql_tablas.py:67`. Mientras tanto `estado_comercial` (`comercial.py:49-67`) clasifica
  con umbrales inventados e iguales para todos (ACTIVA ≤45 / DORMIDA >90) — **un productor
  agropecuario que opera 2 veces al año figura DORMIDO todo el año**, en una ALyC cuyo dueño es una
  cooperativa agropecuaria.
- **Qué proponemos (núcleo, dos entregables)**: (1) service de lectura que devuelva la serie **MAC**
  (clientes activos por mes) con altas / reactivaciones / churn / neto, global y por operador — un
  `GROUP BY` sobre una tabla ya backfilleada; (2) percentiles del intervalo entre meses activos **por
  `nivel_1`**, y con eso mover `dias_activa`/`dias_dormida` de constantes a **parámetros por
  segmento** (la función **ya los recibe como argumentos**: no hay que tocar la lógica, solo dejar de
  pasarle el mismo par a todos).
  **Lo que sobra**: la matriz de cohortes M+1…M+24 en % de cuentas *y* de volumen, y el NRR (que
  además exige agregarle `arancel_ars` al job). Es el paquete de consultora.
- **Datos**: `clientes.actividad_mensual` (todo), `clientes.objetivos_comerciales`. **No falta nada.**
- **Qué DESBLOQUEA**: (a) el semáforo comercial deja de mentirle a media cartera — **como TODA la
  vista comercial cuelga de `estado_comercial`, esto arregla el insumo, no una pantalla**; (b) la
  serie MAC es el primer indicador de salud del negocio que no depende de que suba el mercado; (c)
  medir si una campaña o un cambio de operador movió la retención; (d) es el eje temporal que le falta
  a [C3].
- **Esfuerzo**: **chico (1-2 días)**.
- **Riesgo**: el congelado point-in-time solo es real **desde que el job corre**; los meses
  backfilleados usan la asignación de operador ACTUAL (lo dice el propio job,
  `jobs/actividad_mensual.py:14-18`). Hay que marcarlo en la UI o alguien va a discutir su bonus con
  datos que el sistema ya declara aproximados. **Para la recalibración por segmento eso no importa**
  (el segmento sí está congelado).

### [C5] Historia del operador por cuenta — ACHICADA
- **Para quién**: jefatura (ranking, objetivos, comisión variable), cualquier comercial al que le
  pasaron o le sacaron una cuenta.
- **Problema hoy**: `clientes.comitentes.operador_email` es un valor mutable **sin historia**
  (`INSERT_ONLY_FIELDS` en `jobs/sync_comitentes.py:69`), y todos los agregados joinean el valor
  VIGENTE contra boletos de cualquier antigüedad (`comercial_sql.py::informe_comercial:798`). **El día
  que una cuenta cambia de comercial, el ranking del año pasado cambia de dueño retroactivamente** y
  "Objetivos vs Real" deja de ser comparable.
- **Qué proponemos (núcleo)**: SOLO la tabla — `clientes.operador_historia (id_cuenta, operador_email,
  desde, hasta)`, escrita por un hook en el PATCH de Manager → CLIENTES, con backfill desde
  `actividad_mensual`, y **un solo consumidor**: `objetivos_comerciales` resuelto as-of.
  **Lo que sobra**: el toggle "cartera de hoy / atribución histórica" en los 4 endpoints de informe —
  duplica el costo y genera dos verdades para la misma pregunta.
  ⚠️ **Solapa con [D5]**: las dimensiones congeladas en `ops_agregado_diario` resuelven el mismo
  problema point-in-time por otro camino. **Elegir uno**, ver §8-F.
- **Datos**: `actividad_mensual` (backfill gratis), `objetivos_comerciales`, patrón append-only de
  `manager.role_audit`. **Falta**: la tabla y el hook.
- **Qué DESBLOQUEA**: comisión variable liquidable sin discusión · comparar un comercial contra sí
  mismo el año anterior · **prerrequisito de [C11]**: no se puede rankear por alpha si el portfolio se
  le atribuye a quien lo heredó el mes pasado.
- **Esfuerzo**: chico (1-2 días) con el alcance recortado.
- **Riesgo**: ⚠️ **SIN VERIFICAR**: cuántas cuentas cambiaron de dueño alguna vez (una query contra la
  secuencia de `actividad_mensual`). **Si el número es 3, esto no vale nada.**

### [C6] P&L por cooperativa referidora
- **Para quién**: dirección, jefatura comercial, quien negocia con las cooperativas.
- **Problema hoy**: para una ALyC de un grupo cooperativo **el canal ES el modelo de negocio**, y la
  vista existente mira hacia AFUERA: `/referidos` muestra los clientes de la coop, su AuM y la
  comisión FCI que se le paga (`comercial.py::referido_fci:403`). **No existe la mirada hacia
  adentro**; el uso interno de `referido` es solo como filtro.
- **Qué proponemos**: vista **CANALES** — una fila por coop con `n clientes` (y altas por cohorte
  anual), AuM aportado, volumen, **arancel generado**, **comisión FCI pagada**, margen neto y margen
  por cliente. El cruce que importa: coops que aportan AuM en FCI (donde la comisión se come el
  margen) vs coops que aportan clientes que operan bonos (donde queda arancel + spread).
- **Datos**: `clientes.comitentes.referido`, `operaciones.operaciones.arancel`,
  `negocio_movimientos`, `portafolio.tenencia` + `assets.fee_admin` (fórmula ya implementada).
  **Falta**: nada para la v1. *Corrección de esfuerzo*: `referido` **ya es filtro madre en todos los
  endpoints comerciales** (`operaciones.py:447,470,491,531,566`) y `_scope_cuentas` ya lo resuelve →
  la agregación es un loop sobre una función que existe.
- **Qué DESBLOQUEA**: negociar el fee split **con evidencia** en vez de con relato · decidir a qué
  coops darles soporte comercial (hoy se reparte por intuición) · detectar la coop cuyo negocio es
  neto negativo · presupuestar el canal · y con [C9] medir la **CALIDAD** de lo que trae cada coop
  (permanencia) y no solo la cantidad — que es la conversación que cambia la relación con el accionista.
- **Esfuerzo**: **chico (<1 día)** la v1; +1 día la cohorte anual y el histórico.
- **Riesgo**: ⚠️ **SIN VERIFICAR** la cobertura de `referido` (campo manual, `sync_comitentes.py:46`);
  con la mitad vacía el P&L de canal es parcial y hay que **mostrar la fila "(sin referido)" con su
  peso**, no esconderla. Y el margen histórico **se mueve solo** mientras no exista [C7].

### [C7] Cerrar el período de comisiones a referidos
- **Para quién**: administración, dirección, la cooperativa del otro lado.
- **Problema hoy**: es plata que SALE de la casa y el número **no es reproducible**. `referido_fci`
  (`comercial.py:403`) calcula `saldo = Σ valuación de los días CON foto / nº de días con snapshot` y
  `comision = saldo × fee × días/365` **en vivo, cada vez que alguien abre la tab**. El denominador
  son los snapshots de `portafolio.tenencia`, que el writer diario rellena y auto-cura
  (`portafolio.backfill_log` existe justamente porque hay timeouts y reprocesos): **si mañana se
  backfillea un día que faltaba, la comisión de un mes ya pagado cambia de valor y no queda rastro del
  original**. `grep comision` en `sql/schema.sql` → solo `comisiones_objetivo`.
- **Qué proponemos**: `clientes.liquidaciones_referido (referido, periodo, fondo, saldo_prom, fee,
  dias, comision, n_dias_con_foto, cerrado_por, cerrado_at, hash_insumos)` + botón **CERRAR PERÍODO**
  con allowlist (mismo patrón que Tesorería y Mesa). Cerrado el mes se sirve el número **congelado** y
  se muestra en paralelo el recálculo live con flag si divergen — el `hash_ok` de las fotos.
- **Datos**: la función de cálculo existente (no se toca), `tenencia`, `assets.fee_admin`,
  `backfill_log`. **Falta**: la tabla y el endpoint de cierre.
- **Qué DESBLOQUEA**: auditabilidad de plata que sale hacia terceros, hoy inexistente · defensa
  documentada ante un reclamo de una coop · devengamiento contable mensual · **estabiliza el numerador
  de [C6]** · y `n_dias_con_foto` expone por primera vez la **cobertura real de los snapshots de
  tenencia**, un indicador de salud que hoy solo vive en un log.
- **Esfuerzo**: chico-medio (1-3 días). El patrón ya está implementado dos veces en el repo.
- **Riesgo**: pide un dueño del proceso (quién cierra y cuándo) y una política de ajustes
  retroactivos. Sin eso la tabla se llena a medias y conviven dos verdades — el peor resultado
  posible para un número que se paga.

### [C8] Reloj de cosecha por cliente
- **Para quién**: comerciales de agro, mesa de agro, jefatura.
- **Problema hoy**: es la idea más "de esta casa". La app tiene el dato agro con detalle fino —
  `operaciones.operaciones` materializa `commodity` con índice parcial `ix_ops_commodity_concert`,
  `tipo_agro` y `cantidad` en toneladas, y `/ops/agro` calcula hasta el share nuestro vs mercado
  contra `mercado.volumen_mercado_agro` — y **todo se agrega por commodity y por período, nunca por
  cliente y por mes del año**. `nivel_1 = PRODUCTORES` dice quién es; **nada dice CUÁNDO**.
- **Qué proponemos**: perfil estacional por cuenta (reparto de toneladas/volumen/arancel por mes
  calendario, promediado sobre los años disponibles) → **calendario comercial**: qué clientes entran
  en ventana este mes (cobertura pre-cosecha, colocación del excedente, renovación de coberturas).
  Worklist mensual por operador y corrección estacional de [C9].
- **Datos**: `operaciones.operaciones` (`commodity, tipo_agro, cantidad, concertacion, id_cuenta`),
  `clientes.comitentes` (`nivel_1, nivel_5`), `camara_cereales` y `agro_pizarra` para dimensionar en
  pesos. **Falta**: nada nuevo. El salto de calidad sería hectáreas/producción del productor, que
  **no existe en el modelo**.
- **Qué DESBLOQUEA**: campañas con timing correcto en vez de genéricas · **es el denominador sin el
  cual [C9] es ruido** (un productor que no opera en febrero está esperando la cosecha, no se está
  yendo) · previsión de ingresos por mes basada en el calendario del libro — hoy el presupuesto
  comercial no puede ser estacional · convierte `camara_cereales`/`agro_pizarra` de vistas de mercado
  en **disparadores comerciales** · y le da al copiloto la primera pregunta agro que hoy no puede
  contestar ("¿quién entra en ventana este mes?").
- **Esfuerzo**: medio (2-4 días). Lo delicado es el corte de campaña (no coincide con el año
  calendario) y las cuentas con poca historia.
- **Riesgo**: ⚠️ **SIN VERIFICAR**: cuántos años de `operaciones` hay con `commodity` poblado.
  Necesita 2-3 campañas por cliente para significar algo; para cuentas nuevas hay que caer al perfil
  del segmento y **decirlo explícito**, o se presenta como perfil individual algo que es un promedio.

### [C9] Deterioro del cliente medido en plata, no en silencio — ACHICADA
- **Para quién**: cada comercial (worklist semanal), jefatura (ingreso en riesgo).
- **Problema hoy**: `estado_comercial` clasifica con UNA variable y `_ULT_OP_WHERE = "anulado_en IS
  NULL"` (`comercial_sql.py:48`) cuenta **cualquier** boleto: **un roll de caución mantiene "ACTIVA" a
  un cliente que se fue**. Y un cliente que pasó de USD 5M/mes a USD 100k/mes figura en verde. Cuando
  llega a DORMIDA (>90 días) la señal llegó tarde.
- **Qué proponemos (núcleo)**: UNA lista, "ATENCIÓN ESTA SEMANA", ordenada por **arancel de los
  últimos 90 días** de las cuentas cuyo arancel 90d cayó > X% **contra el mismo período del año
  anterior**, como badge al lado del estado actual (que NO reemplaza).
  **Lo que sobra**: el score de 4 componentes — dos de sus componentes no son computables hoy (el flujo
  de AuM depende de [C3], la diversidad de producto de [C10]) y ya hay comparación contra período
  anterior a nivel firma y operador (`control_comercial_sql.py:5-8`): la novedad es el GRANO.
- **Datos**: `operaciones.operaciones` (índices parciales ya creados),
  `operaciones.ops_agregado_diario` (días cerrados pre-agregados → la serie sale barata). **Falta**:
  nada; sí calibrar el umbral con la jefatura.
- **Qué DESBLOQUEA**: prioriza el tiempo del comercial por **plata en riesgo** y no por antigüedad del
  silencio · primera métrica de retención de la casa (grep `churn`/`retencion` en el dominio comercial
  → 0) · le da a [C6] el numerador de "calidad de los clientes que trae cada coop".
- **Esfuerzo**: chico-medio (1-2 días) con el alcance recortado.
- **Riesgo**: la estacionalidad agro genera falsos positivos masivos si se compara contra el trimestre
  anterior. **Esta versión no existe sin la comparación interanual**; si el histórico con `commodity`
  poblado no cubre 2 campañas (⚠️ SIN VERIFICAR), arranca solo para `nivel_1` NO productor.

### [C10] Vocabulario único de producto (familias) — ACHICADA
- **Para quién**: comerciales, jefatura de producto — y todo lo demás que necesite cruzar tenencia con
  actividad.
- **Problema hoy**: no existe un idioma común: `portafolio.assets.cartera` (HD/DL/ARS/FCI/RENTA
  VARIABLE/DERIVADOS/MONEDAS) y `operaciones.operaciones.mercado`/`nivel_3` **no son la misma
  taxonomía**, así que ninguna pregunta que cruce las dos mitades es contestable. La única mirada de
  composición es hacia adentro de UNA cuenta (`asistente_tools.py:618::posiciones_cuenta`): no se
  puede responder "quién tiene solo FCI" ni "quién nunca tocó CER".
- **Qué proponemos (núcleo)**: una tabla de mapeo **familia de producto** (una fila por valor de
  `assets.cartera` y por valor de `nivel_3`/`mercado` → familia común), cargada una vez y consumida
  por ambos lados. Entregable visible: matriz **segmento × familia** con % de penetración (tiene /
  operó alguna vez / nunca). **Lo que sobra**: el "próximo mejor producto por look-alike" — es un
  recomendador de manual y el ítem más sensible desde compliance (empujar derivados a un perfil
  conservador).
- **Datos**: `tenencia` × `assets.cartera`, `operaciones.operaciones` (`mercado, nivel_3, commodity,
  tipo_agro`), `comitentes.nivel_1..5`. **Falta**: la tabla de mapeo (definición de negocio, chica).
- **Qué DESBLOQUEA**: es **plomería que le falta a media lista** — sin ella la diversidad de producto
  de [C9] no se calcula, el desglose por producto de [C12] no se cruza con lo operado, y el asistente
  no puede contestar nada que mezcle tenencia con actividad (hueco #2 de `docs/TOOLS_IA.md`: puede
  filtrar pero no cruzar). De yapa, dimensionar el negocio potencial de un producto antes de lanzarlo.
- **Esfuerzo**: chico-medio (1-3 días), casi todo en acordar el mapeo.
- **Riesgo**: un mapeo mal acordado se congela y todo lo que cuelga hereda el error. Se mitiga porque
  es una **TABLA editable** y no constantes en el código — el patrón que el repo ya usa con
  `operaciones.tipos_operacion`.

### [C11] Benchmark de carteras: la línea de inflación — ACHICADA
- **Para quién**: comercial (es la conversación de retención), jefatura, dirección, y el cliente vía [C13].
- **Problema hoy**: `grep -rniE "benchmark" api/` devuelve **solo** `rv_motor.py:195` (beta de un
  CEDEAR vs SPY/QQQ) y una línea de prompt del copiloto. **No hay un solo benchmark de cartera en todo
  el producto**: la app calcula muy bien TWR base 100 ARS y USD, TEM/TEA y XIRR
  (`valuaciones.consolidado`) y los muestra **contra nada**. Nadie puede contestar *"¿qué porcentaje
  de nuestros clientes le ganó a la inflación este año?"*.
- **Qué proponemos (núcleo)**: **una sola línea** — CER/UVA base 100 sobre el mismo eje temporal — y
  **un solo número nuevo**: rendimiento real (sobre inflación) por cliente, agregable por operador y
  segmento. **Lo que sobra**: las 4 curvas simultáneas (TAMAR/BADLAR, MEP, MERVAL), el benchmark
  seleccionable y guardado por cliente, y el índice de bonos — esa es la versión que "genera más
  conversaciones de las que resuelve". Se agregan si alguien las pide.
- **Datos**: `valuaciones.consolidado` (`base100_ars`, `tea_*`), `/{id}/mensual`,
  `macro.series_macro` (CER), `macro.uva`. **Falta**: nada de datos. Sí la convención de empalme (día
  sin dato del índice → **arrastrar el anterior, nunca interpolar**, misma semántica que
  `get_mep_for_date`) y la alineación de frecuencias (TWR mensual vs serie diaria).
- **Qué DESBLOQUEA**: (a) el argumento de retención más fuerte que puede tener un comercial argentino,
  con números propios y sin Excel; (b) **cambia qué se le pide a un comercial**: se mide la calidad de
  la asesoría (cuántos de sus clientes le ganaron a la inflación) y no solo el volumen — es la
  contracara exacta de [C2], que mide cuánto deja el cliente; (c) el ranking de `/valuaciones →
  TOTALES` pasa de "quién rindió más" a "quién le ganó a su benchmark"; (d) habilita un ranking por
  alpha, **que solo es honesto con [C5]**; (e) le da a [C13] la línea que convierte un extracto en un
  informe de gestión.
- **Esfuerzo**: chico-medio (2-3 días con un solo benchmark).
- **Riesgo**: comparar contra inflación invita al reclamo por diferencias que dependen del mandato
  (una cartera 100% en dólares contra CER se ve mal por construcción) → mostrar la línea **solo en la
  vista ARS** y rotularla como referencia, no como objetivo. Y expone algo incómodo: **muchas cuentas
  van a perderle al plazo fijo**. Si no hay estómago para ese número, no se construye.

### [C12] Riesgo sobre las carteras REALES de clientes — ACHICADA
- **Para quién**: mesa, jefatura, comerciales al recomendar.
- **Problema hoy**: `mercado.snapshots_cierre_hist` guarda `duration, mod_duration, convexity,
  paridad, tea` **por ticker y por día** y `portafolio.tenencia` no tiene una sola métrica de riesgo;
  las métricas nunca se cruzaron con la tenencia. Para una casa que vende renta fija ARG, **no saber
  la duration de la cartera de un cliente es no saber nada**.
- **Qué proponemos (núcleo)**: **tres columnas, no una vista** — duration modificada de la cartera
  (ponderada por valuación), **escalera de vencimientos** por año y **exposición por emisor**, en
  `/valuaciones` y su agregado en `/aum`, más una línea de sensibilidad a ±100 bps (con
  `mod_duration` y `convexity` ya calculadas es aritmética). **Lo que sobra**: VaR, vol de book y
  contribución al riesgo — el propio autor admite que "el VaR es la vistosa, empezar por lo aburrido"
  (y `rv_motor.py:360-401` solo cubre CEDEARs con posiciones pasadas por parámetro).
- **Datos**: `tenencia` × `assets` (`unidad→ticker`, `emisor`, `calificacion`, `clase_activo`) ×
  `snapshots_cierre_hist` × `curvas`. **Falta**: nada estructural. ⚠️ **SIN VERIFICAR** la cobertura
  de `calificacion` (carga manual en Manager → TÍTULOS) → la exposición por calificación queda fuera
  de la v1; la de emisor no.
- **Qué DESBLOQUEA**: (a) "¿qué clientes están largos duration si sube la tasa?" pasa de imposible a
  una query — el hueco exacto que marca `docs/TOOLS_IA.md`; (b) límites de concentración por emisor
  dejan de ser intuición; (c) **es el insumo de [C13]**: un estado de cuenta con duration y escalera
  es de otra categoría que uno con saldos; (d) la escalera de vencimientos de los clientes es **flujo
  futuro conocido** — la misma información que `acreencias` le da a [T6], vista desde el activo;
  (e) comparte el puente `curvas → assets → tenencia` con [M17].
- **Esfuerzo**: chico-medio (1-3 días). El join `unidad↔ticker` ya está resuelto en
  `api/services/titulos_flujos.py`.
- **Riesgo**: la duration de un bono ilíquido arrastra el último valor disponible → mostrar la **fecha
  del dato**, no el número solo. Una cartera con mucho cash o FCI da una duration "de cartera" que no
  significa nada si no se aclara sobre qué porción se calculó.

### [C13] Estado de cuenta emitido por el sistema — ⚠️ CONTRADICCIÓN ENTRE CRÍTICOS (ver §8-C)
- **Para quién**: comerciales (lo mandan), back office, jefatura. Indirectamente el cliente.
- **Problema hoy**: `grep -riE "reportlab|weasyprint|fpdf|xhtml2pdf"` → **cero**;
  `grep -riE "estado.de.cuenta|estado_cuenta|resumen_cuenta"` → **cero**. La plataforma calcula TWR
  base 100 ARS y USD, TEM/TEA, XIRR, la descomposición mercado vs operado, los flujos del mes, el PnL
  cost-basis y los cobros futuros — y **no puede emitir un documento**. Los únicos exports son `.xlsx`
  armados en el browser, tabla por tabla, que reflejan los filtros de esa pantalla y no son
  reproducibles.
- **Qué proponemos (recortado)**: v1 **`.xlsx` server-side, sin ninguna dependencia nueva**:
  `GET /api/valuaciones/{id_cuenta}/estado-cuenta?periodo=YYYY-MM&moneda=` con carátula, valuación
  inicio/cierre, aporte/retiro neto, resultado separado en **efecto mercado vs efecto operado**,
  TWR y XIRR en ARS y USD, tenencia al cierre por cartera, movimientos boleto por boleto y calendario
  de cobros a 90 días. **Numeración + hash del payload** (patrón de `tesoreria_snapshots`) para que un
  resumen emitido sea reproducible aunque el cron de hoy no haya corrido. El PDF con marca es una
  segunda etapa, no un requisito.
  *Corrección verificada*: **NO está bloqueada por [T14]** — `actualizar_precio_posicion`
  (`tenencia_hd.py:24,605`) escribe solo `id_cuenta = ANY(["100","255","256"])`, las cuentas propias.
  Las valuaciones de clientes vienen de Aunesa y no son editables desde la app.
- **Datos**: `tenencia`, `assets`, `negocio_movimientos`, `acreencias`, `valuaciones.dolar`,
  `comitentes` (ya tiene `email` y `telefono`). **Falta**: la tabla `valuaciones.estados_cuenta` y la
  leyenda legal del pie (decisión de negocio).
  ⚠️ **SIN VERIFICAR y bloqueante**: si Aunesa ya emite un resumen oficial. **Duplicarlo con cifras
  distintas sería peor que no tenerlo.**
- **Qué DESBLOQUEA**: el envío periódico deja de depender de que alguien arme un Excel · con [C11] y
  [C12] adentro deja de ser un extracto y pasa a ser un **informe de gestión**, que es lo que
  diferencia a una ALyC de un custodio · "mostrame qué le informaste al cliente en marzo" pasa de
  buscar mails a una query · **hace innecesario el portal del cliente para el 90% de los casos, al 5%
  del riesgo**.
- **Esfuerzo**: medio (2-4 días) la versión `.xlsx` con trazabilidad.
- **Riesgo**: un documento que el cliente recibe es un compromiso. Si `jobs.consolidado_cuentas` no
  corrió, se manda un número malo con el logo de la casa → el endpoint tiene que **negarse a emitir**
  si faltan días de foto en el período, no emitir con un asterisco.

### [C14] Cupo y riesgo LA/FT: campos de legajo que no controlan nada — ACHICADA
*(fusión de `[AML+F3]` + `[B5]`)*
- **Para quién**: oficial de cumplimiento (si existe), back office, jefatura, y el comercial.
- **Problema hoy**: `clientes.comitentes` tiene `riesgo_la_ft`, `cupo_transaccional_ars`,
  `cupo_usado_ars`, `cupo_utilizacion_pct`, `cupo_fuente`, `cupo_cargado_en`, `estado`,
  `fecha_alta_legajo`. `riesgo_la_ft` aparece en 4 lugares del repo y es **puro passthrough** (se
  sincroniza, se edita, se muestra — no filtra, no ordena, no alerta). El cupo se carga con un Excel
  del custodio y **no existe la noción de "excedido"**. La casa es sujeto obligado y **no monitorea
  nada** — en una inspección **la ausencia de rastro es el hallazgo**.
- **Qué proponemos (núcleo)**: **las reglas que no requieren decisión de nadie, en modo sombra**:
  (1) cuentas operando con `cupo_fuente` nulo (nadie cargó el cupo y están operando); (2) cuentas
  cuyo volumen del período supera su `cupo_transaccional_ars` — los dos campos ya viajan en el payload
  de `analisis_comercial` y el operado ya está agregado en `ops_agregado_diario`; (3) badge **"cargado
  hace N días"** sobre `cupo_cargado_en` (hoy no se sabe si el cupo es de esta semana o de marzo).
  Persistir con severidad y estado en una tabla plana, **sin workflow, sin tablero, sin obligación de
  tratarla**. Cada fila con el **mismo modal de auditoría** que ya existe en Tesorería→BANCOS y en
  Operadores→DÍAS SIN OPERAR.
  **Lo que sobra por ahora**: las reglas de "pasamanos" y "alta reciente con volumen desproporcionado"
  dependen de [D4]; el "cupo usado live" **no se puede codear hasta confirmar qué significa 'usado'
  para el custodio** (si es fondeo, la fuente es `movimientos`, no `operaciones`, y un número que
  contradice al del custodio hace perder la confianza en los dos); el check pre-trade se va a [M5].
- **Datos**: `clientes.comitentes`, `operaciones.operaciones`, `ops_agregado_diario`. **Falta**: la
  tabla de alertas + el catálogo de umbrales (definición de negocio).
- **Qué DESBLOQUEA**: convierte campos de legajo en control · deja el **rastro documentado de que se
  monitoreó** · el mismo runner de reglas sirve después para límites de riesgo comercial sin escribir
  un segundo motor · y habilita el evento comercial "cliente al 90% del cupo", que hoy se descubre
  recién cuando llega el Excel siguiente del custodio.
- **Esfuerzo**: chico (1-2 días) las reglas en sombra. Grande el programa completo (que no se propone).
- **Riesgo**: **es un dominio regulado.** Un tablero que *parece* monitoreo UIF pero no sigue el
  procedimiento formal da falsa tranquilidad. Presentarlo como **herramienta de detección interna** y
  decirlo en la propia pantalla. **Sin dueño identificado, no empezar**: una alerta generada y nunca
  tratada es peor que no tenerla. Nota verificada: el módulo `compliance` **fue eliminado** de
  `core/roles.py::MODULES`.

---

## 6. Oportunidades por dominio — BACK OFFICE Y TESORERÍA

### [T1] Serie de saldos de caja + arrastre del inicial + serie de gastos
*(fusión de `[TES-1]` + `[TES-2]` + `[F7]` + `[TESO-INICIAL]` + `[GASTO]`)*
- **Para quién**: back office / tesorería (allowlist `tesoreria_escritores`), jefatura de
  administración, auditoría, dirección.
- **Problema hoy**: **el sistema calcula el saldo final de ayer, lo tira a los 30 días, y le pide a un
  humano que lo vuelva a tipear cada mañana.** Verificado: `TTL_SNAPSHOTS = 30`
  (`tesoreria.py:734`) con el DELETE dentro del mismo INSERT de `tomar_snapshot`;
  `set_saldo_inicial:2251` solo persiste lo que un humano escribe; y `ingresos_egresos_dia:397-400`
  resuelve la falta con `saldo_inicial = ini if ini is not None else 0.0` — **si nadie carga, el día
  no da error: da un número mal, en silencio**. `grep arrastre|sugerido` en `tesoreria.py` → 0.
  Y **todo Tesorería está scopeado a `fecha = hoy`**: `api/routers/back_office.py:583`, el único
  endpoint de lectura de `tesoreria_registros`, toma **un solo día**, aunque la tabla guarda el gasto
  **categorizado** (`TIPOS_REGISTRO = PROVEEDORES, FONDOS FIJOS, VEP, HABERES, IMPUESTO, TARJETA
  VISA, OTROS`). La empresa genera todos los días el insumo de su propio estado de gastos y lo tira.
  Consecuencia: *"¿cuánta caja teníamos en el Banco X el 15 de marzo?"* **no tiene respuesta** — para
  una ALyC regulada la retención está al revés (se conserva el detalle pesado 30 días y el agregado
  liviano cero).
- **Qué proponemos**, tres cambios chicos:
  1. `operaciones.tesoreria_saldos_diarios` **sin TTL**: una fila por (fecha, banco, unidad) con
     `saldo_inicial, ingresos, ingresos_echeq, egresos, egresos_echeq, mercados, fci, bb_mas,
     bb_menos, saldo_final` — es **exactamente el dict que `ingresos_egresos_dia(...)["cuentas"]` ya
     devuelve**. ~30 filas/día ≈ 7.500 al año. Backfill desde las 30 fotos vivas.
  2. **Encadenar el inicial**: al abrir un día sin saldo cargado, el backend devuelve
     `saldo_inicial_sugerido` = saldo final del día hábil anterior, marcado `origen:'arrastre'`, en
     gris con "propuesto desde la foto del 08/08", **un click lo confirma** y queda como carga manual
     con su actor en `tesoreria_audit`. **Jamás escritura automática.** Más una línea al abrir la
     vista: cuántas cuentas quedaron **sin confirmar** hoy. Y fila de auditoría **DESCUADRE DE
     APERTURA** = inicial declarado − final de ayer, en ámbar si ≠ 0.
  3. **Rango en vez de día** en `GET /tesoreria/registros`: serie mensual por `tipo`, banco y moneda,
     año contra año, con drill-down al registro (el modal de auditoría de BANCOS es el mismo patrón).
- **Datos**: `ingresos_egresos_dia`, `tesoreria_snapshots.datos`, `tesoreria_saldos`,
  `tesoreria_cuentas`, `tesoreria_registros`, `core.calendario`. **Falta**: la tabla (10 columnas
  numéricas), una columna `origen` en `tesoreria_saldos`, y el INSERT en `tomar_snapshot`.
- **Qué DESBLOQUEA**: el saldo de bancos deja de ser foto y pasa a **serie continua** — recién ahí
  existen posición de caja graficable, estacionalidad y **concentración bancaria** (¿qué % de la caja
  está en un solo banco? es riesgo de contraparte y hoy no hay número) · el descuadre de apertura es
  el **primer control de conciliación banco↔sistema de la casa**: si el propuesto difiere del extracto
  real, ESO es una diferencia — hoy se tipea el número correcto encima y **la diferencia se borra
  sola** · elimina el modo de falla más caro del día (cerrar con un banco en cero porque nadie tipeó)
  · **es la única forma de medir el error de [T6]**: sin caja real persistida un forecast no se
  calibra nunca · estacionalidad de HABERES/IMPUESTO y detección de gasto anómalo · con el arancel del
  mes al lado ([C2]) da **meses de gasto cubiertos por comisiones**, el KPI de supervivencia de una
  ALyC chica · y habilita la tool `tesoreria_dia` del backlog de IA sin pegarle a Aunesa en vivo.
- **Esfuerzo**: **chico (<1 día)** la tabla + el INSERT + el sugerido; +1 día la fila de descuadre;
  chico (1-2 días) la serie de gastos.
- **Riesgo**: si la foto de ayer estaba mal, el arrastre **propaga y compone** el error → por eso es
  sugerencia con click y el `origen` se ve en la celda; si el back office confirma sin mirar el
  extracto, se pierde el control que hoy da tipear → la diferencia propuesto-vs-confirmado tiene que
  quedar **registrada**. La foto tiene **una sola por día y TTL 30**: si el cron falla, el encadenado
  se corta y hay que degradar a carga manual sin ruido. Costo irreversible: **definir hoy la fórmula
  canónica de las 10 columnas**, porque con años de serie cambiarla obliga a re-backfillear. Y esto
  **no es contabilidad, es caja**: presentarlo como "resultado de la empresa" chocaría con el sistema
  contable real.

### [T2] Persistir los movimientos bancarios de Aunesa
> **⚠️ ESTADO (2026-08-13): hubo un intento previo y SE BORRÓ. Arranca de cero.**
> Si se retoma T2, la tabla hay que crearla: la que existía se dio de baja (ver abajo)
> tras confirmar que nadie la leía ni la escribía. Queda un CSV de respaldo en el Droplet
> (`tesoreria_movimientos_respaldo.csv`, 2.106 filas) y, sobre todo, **el dato es
> recuperable de Aunesa** — de ahí salió en 44 segundos. Lo que sigue es la historia del
> intento, que vale para no repetirlo igual:
> Tenía **2.106 filas** con las 20 columnas de `aplanar()`
> (`persona_*`, `riel`, `cuenta_operativa`, `banco_codigo`…) cubriendo del **2026-06-08 al
> 2026-08-06**. Todas entraron en **44 segundos el 2026-08-06 14:56 UTC** (`ingestado_en`), o sea
> un backfill de UNA corrida, y desde entonces **cero escrituras** (`n_tup_ins` = exactamente el
> total de filas). El upsert continuo nunca se cableó y el script del backfill **no quedó en el
> repo**: `git log -S tesoreria_movimientos --all` no lo encuentra en ningún commit.
> Consecuencia: la tabla era **invisible desde el código y desde `sql/schema.sql`**, y en el
> relevamiento de índices salía como "peso muerto" (nadie la leía). **Lección para cuando se
> retome**: si se crea la tabla, va a `sql/schema.sql` y el backfill queda en `scripts/` en el
> mismo commit — si no, en tres meses nadie sabe qué es. Lo que falta de T2 es el upsert en el poll y
> la política de estados mutables — no la tabla.
- **Para quién**: back office, tesorería, administración, y quien tenga que responder un pedido de
  información.
- **Problema hoy**: **los movimientos bancarios de la casa viven 15 segundos** — el docstring lo dice
  (*"Se sirve LIVE contra Aunesa sin persistir"*) y `@cached(ttl=15)` está sobre `traer_crudas`
  (`tesoreria.py:149`). El único rastro pasado son los items del `detalle` de la foto, que además
  filtra `if str(r.get("estado")) != ESTADO_EFECTIVO: continue` → **los Rechazados, Anulados,
  Demorados y Pendientes desaparecen para siempre a las 24 horas**. Y esa llamada HTTP son **~1,6 s de
  los ~2,0 s** que tarda armar la vista (82%) contra ~0,35 s de las 16 queries: se paga en cada
  apertura y en cada foto. El hack `default_excluido = not mov["_hora"]` existe porque **sin
  persistencia no se puede ni aprender qué ids se repiten**.
- **Qué proponemos**: `operaciones.tesoreria_movimientos`, PK (`id` de Aunesa, `fecha`), upsert
  idempotente en cada poll con la fila cruda aplanada + un histórico de `estado` (Aunesa muta estados
  hacia atrás → tabla hija o jsonb append-only con timestamp). La vista lee SQL y refresca contra
  Aunesa en background.
- **Datos**: la salida de `aplanar()`, que ya devuelve todos los campos crudos + `persona_*` +
  `_hora`/`_tipo`/`_echeq`. **Falta**: la tabla y la política de re-lectura de días viejos.
- **Qué DESBLOQUEA**: **precondición dura de [T9]** · búsqueda y export por cliente/CUIT/rango ("todas
  las transferencias de este CUIT en el año", "cuántas veces nos rebotó un pago"), hoy sin respuesta ·
  auditar por qué un movimiento cambió de estado, información que hoy se destruye sola · **baja la
  latencia de la vista de ~2,0 s a ~0,4 s** y saca N usuarios polleando cada 20 s de encima de Aunesa
  · y le da a [T6] el histórico contra el cual medirse.
- **Esfuerzo**: medio (2-5 días). La tabla y el upsert son un día; lo caro es la política de estados
  mutables y el backfill (que solo alcanza hasta donde Aunesa devuelva).
- **Riesgo**: los movimientos **sin hora siguen sin ser distinguibles de un duplicado** — persistirlos
  no lo arregla, solo lo hace visible y medible. Y hay que decidir retención: acá el volumen sí importa.

### [T3] `fecha_acreditacion` en los cheques recibidos
- **Para quién**: tesorería, back office.
- **Problema hoy**: quirúrgico, del tipo que solo encuentra quien leyó el código.
  `tesoreria_cheques` tiene `fecha_pago` pero **solo la usan los EMITIDOS**
  (`_cheques_emitidos_t1_rows` filtra `lado='emitido'`); los RECIBIDOS se listan **y se imputan al
  saldo** por el día de CARGA — `ingresos_echeq_dia` (`tesoreria.py:954-966`) filtra
  `(creado_at AT TIME ZONE ART)::date = %(d)s` y esa fila **SÍ suma al saldo final**. Un cheque que
  entró el viernes y se carga el lunes suma al saldo del lunes, y **no hay forma de corregirlo**: los
  recibidos no tienen ningún campo de fecha editable. Si el viernes ya se fotografió, esa plata nunca
  existió ese día.
- **Qué proponemos**: columna `fecha_acreditacion date` (default = día de creación en ART) usada como
  clave de imputación en la tab y en la fila de BANCOS. Backfill con `(creado_at AT TIME ZONE
  ART)::date` → **cero cambio para todo lo ya cargado**.
- **Datos**: `operaciones.tesoreria_cheques`. **Falta**: la columna + un date picker que ya existe en
  las otras tabs.
- **Qué DESBLOQUEA**: el saldo del día deja de depender de la puntualidad del operador → **la grilla
  se vuelve reproducible** (recalcular un día viejo da lo mismo), que es la condición sin la cual [T1]
  persiste una serie que no se puede reconstruir y [T4] detecta divergencias que en realidad son
  cargas tardías. Además habilita darle a los recibidos el mismo tratamiento "T-1 pendiente" que ya
  tienen los emitidos.
- **Esfuerzo**: chico (<1 día).
- **Riesgo**: si el equipo hoy compensa cargando siempre el mismo día, el cambio no mueve nada y
  parece trabajo perdido. El valor aparece en las cargas tardías — **que son justo las que hoy nadie
  puede ver**, así que "no pasa nunca" no es verificable hasta que se mide.

### [T4] Detectar escrituras posteriores a la foto — ACHICADA
- **Para quién**: back office, administración, auditoría.
- **Problema hoy**: `_validar_registro:1486`, `_validar_mercado:1254`, `_validar_bb:1873` y
  `set_saldo_inicial:2251` toman la `fecha` del payload **sin ninguna guarda** → se escribe sobre un
  día ya fotografiado; y `hash_ok` **no lo detecta** porque recalcula sobre `r["datos"]` guardado, no
  contra las fuentes vivas. **La FOTO dice "así cerró el día" y la base puede decir otra cosa, con
  `hash_ok: true`.** Cualquier número histórico de Tesorería tiene un asterisco invisible.
- **Qué proponemos (núcleo)**: **solo la mitad de detección**. Al servir una foto, recomputar el
  payload desde las fuentes SQL del día y comparar contra el congelado → banner *"N cambios
  posteriores a la foto — ver detalle"* con el diff fila por fila. **Sin gate, sin tabla de cierres,
  sin reapertura** — eso es gobierno pesado para un back office chico, y la propia propuesta admite el
  efecto perverso: *"si la reapertura es engorrosa el equipo va a imputar todo al día de hoy"*.
- **Datos**: `tesoreria_snapshots` (`datos`, `hash_sha256`), las mismas fuentes de
  `_payload_dia`/`_detalle_dia` (una sola pasada, ya escrita y reusable), `tesoreria_audit`.
  **Falta**: nada. Ni una columna.
- **Qué DESBLOQUEA**: el histórico de Tesorería se vuelve **defendible sin agregar fricción** — y
  recién con eso tiene sentido apoyar un cierre mensual o un libro de bancos exportable encima. Además
  mide algo que hoy nadie sabe: **con qué frecuencia se escribe sobre días cerrados**, que es el dato
  que decide si el gate de verdad hace falta.
- **Esfuerzo**: chico (<1 día) — es reusar `_payload_dia` y un `!=`.
- **Riesgo**: si se escribe sobre días viejos todo el tiempo (correcciones legítimas), el banner sale
  siempre y se ignora. En ese caso la respuesta correcta no es el gate: es entender por qué, que es
  justo lo que este cambio permite ver.

### [T5] FIX — renombrar un banco deja plata en un banco fantasma — ACHICADA (es un bug vivo)
- **Para quién**: el back office el día que renombre un banco.
- **Problema hoy**: `editar_cuenta` (`tesoreria.py:2140-2145`) arrastra el renombre a exactamente 3
  tablas — `tesoreria_saldos`, `tesoreria_cheques`, `tesoreria_mercados` — y **NO** a
  `tesoreria_banco_a_banco` (que referencia el banco en DOS columnas, `cta_debito` y `cta_credito`),
  ni a `tesoreria_registros.banco`, ni a `tesoreria_veps.banco`. La PK es el nombre y el nombre lo
  decide Aunesa. Nadie toca el catálogo por miedo, con razón.
- **Qué proponemos (núcleo)**: (1) extender el `for tabla, col in (...)` a las 3 tablas faltantes,
  con las DOS columnas de `banco_a_banco`, **en la misma transacción**; (2)
  `scripts/diag_tesoreria_huerfanos.py` read-only que liste referencias sin match en el catálogo.
  **Nada de surrogate key + migrar 6 tablas** — es ingeniería por deporte para un catálogo de ~30 filas.
- **Datos**: `operaciones.tesoreria_{cuentas,saldos,cheques,mercados,banco_a_banco,registros,veps}`.
  **Falta**: nada.
- **Qué DESBLOQUEA**: el catálogo se puede **curar** (renombrar deja de ser peligroso), que es la
  precondición de que [T1] tenga una serie por banco que no se parta en dos entidades a mitad de la
  historia.
- **Esfuerzo**: **2 horas**, no "chico (<1 día)". Es un `for` con 3 entradas más.
- **Riesgo**: ninguno el parche. ⚠️ **SIN VERIFICAR**: el diag puede revelar huérfanos que ya existen
  y que hay que reasignar a mano — eso sí es trabajo.

### [T6] Proyección de caja T+1…T+5
*(fusión de `[CAJA-1]` + `[B6]`; absorbe el forecast que `[F7]` proponía)*
- **Para quién**: tesorería, mesa de dinero, jefatura financiera.
- **Problema hoy**: **Tesorería es 100% retrovisor.** Lo único con horizonte futuro son dos tableros
  de seguimiento sueltos (VEPs con `vencimiento`, cheques emitidos con `fecha_pago`) que **ni siquiera
  se suman entre sí**, y `get_titulos_mercado` (`back_office_titulos.py:68`) se detiene en el día de
  hoy. No hay ningún endpoint que responda *"¿cuánta plata entra y sale en los próximos 5 días
  hábiles?"*. Y el sistema ya sabe casi todo.
- **Qué proponemos**: tab **PROYECCIÓN**: filas = fuente, columnas = próximos 5 días hábiles × moneda,
  fila 0 = saldo de hoy (que el backend ya calcula), pie = saldo proyectado acumulado con semáforo.
  **Dos bloques separados y etiquetados**: *CAJA PROPIA* (liquidación de boletos por `plazo`, senebis
  por `liquidacion`, VEPs, cheques emitidos, mercados/FCI/banco-a-banco `pendiente`, recurrentes
  tipificados de `tesoreria_registros`) y *PLATA DE TERCEROS EN TRÁNSITO* (acreencias). Cada celda
  clickeable reusando `detalle_celda`, que ya está escrito. **En el pie, qué fuentes entran y cuáles
  no** — mismo criterio honesto que la app ya usa con `fechas_sin_mep` y `saldo_cargado`.
  *Correcciones de la fusión*: **el mapeo boleto→banco no existe** (la plata liquida en la cuenta del
  mercado) → la proyección arranca **por MONEDA**, y por banco solo lo que tiene banco asignado. Y
  **`acreencias` es cobro BRUTO CONTRACTUAL DEL CLIENTE**, no caja propia: entra como custodios y sale
  hacia el comitente.
- **Datos**: `acreencias` (`fecha_pago, moneda, monto`), `negocio_movimientos` (`fecha, plazo,
  importe, moneda, categoria`), `senebis` (`liquidacion, monto`), `tesoreria_veps`,
  `tesoreria_cheques` (`lado='emitido'`), `tesoreria_mercados`/`banco_a_banco` con
  `estado='pendiente'`, `mercado.dias_habiles`. **Falta**: nada de datos. **Falta la semántica**, que
  es lo difícil.
- **Qué DESBLOQUEA**: el salto de "cuánta plata tengo" a **"cuánta plata voy a necesitar"**. La mesa
  decide caución y colocación **hoy** y no a las 3 PM del jueves a la tasa que haya · se ve el
  descalce ARS/USD antes de que sea problema · desaparece la sorpresa del pago grande que solo vive en
  la cabeza de quien lo cargó · contra [T1] se puede **medir el error del forecast** y calibrarlo, que
  es lo que separa un tablero de una adivinanza · y [T7] le agrega la línea más grande y más cierta
  del día.
- **Esfuerzo**: medio (3-5 días) para la versión honesta con 4 fuentes de caja cierta; grande si se
  quiere cubrir todo.
- **Riesgo**: **una proyección incompleta es peligrosa** — si falta una fuente de egresos el tablero
  dice "sobra plata" y no sobra. Si acreencias se mezcla con caja propia, el forecast miente hacia
  arriba **de forma estructural**. La separación propia/terceros no es decisión de código: es de
  negocio, y hay que tomarla antes de escribir la primera query.

### [T7] El libro de cauciones propio — el negocio de dinero, sin un solo lector
*(fusión de `[CAUCION]` + `[B8]`)*
- **Para quién**: mesa de dinero, tesorería, back office, jefatura (costo de fondeo).
- **Problema hoy**: `aunesa_negocio.py:63::PATTERN_CAUCION` extrae **rol** (colocadora/tomadora),
  **monto**, **tasa**, **días** y **fase** (Apertura/Cierre) de cada boleto, y `categorizar():225-230`
  los clasifica en `caucion_tom_ap` / `caucion_tom_ci` / `caucion_col_ap` / `caucion_col_ci`. Y
  `core/mav_tasa.py:5-8` documenta **medido en prod** que `precio` está poblada al 100% en esas
  categorías: **la tasa de cada caución propia está en la base**. ¿Quién las consume? **Un solo
  lugar**: `api/services/comercial.py:37`, como **una línea más del volumen comercial** (grep de
  `caucion_tom|caucion_col` en todo el repo → 1 hit). El motor de PnL las excluye a propósito, la
  vista OPERACIONES las tapa y CONTRAPARTES las excluye explícitamente. Mientras tanto la HOME y el
  briefing muestran la tasa **de MERCADO** de caución. **Se ve el precio de mercado y no la posición
  propia: el fondeo de la casa es invisible en su propia plataforma.**
- **Qué proponemos**: tab **MONEY MARKET / CAUCIONES**: (a) **escalera de vencimientos** — cuánto vence
  cada día de lo tomado y lo colocado, por moneda; (b) posición viva y **tasa promedio ponderada** de
  cada lado contra la rueda del día; (c) **spread tomado−colocado = el margen real del negocio de
  dinero**; (d) top clientes colocadores y tomadores; (e) ratio de renovación; (f) serie histórica del
  stock y del spread.
- **Datos**: `negocio_movimientos` (`categoria LIKE 'caucion_%'`, `precio` = tasa, `importe` = monto,
  **`plazo` confirmado: existe, `sql/schema.sql:361`, tipo `text`**, `moneda`, `fecha`, `id_cuenta`,
  `anulado_en`, índice `ix_nm_categoria`), `caucion_snapshot` + `mercado_hist`,
  `macro.series_macro` (BADLAR/TAMAR), `dias_habiles`. **Falta**: la **fecha de vencimiento** no está
  materializada — está implícita en los `días` del texto `informacion`; se resuelve con columna
  generada (mismo truco que `dif_instrumento`) o en el job de ingesta.
  ⚠️ **SIN VERIFICAR, dos diags obligatorios antes de codear**: (1) si `precio` en una caución es TNA
  o TEA y qué formato tiene `plazo` (si trae `'24hs'`/`'7D'` hay que normalizarlo); (2) **qué % de
  aperturas encuentra su cierre** — apertura y cierre son **dos boletos distintos** y si el matching
  no cierra, el stock se cuenta doble y el tablero se desacredita en una semana.
- **Qué DESBLOQUEA**: (1) la escalera de vencimientos es **lo único que permite proyectar la caja de
  mañana** — es la pieza que le falta a [T6], y **los vencimientos de caución son el movimiento de
  caja más grande del día**: sin ellos la proyección proyecta migajas; (2) el spread da el **P&L del
  negocio de dinero**, una línea entera que hoy nadie puede citar; (3) alertar "estamos tomando 3 pp
  por encima del mercado" es plata directa; (4) detectar clientes que se financian sistemáticamente
  con nosotros a tasa vieja (oportunidad comercial nominativa); (5) cruzado con la dispersión de tasa
  MAV da el **margen financiero real**; (6) es el primer ladrillo del dominio **PLATA**, que
  `docs/TOOLS_IA.md` declara con cobertura CERO para el copiloto.
- **Esfuerzo**: medio (3-5 días). El parseo ya está hecho y probado; el grueso es materializar el
  vencimiento, parear las dos patas por comprobante y la vista.
- **Riesgo**: si `precio` resultara ser el monto y no la tasa, se cae media vista → **el diag va
  primero, sin excepción**.

### [T8] Conciliación SENEBIS ↔ boletos
- **Para quién**: back office (los que cargan), traders (los que piden), jefatura.
- **Problema hoy**: el módulo SENEBIS está **completamente aislado** — ningún service cruza
  `operaciones.senebis` con `negocio_movimientos` ni con `operaciones` (la única referencia externa en
  todo `api/services/` es `tesoreria.py:981`, que le pide prestado el autocomplete de comitentes). El
  flujo tiene un **puente manual sin retorno**: el trader carga → el back office baja el `.xlsx` → lo
  sube a Quantex/MAE **por fuera del sistema** → vuelve y marca `completada`. Ese estado significa
  *"alguien dijo que lo cargó"*, no *"existe el boleto"*. Las marcas `campos_editados` /
  `editada_completada` avisan que se editó algo, **no comparan contra la realidad**.
- **Qué proponemos**: un matcher que por cada senebi `completada` busque su boleto por
  (`concertacion`↔`fecha`, `especie`↔`ticker`, `vn`↔`cantidad`, `px`↔`precio`, `cc`↔`id_cuenta`,
  `operacion`↔`op`) con tolerancia configurable, clasificando **CONCILIADO / SIN BOLETO / DIFERENCIA**
  (diciendo qué campo difiere y por cuánto). Badge en la tab + job nocturno enganchado a
  `jobs/controles_datos.py::CONTROLES`, que ya diffea nuevo/vigente/resuelto y ya tiene panel y badge.
- **Datos**: `operaciones.senebis` × `negocio_movimientos`. **Falta**: una columna `comprobante` en
  `senebis` para pegar el match y un estado `conciliado_manual`.
- **Qué DESBLOQUEA**: (a) permite **medir la tasa de error del puente manual**, que es exactamente el
  dato que justifica —o descarta— **integrar contra Quantex**: hoy esa decisión de inversión se toma
  sin un número; (b) `completada` pasa a significar algo verificable; (c) el circo de
  `POST /proximo-id` y `reasignar-id` (que existe porque el ID se consume en Quantex fuera de banda y
  hoy lo mantiene un admin a mano) se puede derivar del boleto real; (d) es el molde exacto que
  después reusa [T9] con el banco.
- **Esfuerzo**: medio (2-5 días). El matcher es una query; lo caro es calibrar tolerancias y el flujo
  de resolución manual.
- **Riesgo**: ⚠️ **SIN VERIFICAR**: cuántos senebis idénticos hay en un mismo día (mismo ticker, misma
  cantidad, distinta contraparte) — si son frecuentes, el match 1-a-1 es ambiguo y genera falsos
  "DIFERENCIA". Si la tasa de error real es cercana a cero, el control es caro para lo que aporta —
  **pero ese resultado también es valioso: cierra la discusión sobre Quantex.**

### [T9] Conciliación banco ↔ comitente: primero el experimento — ACHICADA
- **Para quién**: tesorería, back office, administración.
- **Problema hoy**: es la conciliación más básica de cualquier back office de broker y **no existe**
  (ninguna función cruza los movimientos de Aunesa con `operaciones.movimientos`). Una transferencia
  que entró al banco y nunca se acreditó al comitente —o se acreditó dos veces— es invisible hasta que
  el cliente reclama.
- **Qué proponemos (núcleo)**: **no construir el control todavía. Construir el experimento**: una vez
  que existan [T2] y [D4], un script que corra el match por (día, importe, moneda, CUIT resuelto) y
  reporte **una sola métrica: % de match**. Sin tablero, sin canastas, sin estados manuales. Si cierra
  >90%, entonces sí vale construir el control; si cierra 40%, la conclusión es otra (falta un
  identificador común y hay que pedírselo a Aunesa o al banco).
- **Datos**: la tabla de [T2], `operaciones.movimientos` normalizada por [D4],
  `clientes.comitentes`/`cuentas` para resolver nombre→CUIT (`buscar_comitentes` ya hace ese join).
  **Falta**: [T2] y [D4], ambos previos.
- **Qué DESBLOQUEA**: cierra el circuito de **plata de terceros**, la obligación regulatoria más
  sensible de una ALyC · detecta plata parada sin aplicar (que además es oportunidad comercial: cash
  del cliente que no rinde) · y le dice a [T6] cuánto de lo que hay en el banco **todavía no es de
  nadie**.
- **Esfuerzo**: el experimento, chico (1 día) **después** de sus dos precondiciones. El control
  completo, grande.
- **Riesgo**: el nombre del ordenante casi nunca coincide con la denominación del comitente. Por eso
  el entregable de la primera etapa es **un número, no una pantalla**: si el número es malo, se ahorró
  el tablero entero.

### [T10] Quiebres de settlement (y el motor del T0, usado sin publicarlo)
*(absorbe `[T0]`)*
- **Para quién**: back office (settlement), mesa.
- **Problema hoy**: `api/services/back_office_titulos.py` **no contiene una sola referencia a
  `tenencia` ni al schema `portafolio`** (grep vacío). `get_titulos_mercado:68` resuelve bien la parte
  difícil (settlement de hoy = ops CI/Inm de hoy + ops 24hs del hábil anterior, `Venta`→enviar /
  `Compra`→recibir, agregado por ticker y comitente) y después dice "hay que entregar 10.000 VN de la
  cuenta 916" **sin saber si la cuenta 916 los tiene**. El quiebre se descubre cuando el mercado lo
  rechaza.
- **Qué proponemos**: por cada par (ticker, id_cuenta) a ENVIAR, comparar contra la tenencia T-1 **más
  las compras del propio día con liquidación igual o anterior** — ese es el motor del T0, **usado
  internamente y nunca publicado como AuM** — y marcar **DESCUBIERTO** con el faltante en nominales.
  Columna nueva en la tab Títulos/Mercado, ordenada primero por descubierto. Arranque en **modo
  silencioso**: calcular y loguear una semana, medir la tasa de falsos positivos, y recién mostrarlo.
  *(El `[T0]` original proponía publicar un AuM intradía estimado al lado del oficial; eso se recorta
  — el sistema entero está construido sobre el principio contrario, un número auditable hasta el
  boleto. Lo que vale de T0 es su motor, no su pantalla.)*
- **Datos**: `portafolio.tenencia`, `negocio_movimientos` (`cantidad, ticker, plazo, anulado_en`),
  `portafolio.assets` para el join `unidad↔ticker` (`titulos_flujos.py` ya lo normaliza). **Falta**:
  nada — salvo lo que estructuralmente no existe: tenencia intradía.
- **Qué DESBLOQUEA**: la obvia — ver el quiebre a la mañana y no a la tarde. **La valiosa: es la única
  forma de ponerle NÚMERO al proyecto de [D1].** Midiendo cuántas alertas son falsas *por culpa del
  T-1*, la conversación con Aunesa deja de ser "sería lindo tenerlo" y pasa a ser **"el T-1 nos cuesta
  N quiebres no detectados por mes"**. Y el delta intradía queda construido y validado para el día que
  el T0 llegue, o para reusarlo en el control de cupo contra la posición real.
- **Esfuerzo**: medio (2-5 días), casi todo en los casos borde del join ticker↔unidad y las carteras
  (el join `assets.unidad = operaciones.instrumento` cubre 99,0% del volumen medido, no 100%).
- **Riesgo**: si la proporción de falsos positivos es alta, el equipo aprende a ignorar la columna y
  queda peor que no tenerla. Por eso arranca silencioso **y se encuadra desde el día uno como la
  medición que justifica el T0, no como un control terminado** — así incluso el "fracaso" (muchos
  falsos por T-1) es el entregable.

### [T11] Los tres rastros de auditoría son de sólo escritura
- **Para quién**: administración, jefatura, cualquiera que tenga que investigar un incidente.
- **Problema hoy**: `operaciones.tesoreria_audit`, `senebis_audit` y `mesa_dinero_audit` aparecen
  **únicamente en sus `INSERT`** (`tesoreria.py:2285`, `senebis.py:136`, `mesa_dinero.py:56`) y en
  docstrings. **Cero endpoints, cero UI, cero scripts.** Se paga el costo de auditar en cada escritura
  y no se cobra ningún beneficio: para responder "¿quién cambió el importe de este cheque y cuándo?"
  hay que abrir Supabase — cosa que solo puede hacer el dueño del producto. La allowlist dice quién
  PUEDE escribir; **nadie revisa qué escribió**.
- **Qué proponemos**: `GET /api/back-office/auditoria?dominio=tesoreria|senebis|mesa&desde&hasta&
  actor&target&action` (union de las tres, paginado) + botón **HISTORIAL** en cada fila editable
  (cheque, VEP, registro, mercado, orden SENEBIS, op de Mesa) que abre el before/after de ESA fila,
  con el mismo modal que ya usa la auditoría de celda de BANCOS.
  *Detalle que baja el esfuerzo*: **el patrón de lectura ya existe** — `GET /api/manager/roles/audit`
  (`api/routers/manager/roles.py:50`) sirve `manager.role_audit` con el mismo shape. Es **copiar un
  endpoint, no diseñarlo**.
- **Datos**: las tres tablas tal cual están. **Falta**: nada. Ni una columna.
- **Qué DESBLOQUEA**: control de **cuatro ojos** real, que hoy no existe ni siquiera en intención ·
  investigación de incidentes en minutos en vez de un pedido de SQL al PM · una métrica operativa
  nueva y gratis: **cuántas ediciones post-alta hace cada persona por semana**, el mejor proxy de
  dónde está la fricción del proceso · y le da a [T4] el "quién y cuándo" de cada cambio posterior a
  la foto, que sin esto es un diff anónimo.
- **Esfuerzo**: chico (<1 día el endpoint copiando el de roles, +1 día la UI).
- **Riesgo**: el feed muestra emails e importes → admin/back-office, **nunca invitado (REGLA #8)**. Y
  `data` es jsonb libre: renderizar genérico, no por dominio, o se convierte en mantenimiento perpetuo.

### [T12] Bandeja de pendientes del cierre
- **Para quién**: back office, tesorería, jefatura de administración.
- **Problema hoy**: cerrar el día depende de que alguien se acuerde de mirar **6 tabs distintas**:
  bancos sin saldo inicial (`saldo_cargado: false`), senebis `pendiente` de días anteriores, cheques
  emitidos con `fecha_pago` vencida sin `completado`, VEPs con `vencido: true` (el backend ya lo
  calcula en `_fila_vep`), mercados/FCI y banco-a-banco `pendiente` arrastrados, la foto no tomada, y
  los movimientos destildados por falta de hora que nadie revisó. Y
  `grep -riE "smtplib|send_mail|sendgrid|slack|telegram|push_notif"` sobre todo el backend → **cero**:
  **no existe ningún canal de aviso**, todo es "abrí la vista y fijate".
- **Qué proponemos**: `GET /api/back-office/pendientes` → **una** checklist de lo que impide cerrar el
  día, con contador por ítem y **badge numérico en la entrada BACK OFFICE del nav** (copiando el badge
  que Manager → CONTROLES ya tiene). Cada ítem linkea a la tab con el filtro puesto (la navegación
  asistida del copiloto ya sabe abrir vistas con filtros vía sessionStorage — se reusa tal cual).
- **Datos**: todo ya calculado dentro de `ingresos_egresos_dia`, `veps`, `cheques`, `mercados`,
  `banco_a_banco`, `listar_snapshots`, `senebis.listar_ops`. **Falta**: nada de datos; es un agregador.
- **Qué DESBLOQUEA**: convierte el back office de "acordate" a **checklist cerrable** — y esa lista es
  la **precondición de cualquier canal de notificación futuro**: hoy no se puede mandar un mail de
  cierre porque no existe QUÉ mandar. Además da la primera métrica de calidad operativa de la casa:
  **cuántos días se cierran con pendientes**, y cuáles se repiten (input para arreglar el proceso, no
  para perseguir a la persona).
- **Esfuerzo**: chico-medio (1-3 días). Las queries existen como fragmentos; el trabajo es el
  agregador y el badge.
- **Riesgo**: el clásico de las bandejas — si nunca llega a cero se vuelve empapelado. **Solo lo
  bloqueante del cierre**, con criterio explícito. Si el primer día tira 40 ítems, el criterio está
  mal y hay que recortarlo, no explicarlo.

### [T13] SENEBIS: tasa de retrabajo y qué campo se corrige — ACHICADA
*(fusión de `[SEN]` + `[SLA-BO]`)*
- **Para quién**: back office, jefatura de operaciones, mesa (que espera la carga).
- **Problema hoy**: `operaciones.senebis` tiene `creado_at`/`creado_por`,
  `completada_at`/`completada_por`, `campos_editados text[]` y `editada_completada`; y
  `grep -nE "percentile|AVG\(|GROUP BY" api/services/senebis.py` → **0**: sus ~50 funciones son ABM,
  export a Excel, allowlist y presencia; el router tiene 13 rutas y **ninguna agrega nada**. El
  proceso se opera, no se mide. `campos_editados` se construyó **para pintar asteriscos y filas
  amarillas** (reemplazar un resaltador de una planilla) y nadie lo agregó nunca.
- **Qué proponemos (núcleo)**: bloque chico con (1) **tasa de retrabajo** = `editada_completada` /
  total, mensual; (2) **ranking de campos editados** (`unnest` sobre `campos_editados`); (3) cola de
  pendientes por antigüedad (cuántas llevan >2h), que es lo único accionable en el momento; (4)
  **volumen bilateral por agente/contraparte** — los senebis **no entran a la vista CONTRAPARTES**,
  así que "con quién operamos bilateral" es hoy una pregunta sin respuesta en la app.
  **Lo que sobra**: el tiempo de ciclo como KPI (si el back office marca en lote al final del día,
  mide "hasta que tildó", no "hasta que cargó en Quantex" — como métrica de personas es engañosa) y
  la desagregación **por persona**, y replicar el tablero a Mesa y Tesorería vía sus `*_audit`.
- **Datos**: `operaciones.senebis`, `senebis_audit`, `senebis_agentes`, `clientes.contrapartes`.
  **Falta**: nada.
- **Qué DESBLOQUEA**: (a) el ranking de campos editados es una **especificación de producto gratis**:
  el campo que más se corrige es el que hay que validar o autocompletar en el alta — hoy se validan
  las derivaciones (monto, plazo↔liquidación) y **nadie sabe cuáles son los errores reales**;
  (b) **prioriza el roadmap de integraciones con datos**: si el 30% de las órdenes son `cargan_ellos`
  o `es_mae`, el esfuerzo va al Excel MAE y no al de Quantex — decisión que hoy se toma por impresión;
  (c) cierra el hueco de CONTRAPARTES (un canal de negocio entero fuera del mapa); (d) el mismo patrón
  sobre `ordenes_audit` (`SEND_REQUEST`/`SEND_OK`/`SEND_ERROR`) da la **tasa de rechazo del broker**,
  que tampoco existe y **sí** es métrica de riesgo operativo.
- **Esfuerzo**: chico (1-2 días). `percentile_cont` y un `unnest` sobre columnas que ya están.
- **Riesgo**: mide un proceso que hace un equipo chico, así que la desagregación por persona es
  inevitable aunque no se muestre — si se lee como control de productividad, el equipo empieza a
  "completar" órdenes para bajar el número y **la métrica se destruye sola**. Arrancar por proceso y
  por tipo de operación, **nunca por usuario**. No es métrica de personas: es métrica del formulario.

### [T14] Auditoría del override de precio del libro propio — ACHICADA
- **Para quién**: back office, jefatura (sobre el libro PROPIO de la casa).
- **Problema hoy**: dos cosas concretas. (1) `actualizar_precio_posicion` (`tenencia_hd.py:605`)
  **no tiene actor, ni auditoría, ni allowlist** — mientras Tesorería, SENEBIS y Mesa de Dinero tienen
  las tres; **cualquier rol con el módulo `back-office` (que por default incluye `sales`) puede
  reescribir la valuación del libro propio y no queda rastro**. (2) Consecuencia que no observó nadie:
  después de una edición, **la misma `unidad` en la misma `fecha` tiene un `precio` distinto para las
  cuentas 100/255/256 que para todas las cuentas de clientes** — una inconsistencia dentro de una sola
  tabla, hoy invisible.
- **Qué proponemos (núcleo)**: (1) `actualizar_precio_posicion` recibe `actor` + `motivo`, escribe
  before/after en una tabla de auditoría (patrón copiado tal cual de Tesorería) y se gatea con
  allowlist propia; (2) un control en `jobs/controles_datos.py::CONTROLES` que liste, por día, las
  `unidad` donde el precio de las cuentas propias difiere del de las de clientes.
  **Lo que sobra**: la columna `precio_fuente` y todo el encuadre "gobierno de precios / IPV" — el
  backfill histórico es imposible y **el alcance es de 3 cuentas**, no del libro de clientes. Y la
  dependencia que la propuesta original afirmaba ("B7 antes que B1") **no existe**: [C13] no está
  bloqueada por esto.
- **Datos**: `portafolio.tenencia`, `snapshots_cierre`/`_hist` como referencia,
  `jobs/controles_datos.py`. **Falta**: la tabla de auditoría + la allowlist. Cero fuentes externas.
- **Qué DESBLOQUEA**: el P&L del libro propio deja de ser editable sin rastro (es la plata de la casa,
  y [T7] y [M7] cuelgan de ahí) · el control de divergencia cuenta cuántos overrides hay por mes y
  sobre qué instrumentos, que casi siempre apunta a un **problema de fondo en el maestro de títulos**.
- **Esfuerzo**: chico (<1 día) el actor+auditoría+allowlist; +1 día el control de divergencia.
- **Riesgo**: fricción para el back office, que hoy corrige un precio en 5 segundos. Es fricción
  deseada, pero hay que venderla como tal — y **en el libro propio, no en el de clientes**.

---

## 7. Oportunidades por dominio — DATOS E INFRAESTRUCTURA

### [D1] Tenencia T0 = snapshot T-1 + boletos de hoy
- **Para quién**: comerciales (`/operadores`, `/valuaciones`), mesa, back office (Tenencia
  Valorizada), jefatura (`/aum`).
- **Problema hoy**: `jobs/portafolio_backfill.py` (docstring, líneas 5-8): *"para la tenencia AL día D
  se consulta Aunesa con `desde = D + 1 día hábil` (corrimiento confirmado: desde=X devuelve X-1)"*;
  el modo `--diario` (cron `0 11 * * 1-5`) snapshotea el día hábil **ANTERIOR**. El endpoint de debug
  lo re-confirma con el nombre de la regla (`manager/aunesa.py:63-66`: *"Por la regla H1, `desde=X`
  devuelve la posición del día hábil ANTERIOR a X"*). **Todo lo que cuelga de `portafolio.tenencia` es
  T-1**: `/aum`, `/valuaciones`, el PnL, `operaciones.acreencias`, el "AuM gestionado" del Tablero
  Comercial y la Tenencia Valorizada.
- **Qué proponemos**: capa `tenencia_live` = último snapshot **+ Δ de `negocio_movimientos` con
  `fecha = hoy`**, valuada con `valuaciones.portfolio_snapshot.last_price`. **Alcance honesto: TÍTULOS
  únicamente.** El cash (`cartera='MONEDAS'`) queda explícitamente fuera y sigue T-1 — los
  depósitos/extracciones viven en `operaciones.movimientos`, que se ingiere 1×/día y encima con
  `fecha` como **texto dd/mm/yyyy sin índice**: el T0 de efectivo sería peor que el T-1. **Dos números
  con badge** en toda pantalla que hoy dice AuM: `T-1 conciliado` / `T0 estimado (títulos)`.
- **Datos**: `portafolio.tenencia`, `negocio_movimientos` (índice `ix_nm_id_cuenta (id_cuenta, fecha)`
  ya existe), `valuaciones.portfolio_snapshot`, `portafolio.assets`. **Infra de precios ya apuntando
  exactamente acá**: `engines/_universo_portfolio.py::tickers_de_tenencia()` ya suscribe "unidades con
  tenencia + tickers operados hoy en `negocio_movimientos`". **No falta nada para títulos.**
- **Qué DESBLOQUEA**: (a) `/aum` y la ficha del cliente **dejan de mentir un día entero**; (b) el
  comercial abre la cartera con la operación de la mañana adentro; (c) **conciliación automática
  T0-estimado vs T-1-real del día siguiente** = un control de calidad de la ingesta de Aunesa que hoy
  NO existe y que habría cazado el incidente del 2026-08-07 (1868 cuentas con `error_http_500`,
  tablero en verde **dos días**, `portafolio_backfill.py:302-309`); (d) es el numerador vivo de [C14]
  y de [M6]; (e) alertas de concentración/exposición intradía por cliente y para toda la casa.
- **Esfuerzo**: medio (2-5 días). Sin infra nueva: un service sobre 3 tablas que ya se leen + el
  badge. **Precondición barata: correr primero [D2]** — si Aunesa acepta `desde` = próximo hábil, el
  camino es más corto y este trabajo cambia de forma.
- **Riesgo**: el estimado nunca cierra exacto (liquidación T+1, cuota FCI del día siguiente, garantías
  que `aunesa_negocio.EXCLUIR_SUBSTRINGS` filtra a propósito). **Si alguien lo usa para facturar, es
  peor el remedio**: *T0 para decidir, T-1 para reportar*, y la UI tiene que gritarlo.

### [D2] Diag: ¿Aunesa acepta `desde` = próximo día hábil? (+ reparación on-demand) — ACHICADA
- **Para quién**: quien decide [D1]; back office para la segunda mitad.
- **Problema hoy**: `GET /api/manager/aunesa/posicion?id_cuenta&desde` ya existe
  (`manager/aunesa.py:59`), pega LIVE a `posicionValuada` y **no escribe nada**, enterrado en una
  sub-pill de Manager que solo ve admin. Su docstring fija la regla H1 → para HOY hay que mandar el
  **próximo hábil**, una fecha futura, y **nadie probó si Aunesa la acepta**.
- **Qué proponemos (núcleo)**: **el diag, no la feature**. Un script de 20 líneas cuyo resultado
  **cambia la forma de [D1] entero** (si acepta: camino exacto, sin estimar; si no: [D1] como está).
  Y como segunda pieza, la **reparación on-demand de cuentas en `timeout`**, que hoy obliga a un
  `python -m` en el Droplet (existe `jobs/portafolio_reparar_timeouts.py` justamente porque cada
  corrida deja cuentas rotas): acción **asíncrona con estado**, solo back office, escribiendo con
  `backfill_log status='on_demand'` y reusando `_parse()`/`_write_date()`, que ya son puras.
  **Lo que sobra**: el "botón REFRESCAR POSICIÓN para 20 comerciales" — verificado que es peligroso:
  `portafolio_backfill.py:52-55` tiene `HEAVY_IDS` con `TIMEOUT_HEAVY=240`, **una llamada tarda hasta
  4 minutos**; como request HTTP sincrónico tumba el pool, y 20 comerciales apretando en rueda es un
  DoS contra Aunesa.
- **Esfuerzo**: el diag, **<1 hora**. La reparación asíncrona, chico (<1 día).
- **Qué DESBLOQUEA**: decidir [D1] con un hecho en vez de una hipótesis, y sacar una tarea recurrente
  de la consola del Droplet (REGLA #0).
- **Riesgo**: ninguno el diag (read-only).

### [D3] Bandeja de NOVEDADES: los detectores escriben a un buzón ciego
*(fusión de `[F8]` + `[NOVEDADES]`; absorbe `/api/manager/status` de `[HUERFANOS]`)*
- **Para quién**: admin/PM como dueño de la plataforma; back office y mesa como destinatarios. Es el
  **prerrequisito de confianza de casi todo el resto de esta lista**.
- **Problema hoy**: cinco pipelines producen hallazgos y **solo dos tienen dónde mostrarse**
  (`controles_datos` → tab CONTROLES; el VIGÍA → toasts en `/trading`).
  `grep -rn "triage_incidentes|calidad_flags" api/` → **0 lecturas**: `triage` (LLM tier **pro**, cada
  10 minutos), `ia_calidad` (LLM diario) y `guardrails` escriben a tablas o a stdout **sin lector**. Y
  el caso más grave: `config.py:164-168` tiene los **TRES umbrales de guardrails en `None`** con el
  comentario *"None = SIN CALIBRAR: el check corre igual pero JAMÁS marca violación"*, y
  `jobs/guardrails.py:234` imprime *"Checks SIN calibrar"* en un log que nadie lee — **el detector
  post-cierre está apagado y solo lo dice un `print`**. `/api/manager/status` (semáforo de frescura)
  figura en el MAPA como **sin consumidor**. Desde el decomiso de Telegram (2026-07-25) **no existe
  ningún canal de notificación**. Caso testigo escrito en el código: el 2026-08-07 las 1868 cuentas
  devolvieron `error_http_500` y **el tablero siguió en verde dos días**.
- **Qué proponemos**: UNA tabla `manager.novedades` (`origen, clave, severidad, titulo, detalle jsonb,
  first_seen, last_seen, resuelto_at, visto_por`) con el patrón de diff/dedup que
  `controles_datos._diff_y_persistir` **ya tiene resuelto**, un `GET /api/manager/novedades` y una
  **campanita con badge en el header** — no una tab adentro de Manager: si hay que ir a buscarlo, no
  se lee. Los cinco emisores escriben ahí; `/api/manager/status` se cablea como fila de cabecera
  (frescura por fuente) en vez de borrarse. Más: **calibrar los 3 umbrales de guardrails** con la
  distribución histórica que ya está en la base (`snapshots_cierre_hist` para saltos de precio,
  `tenencia` para el Δ de AuM) — un diag read-only y tres números en `config.py`. Y un bloque **SALUD
  DE LA DATA DE HOY** en el modal de BRIEFING, que **ya se auto-abre a las 10:00 ART para todos y ya
  es 100% determinista (0 tokens)**: 4 semáforos (tenencia, boletos, motores, tesorería) con "atrasado
  desde cuándo".
  **La decisión honesta que va pegada**: si no se construye el lector, **se apaga el cron de `triage`**
  — hoy es gasto de tokens tier `pro` cada 10 minutos contra un buzón ciego.
- **Datos**: `ia.triage_incidentes`, `ia.calidad_flags`, `manager.controles_datos`, `manager.job_runs`,
  `/api/manager/status`, `snapshots_cierre_hist`, `portafolio.tenencia`, `config.GUARDRAILS_UMBRALES`,
  `api/services/briefing.py` — **todo poblado**. **Falta**: la tabla unificada, el endpoint y la
  campanita. Es **cableado de piezas ya construidas**.
- **Qué DESBLOQUEA**: es **la superficie de entrega que no existe**, y sin ella cualquier detector
  nuevo nace huérfano — por eso [M5] (check post-trade), [C2] (outliers de arancel), [M17] (límite de
  DV01), [T13] (SLA) y la completitud de [C3] bajan de "proyecto con UI" a **"emitir una fila"**.
  Además: que ninguna decisión se tome sobre un número podrido — un AuM T0 ([D1]) sin watchdog es más
  peligroso que un T-1 confiable, y el arrastre de saldos ([T1]) **compone** errores si nadie mira.
  Y con los umbrales calibrados, `guardrails` deja de ser decorativo y pasa a ser el **test de
  regresión de datos que cualquier backfill nuevo tiene que pasar** (REGLA #4) — hoy un backfill mal
  scopeado no dispara nada. Cierra también el loop que `scripts/gen_evals_desde_flags.py` dejó a
  medias.
- **Esfuerzo**: medio (2-5 días): tabla + writer compartido + endpoint + campanita + migrar los 5
  emisores. El bloque del briefing y la calibración, chico.
- **Riesgo**: **ruido**. Una bandeja que se llena se ignora igual que un log; si pinta rojo todos los
  días por un feed medio atrasado, en dos semanas nadie la mira. Obligatorio: severidad,
  auto-resolución por diff, y que el badge cuente **solo severidad alta no vista**. Arrancar con 4
  señales y no más. **Y no inventar un canal de notificación** — el briefing y la campanita son el
  vehículo porque ya existen; abrir mail/push es otro proyecto. Si no hay quien sostenga la curaduría,
  la alternativa correcta es **apagar `triage` e `ia_calidad`**, no construir el buzón.

### [D4] Normalizar `operaciones.movimientos`
*(extraída por la crítica: `[NNM]`, `[CON-2]` y `[B5]` chocan las tres contra el MISMO muro)*
- **Para quién**: nadie directamente. Es **plomería** — y es el cuello de botella del dominio "PLATA",
  que `docs/QUANTAI.md` declara en **cobertura CERO**.
- **Problema hoy**: es la tabla más pobre del modelo:
  `comprobante text PK · cuenta text ("[N] NOMBRE") · fecha text (dd/mm/yyyy CRUDO) · informacion text
  · total numeric · unidad text · data jsonb`. **Sin `id_cuenta`, sin fecha tipada, sin ningún índice,
  sin clasificación del tipo de movimiento.** Consecuencias verificadas:
  `cashflow_sql.py::listar_flujos:54-70` **resuelve el rango de fechas y el orden en Python** porque no
  puede hacerlo en SQL; el scope por usuario se aplica con un **regex sobre el string bracketed**; y
  `docs/MAPA_APP.md` §4.7 ya documenta el join por regex como trampa conocida.
- **Qué proponemos**: tres columnas y un índice. `id_cuenta text` (extraída del bracket,
  materializada), `fecha_iso date` (parseada, indexada) y `tipo text` (deposito / extraccion /
  transferencia_interna / otro, derivada de `informacion` con reglas deterministas + un catálogo de
  patrones **editable**, mismo espíritu que `operaciones.tipos_operacion`). Backfill idempotente y
  batcheado (REGLA #4). Se puede entregar en dos etapas: id+fecha primero, `tipo` después.
- **Datos**: `operaciones.movimientos` + `clientes.cuentas` para validar el `id_cuenta` extraído.
  **Falta**: las 3 columnas. El `tipo` es lo único que requiere criterio — y lo requiere igual
  cualquiera de las tres propuestas que dependen de esto.
- **Qué DESBLOQUEA**: **una tabla desbloquea tres propuestas y un dominio entero del roadmap de IA.**
  Es la llave de la pata de **cash** de [C3] (sin distinguir transferencia interna de plata que entra
  a la ALyC, la captación neta nace inflada y pierde credibilidad en la primera reunión), de [T9]
  (conciliar contra el banco sin un id de cuenta es imposible) y de 4 de las 5 reglas de [C14] (hoy
  cada regla sería un scan completo con parseo en Python). Además arregla la performance de la tab
  DEPÓSITOS & EXTRACCIONES y **le permite al asistente de negocio entrar por primera vez al dominio
  PLATA**. Y libera la tabla del encapsulamiento en una sola pantalla — hoy el formato de fecha es lo
  único que la mantiene ahí.
- **Esfuerzo**: chico-medio (1-3 días) — 2 columnas triviales, la tercera es donde está el trabajo.
- **Riesgo**: el `tipo` derivado de texto libre nunca va a ser perfecto → tiene que dejar una
  categoría `otro` **explícita y contable**, y ninguna métrica puede asumir que la clasificación es
  completa. **Medir el % clasificado ANTES de que algo cuelgue de él.**

### [D5] Dimensiones congeladas en `ops_agregado_diario` (no un warehouse) — ACHICADA
- **Para quién**: transversal — es el habilitador de [C2] y [C4].
- **Problema hoy**: `operaciones.ops_agregado_diario` (`sql/schema.sql:316-324`) tiene PK
  `(fecha, moneda_calc)` y tres medidas — **cero dimensiones**; con cualquier filtro la serie va 100%
  en vivo. Cualquier pregunta cruzada ("arancel por producto por comercial por mes") se resuelve
  escaneando `operaciones.operaciones`, y el schema está lleno de índices parciales puestos a mano para
  tapar el problema (`ix_ops_arancel_concert`, `ix_ops_dlr_concert`, `ix_ops_commodity_concert`…),
  varios documentados como respuesta a incidentes de performance medidos. **Preguntas nuevas son caras,
  y por eso no se hacen.** Y `comercial_sql` lee `operador_email` **en vivo**: reasignar una cuenta
  **reescribe la historia de los dos comerciales**.
- **Qué proponemos (núcleo)**: **extender la PK del agregado que YA existe y ya tiene el mecanismo
  correcto** — `jobs/ops_agregado.py` recomputa por **día sucio** vía `ingestado_en`, es idempotente y
  sobrevive a backfills: **ese es el activo, no la tabla**. PK
  `(fecha, moneda_calc, id_cuenta, nivel_3)` con `operador_email` y `nivel_1` **desnormalizados al
  momento del cálculo**, medidas `bruto, arancel, n_boletos`. Migrar **UNA** vista a leerlo (la serie
  de `/ops/aranceles`) y comparar contra el cálculo en vivo antes de migrar la segunda.
  **Lo que sobra**: un schema `analitica` nuevo, dos fact tables, 2-3 semanas y un job de
  reconciliación permanente — el propio texto original admite el riesgo de "dos verdades" y el
  precedente (`ops_rollup` drifteaba). Proponer un warehouse a un equipo de un PM es la clase de
  proyecto que se abandona a mitad y deja dos números distintos para lo mismo.
- **Datos**: `operaciones.operaciones`, `clientes.comitentes`. **Falta**: nada de datos; el backfill
  del agregado con el grano nuevo (que el job ya sabe hacer por día).
- **Qué DESBLOQUEA**: (a) **resuelve el point-in-time**, el bug silencioso más caro del stack
  comercial: con las dimensiones congeladas, el número de marzo sigue siendo el de marzo; (b) [C2] y
  [C4] pasan de "proyecto cada uno" a **"un `GROUP BY` cada uno"**; (c) series largas sin miedo (hoy
  `/ops/aranceles` se acota a ~18 meses y el botón ALL dispara `serie_full` en vivo); (d) export a
  Excel/BI sin pegarle a la transaccional.
- **Esfuerzo**: medio (3-5 días) en vez de 2-3 semanas, precisamente porque **no se construye nada
  nuevo**.
- **Riesgo**: el grano por cuenta multiplica las filas — **medirlo antes** (REGLA #2 y #4: contar
  cuántas combinaciones `(fecha, cuenta, nivel_3)` hay por día). Y la regla de entrada sigue valiendo:
  **ninguna vista lee el grano nuevo hasta que un check compruebe que coincide al centavo con el
  cálculo en vivo** — el precedente de `ops_rollup` está en el propio schema.
  ⚠️ **Solapa con [C5]**: dos caminos al mismo problema point-in-time. Ver §9-F.

### [D6] `valuaciones.consolidado`: la vista aparenta un dato vivo que tiene 2 días — ACHICADA
- **Para quién**: comerciales y jefatura que leen rendimiento por cuenta.
- **Problema hoy**: `jobs/consolidado_cuentas.py` corre `30 12 * * 1-5` — **una vez por día y sobre la
  tenencia T-1 escrita a las 11:00**, o sea T-1/T-2; el MAPA lo confirma (*"TOTALES y consolidado NO
  recalculan en vivo"*). La columna `computed_at` **ya existe en la tabla y no se devuelve**, así que
  la UI muestra rendimiento sin decir de cuándo es. El contraste está al lado: `pnl_totales_cache` se
  recalcula cada 30' con `use_job_pool()`.
- **Qué proponemos (núcleo)**: **exponer `computed_at`** y evaluar una segunda corrida post-cierre.
  **Lo que sobra**: el "ranking de mejores y peores carteras del mes" y la "alerta de rendimiento
  negativo N meses" — son features comerciales propias, no consecuencias de mover un cron.
- **Esfuerzo**: chico (<1 día): devolver un campo + una línea de cron **si la medición lo permite**.
- **Riesgo**: la corrida post-cierre es **condicional**: `construir_consolidado()` recorre TODAS las
  cuentas llamando `valuacion_mensual`, y la ventana post-cierre ya tiene `snapshot_cierre`,
  `fair_value`, `forwards_zscore`, `cierre_canje`, `options_rollup` y `guardrails`. **Medir cuánto
  tarda antes** (REGLA #2 + #4); si son >20 minutos, no va ahí — meter un job pesado en esa ventana es
  el anti-patrón del incidente 2026-06-03.

### [D7] Commit de limpieza de endpoints huérfanos — ACHICADA
- **Para quién**: quien mantiene el repo y la matriz RBAC.
- **Problema hoy**: hay ~10 endpoints montados sin ningún consumidor en el front. **Borrarlos no es
  una oportunidad, es una tarea**: la REGLA #5 ya obliga. Lo que sí era producto ya está repartido:
  `/api/operar/brackets/dia` → [M5]; `/api/manager/status` → [D3]; `/api/estrategia/*` → [M1];
  `/api/scanner/day-trading` y `/companeros/{ticker}` → **NO borrar**, son el backing exacto de dos
  tools ya priorizadas (`lab_intradia`, `papeles_correlacionados`) y borrarlas antes de cablear las
  tools **destruye trabajo, no deuda**.
- **Qué proponemos**: un commit que borre `/api/market/candle`, `/profile`, `/scanner/cedears/*`,
  `/trading/renta-fija`, `/risk/account/positions`, `/portfolio/aum`, `/titulos/assets`,
  `/valuaciones/{id}/posiciones` (+ su proxy Next huérfano), `/checks/debug-comercial`,
  `/checks/futuros-dlr`, `PATCH /derivados/agro/pizarra`, `PATCH /manager/ons/sector`. **Salvedad
  verificada**: `debug_comercial` (`comercial_sql.py:999-1049`) es hoy la **ÚNICA función que arma
  volumen Y arancel por cuenta en una tabla** — antes de borrarla, [C2] la necesita como referencia de
  cálculo. Y **corregir el MAPA**: `valuacion_mensual_debug` **está expuesto y con UI**
  (`manager/valuaciones.py:29` + `manager-debug-xirr.tsx:118`) — `docs/MAPA_APP.md:1403,2382` mienten.
  Un doc de deuda que se equivoca deja de leerse.
- **Esfuerzo**: chico (<1 día).
- **Qué DESBLOQUEA**: cada endpoint borrado es una ruta menos que puede quedar del lado equivocado del
  portal invitado (REGLA #8) y una menos que auditar en una matriz RBAC **cuyo tooling está ciego**
  (`scripts/audit_rbac.py` ve 5 rutas de 423 por la trampa de `_IncludedRouter`).
- **Riesgo**: bajo. ⚠️ **SIN VERIFICAR**: si el script local de la PC de oficina (feed del dólar MAE)
  consume alguno — se pregunta antes de borrar.

### [D8] `cuentas_visibles` fail-closed por clase de identidad — ACHICADA
- **Para quién**: quien mantiene la seguridad del producto. **Hoy — no el día del portal.**
- **Problema hoy**: `core/grupos.py::cuentas_visibles:48-56` es **fail-open a propósito**: el docstring
  dice *"Ante error de DB devuelve `None` (fail-open) — los grupos no deben tumbar la app; el peor caso
  es 've de más'"*. Eso es defendible para empleados (el peor caso es que un comercial vea la cartera
  de otro comercial) y **es inaceptable para cualquier identidad que no sea empleado**: ahí "ve de más"
  significa que un cliente ve la cartera de otro cliente. Además **"sin grupo" hoy significa ve todo**,
  el default opuesto al que necesita un tercero.
- **Qué proponemos (núcleo)**: (1) que `cuentas_visibles` reciba la clase de identidad y sea
  **fail-closed** (y "sin grupo" = `set()`, no `None`) para todo lo que no sea empleado; (2) un test
  que congele la invariante, igual que los que ya congelan "invitado nunca tiene `ia`".
  **Lo que sobra**: el **portal del cliente** queda diferido detrás de [C13] — la propia propuesta
  admitía que era *"el mayor riesgo de seguridad de toda la lista"* y que su valor queda cubierto por
  el estado de cuenta **al 5% del riesgo**.
- **Datos**: `core/grupos.py`, `manager.grupos`, los `verificar_id_cuenta` ya aplicados en
  `/api/portfolio/*` y `/api/valuaciones/*`. **Falta**: nada para la parte que se hace ahora.
- **Qué DESBLOQUEA**: deja la puerta cerrada **ANTES** de que exista algo del otro lado — el único
  orden que sirve. El día que llegue cualquier identidad externa (portal, auditor, una cooperativa
  mirando sus propios clientes vía [C6]), la invariante ya está y no hay que auditar 40 endpoints con
  apuro.
- **Esfuerzo**: chico (<1 día) la inversión + el test.
- **Riesgo**: invertir el default puede romper el acceso de un usuario legítimo sin grupo asignado →
  por eso el cambio es **por clase de identidad**, no global: los empleados siguen con el fail-open
  que el comentario justifica.

---

## 8. Oportunidades por dominio — IA

> El programa de IA tiene su doc vivo (`docs/QUANTAI.md`) con lo **ya descartado**. Nada de acá
> re-propone algo cerrado ahí: Telegram (decomisado 2026-07-25), P4 prep de reuniones (descartado
> 2026-07-13) y el TC automático de Mesa (2026-07-29) **no se re-proponen** — y el `[EXTRACTO]` que
> intentaba reabrir P4 con otra ropa está en la tabla de descartadas.

### [I1] La aduana de PII está apagada en las 9 vistas de mercado del copiloto
- **Para quién**: toda la mesa (trader, sales, asistente_comercial) + el admin como dueño del riesgo.
- **Problema hoy**: `api/services/copiloto/registro.py` tiene **`"aduana": True` en UNA sola entrada**
  (línea 103, vista `ayuda`) y `motor.py:113` solo tokeniza `if cfg.get("aduana")`. Las 9 vistas de
  mercado (`home`, `renta_variable`, `renta_fija`, `trading`, `research`, `reuters`, `agro`,
  `derivados`, `ons`) mandan la pregunta del usuario **tal cual** al proveedor default: un trader que
  en `/renta-fija` escribe *"¿le armo carry a Fulanez con los 500M que tiene en TX26?"* manda ese
  nombre afuera. Y `motor.py:227` escribe `detalle=pregunta_llm` en `ia.trazas` — **la pregunta cruda**
  cuando la vista no tiene aduana, así que el nombre queda además en nuestra propia tabla y llega al
  panel de OBSERVABILIDAD. El comentario que justifica la aduana en `ayuda` (`registro.py:98-102`)
  escribe el argumento entero.
  **Objeción considerada y descartada**: `docs/QUANTAI.md:64` dice *"las features de mercado no
  necesitan aduana (datos públicos)"* — eso habla de **los datos que la vista trae**, no de **lo que
  el usuario tipea**, y la misma decisión declara dos líneas antes *"ninguna persona física cruza el
  perímetro"* como innegociable. Es una **inconsistencia de política**, no una re-propuesta.
- **Qué proponemos**: `aduana: True` en las 9 vistas (una línea por vista, el camino está probado en
  producción), **con el fallback que hoy falta**: la aduana es fail-closed (`motor.py:118-124` corta
  la respuesta si falla) y el copiloto de mercado lo usa también el **portal invitado** → hay que
  degradar a enmascarado defensivo sin catálogo, o saltear la carga del catálogo para identidades
  `guest:*`. Segundo paso, independiente y aún más barato: que `_trazar()` guarde `detalle`
  **siempre** tokenizado.
- **Datos**: `core/pii_gateway.py` (ya construido: 3 capas, fichas estables,
  `manager.asistente_mappings` con TTL 48h), `registro.py`, `motor.py`, `ia.trazas.detalle`.
  **No falta ningún dato.**
- **Qué DESBLOQUEA**: hoy **negocio y mercado son dos mundos separados por el perímetro** — el
  asistente de negocio corre en un proveedor caro con no-retención (`core/ai.py:99`, única tarea con
  `proveedor: openai`) y el copiloto de mercado en el barato. Con la aduana pareja en las 11 vistas se
  puede **abrir el copiloto de mercado a preguntas mixtas (papel + cliente)** sin cambiar de proveedor
  y sin duplicar tools — **que es la razón estructural por la que hoy son dos productos**. Y baja el
  piso para exponer IA en el portal invitado, hoy explícitamente fuera de alcance.
- **Esfuerzo**: chico (<1 día) las 9 vistas + la traza; medio si el caso invitado se resuelve bien.
- **Riesgo**: **no es flip-and-forget.** La aduana tacha lo que *parece* nombre y ya rompió cosas tres
  veces (v1.86 tachaba el vocabulario del negocio, v1.94 rompía nuestras propias etiquetas, v1.96 fue
  un leak introducido por v1.94). En vistas de mercado va a tachar **emisores y tickers** si no se
  amplía `_vocabulario_protegido` (`docs/TOOLS_IA.md` §6 ya lo marca como riesgo "Inverso"). Hay que
  correr las baterías de las 9 vistas después; **si eso no se hace, el cambio empeora el producto sin
  mejorar el perímetro**.

> **Las otras dos piezas de IA de esta lista viven en sus dominios porque ahí está su valor:**
> **[D3]** es el lector que le falta a `triage` (tier `pro`, cada 10') y a `ia_calidad` — sin él, la
> decisión correcta es apagarlos. **[M1]** mete el único modelo propio de la casa en el contexto del
> copiloto de `trading`, la vista más cara del sistema, que hoy no lo ve.

---

## 9. DEPENDENCIAS — qué destraba a qué

Esta es la sección que importa. El patrón que se busca no es "agregar algo": es **destrabar una
limitación y ver qué se abre**. Encadenamientos, de mayor a menor alcance:

- **[D2] diag Aunesa (<1h)** → decide la forma de **[D1] Tenencia T0**
  - **[D1] T0** →
    - `/aum`, ficha del cliente y Tablero Comercial dejan de mentir un día entero
    - **conciliación T0-estimado vs T-1-real** = control de calidad de la ingesta que hoy no existe
    - numerador vivo de **[C14]** (cupo) y de **[M6]** (MTM de futuros)
    - alertas de concentración/exposición **intradía**, hoy imposibles
  - **[T10] quiebres de settlement** construye el **motor** del delta intradía sin publicarlo, y es
    **lo único que le pone número al costo del T-1** → retro-alimenta la decisión de [D1]
  - **[D3]** es su watchdog: un T0 estimado sin vigilancia es más peligroso que un T-1 confiable

- **[D4] normalizar `operaciones.movimientos`** (3 columnas y un índice) →
  - **[C3]** pata de **cash** (sin distinguir transferencia interna, la captación nace inflada)
  - **[T9]** conciliación banco↔comitente (sin `id_cuenta` es imposible)
  - **4 de las 5 reglas de [C14]** (hoy cada regla sería un scan completo con parseo en Python)
  - el dominio **PLATA** del asistente, hoy en cobertura CERO
  - de yapa: performance de la tab DEPÓSITOS & EXTRACCIONES

- **[D3] bandeja de NOVEDADES** (la superficie de entrega que no existe) →
  - da lector a **`triage`** (tier pro cada 10'), **`ia_calidad`** y **`guardrails`** — sin ella, lo
    correcto es **apagarlos**
  - convierte en "una fila más" (y no en un proyecto con UI) a: **[M5]** check post-trade · **[C2]**
    outliers de arancel · **[M17]** límite de DV01 · **[T13]** SLA de senebis · **[C3]** guardrail de
    completitud del AuM · **[M14]** alertas de mercado
  - con los umbrales calibrados, `guardrails` pasa a ser el **test de regresión de datos** que todo
    backfill nuevo tiene que pasar (REGLA #4)

- **[M9] archivar el tape de bonos** (ventana de 7 días, ⚠️ ver contradicción A) →
  - **[M8] TCA sobre renta fija** (sin archivo solo se pueden medir CEDEARs) →
    - **calibra los umbrales de [M5]** (sin saber cuánto cuesta una mala ejecución no hay argumento
      para elegir el valor de un límite) →
      - **[M5]** permite **sacar `operar` de admin-only** →
        - **[M12]** ejecución MEP multi-bono · **[M14]** alertas con orden precargada · subir el
          autonomy slider en cualquier cosa que toque órdenes
    - mide a las **contrapartes de SENEBIS** (¿el bilateral dio mejor precio que la rueda?) → cierra
      la pregunta económica del módulo que **[T8]** concilia
  - liquidez real por bono → dimensionar una punta; **[M13]** lo mejora a "contra el mid" en vez de
    "contra el last"
  - **[M4] blotter** es el otro prerrequisito de [M5]: no se puede limitar exposición agregada sin un
    lugar donde esté toda junta

- **[T7] libro de cauciones propio** →
  - **[T6] proyección de caja**: los vencimientos de caución son **el movimiento de caja más grande
    del día** — sin ellos la proyección proyecta migajas
  - **[T1]** + **[M10]** cierran el triángulo del negocio de dinero: a qué tasa se podía colocar,
    cuánto quedó sin colocar, y a qué tasa nos fondeamos = el P&L que hoy no mira nadie
- **[T1] serie de saldos** → única forma de **medir el error del forecast de [T6]** y calibrarlo;
  y **[T3]** (fecha de acreditación) y **[T5]** (renombre de banco) son sus precondiciones de
  higiene: sin ellas la serie no es reproducible ni continua
- **[T2] persistir movimientos** → **[T9]** (precondición dura) y baja la vista de ~2,0 s a ~0,4 s

- **[C2] arancel en bps** →
  - escalón 3 (**tarifario**) pasa de "relevar 1.836 clientes" a "confirmar 20 valores sugeridos"
  - margen por **[C6]** canal y por **[M22]** cliente · objetivos de **margen** en
    `objetivos_comerciales` · elasticidad de precio
- **[D5] dimensiones congeladas en el agregado** → **[C2]** y **[C4]** pasan de "proyecto cada uno" a
  "un `GROUP BY` cada uno", y **resuelve el point-in-time** (hoy reasignar una cuenta reescribe la
  historia de dos comerciales)
- **[C5] historia del operador** → **[C11]** ranking por alpha honesto · objetivos as-of
  *(atención: [C5] y [D5] atacan el mismo problema por caminos distintos — §10-F)*
- **[C10] vocabulario de producto** → **[C9]** diversidad · **[C12]** cruce tenencia↔actividad · el
  asistente puede mezclar las dos mitades (hueco #2 de `TOOLS_IA.md`)
- **[C8] reloj de cosecha** → es el **denominador sin el cual [C9] es ruido** (un productor que no
  opera en febrero está esperando la cosecha, no se está yendo)
- **[C7] cerrar comisiones** → estabiliza el numerador de **[C6]**, que sin eso se mueve solo cada vez
  que corre un backfill
- **[C11] + [C12]** → convierten **[C13]** de extracto en **informe de gestión**
- **[M20] TC de Mesa** → **[M22]** (atribuir resultado en una moneda con agujeros no rankea nada)
- **[M1] vista de estrategia** → Fase 4 del propio doc · y es el **precedente** que hace exigible el
  mismo track record a los pivots (**[M15]**), al VIGÍA y a la `idea` del lab (**[M16]**)
- **[M18] cache de risk.py** → prerrequisito de cualquier check de saldo en **[M5]**
- **[D8] fail-closed** → precondición de **cualquier** identidad externa futura (portal, auditor,
  cooperativa mirando sus clientes vía [C6])

---

## 10. Contradicciones entre críticos (resolverlas antes de codear)

| # | Tema | Crítico A | Crítico B | Cómo resolver |
|---|---|---|---|---|
| **A** | **Retención de `mercado.timesales`** — *load-bearing* | c1: **7 días**. `engines/valores.py:52-56` corre `prune_native("mercado.timesales","ts",7)` en **cada arranque de motor** (= `DELETE`, `pg_mirror.py:226-233`), con el comentario *"El tape muestra solo el día"* | c2 ([B4]): **historia completa**, "no está en `jobs/cleanup_retencion.py::TABLAS` → crece sin límite" | La evidencia de c1 es **más directa** (mira el motor, no solo el job de cleanup). **Un `SELECT min(ts) FROM mercado.timesales` cierra la discusión en 5 segundos.** Si son 7 días: [M9] es **urgente** (cada día sin el job es irrecuperable) y el TCA sobre boletos históricos de clientes **no se puede hacer hacia atrás** |
| **B** | **[M19] z-score de sintéticos** | c1: **PASA** — chico, el job de forwards se copia casi literal, y la TNA mueve una recomendación al productor | c3: **MATAR** como oportunidad — "es una decisión de un día: o se hace el chart o se apaga el job y se dropea la tabla" | No se contradicen en el fondo: **son la misma decisión**. Antes, medir cuánta historia hay y si tiene huecos (el par depende de que el futuro DLR **y** la LECAP hayan operado ese día) |
| **C** | **[C13] estado de cuenta** | c2: **PASA** — no hay ningún generador de documentos en el repo; v1 `.xlsx` server-side con hash | c3 ([EXTRACTO]): **MATAR** — fue descartado en `QUANTAI.md:373` (P4, 2026-07-13) y su mejor parte (cobros futuros) **ya existe** en `acreencias-view` y en la subvista COBROS FUTUROS | El descarte de P4 era de un **entregable de IA**, no de un documento server-side. Pero c3 tiene razón en que el gancho ya está construido. **Queda dentro con la v1 recortada y un gate duro**: ⚠️ verificar si Aunesa ya emite un resumen oficial — si lo emite, **no se hace** |
| **D** | **[C11] benchmark** | c2 ([B2]): 4 curvas (TAMAR/BADLAR, CER, MEP, MERVAL) + fila de alpha | c3 ([BENCH]): **una sola línea de inflación** y un solo KPI; lo demás "genera más conversaciones de las que resuelve" | Gana el recorte (regla: las ACHICAR entran con su núcleo). Las otras 3 curvas se agregan **si alguien las pide** |
| **E** | **[C3] ¿depende de [D4]?** | c2: sí — `delta_operado` **no es captación** (una rotación mueve cantidades sin que entre un peso); la pata de cash vive en `movimientos` | c3: no — la descomposición cantidad-vs-precio sobre `tenencia` ya separa aporte de mercado **sin tocar esa tabla** | **Los dos tienen razón sobre cosas distintas**: la descomposición **mercado vs cantidad** sale sin [D4]; distinguir **aporte de efectivo vs rotación vs traspaso** necesita [D4]. Entregar en ese orden y **rotular qué mide cada versión** |
| **F** | **Point-in-time del operador** | c2 ([ATRIB]): tabla `clientes.operador_historia` + hook en el PATCH | c3 ([WAREHOUSE]): dimensiones **congeladas** en `ops_agregado_diario` | **Dos soluciones al mismo bug. Elegir una** — hacer las dos deja dos verdades, que es justo lo que este repo evita. [D5] cubre más superficie (todo el agregado) pero solo desde que se recomputa; [C5] es más barato y da la línea de tiempo explícita |
| **G** | **[M8] alcance del TCA** | c1: órdenes **propias** ROFEX, arrival price desde `ordenes_audit.SEND_REQUEST` | c2 ([B4]): **boletos de clientes** de renta fija contra mín/máx/VWAP del día (sin hora de ejecución) | No se contradicen: **son dos proyectos con datos distintos**. El de clientes es el que tiene volumen y valor regulatorio; el de órdenes propias es el que puede medir slippage real. Ambos listados dentro de [M8] |

---

## 11. DESCARTADAS Y POR QUÉ — no volver a proponerlas

| ID | Qué era | Por qué murió |
|---|---|---|
| `[F10]` | `/ops/aranceles` HOT/COLD | El entregable es *el mismo número, más rápido*. El botón **ALL** (`serie_full=true`) ya devuelve todo: interanual y estacionalidad **ya se pueden hacer hoy**. Es un ticket de performance, no producto (y `CLAUDE.md` ya lo tenía anotado como candidato) |
| `[TAPE]` | Perfil de liquidez histórico desde `timesales` | **Premisa falsa**: afirmaba que la tabla es un archivo completo. `engines/valores.py:52-56` la poda a 7 días en cada arranque de motor. Todo lo que prometía es imposible con los datos que existen. Lo rescatable es [M9] |
| `[F6]` | Alimentar INTRADAY desde los boletos | Duplicado con menos alcance de **[M7]**, que cubre las dos superficies manuales (CSV **y** cuaderno) |
| `[F3]` | Cupo de fondeo live | Duplicado partido en dos: el numerador es **[C14]**, el check pre-trade es **[M5]**. Mantenerlo aparte triplicaba el mismo hallazgo |
| `[TCA]` (datos-ociosos) | Panel de calidad de ejecución en vivo | Duplicado peor: proponía consultar en vivo un tape que a esa altura ya no está. Gana la versión con job post-cierre → **[M8]** |
| `[DEAD]` | Medir endpoints sin uso con `latencia_endpoints` | Housekeeping de repo, no negocio. El precedente lo condena: `manager.uso_modulos` **se decomisó el 2026-08-04 porque nunca se usó**. Desbloquea borrar código, y para eso está el grep de la REGLA #5 |
| `[WL]` | Workspace del trader persistido server-side | "Persistir preferencias en vez de `localStorage`" es de manual. Lo único no-genérico (el copiloto y el VIGÍA reciben las cards como `params` del browser) es **un prerrequisito de [M14]**, no un proyecto |
| `[ONB]` | Funnel de alta → primera operación | Cohortes de activación es plantilla de CRM/SaaS. Lo accionable ("legajos abiertos que nunca operaron, ordenados por cupo") es **una query de 10 líneas** que entra gratis en **[C1]** |
| `[CONC]` | Concentración del libro (Pareto / HHI) | Genérica: el gráfico de cualquier deck de cualquier empresa. Técnicamente es un `row_number()` sobre la query que **[C2]** construye igual — dos columnas, no un proyecto. Y su corte "por grupo económico" no existe: `manager.grupos` es un **scope de permisos**, no un grupo económico |
| `[B10]` | Alquiler de títulos: devengar el ingreso | **Media justificación es falsa**: `portafolio.alquiler` está acotada a las cuentas PROPIAS 100/255/256 (`tenencia_hd.py:24`) → no hay cliente del otro lado, así que "mostrarle al cliente lo que ganó" no existe. Queda un ingreso propio de volumen desconocido y sin verificar si ya llega categorizado desde Aunesa |
| `[B11]` | Perfil del inversor / suitability | La propia propuesta lo entierra: *"un campo de perfil cargado a las apuradas es peor que ninguno"*. Es proyecto de **proceso**, no de software (el grid de Manager ya soporta PATCH campo a campo: son 4 columnas el día que exista un oficial de cumplimiento) |
| `[TAKE-RATE]` | Arancel efectivo en bps | Duplicada → fusionada en **[C2]**, que la enmarca mejor (control contra un pactado). Se rescataron sus 3 trampas de implementación |
| `[NNM]` (kpis) | Net New Money | Duplicada → **[C3]**. Su estimación de 1-2 semanas estaba inflada por proponer conciliar contra `movimientos` |
| `[ROAUM]` | Rentabilidad por cliente sobre AuM | Duplicada → una **columna** de [C2]. El AuM y el arancel por cuenta ya se ensamblan en el mismo service: es una división. La matriz 2×2 con cuadrantes bautizados es packaging de consultora |
| `[GASTO]` | Serie de egresos por tipo | Duplicada → misma limitación raíz que **[T1]** (todo Tesorería scopeado a `fecha = hoy`); su aporte se conserva entero ahí |
| `[MIX]` | Ingreso recurrente vs transaccional | **El dato central no existe y conseguirlo no es código**: la porción del fee que le queda a la ALyC (vs sociedad gerente y coop) no está en ninguna tabla, y `assets.fee_admin` es el honorario del fondo, no nuestro revenue share. Sin eso, el "% de ingreso recurrente" es un número inventado con cara de estado de resultados |
| `[ERROR]` | Tasa de error operativo | Mide un síntoma cuyo remedio **ya está construido** (`anulados.py::detectar_marca_a`, `anular_lista`, expuesto en Manager) y el propio KPI **se sabe sesgado hacia abajo** (depende de que la reconciliación haya corrido). Lo rescatable —el lag `concertacion → anulado_en`— es una columna del barrido existente |
| `[ACTIV]` | Funnel de activación de cuentas nuevas | **El "problema hoy" es parcialmente falso**: `estado_comercial` ya devuelve `NUEVA` = nunca operó y la tabla ya lista esas cuentas con su operador; `fecha_alta_legajo` tampoco está sin usar. La "lista de zombis" es un `ORDER BY` sobre una tabla que ya se muestra |
| `[DESK]` | Calidad de la mesa: hit rate, profit factor | Métrica de manual sobre una tabla de **carga 100% manual** con cobertura no verificada. Con sesgo de registro el hit rate **miente hacia arriba** — un número preciso y falso que después se usa para evaluar gente |
| `[VIGILANCIA]` | Perfil transaccional y alertas PLD | Su primera pregunta está sin responder y decide todo (⚠️ si un proveedor externo ya lo cubre, el proyecto es cero). Es "grande (semanas)", la mitad no es código, y su propio riesgo es el más alto del informe: *"un sistema de alertas a medio construir genera un registro de alertas no tratadas, que es lo que una inspección castiga"*. La mitad barata ya está en [C4] y [C2]. Nota: el módulo `compliance` **fue eliminado** de `core/roles.py::MODULES` |
| `[EXTRACTO]` | Resumen mensual PDF para el cliente | Re-propone P4, **descartado el 2026-07-13** (`QUANTAI.md:373`), con otra ropa; y su mejor parte —el calendario de cobros a 90 días— **ya está construida** (`acreencias` precomputada + subvista COBROS FUTUROS + `acreencias-view`). Lo que queda es "generar un PDF" con un estándar de exactitud que nadie resolvió. *(Ver contradicción C: lo que sobrevive es la v1 `.xlsx` de [C13], con gate)* |
| `[CI-HUERFANOS]` | Detectar endpoints huérfanos en CI | Tooling sobre tooling, con el precedente en contra escrito en el propio ítem: admite que "el 80% del trabajo son los falsos positivos" y que se ignoraría "igual que el `perf_scan` `continue-on-error`" — que es la **prueba empírica en este mismo repo**. El caso que lo motiva no lo hubiera evitado un linter: el doc [VIVO] decía que estaba entregado |
| `[SINTETICOS-HIST]` | Job que escribe un histórico que nadie lee | Correcto como hallazgo, pero **es una decisión de un día, no una oportunidad**: chart o apagar. *(Ver contradicción B; sobrevive como [M19] con esa disyuntiva explícita)* |
| `[HUERFANOS]` (borrado) | Barrido de endpoints sin consumidor | Borrar 10 endpoints **es una tarea que la REGLA #5 ya obliga**, no una oportunidad. Sobrevive como el commit de limpieza **[D7]**; las exposiciones que sí eran producto se repartieron en [M1], [M5] y [D3] |
| `[B12]` (portal) | Portal del cliente | Diferido: la propia propuesta lo pone último, admite que es *"el mayor riesgo de seguridad de toda la lista"*, y su valor queda cubierto por [C13] **al 5% del riesgo**. Sobrevive solo su hallazgo de seguridad → **[D8]** |
| `[TARIFA]` | Tarifario pactado vs cobrado | No es iniciativa propia: es **el escalón 3 de [C2]**, que consigue el 80% del valor con **cero datos nuevos** derivando el arancel de facto. Y no se escribe una línea sin responder: **¿existe un tarifario escrito?** |

---

## 12. Reglas que se aplicaron al armar este documento

1. **Nada entra sin evidencia `archivo:línea`.** Lo que no se pudo verificar está marcado
   ⚠️ **SIN VERIFICAR** y trae el diag que hay que correr antes.
2. **Las ACHICAR entran con su núcleo**, no con la versión inflada. Lo que se recortó está dicho
   explícito en cada una ("lo que sobra"), para que nadie lo vuelva a agregar sin argumento nuevo.
3. **Los duplicados se fusionaron** (14 fusiones en total) y la entrada superviviente se queda con lo
   mejor de cada versión, incluidas las correcciones de hecho.
4. **Lo genérico se mató.** Un Pareto, un HHI, una matriz de cohortes o un funnel de activación no
   dicen nada de una ALyC argentina: si la propuesta aplica igual a cualquier app del mundo, no entró.
5. **Lo ya descartado en `docs/QUANTAI.md` no se re-propone** — y cuando alguien lo intentó con otra
   ropa, está anotado en la tabla de descartadas para que no vuelva.

