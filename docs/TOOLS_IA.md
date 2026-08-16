# TOOLS_IA — roadmap de herramientas del asistente

> **DOC VIVO (2026-07-21).** Es el backlog de qué puede y qué NO puede preguntar
> la IA. **Cada ítem que se construye se BORRA de acá** y se asienta en el
> changelog de `docs/COPILOTO.md`; cada uno que se descarta se borra con una
> línea de porqué. Si el doc no refleja lo que falta HOY, está mintiendo.
> Programa: `docs/QUANTAI.md`. Detalle del asistente: `docs/COPILOTO.md`.
>
> **Cómo se generó:** auditoría multi-agente (9 dominios en paralelo +
> verificación adversarial de cada propuesta contra el código real). De 63
> propuestas, 55 sobrevivieron a la verificación; el resto está en §5 para no
> re-proponerlas. Los números de línea son del commit `ba6e9db`.

*Consolidado de 9 barridos por dominio + verificación adversarial contra el código. 55 propuestas sobrevivieron. Este informe las reduce a lo que hay que construir de verdad.*

---

## 1. El veredicto en 3 líneas

**El asistente sabe responder "cuánto es HOY" y no sabe responder "cómo veníamos, contra qué, y quién.".** Todo el sistema está construido sobre snapshots y vistas live; la historia está guardada (hay ~15 tablas con serie diaria y cron vivo), pero ningún camino la lleva al modelo — y el modelo tiene prohibido hacer la resta él mismo.

**El segundo hueco es la PLATA, no el mercado.** El asistente conoce el stock (AuM) y lo operado (volumen), pero no ve un peso de lo que **entra, sale, se cobra o se tiene que entregar**: flujos de fondeo, acreencias, tesorería, liquidación de títulos. Es el dominio con cobertura CERO más grande y el que más pregunta un jefe de mesa.

**Y una parte del hueco no es un hueco: son dos líneas en una whitelist.** El ranking de clientes y la serie mensual del negocio no necesitan tools nuevas — necesitan agregar `denominacion` y `mes` a `_DIMENSIONES_CONSOLIDADO` (operaciones_sql.py:387), que ya tiene el `ORDER BY valor DESC LIMIT` puesto.

---

## 2. Tabla priorizada

`E` = esfuerzo · `V` = valor · `T` = tanda. Ordenada por valor/esfuerzo, no por dominio.

| # | Tool | Qué pregunta responde | De dónde sale | E | V | T |
|---|---|---|---|---|---|---|
| 1 | **dimensión `cliente`** (en volumen_operado / aranceles_consolidado) | "Los 10 clientes que más operaron y los que más arancel dejaron; ¿cuánto pesa el top 5?" | `ops_consolidado`: agregar `denominacion` a la whitelist + fichar | chico | alto | 1 |
| 2 | **dimensión `mes`** (mismas dos tools) | "¿Cómo viene el volumen mes a mes? ¿Cuánto cayó julio contra junio?" | `ops_consolidado`: clave `to_char(concertacion,'YYYY-MM')` | chico | alto | 1 |
| 3 | **posiciones_cuenta** | "¿En qué está invertido CLIENTE_1? Top 10 posiciones y si está concentrado" | `valuaciones_sql.posiciones_actuales` (:95) — ya trae orden y share | chico | alto | 1 |
| 4 | **pulso_mesa** | "¿Cómo viene el mes vs el mes pasado en volumen, comisiones y clientes activos? ¿Y el YTD?" | `control_comercial_sql.datos_totales_alyc` (:236), cacheada, 2 queries | chico | alto | 1 |
| 5 | **cobros_futuros** | "¿Cuánto cobran los clientes de renta y amortización en 30 días, qué día es el pico?" | `cashflow_sql.por_dia` (:137) sobre `operaciones.acreencias` | chico | alto | 1 |
| 6 | **spread_1816** | "¿El spread AL30–GD30 está caro o barato contra su historia? Percentil y z" | `research_1816_sql.spread` (:109) — devuelve stats hechos | chico | alto | 1 |
| 7 | **jobs_fallidos** | "¿Qué procesos vienen fallando esta semana? ¿Corrió el job de aranceles anoche?" | `manager_infra_sql.jobs_history_stats_sql` (:83) | chico | alto | 1 |
| 8 | **costo_ia** | "¿Cuánto nos costó la IA, cuánto del presupuesto vamos quemando?" | `ia_obs.observabilidad` (:141) + `saldo` (:20) | chico | alto | 1 |
| 9 | **controles_calidad_datos** | "¿Qué está mal cargado hoy? ¿Cuántos comitentes sin segmentar me ensucian el consolidado?" | `controles_sql.listar_controles` (:16) | chico | alto | 1 |
| 10 | **aum_composicion** | "¿Cómo está repartido el AuM? Pesos vs hard dollar vs FCI + top 10 títulos" | `portfolio_sql.total_serie` (:204) / `total_snapshot` (:232) | chico | alto | 1 |
| 11 | **rendimiento_esperado_curva** | "Si la curva no se mueve, ¿qué LECAP/LECER rinde más a 30 días?" | `descomposicion_retorno.rolldown_esperado` (:427) | chico | alto | 1 |
| 12 | **rendimiento_ranking** | "¿Qué cuentas rindieron mejor en dólares este año? ¿Qué TEA lleva CLIENTE_1?" | `valuaciones_sql.valuacion_consolidada` (:282) | chico | alto | 2 |
| 13 | **expectativas_rem** | "¿Qué inflación espera el REM a 6 meses, con qué dispersión?" | `rem_sql.expectativas` (:82) | chico | alto | 2 |
| 15 | **papeles_correlacionados** | "Estoy largo NVDA: ¿qué papeles del panel duplican la apuesta?" | `day_trading.get_companeros` (:342) | chico | alto | 2 |
| 16 | **screener_fundamentals** | "De los papeles del tablero Reuters, ¿cuáles están baratos por P/E fwd y EV/EBITDA?" | `eikon_live.tablero_fundamentals` (:147) | chico | alto | 2 |
| 17 | **serie_historica** *(genérica — ver §4)* | "¿Cómo rindió TZXD6 en junio? ¿Dónde estaba la UST10Y en abril? ¿La IV del call subió?" | 8 tablas distintas detrás de UN registro de series | grande (1ª vez) / chico (c/serie) | alto | 2 |
| 18 | **pnl_mesa** | "¿Cuánto PnL no realizado tiene la mesa, en pesos y dólares? ¿Qué títulos ganan y pierden?" | `pnl_sql.pnl_todas_cuentas_sql` (:207) + groupby propio | medio | alto | 2 |
| 19 | **salud_sistema** | "¿Está todo andando? ¿Qué jobs fallaron esta semana?" | `manager.job_runs` + `diagnostico.arbol` (:120) — **falta el reader** (`manager.health_reports` se eliminó con el decomiso Telegram 2026-07-25) | medio | alto | 2 |
| 20 | **estado_cartera_comercial** | "¿Cuántos clientes de OPERADOR_1 están DORMIDOS y cuánto AuM representan?" | `comercial_sql.analisis_comercial` (:355) | medio | alto | 2 |
| 21 | **aum_variacion_cuentas** | "Contra fin del mes pasado, ¿el AuM subió por quién? Altas y bajas" | `portfolio_sql.total_diff` (:265) | medio | alto | 2 |
| 22 | **flujo_de_fondos** | "¿Cuánta plata entró y salió este mes, neto, en pesos y dólares?" | `cashflow_sql.flujos_resumen` (:104) — **arreglar el scan primero** | medio | alto | 2 |
| 23 | **agro_toneladas** | "¿Cuántas toneladas de soja operamos en junio y qué share de mercado?" | `operaciones_sql.ops_agro` (:608) + `volumen_mercado_agro` | medio | alto | 2 |
| 24 | **cuentas_activas_mensual** | "¿Cuántas cuentas operaron cada mes del último año, por segmento?" | tabla `clientes.actividad_mensual` — **sin reader, SQL nuevo** | medio | alto | 2 |
| 25 | **uso_plataforma** | "¿La mesa usa lo que construimos? ¿Qué módulos, quién entró?" | `uso_modulos.get_uso` (:40) | medio | alto | 2 |
| 26 | **lab_intradia** | "Quiero 1% intradía: ¿qué papel lo da hoy y cuál acostumbra darlo?" | `day_trading.get_day_trading` (:179) | medio | alto | 2 |
| 27 | **objetivos_vs_actual** | "¿Cómo viene cada comercial contra su objetivo del mes?" | `control_comercial_sql.objetivos_vs_actual` (:387) | medio | alto | 2 |
| 28 | **cadena_opciones_agro** | "¿Qué puts de soja mayo cotizan y a qué prima?" | `agro_sql.get_panel_opciones` (:133) | medio | alto | 3 |
| 29 | **ranking_operadores** | "¿Cómo ranquean los comerciales: activos vs inactivos, AuM gestionado?" | `control_comercial_sql.datos_por_operador` (:337) | medio | medio | 2 |
| 30 | **cuentas_sin_operador** | "¿Hay cuentas operando sin comercial asignado? ¿Clientes reales o ruido?" | `sin_operador.cuentas_sin_operador` (:45) | chico | medio | 2 |
| 31 | **pendiente_curva_comparada** | "¿La curva CER se empinó contra fin de mayo? ¿Cuántos bps?" | `analitica.calcular_pendiente_curva` (:184) — **tiene un sesgo, ver §6** | chico | medio | 3 |
| 32 | **carry_usd_periodo** | "¿Cuánto rindió el carry en dólares en el año? ¿Cambia contra el oficial?" | `carry_trade.serie_carry_trade` (:112) — **hay un bug vivo antes, ver §3** | chico | medio | 3 |
| 33 | **mejoras_precio_dispo_agro** | "Si el productor coloca los pesos 60 días, ¿cuánto mejora el precio de la soja?" | `agro_sql.get_mejoras_dispo` (:314) | chico | medio | 3 |
| 34 | **cartera_propia** *(+ garantía, fusionadas)* | "¿Cómo viene la cartera propia en dólares? ¿Cuánto está inmovilizado en garantía?" | `tenencia_hd.tenencia_dias` (:62) / `tenencia_posiciones` (:91) | medio | medio | 3 |
| 35 | **titulos_a_liquidar_hoy** | "¿Qué títulos entregamos hoy al mercado y cuáles recibimos?" | `back_office_titulos.get_titulos_mercado` (:68) | medio | medio | 3 |
| 36 | **serie_fundamental** (trimestral) | "¿Cómo crece la facturación de NVDA trimestre a trimestre?" | `research_fundamentals.get_analisis(freq='Q')` (:103) | medio | medio | 3 |
| 37 | **flujo_contrapartes** | "¿Con qué contrapartes operamos más? ¿Se concentró el flujo?" | `operaciones_sql.flujo_resumen` (:66) — **necesita ficha nueva** | medio | medio | 3 |
| 38 | **tesoreria_dia** | "¿Cuánto entró y salió por banco hoy, en pesos y dólares?" | `tesoreria.ingresos_egresos_dia` (:64) — **live contra Aunesa** | medio | medio | 3 |
| 39 | **cartera_referido** | "¿Cuánto aporta el referido X y cuánta comisión de FCI le toca?" | `comercial.referido_clientes` (:307) / `referido_fci` (:402) — **ficha nueva** | grande | alto | 3 |
| 40 | **tasas_sinteticas** | "¿Qué tasa paga el sintético en USD y cómo viene contra la semana pasada?" | `sinteticos.get_sinteticos` (:80) + `snapshots_sinteticos` — **vista nueva + reader nuevo** | grande | alto | 3 |
| 41 | ~~pagos_ons~~ | "Calendario de pagos de ONs por emisor" | **la fuente ya no existe**: `calendario_ons` se borró el 2026-08-16 con la vista `/ons` | — | — | descartada |

---

## 3. Tandas de implementación

### Tanda 0 — Plomería (antes de la primera tool nueva)

> ✅ **HECHA el 2026-07-21** (commit tras la auditoría). Lo cerrado:
> · el dispatcher recibe `usuario` + helper `puede_control_comercial`
>   (fail-closed) → ya se puede gatear una tool por permiso POR USUARIO;
> · la aduana pasa de 4 a 7 tipos de ficha (suma REFERIDO, CONTRAPARTE,
>   USUARIO) y `asignar_ficha` normaliza el mapping;
> · **bug del carry ARREGLADO** (comparaba y reimprimía ×100 un valor que ya
>   venía en %: el bloque salía vacío o 100× inflado);
> · **hint falso del REM ARREGLADO** (decía "falta job jobs/rem.py" cuando
>   `macro.rem` la escribe `jobs/argentina_datos`);
> · `rendimiento_cuenta` ahora declara que devuelve SOLO totales;
> · diccionario de métricas en el system del asistente (volumen / comisiones /
>   cartera / rendimiento significan cosas distintas según la vista).
>
> **Queda pendiente de la Tanda 0:** declarar `tools` en las vistas de mercado
> (wiring por vista) y arreglar los 3 services que escanean la tabla entera
> (`listar_flujos`, `ops_serie`, `total_snapshot`) ANTES de exponerlos.

No es opcional: **seis propuestas confirmadas no se pueden construir bien sin esto**, y dos cosas de acá son bugs vivos hoy.

| Ítem | Por qué bloquea |
|---|---|
| **El dispatcher tiene que recibir el usuario.** `asistente_tools.ejecutar(nombre, args, *, mapping)` no sabe quién pregunta. | `pulso_mesa` y `objetivos_vs_actual` exponen datos que la web protege con un permiso **por usuario** (`require_control_comercial`, auth.py:334, que explícitamente NO mira la matriz de roles). Sin el email adentro de la tool, un admin sin el flag obtiene por chat lo que la web le niega. El copiloto de vistas SÍ pasa el usuario (motor.py:207) — es solo el asistente de negocio el que está ciego. |
| **Extender el catálogo de fichas de la aduana.** `pii_gateway.asignar_ficha` solo acepta CLIENTE / CTA / DOC / OPERADOR (:579, el resto tira `ValueError`). | Bloquea `cartera_referido` (referido), `flujo_contrapartes` (institucionales) y `uso_plataforma` (empleados que no son operadores comerciales). Meter esos nombres en `_vocabulario_protegido` en lugar de fichar es **peor**: los protegidos son intocables en todo el chat, y muchas referidoras son cooperativas que además son comitentes → destaparías una identidad de cliente. |
| **Diccionario de métricas (un doc + un sufijo fijo en cada description).** | Ver §4: hoy conviven dos definiciones de "volumen", tres de "cartera" y dos de "comisiones", cada una en una vista distinta. Juntas en un asistente se contradicen en el mismo chat. |
| **Declarar `tools` en las vistas de mercado.** Hoy solo `research` y `ayuda` las declaran (registro.py); `renta_fija`, `derivados`, `agro`, `home`, `trading`, `reuters` no. | El motor ya lo soporta genéricamente (motor.py:207-208). Es wiring de una vez por vista — conviene hacerlo de una en vez de por tool. |
| **Fix: el bloque de carry está roto en producción.** `copiloto/renta_fija.py:520` filtra `abs(carry_usd * 100) <= 15` pero el service ya devuelve el carry **en porcentaje** → sobreviven solo bonos con \|carry\| ≤ 0,15%, y lo que pasa se re-multiplica por 100 en :528 e imprime cifras 100× infladas. | El bloque [carry y canje] hoy está casi siempre vacío y cuando no, miente. Es gratis arreglarlo y no depende de ninguna tool. |
| **Fix: `rendimiento_cuenta` le miente al modelo.** Su description promete "qué TIENE un cliente, su cartera, su posición" y devuelve un solo número (`sum(valuacion)` + 4 totales de PnL). | El modelo la elige para preguntas que no puede contestar. Se arregla con la tool #3 (`posiciones_cuenta`) o corrigiendo el texto. |
| **Fix: `macro_sql.py:83` devuelve un hint falso** ("REM BCRA no cargado — falta job jobs/rem.py"), cuando `macro.rem` está poblada por `argentina_datos`. | Si se expone tal cual, el asistente afirma una falsedad sobre el propio sistema. |

### Tanda 1 — "Ya está servido y me lo preguntan todos los días" (ítems 1-11)

> ✅ **HECHOS (2026-07-21):** #1 dimensión `cliente` · #2 dimensión `mes` ·
> #3 `posiciones_cuenta` · #5 `cobros_futuros` · #10 `aum_composicion`.
> Con #1 y #2 el asistente deja de ser solo un filtro: **ya puede rankear y
> comparar mes a mes**, que eran las dos formas de pregunta más frecuentes y
> estructuralmente incontestables (Patrón 2). Con #5 arranca el dominio PLATA
> (Patrón 3). Notas de implementación:
> · la dimensión `cliente` devuelve NOMBRES → se fichan en el perímetro antes
>   de volver al modelo (es el dato más sensible que emitimos);
> · `mes` ordena cronológicamente, no por valor (es una serie, no un ranking);
> · `aum_composicion` agrega por cartera y DESCARTA `cuenta`/`id_cuenta`, que
>   `total_snapshot` trae por fila.
>
> ✅ **TANDA 1 COMPLETA (2026-07-21/22).** Se cerraron también #4 `pulso_mesa`
> (GATEADO por Control Comercial — el permiso por usuario que la auditoría
> marcó como fuga potencial), #6 `spread_entre` (en la vista research: el
> service ya devuelve percentil y z, así que el modelo no infiere nada), #7
> `jobs_fallidos`, #8 `costo_ia`, #9 `controles_calidad_datos` (devuelve el
> CONTEO por control, nunca los casos — dos de ellos traen denominaciones) y
> #11 `rendimiento_esperado` (en la vista renta_fija).
>
> De paso quedó cerrado el wiring de Tanda 0: **`renta_fija` es la primera
> vista de MERCADO con tools propias** además de research.

**Criterio de corte:** el service ya devuelve el shape correcto (no hay que plegar, agregar ni escribir SQL), la pregunta es de rutina diaria, y no hay decisión de producto pendiente. Todo esto es wrapper + ficha.

Once piezas. Dos de ellas (#1 y #2) ni siquiera son tools: son entradas en una whitelist que ya tiene el `ORDER BY … LIMIT` puesto — con eso el asistente pasa de "solo puedo filtrar a un cliente" a "puedo rankear el universo y darte la serie mensual", que son las dos formas de pregunta más frecuentes del jefe comercial.

Incluí a propósito los tres de **meta-sistema** (#7 jobs, #8 costo IA, #9 controles): son baratísimos, cero PII en la parte agregada, y le dan al PM control sobre su propia plataforma sin abrir Manager. `controles_calidad_datos` además cierra un círculo: es el que explica por qué el consolidado por segmento no cuadra.

### Tanda 2 — La máquina de series + el tablero comercial (ítems 12-27, 29-30)

> ✅ **#17 `serie_historica` HECHA (2026-07-22)** — la pieza de la que colgaban
> 8 propuestas. `api/services/copiloto/series.py`: registro de series (agregar
> una es una FILA, no una tool), contrato único de salida (stats + muestra
> ralificada, nunca puntos crudos), percentil/z calculados por CÓDIGO con su
> lectura. Común a todas las vistas + al asistente. Familias: macro, bono_1816,
> aum, bcra, internacional. **Las 8 propuestas que colgaban de ella se
> resuelven agregando filas al registro, no tools nuevas.**
> ✅ **Prerequisito de perf resuelto:** `listar_flujos` ya filtra fechas en SQL
> (era full scan + transferencia de ~2 años por llamada) → `flujo_de_fondos`
> (#22) queda desbloqueada.
> ✅ **#22 `flujo_de_fondos` HECHA (2026-07-22)** — entradas/salidas/neto por
> moneda sobre `cashflow_sql.flujos_resumen`, sin emitir cuentas. Es lo único
> que separa "el AuM subió por mercado" de "subió porque entró plata nueva";
> la salida se lo dice explícito al modelo para que no confunda una cosa con
> la otra.
>
> ⚠️ **Hallazgo transversal del control de calidad (2026-07-22): tres tools ya
> entregadas estaban MUDAS.** `aum_composicion` (leía `rows`, el service emite
> `docs`), `controles_calidad_datos` (iteraba un nivel de más) y
> `rendimiento_esperado` de renta fija (filtraba por `total`, el campo es
> `total_esperado`) devolvían "sin datos" SIEMPRE. El patrón culpable es
> `r.get("a") or r.get("b")`: escrito como defensa, funciona como tapadera —
> el shape cambia y la tool no falla, enmudece, y el modelo improvisa en vez
> de avisar. Los unit tests no lo detectaban porque **mockean el service con
> la clave inventada**. Candados nuevos: `scripts.smoke_asistente --tools`
> (corre cada tool contra la DB real) y `scripts.smoke_copiloto --contexto`
> (arma el contexto de cada vista y lista sus bloques). **Antes de dar por
> entregada cualquier tool nueva, tiene que salir ✓ en el sondeo real.**

> ✅ **Bloque COMERCIAL HECHO (2026-07-22): #20, #27, #29, #30 en UNA tool.**
> `api/services/asistente_comercial.py` → `tablero_comercial` con registro de
> lentes (`operadores`, `objetivos`, `cartera`, `sin_operador`). Se hizo en una
> sola tool y no en cuatro justamente por el patrón #4 de este documento: mismo
> gate (Control Comercial), mismo tratamiento de identidades, misma forma de
> salida → cuatro descriptions gemelas solo le dan al modelo cuatro formas de
> rutear mal. **Los comerciales salen como `OPERADOR_n` y sus emails no salen
> nunca**, ni fichados.
> ✅ **#21 `aum_variacion` HECHA** — por quién se movió el AuM entre dos cierres,
> con concentración. La description le aclara al modelo que mezcla mercado con
> aportes, y que "¿entró plata nueva?" se contesta con `flujo_de_fondos`.
> 📌 **Queda de la tanda:** #12, #13, #14, #15, #16, #18, #19, #23, #24, #25, #26.

**Criterio de corte:** requiere plegar/agregar en Python o escribir un reader, o la pregunta es semanal/mensual en vez de diaria. Acá está el grueso del valor pero también el grueso del trabajo.

El orden interno importa: **primero `serie_historica` genérica (#17)**, porque de ella cuelgan 8 propuestas confirmadas y define el contrato de "cómo se devuelve una serie" (stats, no puntos crudos) que después reutiliza todo. Después el bloque comercial completo (#20, #21, #24, #27, #29), que se hace de una porque comparte el mismo problema de fichas y el mismo gate de permisos.

`flujo_de_fondos` (#22) está en tanda 2 y no en 1 por una razón concreta: `listar_flujos` **no filtra fechas en SQL** — se trae `operaciones.movimientos` entera y descarta en Python (porque `fecha` es texto dd/mm/yyyy). Exponer eso a un chat significa un full-table scan por pregunta, en el mismo pool que sirve la web. Primero se arregla el service, después se expone.

### Tanda 3 — Decisiones pendientes, nicho y dependencias frágiles (ítems 28, 31-40)

**Criterio de corte:** algo que no es código. O necesita una decisión (política de fichas para referidos y contrapartes), o una vista nueva (`tasas_sinteticas`: no hay módulo de copiloto de sintéticos **y** nadie lee `snapshots_sinteticos` — grep = 0), o depende de una carga manual que puede no estar (objetivos, cámara de cereales, volumen de mercado agro), o mete una llamada HTTP a un tercero adentro del turno de chat (`tesoreria_dia` pega live contra Aunesa y tira `RuntimeError` si no responde).

---

## 4. Patrones — esto vale más que la lista

### Patrón 1: el sistema no sabe contestar "contra qué" (17 de 55 propuestas)

`serie_negocio`, `pulso_mesa`, `cuentas_activas_mensual`, `aum_variacion_cuentas`, `serie_historica_bono`, `pendiente_curva_comparada`, `liquidez_historica_curva`, `carry_usd_periodo`, `fair_value_historico_bono`, `serie_futuro_dlr`, `serie_iv_opcion`, `serie_macro_research`, `nivel_historico_macro`, `retorno_periodo`, `serie_fundamental`, `flujos_fondeo`, `rendimiento_ranking` son **la misma pregunta con distinta ropa**: *"¿esto está alto o bajo contra su propia historia / contra el período anterior?"*.

La causa raíz no es que falte el dato — la historia está guardada y con cron vivo en todos los casos. Son dos cosas:

1. **Los services están escritos para pintar una vista, no para responder una pregunta.** Devuelven la foto de hoy (`@cached(ttl=5..300)`) o la serie completa sin rango. El copiloto de home literalmente **baja 7 días de futuros DLR y se queda con la última fecha** (home.py:175-176) — tiene la serie en la mano y la tira.
2. **Al modelo le está prohibido restar** (`copiloto/base.py:38`: "PROHIBIDA la aritmética propia"). Es la regla correcta, pero implica que **si el código no calcula la comparación, la comparación no existe**. Nadie es dueño de ese cálculo.

> **La tool genérica que reemplaza a ocho.**
> Proponer `serie_historica(que, clave, desde, hasta)` con un **registro de series** — el mismo patrón que `copiloto/registro.py` usa para las vistas. Cada dominio agrega una fila de tres líneas (`nombre → reader, campos, unidad, granularidad`) en vez de una tool nueva con su description, su ficha y su capeo.
> Devuelve **siempre el mismo bloque**: `{primero, ultimo, min, max, media, z, percentil, n_obs, ventana, unidad}` + una serie ralificada, nunca los puntos crudos.
> Ya hay dos precedentes en el repo para copiar: `research_1816_sql.spread` devuelve exactamente ese bloque, y `quant/stats.compute_stats` ya calcula percentil y z-score.
> **Beneficio real:** una sola vez se resuelven el capeo de tokens, el etiquetado de la ventana (nadie más va a decir "el último año" cuando el default son 182 días), el "no hay histórico para esta curva" y el sesgo de supervivencia. Ocho tools con ocho descriptions distintas es ocho veces la chance de que el modelo elija mal.

### Patrón 2: el asistente puede filtrar, no puede ordenar

`ops_consolidado` tiene una whitelist de 7 dimensiones y **ninguna es el cliente**. La tool puede responder "¿cuánto operó CLIENTE_1?" pero jamás "¿quiénes son los 10 que más operaron?". Lo mismo aparece en AuM (hay total, no top títulos), en PnL (hay total por cuenta, no ranking por ticker), en rendimiento (hay una cuenta, no el ranking) y en fundamentals (hay ficha por empresa, no screener).

El jefe de mesa piensa en rankings y en concentración. **Es la forma de pregunta número uno del negocio y hoy es estructuralmente incontestable.** La buena noticia: en operaciones se arregla con dos entradas en un diccionario; en el resto, con un `sorted()[:N]` dentro del perímetro.

### Patrón 3: el dominio "plata" no existe para el asistente

Seis propuestas confirmadas (que en realidad son **dos tools**, ver §5) y dos más de back office cubren: qué entra, qué sale, qué se cobra, qué se paga, qué se entrega. Cobertura actual: **cero**. Las 6 tools de hoy miran tenencia (stock), PnL (resultado) y boletos (volumen operado). El movimiento de caja es un agujero completo, y es donde vive la pregunta que el AuM no responde: *"el AuM subió 8%: ¿es mercado o es plata nueva?"*.

### Patrón 4: la misma palabra significa cosas distintas según quién la diga

Esto es, en mi lectura, **el riesgo más serio del programa entero** y ninguna propuesta individual es dueña de él. Verificado:

| Palabra | Definición A | Definición B |
|---|---|---|
| **volumen** | `operaciones.operaciones.bruto` con `_ops_where` (excluye cierres) → `volumen_operado` | Σ\|importe\| pesificado de `negocio_movimientos` filtrado a 6 categorías, sin USDL → `pulso_mesa` |
| **comisiones** | arancel con la regla de cierres/caución → `aranceles_consolidado` | `arancel > 0 AND etapa <> 'solicitud'` → `pulso_mesa` |
| **cartera** | columna `portafolio.tenencia.cartera` → `aum_historico` | `assets.cartera` vía JOIN → `aum_composicion` | 
| **cartera** (otra vez) | subquery HD/ARS/MONEDAS de back office → `cartera_propia` | |
| **rendimiento** | PnL cost-basis (motor de pnl) → `rendimiento_cuenta` | base100 / TEM / TEA de la serie de valuación → `rendimiento_ranking` |

Hoy nadie lo nota porque cada definición vive en una vista distinta y nadie las mira juntas. **El asistente las va a mirar juntas, en el mismo párrafo.** Mitigación obligatoria: un diccionario de métricas (Tanda 0) y un sufijo fijo en cada description del tipo *"volumen de boletos con la regla de la vista Operaciones — NO coincide con el volumen del tablero comercial, que se calcula sobre otra base"*.

### Patrón 5: la aduana tiene 4 tipos de ficha y el negocio tiene 7 tipos de identidad

Clientes, cuentas y operadores están cubiertos. Referidos, contrapartes institucionales y usuarios de la plataforma, no. Es infraestructura de una vez, no un problema por tool.

---

## 5. Lo que NO hay que construir

**Duplicados — 7 propuestas que son 3 tools.** Tres agentes distintos propusieron por separado la misma tool de flujos (`flujo_de_fondos` / `flujos_fondeo` / `flujo_fondos_clientes`, todas sobre `cashflow_sql.flujos_resumen`), tres la misma de acreencias (`cobros_futuros_mesa` / `cobros_futuros` / `cobros_futuros_calendario`, todas sobre `cashflow_sql.por_dia`), dos la de agro y dos la de pulso comercial (`totales_mesa_por_periodo` y `pulso_comercial` llaman a la **misma función**, `datos_totales_alyc`). Construir cada una es duplicar fetch, description y mantenimiento, y darle al modelo tools gemelas entre las que va a rutear mal.

**`titulos_en_garantia` como tool separada:** llama a la **misma función con los mismos argumentos** que `cartera_propia` (`tenencia_hd.tenencia_posiciones`), que ya devuelve `gar`, `gar_cant`, `gar_total` y `total_gar` en la misma respuesta. Es un campo, no una tool.

**`pagos_ons`:** el service ya está inyectado en la vista, el copiloto ya le dice al modelo cuántos pagos hay en total, y el agregado por emisor/mes que pediría la tool **no existe en ningún service** — habría que escribirlo en la tool, que es exactamente lo que la regla de oro prohíbe. Valor bajo, y sumar ARS con USD ahí produce un número directamente falso.

**De los descartes del verificador, los tres que más importa no reabrir:**

- **`research_ultimos_dias`** — la premisa era falsa. El campo `destilado` es NULL en producción **por decisión explícita del user** (el cron corre sin `--destilar`; "cero tokens al ingestar"). No es un hueco técnico, es una decisión de producto que habría que revertir primero.
- **`titulares_del_dia`** — no meter noticias en el contexto **ya fue decidido y rechazado** (`copiloto/trading.py:945`), y el prompt base está escrito asumiendo que el modelo no las tiene ("el POR QUÉ no está en tus datos"). Además la retención es de 48 horas y el filtro es un `ILIKE` sobre el título. Si se quiere, es una conversación de producto, no una brecha a tapar.
- **`incidentes_recurrentes`** — el contador `ocurrencias` se **pisa** con `ON CONFLICT DO UPDATE SET ocurrencias = EXCLUDED.ocurrencias` cada vez que se corre con `--force`. Responder "pasó N veces" con un número reseteable es lo que la regla de oro existe para evitar. (Como beneficio lateral: eso es un bug real de `jobs/triage.py`, vale arreglarlo aparte.)

**Y dos más que recomiendo recortar, no descartar:** `base_clientes_por_segmento` sirve si se le saca "altas" de la pregunta (la fuente no da altas: `mes_min` es el mínimo histórico de la tabla, no del mes); `proximos_balances` es una columna del mismo objeto que ya trae `screener_fundamentals` — pliéguenla ahí.

---

## 6. Riesgos

### PII — dónde hay nombres de gente

| Nivel | Dónde | Qué hacer |
|---|---|---|
| **Alto** | Rankings de clientes (#1, #21), `estado_cartera_comercial`, `posiciones_cuenta`, `flujo_de_fondos` (cada fila trae `"[N] NOMBRE"`), acreencias por cliente, filas de `pnl_mesa` (`r2["cuenta"]` = nombre real), `total_snapshot` (trae `cuenta` e `id_cuenta` por fila) | Fichar **dentro del perímetro** con `asignar_ficha`, o no devolver el campo. El `tokenize` final del dispatcher es red de seguridad, **no diseño** — lo dice el propio docstring del archivo. |
| **Alto** | `tesoreria_dia` (aplana la persona a `persona_*` con todos los campos crudos de Aunesa), `ia_obs.ultimas` (trae email, prompt, respuesta y razonamiento de cada llamada), `controles_datos` (los dos controles marcados "no publicable" traen denominaciones) | Proyectar SOLO el bloque agregado (`resumen`, `hoy/por_dia/por_tarea`, `totales`) y descartar el detalle. |
| **Medio** | Empleados: `operador_nombre` y sobre todo `operador_email` (#27, #29). Hoy **ninguna tool emite emails**. | OPERADOR_n siempre; el email nunca sale. |
| **Nuevo** | Referidos, contrapartes, usuarios de plataforma | Requiere tipo de ficha nuevo (Tanda 0). No resolverlo metiéndolos en `_vocabulario_protegido`. |
| **Inverso** | `_vocabulario_protegido` hoy no incluye emisores, tickers ni carteras | Sin eso, la aduana puede **tachar un emisor como si fuera una persona** y el modelo recibe un ranking en jeroglífico. |

### Tokens — dónde se dispara el costo

- **Series crudas.** Ocho propuestas devuelven series diarias. Regla única: **stats + serie ralificada, jamás los puntos crudos**. Es la razón principal por la que conviene la tool genérica.
- **Cadenas de opciones.** Agro por commodity son N vencimientos × M strikes × 2 patas. Acotar a commodity+vencimiento y descartar strikes sin cotización (criterio que `copiloto/opciones.py:18` ya aplica).
- **Payloads que ignoran el rango pedido.** `ops_agro.serie_share` itera **todos** los períodos cargados ignorando desde/hasta; `tenencia_dias` devuelve la serie completa desde el primer snapshot; `flujo_resumen` devuelve una fila por día×contraparte×moneda. Recortar en la tool o el payload se dispara solo.
- **Rankings sin LIMIT en SQL.** `por_denominacion`, `por_cuenta` y `total_diff` devuelven **todas** las cuentas. El top N se corta en Python, no se delega al modelo.

### Costo de base de datos — el riesgo silencioso

Tres services que las tools querrían usar **escanean la tabla entera en cada llamada** porque el filtro de fechas está en Python, no en SQL: `listar_flujos` (movimientos, ~2 años), `ops_serie` (operaciones, ~490k filas, sin ningún `WHERE` de fecha) y `total_snapshot`. Un chat que hace 4 preguntas dispara 4 full scans en el mismo pool que sirve la web, potencialmente durante la rueda. **Arreglar el service antes de exponerlo** — no es opcional y es trabajo de media hora en cada caso.

### Riesgo de gobierno

`require_control_comercial` es un permiso **por usuario**, default-deny, que explícitamente no mira la matriz de roles. Ser admin del módulo `asistente` **no** equivale a tener Control Comercial. Sin el fix del dispatcher (Tanda 0), exponer `pulso_mesa` u `objetivos_vs_actual` le da a un admin sin el flag un dato que la web le niega. Esto no es un detalle técnico: es una fuga de permisos.

### Riesgo de alucinación por promesa mal escrita

Cuatro casos donde la tool **puede inducir al modelo a inventar** si la description es ambiciosa:

- `titulos_a_liquidar_hoy` calcula `neto_qty` pero **no lo cruza contra tenencia ni garantía** → no prometer "riesgo de entrega", que ningún service calculó.
- `flujo_de_fondos` da el flujo, pero la atribución "mercado vs plata nueva" **no está calculada en ningún lado** — la comparación contra `aum_historico` la tiene que hacer el modelo con dos llamadas, y hay que decírselo.
- `calendario_economico` trae `estimate`: prohibir explícitamente pronosticar el `actual`.
- El campo `idea` de `lab_intradia` es una heurística (posición en el rango + momentum + vs VWAP): se entrega como observación, jamás como recomendación.

### Riesgo de dato faltante presentado como cero

Cuatro fuentes son **carga manual**: `volumen_mercado_agro` (mensual), la Cámara de Cereales (diaria), `objetivos_comerciales` (self-create, sin healthcheck, no pude verificar que tenga filas) y las tablas de alquiler. En todas, si la mesa no cargó, la respuesta correcta es **"sin dato cargado"**, nunca 0% ni $0. Y dos son **proyecciones sobre el snapshot de hoy** (acreencias) o **congeladas point-in-time** (`actividad_mensual` guarda el operador del momento, no el vigente) → van a contradecir a otras tools si no se declara.

---

### Qué me llevaría de todo esto si tuviera que quedarme con una sola cosa

No construyas 40 tools. Construí **dos entradas en una whitelist**, **una tool genérica de series con su registro**, y **el diccionario de métricas**. Eso cubre los tres patrones que explican 30 de las 55 propuestas, y deja el resto como wrappers de un día cada uno en vez de 40 decisiones de diseño repetidas.