# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **📌 REMOTES POR PROYECTO (actualizado 2026-07-27).** DOS remotes por repo, ambos
> de la MISMA cuenta corporativa **NMolloAV** (por eso nunca piden login aparte):
> - **`origin` → NMolloAV (PRINCIPAL, default)**
>   - Backend: `github.com/NMolloAV/acaquant-backend.git`
>   - Frontend: `github.com/NMolloAV/acaquant-frontend.git`
> - **`org` → organización ACA (acacoop) — misma cuenta NMolloAV**
>   - Backend: `github.com/acacoop/acaquant-backend.git`
>   - Frontend: `github.com/acacoop/acaquant-frontend.git`
>
> El remote `personal` (cuenta NicolasEzequielMollo) fue ELIMINADO: usaba OTRA
> cuenta y pedía login en cada push. Tampoco existe ya un remote `corp` (ese URL
> ES `origin`). Cada remote tiene UN solo fetch/push URL a su propio repo.
>
> **AUTH automática (Git Credential Manager, Windows local).** Los dos remotes usan
> la cuenta **NMolloAV**, pinneada en `~/.gitconfig` global:
> `credential.https://github.com/NMolloAV.username = NMolloAV`,
> `credential.https://github.com/acacoop.username = NMolloAV`
> (+ `credential.usehttppath = true`). NO hay que hacer `gh auth switch` ni loguearse.
>
> **REGLA — sincronizar los 2 remotes:** alias global **`git pushall`** (= `git push
> origin HEAD && git push org HEAD`). Un `git push` normal va solo a `origin`.
>
> **⚠️ AUTOR DE LOS COMMITS — Vercel BLOQUEA por identidad.** Vercel tiene proteccion
> de despliegue por autor: si el commit lo firma alguien que NO es miembro del
> proyecto, el deploy queda **Blocked** y produccion sigue sirviendo la version
> anterior **sin ningun error visible**. Paso el 2026-08-09: cuatro deploys seguidos
> (VEPS, saldo final, pantalla SALUD) quedaron bloqueados por venir firmados con
> `mollonicolas95@gmail.com`, que GitHub asocia a la cuenta PERSONAL
> `NicolasEzequielMollo` — la misma que se saco del flujo de remotes. Sintoma: el
> codigo esta en `main`, el build compila, y aun asi la app no cambia.
> **REGLA: no pisar `user.name`/`user.email` al commitear.** El default del checkout
> es el que Vercel acepta. Si algo no aparece en la app, mirar
> Deployments en Vercel ANTES de buscar el bug en el codigo.
>
> Vercel (frontend) deploya de `origin` (`NMolloAV/acaquant-frontend`) y el Droplet
> (backend) tira de `origin` corp — ambos migrados 2026-07-27. Un push a `origin`
> (o `git pushall`) cubre deploy + Droplet.

## Overview

TradingAV — plataforma quant MERVAL/ROFEX. pyRofex WS → **Postgres/Supabase** → FastAPI (`api.acaquant.com`) → **acaquant-web** Next.js en Vercel (`trading.acaquant.com`). Server en `/root/TradingAV` (Droplet DO **nyc1**, Nueva York — verificado 2026-08-13), venv en `/root/TradingAV/venv`. Vercel corre las Functions en **iad1** (Washington DC): las funciones de Next son un PROXY (las 40 rutas de `src/app/api` pegan a `api.acaquant.com` y el front NO tiene cliente de base — verificado 2026-08-13; decían 20, el número había quedado viejo), asi que la pata que paga Vercel es Vercel→Droplet, ~330km de distancia. Mover la region de Vercel NO toca el viaje Droplet→Supabase.

> **MONGO DECOMISADO (2026-06-29).** El sistema es 100% Postgres/Supabase: motores,
> jobs, API y MCP leen y escriben SQL. NO queda una sola referencia a
> Mongo en el código (`grep -rE "from core.mongo|import pymongo|MongoClient"` → 0).
> El cliente Mongo (`core/mongo.py`), `api/db.py` y el tooling Mongo fueron borrados.
> Si ves "Mongo"/"colección"/"Atlas" en algún doc viejo, es residual — la fuente de
> verdad es `sql/schema.sql` + `docs/SQL.md`.

> **⚡ PROGRAMA DE IA EN CURSO → `docs/QUANTAI.md` (LEER al arrancar la sesión).**
> Es el roadmap VIVO del programa "QuantAI": proveedor DeepSeek, marca AI
> (módulo `ia` del RBAC), Fase 0 (gateway core/ai + observabilidad + evals) y
> 5 proyectos (briefing modal en HOME, triage de incidentes, copiloto de mesa,
> prep comercial, analista ad-hoc). Ahí viven las decisiones tomadas, el estado
> de cada proyecto, los principios de ingeniería que guían TODO el desarrollo
> de IA y qué está diferido/descartado (no re-proponer sin novedad). REGLA:
> cualquier trabajo de IA se hace leyendo ese doc primero, y todo avance/
> cambio/descarte se actualiza AHÍ en el mismo commit — si el doc no refleja
> el estado real, el trabajo está incompleto.

**Schemas SQL** (Postgres/Supabase; `sql/schema.sql` es la fuente — OJO: no siempre 100% aplicado en la DB real, ver "Capa SQL"):
- `mercado` — núcleo de mercado: `curvas` (master RF, antes Trading.Curvas+BondsMaster), `especies` (las PATAS de cada bono — `ticker` AL30 une con la curva, `ticker_especie` AL30D une con la posición (`tenencia`/`acreencias`), + moneda/plazo/`es_default`; sembrada desde `manager.pyrofex_instruments` con `scripts/sembrar_especies.py` — ver `docs/RENTA_FIJA.md` §0 paso 8; es la FUENTE ÚNICA de símbolos: `portafolio.assets.instrumento`/`instrumento_usd` se derivan de acá vía `jobs/assets_autofill` en vez de cargarse a mano), `market_snapshot`, `snapshots_cierre(+_hist)`, `canje_cierre`, `timesales`, `dias_habiles`; renta fija derivada `forwards_zscore`, `fit_params`, `fair_value_residuos`, `ons_ignoradas`; `futuros_dlr_snapshot`, `caucion_snapshot`; opciones `options_data(+_hist)`, `options_snapshot`, `options_metadata`, `options_vr`; renta variable `cedears`(master), `cedears_snapshot`, `adr_snapshot`, `precios_acciones`, `cedears_time_sales`, `day_trading_stats`; agro `agro_snapshot`, `agro_opciones_snapshot`, `agro_pizarra`, `camara_cereales`, `volumen_mercado_agro`; `snapshots_sinteticos`; `mercado_hist`; `rubros`, `adhoc_subscriptions`.
- `macro` — series macro `series_macro` (DOLAR/CER/BADLAR/TAMAR/RiesgoPais/Inflación), `uva`, `rem`.
- `valuaciones` — `consolidado`, `pnl_totales_cache`, `portfolio_snapshot`, `dolar`, `dolar_snapshot`, `dolar_oficial_live` (MEP/CCL).
- `portafolio` — `tenencia` (AuM, fuente única), `assets` (catálogo de títulos), `backfill_log`; `tenencia_live` (posición T0/T1 del día, daemon `jobs/tenencia_live.py`); `control_saldos` (**saldo LIQUIDADO** de hoy por cuenta y moneda, daemon `jobs/control_saldos.py` — fuente distinta: el endpoint `cuentas/{id}/posiciones` de Aunesa, que separa lo liquidado de lo pendiente de liquidar. Es lo que distingue un DESCUBIERTO REAL de un falso negativo por caución sin vencer, que es lo que muestra cualquier posición proyectada. Signo YA corregido (Aunesa manda las tenencias al revés, igual que `posicionValuada`) → negativo en esa tabla = descubierto. Solo monedas `MONEDAS` = ARS/USD/USDL/**USDC** (dólar cable, sumado 2026-08-14 — sumar una moneda es UNA línea en esa constante: el service y las pills de la vista son agnósticos y la moneda nueva aparece sola), cuentas de `clientes.contrapartes` EXCLUIDAS, sin histórico: siempre el día de hoy).
- `operaciones` — `operaciones` (vista MOVIMIENTOS), `negocio_movimientos` (cost-basis), `acreencias`, `movimientos`, `tipos_operacion`; órdenes `ordenes_live`, `ordenes_audit`, `ordenes_idempotency`, `triggers_mep`, `brackets_live`, `operativas_mep`, `motor_heartbeat`, `accounts_descubiertas`; Mesa de Dinero `mesa_dinero` (ops manuales compra+venta, montos/resultado/% derivados server-side; **regla 50/50**: la columna OBS dice QUIÉN generó el trade — si NO es `Mesa` es un operador comercial y el resultado se reparte mitad al operador y mitad a la Mesa en RESULTADO POR COMERCIAL, si no se infla el comercial y se subvalúa la Mesa; es solo ATRIBUCIÓN — el total del período y el lado por cliente no cambian; OBS vacía no se reparte; `n` sigue contando ops ORIGINADAS y `desde_operadores_ars/usd` dice cuánto de la Mesa vino de ese 50%), `mesa_dinero_tc` (TC manual por día), `mesa_dinero_traders` (catálogo), `mesa_dinero_escritores` (allowlist de escritura por email, admin siempre puede), `mesa_dinero_lectores` (**allowlist de ACCESO a la vista**, 2026-08-11: VERLA ya NO la da el módulo `operaciones` — la vista se gatea por PERSONA, no por puesto, porque el criterio es "estos usuarios" y un módulo obligaría a crear un rol por combinación; `puede_ver` = admin ∪ lectores ∪ **escritores** — escribir IMPLICA ver, así las dos listas no se contradicen; el gate `require_lectura_mesa` se monta sobre TODO el router y `/api/me` publica la capacidad `mesa-dinero` dentro de `modules` para que el nav filtre igual que con un módulo — **NO agregarla a `core.roles.MODULES`**: sería un checkbox en ROLES Y PERMISOS que no controla nada), `mesa_dinero_audit` (trazabilidad before/after) — vista NEGOCIO `/mesa-dinero`, gestión en Manager → MESA (tab partida 50/50: arriba catálogo + allowlists de ESCRITURA, abajo ACCESO); SENEBIS `senebis` (órdenes que cargan los TRADERS para que el BACK OFFICE las procese afuera y marque `completada` — 2 estados; escritura gobernada por `senebis_escritores` (allowlist propia por email, admin siempre puede, se gestiona en Manager → MESA junto a la de Mesa de Dinero — al separarlas heredó los mismos usuarios); server-side: monto=vn×px/100, concertación=HOY, cp='255', plazo CI/24 ↔ liquidación inferidos con día hábil; export .xlsx formato sistema destino ID·OPERACION·INSTRUMENTO·PLAZO·PRECIO·CANTIDAD·CONTRAPARTE·COMITENTE·CARTERA PROPIA·MERCADO — el archivo/espejo lleva SOLO pendientes no-MAE (se sube varias veces por día; lo completado ya está cargado) — con ID = secuencia GLOBAL que espeja la numeración Quantex y no se resetea — visible y ajustable desde la vista ("PRÓXIMO ID", `POST /proximo-id`, SOLO admin — la secuencia es global; si Quantex ya consumió el número y la carga falló por mercado, `POST /ops/{id}/reasignar-id` le da a ESA orden el siguiente ID libre y quema el viejo, sin renumerar el resto); contraparte del senebi: `tipo_contraparte` interno/externo — externo elige agente del catálogo `senebis_agentes` (nombre→número BYMA + `codigo_mae` AAAOO para el futuro Excel MAE) y en el Excel va COMITENTE vacío + CONTRAPARTE=número; interno resuelve `cc` por número o denominación contra `clientes.cuentas` y en el Excel COMITENTE=cp si GARANTIZADO, sino cc; DESTINO MAE de cuentas internas: es un ATRIBUTO de la contraparte — `clientes.contrapartes.codigo_mae` (FXXX fondo / C+CUIT comitente / SXXX aseguradora), editable en Manager → CONTRAPARTES; el Excel MAE lo resuelve en vivo por la cc de la orden (cc → id_cuenta → codigo_mae), el trader no carga nada; tab EXCEL MAE (`/excel-mae` espejo + `/export-mae` archivo): SOLO pendientes es_mae (misma lógica temporal que Quantex, sin "cargan ellos") con columnas Operacion·Instrumento·Plazo·Moneda('ARS' fijo hasta tener el campo)·Precio UNITARIO (px÷100)·Cantidad·Destino resuelto en vivo (interno→contrapartes.codigo_mae, externo→senebis_agentes.codigo_mae; falta el código → celda vacía + fila marcada `sin_destino`)·Segmento (campo de la orden `senebis.segmento`, se elige al cargarla entre los del catálogo `senebis_segmentos` — ABM en la vista, botón SEGMENTOS MAE — y nace en `senebis.SEGMENTO_MAE_DEFAULT` = 'Bilateral MAEClear', que no se puede borrar del catálogo; el selector aparece en el form SOLO si ¿MAE?=SÍ) — la tab tiene su **TILDE PROPIA de completada** (`senebis.mae_completada` + `_por`/`_at`, `POST /ops/{id}/mae-completada`, 2026-08-13): la marca el TRADER cuando ya cargó esa orden en el MAE y esa fila deja de salir en el `.xlsx`/espejo pero SIGUE visible **grisada y tachada** para poder destildarla. Es INDEPENDIENTE de `estado` a propósito: `estado` es el tablero del BACK OFFICE (su carga en Quantex) y gobernar con él el archivo del MAE mezclaba el laburo de los dos equipos. Sin corte por fecha (a diferencia de `set_estado`): una orden vieja sin tildar sigue entrando al archivo y sacarla de ahí es justo para lo que existe la tilde. Y tiene su ESPEJO del amarillo de Quantex: editar una orden ya TILDADA prende `mae_editada_completada` (el MAE quedó cargado con los datos viejos → fila AMARILLA que gana sobre el grisado + botón ⚠ EDITADA que la baja vía `POST /ops/{id}/mae-visto`); son DOS marcas y DOS vistos y no uno solo porque los baja OTRO equipo cada uno (back office mira Quantex, trader mira el MAE) y el visto de uno no puede tapar el del otro; la orden editada NO vuelve al Excel (igual que en Quantex: lo ya cargado se corrige del otro lado), pero DESTILDAR sí baja el amarillo — ahí la orden vuelve al archivo y se re-carga con los datos buenos, así que el aviso deja de aplicar; `es_mae`=true → la orden se carga en el MAE: excluida del Excel/espejo y tipo fijo 'MAE' — la vista filtra CON / SIN / SOLO MAE (`?mae=solo|sin`); `cargan_ellos` es flag SI/NO (era texto libre, migrado 2026-08-05): SI = la orden la carga la CONTRAPARTE en Quantex → también excluida del Excel/espejo, pero visible en la vista con su flujo pendiente→completada normal; marcas de edición persistentes que reemplazan al amarillo que se pintaba a mano en la planilla vieja: `campos_editados` acumula qué campos se tocaron post-alta (el front les pone `*`, el estado NO cambia) y `editada_completada` se prende si se editó algo que YA estaba completada (el back office la cargó en Quantex con los datos viejos → fila amarilla + botón ⚠ EDITADA que la baja vía `POST /ops/{id}/visto`); se calculan server-side en `editar_op` comparando before/after de lo persistido, no del payload), `senebis_presencia` (heartbeat: quién tiene la vista abierta, para no pisarse), `senebis_audit` (before/after) — vista BACK OFFICE, módulo `back-office`, router `api/routers/senebis.py`. **La vista se sirve en UN request: `GET /vista`** (2026-08-13) — trae órdenes + los dos espejos + presencia + próximo ID. Antes el front polleaba `/ops` + `/excel` + `/excel-mae` cada 10s y los TRES corrían la misma query con su propio `marcar_presencia`/`conectados`: ~13 viajes a la base por ciclo y por usuario, contra ~7 ahora (con el peaje medido de ~8.5ms, ese trío era el #2 del ranking de tiempo consumido: 962s/día). El truco es que `vista()` lee por RANGO y filtra en MEMORIA (`_coincide`, mismo predicado que el WHERE de `listar_ops`, escrito una sola vez): los espejos del Excel NO pueden depender de cómo el trader filtró la pantalla. `/ops`, `/excel` y `/excel-mae` quedan vivos pero DEPRECADOS (se borran cuando prod esté estable — mantenerlos evita que un deploy desparejo de front/backend rompa la vista). Tesorería (`api/services/tesoreria.py`): los movimientos del día se sirven LIVE desde Aunesa (`consultaMovDocsSolicitados`, no se persisten — persistirlos es la propuesta [T2] de `docs/OPORTUNIDADES.md`, todavía sin hacer. Existió en prod una `operaciones.tesoreria_movimientos` con 2.106 filas de un backfill ÚNICO del 2026-08-06 que nunca dejó código en el repo; **se borró el 2026-08-13** tras confirmar que nadie la leía ni la escribía, con respaldo a CSV en el Droplet) y se agrupan por CUENTA OPERATIVA (denominación del banco) × moneda; **la tab BANCOS calcula SIEMPRE sobre estado `Procesado`** (`ESTADO_EFECTIVO`) e IGNORA el selector ESTADO de la barra — un movimiento Rechazado/Anulado/Pendiente nunca movió plata en el banco y no puede entrar a un saldo; el selector aplica solo a la tabla de MOVIMIENTOS (por eso se le pide a Aunesa TODOS los estados de una y se separa en Python: una llamada, dos tabs). Esa llamada esta cacheada AUNESA_TTL_S=15s (`@cached` sobre `traer_crudas`): medido en el Droplet son ~1.6s de los ~2.0s que tarda armar la vista (el 82%), mientras las 16 queries suman ~0.35s. OJO con el modelo mental: cada roundtrip a Supabase cuesta un peaje fijo aunque la query ejecute en 0.1ms, asi que lo que importa es la CANTIDAD de queries, no su plan. **Medido 2026-08-13 (`scripts/diag_ubicacion`): RTT ~8.5ms** (min 8.5 / p50 8.7) — los ~26-28ms que decia este doc son de julio y quedaron viejos. **Ese peaje es 100% DISTANCIA, no software**: el handshake TCP puro mide 9.3ms y la query completa 8.5ms, o sea que el pooler + Postgres aportan ~0 y no hay nada que tunear ahi. Es la geografia: Droplet en **DO nyc1 (Nueva York)** ↔ base en **Supabase AWS us-east-1** (`aws-1-us-east-1.pooler.supabase.com`, N. Virginia), ~370km. Y nyc1 ES la region de DO mas cercana a us-east-1, asi que ese numero es el piso disponible sin migrar el backend a AWS (los `Seq Scan` sobre tablas de 30 filas son OPTIMOS y NO hay que indexarlas). El TTL es menor que el poll del front (20s), asi que la vista nunca se atrasa: solo evita repetir la MISMA llamada cuando pollean varios usuarios o cuando se abre el detalle de una celda. Por eso mismo `_detalle_dia` pide TODOS los estados (no solo `Procesado`) y filtra en Python — comparte la cache key con la grilla en vez de pagar Aunesa de nuevo. Lo único que Aunesa NO da es el saldo inicial, que se carga a mano por día en `tesoreria_saldos` (PK fecha+cuenta_operativa+unidad → saldo final = inicial + ingresos − egresos; si el back office NO lo cargó el inicial vale **0**, no null, y el saldo final cierra igual — `saldo_cargado` en la respuesta distingue "cargado en cero" de "sin cargar" y el front lo pinta apagado), con allowlist `tesoreria_escritores` (admin siempre puede, se gestiona en Manager → MESA) y `tesoreria_audit`. El universo de cuentas operativas NO lo da Aunesa (no hay endpoint de cuentas): se descubre viendo movimientos y se persiste en `tesoreria_cuentas` (auto-alta en cada carga de la vista + siembra hacia atrás con `python -m scripts.diag_tesoreria_cuentas --registrar` + **alta manual desde la tab BANCOS** `POST /tesoreria/cuentas`, para el banco que todavía no operó nunca; misma tabla y modelo que las descubiertas, `aunesa_id` se completa solo cuando opera). Tab **MERCADOS**: 4 tableros del día al 50% — bloque MERCADO (`ingreso` izq / `pago` der) y bloque FCI (`rescate` izq / `suscripcion` der), todos carga manual en `tesoreria_mercados` (mismo modelo con distinto `tipo`; banco y `entidad` validados server-side contra sus catálogos, estados `pendiente`/`completado`, estado editable desde la celda). El catálogo de mercados/FCI vive en `tesoreria_entidades` (`bloque` + `codigo` + `nombre` → etiqueta `[BYMA] BYMA`; sembrado con los 9 mercados iniciales; **ABM en modal desde la vista**, baja LÓGICA para no huerfanar históricos). Los bancos tienen `numero_cuenta` (Aunesa no lo manda) y `numero_hygirus` (identificador en HYGIRUS: se guarda para otra funcionalidad y **NO** se muestra en la grilla), más su propio **ABM en modal** — renombrar arrastra saldos, cheques y movimientos en UNA transacción. La grilla es la UNIÓN de (catálogo ACTIVO) ∪ (lo que aparezca hoy en cualquier fuente) mientras que el ABM lista solo `activa`, así que un banco puede estar en la grilla y no en el ABM: el backend lo declara en **`fuera_catalogo`** con su motivo (`sin_catalogo` = no hay fila / `dado_de_baja` = la hay apagada y el banco sigue operando — el auto-alta NO la revive) y el ABM los muestra arriba con un botón DAR DE ALTA que manda el nombre exacto. **BORRAR es físico solo si NADIE lo referencia** (`_REFS_CUENTA`: saldos, cheques, mercados, registros, VEPs + banco-a-banco) y no tiene `aunesa_id`; si no, degrada a baja LÓGICA. Faltaban registros y VEPs en esa lista (incidente 2026-08-10): un banco de alta MANUAL con solo registros se borraba de verdad y, al no traerlo Aunesa, no volvía nunca. **Dos filas más en BANCOS que entran al saldo final: `mercados` = ingresos − pagos y `fci` = rescates − suscripciones**, del día — la grilla BANCOS se arma con el catálogo COMPLETO, no con quién operó hoy. Los egresos por RIEL **e-cheq** (`tipoDocSoli` con código `[E CHEQ]`) salen en `egresos_echeq`, **fila propia** de la grilla y fuera del total de `egresos` — pero **sí restan del saldo final**: la separación es solo para que el back office los distinga. Esa MISMA fila suma además los **cheques EMITIDOS** de la tab CHEQUES (`lado='emitido'` + `estado='emitido'` + `fecha_pago` MENOR O IGUAL al día — el día MISMO entra, porque un cheque que se paga hoy se debita hoy; los `pendiente` y los de `fecha_pago` NULL NO entran), agregados por banco+moneda y **partidos en renglones** del modal de auditoría para no explotarlo cheque por cheque pero sin mezclar el arrastre con la carga del día: `vencido` (manual, `fecha_pago < día`, `ref = emitidos_t1|<banco>|<moneda>`), `hoy` (manual, `ref = emitidos_hoy|…`) y `auto_vencido` (espejo, `ref = emitidos_auto|…`). Se tildan/destildan por separado. **REGLA DEL BACK OFFICE (2026-08-11): un emitido sigue restando TODOS los días hasta que se marca `completado`** — lo saca el ESTADO, no el paso del tiempo; la fecha solo decide desde cuándo empieza. Eso vale **también para los espejo** (`origen='aunesa'`, ver abajo): lo único que los excluye es que **su propio movimiento e-cheq esté en la vista de ESE día** (`ids_echeq_egreso`, comparación por `mov_id` — ese día lo resta el movimiento y contarlo dos veces es el doble conteo que el espejo vino a eliminar). Del día siguiente en adelante los sostiene el cheque, porque `traer_crudas` filtra los movimientos AL día pedido y el de ayer ya no está. **Hasta el 2026-08-11 el espejo quedaba afuera SIEMPRE** (`origen='manual'` en el SQL) y por eso impactaba un solo día y después se evaporaba del saldo: el back office lo detectó porque el faltante era, clavado, el total de la columna AUTO del consolidado. Se compara por `mov_id` y NO por `fecha_pago` a propósito — `mov_id` es la identidad real del movimiento y no depende de que la fecha estampada por el espejo sea la correcta; de yapa, si Aunesa está caído el conjunto viene vacío, los movimientos tampoco están en la grilla y el cheque queda como única representación de esa plata. Un banco cuyo ÚNICO movimiento del día sean esos cheques igual aparece en la grilla. La fila **Neto** se eliminó de la grilla (era redundante con el saldo final). MOVIMIENTOS tiene además filtros client-side por **RIEL / TIPO / SOLICITUD**. Tab **CHEQUES** (50/50, tabla única `tesoreria_cheques` con columna `lado`): los **RECIBIDOS** son 100% carga manual; los **EMITIDOS** conviven en dos `origen`. El `manual` lo carga el equipo. El **`aunesa` es un ESPEJO AUTOMÁTICO** (`sincronizar_echeq_emitidos`, corre en cada poll de la tab): un egreso de Aunesa con RIEL `[E CHEQ] E CHEQ` **ES** un cheque emitido, así que la fila se crea sola con TODO lo que trae el movimiento (cliente, CUIT sin guiones, cuenta operativa, moneda, importe), estado `emitido` y `fecha_pago` = el día del movimiento. `mov_id` es ÚNICO → resincronizar no duplica ni pisa lo editado. **Solo se espejan los `Procesado`** (un e-cheq rechazado dejaría una fila fantasma en un tablero sin filtro de fecha) y **no se pueden borrar** (el próximo poll los recrea): se cierran con `completado`. Antes de esto el back office los cargaba a mano y el saldo los restaba DOS veces. Cada lado tiene su horizonte: **EMITIDOS** (derecha) es un tablero de SEGUIMIENTO y **NO filtra por fecha** — un cheque de hace un año sin cerrar sigue a la vista, `fecha_pago` futura = fila **naranja**; columnas comitente, CUIT, banco, importe, moneda, estado `pendiente`/`emitido`/`completado`, fecha de pago; `completado` lo saca de la vista y la fila **no se borra** (queda para auditoría). El **TOTAL del tablero EMITIDOS es lo que impacta HOY**: un `emitido` con `fecha_pago` FUTURA todavía no movió plata y NO suma (se muestra aparte como «+X futuros» y entra solo cuando llega el día); los vencidos SÍ cuentan (puede tocar pagarlos hoy) y los `pendiente` también. **OJO — el tablero y la fila `egresos_echeq` de BANCOS no usan el mismo predicado**: a BANCOS solo entran los `emitido` con `fecha_pago` vencida o del día (los `pendiente` NO), así que el total del tablero y lo que resta del saldo pueden no coincidir. El **CONSOLIDADO POR BANCO** (botón «ver consolidado») abre esa caja: agrupa los emitidos abiertos en `vencido` / `hoy` / `futuro` / `auto` / `pendiente` / `sin_fecha` y muestra **IMPACTA EN BANCOS = vencidos + de hoy**. La columna **AUTO (AUNESA) es SOLO para los espejo con `fecha_pago` de HOY** (hoy los resta su movimiento; mañana caen en `vencido` como cualquier otro) — clasificarlos SIEMPRE como `auto`, que fue el bug hasta el 2026-08-11, los hacía desaparecer del saldo al día siguiente. **RECIBIDOS** (izquierda) son **todos del día** (se registran intradía y no se arrastran): se filtran por día de carga (`creado_at` en ART) y se ven los dos estados; columnas comitente, `tipo` (`echeq`/`fisico`), banco, importe, moneda, estado `pendiente`/`finalizado`. El estado se cambia clickeando la celda (`PUT /cheques/{id}/estado`), sin reabrir la operación; al cerrar se sella `cerrado_at`. La **moneda no se carga**: la define el banco elegido (las cuentas operativas ya son específicas por moneda). Los RECIBIDOS **finalizados** alimentan la fila **Ingresos e-cheqs** de BANCOS (esa plata NO viene en los movimientos de Aunesa → no hay doble conteo). Tab **VEPS** (`tesoreria_veps`): agenda de vencimientos, **TODOS egresos** (no hay ingresos). Mismo horizonte que los cheques EMITIDOS — tablero de SEGUIMIENTO, **no filtra por fecha** (un VEP viejo sin pagar sigue a la vista) y la fila NO se borra al pagarse. Columnas: numero_vep, concepto, importe, banco (default **AL2**), vencimiento, estado `pendiente`/`pagado` (se cambia clickeando la celda). **Vencido = fila AMARILLA**, y lo decide el BACKEND (campo `vencido` = vencimiento anterior a HOY en ART **y** sin pagar — un pagado no se marca aunque haya vencido), asi la marca no depende del reloj del navegador. **NO IMPACTA EL SALDO de BANCOS**, a proposito: el egreso ya entra al banco por REGISTROS MANUALES (tipo `VEP`) y contarlo tambien aca lo duplicaria — hay un test que falla si alguna fuente de la grilla llega a leer esa tabla. Dos origenes que conviven: carga manual en la tab (`origen=manual`) y **espejo automatico** al crear un registro manual de tipo VEP (`origen=registro`), que nace solo con lo que ese registro sabe (importe/banco/moneda) y se completa despues desde la tab; `registro_id` es UNICO, asi que re-guardar no duplica, y si el espejo falla el registro manual igual se guarda (es el que mueve el saldo). Misma allowlist de escritura que el resto de Tesoreria. Tab **BANCO A BANCO**: transferencias INTERNAS entre cuentas propias (`tesoreria_banco_a_banco`, carga manual del día) — el equipo mueve saldo de un banco a otro para dejarlos cubiertos. Las dos cuentas DEBEN estar en `tesoreria_cuentas` y ser de la MISMA moneda (con un solo importe no se puede representar un cambio de divisa), y distintas entre sí; todo validado server-side. Genera **dos filas más en BANCOS**: `bb_mas` (la cuenta de CRÉDITO recibe, suma) y `bb_menos` (la de DÉBITO entrega, resta) — entre todos los bancos suman **cero**: mueven el reparto, no el total. Botón **TXT HYGIRUS** (`GET /tesoreria/banco-a-banco/export-txt` → archivo `Bco a Bco.txt`): asiento de ajuste que el back office antes tipeaba a mano — cabecera `DD/MM/AAAA HH:MM:SS Asiento de ajuste` y, por cada transferencia con estado **≠ `completado`**, DOS líneas separadas por TAB (`-importe⇥hygirus_débito⇥moneda` y `importe⇥hygirus_crédito⇥moneda`, coma decimal y sin separador de miles). La cuenta que se escribe es el `numero_hygirus` del catálogo, NO la denominación. El archivo **se genera siempre**: si no quedan pendientes sale solo con la cabecera y sin filas, y si a algún banco le falta el `numero_hygirus` la cuenta va vacía pero la vista avisa cuál es. El endpoint **nunca devuelve error** y manda el contenido dentro de un JSON — el proxy de Next parsea toda respuesta como JSON y mapea cualquier error a un 502 sin mensaje. **Saldo final = inicial + ingresos + ingresos_echeq − egresos − egresos_echeq + mercados + fci + bb_mas − bb_menos** (las dos filas e-cheq van separadas de los totales solo para distinguirlas; las dos entran al saldo). COMITENTE sale del padrón `clientes.cuentas` con CUIT autocompletado de `clientes.comitentes`; BANCO del catálogo `tesoreria_cuentas` (las mismas cards de BANCOS); misma allowlist de escritura que los saldos. Modal **REGISTROS MANUALES** (botón en la barra de BANCOS, `tesoreria_registros`): FUENTE NUEVA de movimientos que NO viene de la API. Tiene **DOS tabs** y la columna `grupo` es lo único que las separa: **RESCATE ACA VALORES** (`grupo='rescate'`, default) con `tipo` acotado al catálogo fijo (PROVEEDORES/FONDOS FIJOS/VEP/HABERES/IMPUESTO/TARJETA VISA/OTROS), y **OTROS REGISTROS** (`grupo='otros'`) con `tipo` de **texto LIBRE**. Cada tab es 50/50 — izquierda la carga (TIPO · IMPORTE · BANCO · sentido `egreso`(default)/`ingreso`, con usuario y hora del backend), derecha el resumen por TIPO; la fila `SALDOS` (solo en el rescate) es MANUAL y vive aparte en `tesoreria_registros_saldo` (PK fecha+unidad). **Los dos grupos entran igual a las filas Ingresos/Egresos** del banco elegido según su sentido (el detalle de esa celda los marca como `registro manual`, y agrega `(otros)` cuando no son del rescate) — el `grupo` NO cambia el saldo, solo separa los resúmenes: lo cargado en OTROS REGISTROS **no suma** al total del rescate. El **TOTAL de RESCATE ACA VALORES** (saldo manual + registros del grupo, por moneda) se muestra en la **barra de la vista, al lado de SACAR FOTO** — lo calcula el backend (`totales_rescate`, campo `rescate` de `/tesoreria/dia`) con la MISMA fuente que el modal, así la barra no puede contradecirlo. **Cada celda de la grilla BANCOS es auditable**: clic → `GET /tesoreria/detalle?banco&unidad&fila&fecha` devuelve las operaciones individuales que la componen, cada una con su **tilde** (destildar = no cuenta en el saldo final, `PUT /tesoreria/exclusion` → `tesoreria_exclusiones`, solo overrides) y su **observación** («anulado por x@y a las 14:32»). **Los movimientos de Aunesa SIN HORA arrancan DESTILDADOS** por duplicidad (su `id` no trae fecha-hora, no se distinguen de un duplicado); se tildan a mano si corresponde. **El ESTADO del detalle no miente**: mercados/FCI/banco-a-banco cuentan también lo `pendiente` (decisión del back office) y el modal muestra el estado REAL de cada fila, resaltado en ámbar cuando no es plata cerrada; los REGISTROS MANUALES van con estado vacío (no tienen estado — que son manuales ya lo dice el detalle). El **saldo final tambien sale del backend**: el front lo muestra tal cual, ya no lo recalcula (se borro la copia de la formula que vivia en `normalizarCuentas`, verificada antes con `scripts/diag_tesoreria_front_vs_back` — 48 filas, diferencia 0.000000). Las operaciones del modal se calculan SERVER-SIDE con las mismas fuentes y filtros que la grilla (el front no recalcula nada, así el detalle no puede contradecir al total); `fila=saldo_final` devuelve la ecuación completa fila por fila. **FOTO del día** (`tesoreria_snapshots`, 2026-08-06): la vista es live y no se persiste, así que al cierre se congela la grilla BANCOS + el detalle de CADA celda (`_detalle_dia`, una pasada: 1 llamada a Aunesa + 1 query por fuente — el modal live y la foto usan la MISMA función, no pueden divergir). **UNA foto por `fecha`** (re-sacarla PISA la del día; el historial de quién/cuándo queda en `tesoreria_audit`) y **TTL de 30 fechas**, purgadas en el mismo INSERT. `hash_sha256` del payload detecta ediciones hechas por fuera de la API (`hash_ok` en la respuesta). La saca `jobs/tesoreria_snapshot.py` (23:50 ART = `50 2 * * 2-6` UTC) y el botón **SACAR FOTO** de la barra (allowlist de escritura). **El selector FECHA aparece SOLO en la tab BANCOS**: elegir un día pasado sirve la foto (`GET /tesoreria/foto`, solo lectura, sin ABM ni carga) y el modal de auditoría lee el detalle congelado sin volver a pegarle a Aunesa. MOVIMIENTOS (y CHEQUES/MERCADOS/BANCO A BANCO) son SIEMPRE del día: **el histórico de movimientos NO se duplica** — se navega desde la celda de BANCOS que los usa. `tesoreria_presencia` = quién tiene la vista abierta (el poll de 20s ES el heartbeat, TTL 90s). `acavalores_retorno` (informe Excel "OP Aca Valores FCI" del fondo ACA R.TOTAL, carga NO diaria y EXCLUSIVA por `scripts/import_acavalores_retorno.py`, idempotente por `periodo` YYYY-MM — alimenta la tab "ACA VALORES RETORNO TOTAL": Σ Valor Nominal por operación/agente/papel).
- `clientes` — `comitentes`, `cuentas`, `contrapartes`, `accionistas`, `actividad_mensual`, `operadores`, `objetivos_comerciales` (segmentación/operador).
- `manager` — `manager_users`, `role_matrix`, `role_audit`, `grupos`, `job_runs`, `pyrofex_instruments`/`pyrofex_discovery`; `latencia_endpoints` (telemetría de LATENCIA endpoint × hora — agregado que flushea el middleware `api/telemetria.py`, lo lee `GET /api/manager/latencia`; la vieja `uso_modulos` fue ELIMINADA 2026-08-04); `asistente_chats`/`asistente_mappings` (ASISTENTE DE NEGOCIO — QuantAI P7: transcript real + mapping ficha↔identidad de la aduana `core/pii_gateway.py`; módulo RBAC `asistente`, admin-only; NO tiene endpoint propio — es la vista `negocio` del copiloto, cerebro en `api/services/asistente.py`).
- `home` — `market_quotes` (watchlist HOME), `news_headlines`.
- SALUD (Manager → OBSERVABILIDAD → **SALUD**, la primera pill y el default): UN modelo de CHEQUEO que unifica toda la observabilidad (`api/services/salud.py`). Nace del incidente 2026-08-07 — el backfill de tenencias fallo dos dias y nadie se entero: no faltaban datos (job_runs, controles_datos, latencia, arbol de diagnostico, triage con IA) sino que estaban en seis pantallas y **ninguna respondia si el sistema estaba sano** (la card de AuM en VERDE con el job muerto hacia 48h). De un job importan TRES preguntas y solo se miraba la segunda: **corrio cuando debia** (la resta que nadie hacia), **salio bien**, **dejo el dato fresco**. Los chequeos se generan SOLOS: los jobs de `deploy/crontab.txt` via `jobs_catalogo` (un cron nuevo aparece sin tocar nada) cruzando el schedule contra el ultimo run; lo unico a mano son los CONTRATOS DE FRESCURA (`CONTRATOS` en el service, 5 tablas criticas — sumar una es UNA linea) porque ninguna maquina puede adivinar que `portafolio.tenencia` debe tener el ultimo dia habil. Un job sin instrumentar es **WARN, no verde**: no se puede afirmar que esta bien algo de lo que no se sabe nada. El estado actual NO se persiste (se evalua en vivo); si se guardan `manager.salud_eventos` (las TRANSICIONES, append-only, con la evidencia congelada — una vez que el job vuelve a correr el motivo ya no existe), `manager.salud_config` (toggle de alerta por chequeo; silenciar NO lo saca de la pantalla, solo deja de abrir el modal; lo no configurado ALERTA por default para que un chequeo nuevo avise sin darlo de alta) y `manager.salud_vistos`. El MODAL vive en el layout del front (no en Manager) y solo abre ante una transicion NUEVA a problema y sin ver — nunca cuando algo se arregla, y nunca en intervalo fijo: un modal que repite lo mismo se cierra sin leer. Endpoints `GET /api/manager/salud` (sincroniza de paso, idempotente), `/salud/historial`, `PUT /salud/alerta`, `POST /salud/vistos`.
- `mcp` — `oauth_clients`/`oauth_codes`/`oauth_tokens` (TTL automático).
- `ia` — observabilidad del gateway de IA (`core/ai.py`, ver `docs/QUANTAI.md`): `trazas` (cada llamada LLM: tarea, modelo, tokens, latencia, ok/error, feedback, detalle/respuesta/razonamiento, conv_id) y `config` (presupuestos editables desde Manager). Router HTTP: `api/routers/ia.py` (bearer + `require_module("ia")`): briefing, observabilidad, presupuestos/saldo, y el COPILOTO de mesa (`/api/ia/copiloto*` — **doc vivo con changelog OBLIGATORIO: `docs/COPILOTO.md`**, leerlo antes de tocar `api/services/copiloto.py`). También: `triage_incidentes`/`triage_estado` (triage IA de jobs fallidos, `jobs/triage.py` cada 10') y `research` (mail diario 1816 vía IMAP, `jobs/research_mail.py` — cuerpo crudo + destilado LLM + FTS español).
- `research` — market data 1816 para la vista Research: `mkt_1816_series`/`mkt_1816_watch`/`mkt_1816_instrumentos` (feed SEPARADO de `mercado.curvas` — ver `docs/VISTA_RESEARCH.md`); tab BCRA: `bcra_variables`/`bcra_watch`/`bcra_series` (ver `docs/RESEARCH_BCRA.md`).
- `aca` — **RESUMEN EJECUTIVO DE INVERSIONES** (vista `/aca` — **doc vivo: `docs/ACA.md`**). La cartera PROPIA de ACA contada para los gerentes: NO es live, es una **foto MENSUAL** que la mesa carga a mano. Criterio: *un Excel con las fórmulas ya puestas* — lo derivable se deriva SIEMPRE server-side (montos, ponderaciones, share, métricas, acumulados) y solo se tipea lo que ninguna fuente tiene (el **precio de corte del mes**, el VN, el MEP/A3500 del informe, los rendimientos externos). Tablas: `periodos` (cabecera: fecha del informe + MEP + A3500), `activos` (PK periodo+unidad — **solo inputs**: vn, px, override de monto, tasa, obs), `moneda_regla`, `emisor_destacado`/`clase_destacada` (catálogos de las métricas), `series` + `historico` (la planilla mensual), `audit`. **La FICHA del título NO se duplica**: cartera/emisor/calificación/clase/vencimiento/ticker se resuelven en cada lectura contra `portafolio.assets` (el maestro de Manager → Títulos) — copiarla habría creado una segunda verdad que se desincroniza sola, como ya mostró el rebautizo de especies de Aunesa. `monto = vn × px / 100` para carteras ARS/DL/HD (paridad) y `vn × px` para FCI/RV — **la misma regla de divisor que la valuación del AuM**; sin vn o sin px el monto es **null, no 0** ("sin cargar" ≠ "vale cero"). **Total Dolarizado / Total Pesos** salen de `moneda_regla`, editable en Manager → ACA, donde la regla de **CLASE gana sobre la de CARTERA**: eso es lo que parte el FCI por moneda (MM USD y HD T1 → dólares; MM ARS, ARS T1 y RENTA VARIABLE → pesos) **sin sacarlo de su cartera** — verificado contra la planilla, donde HD+DL da 83% y el Total Dolarizado del informe dice 92,7%, y la diferencia son exactamente los FCI en USD. Lo que ninguna regla ubica NO se reparte a dedo: cae en `sin_clasificar` y la vista lo canta. **Acumulado del histórico = (1 + acum anterior) × (1 + mensual) − 1, y NO se persiste** (se deriva en la lectura, así no puede contradecir a sus insumos); un mes sin dato ARRASTRA el acumulado, no lo reinicia. Benchmarks: `fuente` puede ser `manual`, `macro_var:<SERIE>` (variación mes contra mes — es un cociente, **no depende de la unidad**, y es el único sembrado: A3500 → DOLAR) o `macro_pct:<SERIE>` (**sí depende de la unidad** → medir con `python -m scripts.diag_aca_benchmarks` antes de activarla, REGLA #2); **el valor manual siempre gana sobre el automático**. RBAC: lectura = módulo `aca` (rol **`empleado_aca`**) ∪ admin ∪ escritores; escritura = **la allowlist de Mesa de Dinero** (`operaciones.mesa_dinero_escritores`) + admin. `empleado_aca` es un rol NUEVO y no `sales` renombrado a propósito: `sales` es `DEFAULT_ROLE` y todo email nuevo cae ahí, así que colgarle `aca` habría dado la cartera de la casa a cualquier alta automática. **JAMÁS al portal invitado** (REGLA #8, congelado por test). La vista se sirve en UN request: `GET /api/aca/vista`.
- `estrategia` — ESTRATEGIA QUANT (señal intradía con trazabilidad, tab ESTRATEGIA de Trading — **doc vivo: `docs/ESTRATEGIA_QUANT.md`**): `senales` (ledger append-only), `resultados` (resolver intradía por horizonte), `modelo_pesos` (versionado), `eval_live` (última evaluación por ticker).

## Contexto por subdirectorio

Cada carpeta grande tiene su propio `CLAUDE.md` con lo que aplica SOLO ahí —
se carga automáticamente al trabajar en esa carpeta. Este archivo (raíz)
tiene lo que aplica a todo el repo.

- **`api/CLAUDE.md`** — ⚠️ REGLA #1 (validar imports), RBAC, services `@cached`, filtros de cuenta, live fallback, motor de PnL.
- **`engines/CLAUDE.md`** — patrón de escritura a `mercado.market_snapshot`, motores stale.
- **`jobs/CLAUDE.md`** — filtros de exclusión del AuM, patrón de jobs nuevos (`JobRunLogger`).
- **`scripts/CLAUDE.md`** — REGLA #0 aplicada, minimalismo (REGLA #5), backfills seguros (REGLA #4).

## ⚠️ REGLA #0 — Cómo entregar trabajo al usuario (LEER PRIMERO)

**Claude NO tiene ni va a tener acceso al Droplet.** Todo lo que tenga que correr en producción se entrega como código en el repo, no como comando para copiar.

- **Nada de bloques de comandos / queries / snippets para que el user copie y pegue.** Operar el Droplet desde la consola web de DigitalOcean hace que copiar y pegar sea doloroso (line wrapping, multilinea, caracteres especiales). Esta regla ya se pidió varias veces y se sigue violando.
- **Workflow correcto**: Claude escribe el código → archivo en el repo (`scripts/<x>.py`, `jobs/<x>.py`, endpoint en `api/`) → commit + push a `main` → el user hace `git pull` en el Droplet y lo ejecuta con `python -m scripts.<x>`.
- **Diagnóstico one-shot también va a `scripts/`** (ej. `scripts/diag_*.py`). Una query SQL de 5 líneas igual va en archivo, no en chat.
- **Excepción mínima**: si es UNA sola línea trivial (`systemctl status x`, `tail logs`), se puede pasar inline — pero el default es siempre script.
- **Cero "probá esto, si no andá probá esto otro"**. Una solución por vez, comiteada al repo.

## ⚠️ REGLA #2 — NUNCA ASUMIR: verificar antes de afirmar o codear

**Bloqueante. Es la causa #1 de romper cosas.** Claude NO tiene acceso al
Droplet ni a la DB de prod (Postgres/Supabase) → no puede inferir nada sobre los datos reales. Afirmar
hechos sobre prod sin medir (proporciones, volúmenes, esquema, qué campos
existen, qué valores tienen, cómo se comporta algo) y después escribir código
en función de eso es lo que rompe todo.

- **Prohibido afirmar hechos no verificados sobre los datos/prod.** Nada de
  "X es una minoría", "esto normalmente trae…", "probablemente el campo…",
  "la mayoría de los docs…". Si no lo mediste, no es un hecho.
- **Distinguir SIEMPRE hipótesis de hecho verificado**, explícito y en voz alta.
  "Hipótesis (sin medir): …" vs "Verificado (corriste el diag): …".
- **NUNCA escribir código cuya CORRECTITUD dependa de una suposición no
  verificada.** Si la decisión necesita un dato de prod, primero conseguirlo.
- **Para conseguir un dato de prod**: escribir un diag read-only
  (`scripts/diag_*.py`, REGLA #0), el user lo corre y devuelve el número. Recién
  ahí se decide/codea. Si no se puede medir, decir explícito "no puedo verificar
  esto" y esperar confirmación — no avanzar a ciegas.
- **Optimizar = medir primero** (`explain()` / timing), después tocar. Nada de
  optimizaciones justificadas por una corazonada sobre cómo lucen los datos.

## ⚠️ REGLA #3 — TODO cambio lleva EXPLICACIÓN EJECUTIVA (a raja tabla)

**Obligatorio, sin excepción.** Cada cambio que se entrega (commit, script,
endpoint, fix, refactor, config) va acompañado de una explicación ejecutiva
breve, en lenguaje claro (no técnico-críptico), con DOS partes:

- **Qué soluciona** — el problema concreto que resuelve / la pregunta que
  responde. Por qué se hizo.
- **Qué genera / qué impacto tiene** — qué cambia en el sistema a partir de
  ahora: comportamiento nuevo, efectos colaterales, qué hay que correr/deployar,
  qué se gana (perf, plata, visibilidad), qué riesgo introduce si lo hay.

Formato sugerido (corto, va en el mensaje al user, no necesariamente en el código):

```
📋 Qué soluciona: …
📋 Qué genera:    …
```

El user opera solo un proyecto enorme y necesita entender el "qué" y el "para
qué" de cada cambio sin leer el diff. Un cambio sin esta explicación está
INCOMPLETO. Aplica también a los diags y a los cambios de doc.

## ⚠️ REGLA #4 — Backfills/migraciones JAMÁS escanean prod a ciegas

**Causó dos veces el CPU 100% en el M10. Bloqueante.** Ningún backfill,
migración o `--full` se corre sin cumplir TODO esto:

- **Scopeado**: apuntar SOLO a los docs que realmente cambian (ej. `bruto=0`),
  nunca un scan de toda la tabla si se puede filtrar por índice.
- **Batcheado + throttle**: procesar en lotes con `sleep` entre lotes para no
  starvar a los motores. Nada de un `bulk_write` gigante de una.
- **Medir el costo ANTES** (REGLA #2): `explain()` / contar docs afectados. Si
  toca un scan grande, decirlo explícito y decidir.
- **Vía `run_job.sh`** (lock + timeout) y, salvo que sea liviano y scopeado,
  **fuera de rueda** (no 13-20 UTC L-V, cuando corren los motores).
- **Idempotente**: cortarlo a la mitad y re-correrlo no rompe nada.

Un `--full` a ciegas en horario de mercado es exactamente el anti-patrón del
incidente 2026-06-03. Si dudás del volumen, NO lo corras: medí primero.

## ⚠️ REGLA #5 — Minimalismo en `scripts/` Y `docs/`: se BORRA lo cumplido

**Minimalismo, no cementerio.** Aplica a código Y documentación.

- **`scripts/`:** cada `diag_*`/`fix_*`/`backfill_*`/`seed_*` one-shot, una vez que
  el user confirma que el tema cerró, **se elimina**. Queda solo lo recurrente
  (generadores, monitoreo, perf, audit, feeds) + lo referenciado por skills/CI/cron.
- **`docs/`:** las auditorías/análisis point-in-time, los `wip_*`, las imágenes
  scratch y todo lo superseded **se borran o se consolidan**. La arquitectura/
  datos/estrategia/roadmap viven en **UN doc madre: `docs/ARQUITECTURA.md`** — no
  esparcidos en N archivos. El resto de `docs/` es referencia operativa viva (API,
  RUNBOOK, MCP, seguridad, etc.) + el `vault/` auto-generado.
- Ante la duda, preguntar "¿lo borro?" — no acumular por las dudas.

## ⚠️ REGLA #6 — Credencial/acceso faltante: se pide UNA vez, no se insiste

Si para avanzar hace falta una credencial/usuario/permiso que **solo el user
puede crear** (ej. una env var o un rol de Postgres/Supabase), se
dice **una vez**, claro, y se marca como PENDIENTE. **No repetir el pedido cada
turno ni bloquear todo en eso** — seguir con lo que sí se puede hacer. El user
lo provee cuando puede.

## ⚠️ REGLA #7 — IR MÁS ALLÁ: enseñar y proponer, no solo ejecutar

El user es PM (no dev) y depende de Claude para crecer técnicamente: *"no tengo
manera de capacitarme y aprendo si no es con vos"*. En CADA trabajo, además de
resolver lo pedido:

- **Detectar lo que él no sabe pedir**: joins innecesarios, queries ineficientes,
  tablas mal modeladas, código que se puede simplificar, deuda técnica,
  riesgos de datos. Traerlo proactivamente aunque no lo haya pedido.
- **Enseñar el porqué**: explicar el concepto nuevo en lenguaje claro (gerencial
  + técnico), no solo aplicarlo. Que aprenda algo en cada interacción.
- **Proponer estructura nueva**, no solo optimizar lo existente al máximo. Leer
  como arquitecto SR: cuestionar el diseño de base.
- Esto NO reemplaza la REGLA #2 (no asumir, medir primero) ni el formato ejecutivo
  (REGLA #3). Va arriba de eso: hacer el trabajo Y dejar conocimiento.

Ver memorias [[feedback_proactive_architect]] y [[feedback_autonomy_lanes]].

## ⚠️ REGLA #8 — Portal INVITADO (www.acaquant.com): SOLO mercado/research, nunca filtrar datos del negocio

**Bloqueante.** Conviven dos portales:

- **trading.acaquant.com** — app interna de la mesa. Usuarios de la mesa
  (admin/trader/sales/etc.), ven todo según su rol.
- **www.acaquant.com** — **portal INVITADO**: lo usa gente de **OTRO SECTOR de
  la MISMA empresa** (grupo ACA — aclaración del user 2026-07-21; NO son
  terceros, así que el contenido licenciado 1816/Reuters no sale de la
  compañía). Ven **mercado + research** (read-only) + los copilotos de IA de
  esas vistas (identidad `guest:<email>`, tope 100k/día c/u, sin la guía).

Lo que NO cambia y JAMÁS se pasa por alto: cualquier cosa del **NEGOCIO de la
mesa** (portfolios, operaciones, manager, back-office, acreencias, gestión de
ONs, clientes, AuM, P&L, contrapartes, segmentación, la guía de la plataforma,
etc.) **NUNCA** puede quedar accesible al invitado — otro sector tampoco ve el
negocio de la mesa. Si un desarrollo nuevo no es de mercado/research, no se
mete en el portal www — punto.

- El backend fuerza rol `invitado` (default-deny) cuando ve el header
  `x-acaquant-portal: guest` (`api/auth.py::is_guest_portal` + check contra
  `core.roles.INVITADO_MODULES`). Agregar algo a `INVITADO_MODULES` es una
  decisión de SEGURIDAD — solo mercado.
- El frontend `acaquant-web` filtra nav/vistas por módulo; el invitado no debe
  ver ni el link de algo que no sea mercado.
- **Default-deny**: ante la duda, NO exponer al invitado.

Ver memoria [[feedback_portal_invitado_www]].

## Reglas que rompen todo si se olvidan

- **`python -m <módulo>` desde la raíz siempre**. `python engines/x.py` falla (`core` no es discoverable).
- **Conexión SQL**: pool singleton `core.postgres.get_pool()` (no cerrarlo).
- **Regla de capas**: `core/` no importa nada del proyecto. `engines/` y `jobs/` usan `core/` + `quant/`. `api/services/` es puro (sin FastAPI), `api/routers/` solo HTTP plumbing.
- **Commits**: estilo `feat/fix/docs/refactor(scope): mensaje` en español, como el `git log`.
- **Constantes globales y feature flags** viven en `config.py` (raíz): `TICKERS_EXTRA_PRECIOS`, `TICKERS_BOOK_FULL`, etc. Env vars en `.env` local / systemd unit files en el Droplet (`MANAGER_EMAILS`, `DEFAULT_ROLE`, `MCP_*`, `POSTGRES_URI`).

> Validar imports antes de pushear router/service (REGLA #1) y la regla de
> services `@cached` → ver `api/CLAUDE.md`.

## Estructura

```
core/        # infra (postgres, pg_mirror, ai [gateway LLM], ai_resumen, curvas_sql, dolar_sql, grupos_sql, roles_sql, series_macro, market_snapshot, websocket, rofex_session, rofex_orders_session, roles, job_runs, profiler, byma, mae, cafci, finnhub, yahoo, argentina_datos, dolar_oficial)
engines/     # motores WS → SQL (always-on L-V 13-20 UTC) — incluye motor_cedears (alimenta Scanner CEDEARs)
jobs/        # batch/cron — incluye precios_acciones_daily (alimenta scanner via SQL mercado.precios_acciones)
quant/       # cálculo puro (black_scholes, stats, curve_fit, pivot_points, rolling_stats)
api/services # lógica pura (invocada por routers y por el agente)
api/routers  # thin HTTP wrappers. manager/ es paquete de sub-routers
api/mcp/     # MCP server (FastMCP) + OAuth 2.1 provider + discovery
scripts/     # one-shot / migraciones / smoke
tests/       # pytest — unit/ + integration/ (marker `integration`, excluido por defecto via addopts)
evals/       # datasets de evaluación del programa QuantAI (ver docs/QUANTAI.md)
sql/         # schema.sql — espejo relacional Postgres/Supabase (ver "Capa SQL")
deploy/      # systemd + crontab.txt (fuente de verdad)
.claude/     # settings.json + hooks + commands + skills + agents (ver .claude/INDEX.md)
docs/        # documentación (ver "Mapa de docs" abajo) + vault/ (cerebro Obsidian, auto-generado)
```

## Mapa de docs — cuál leer ANTES de tocar cada dominio

`docs/ARQUITECTURA.md` es el **DOC MADRE** (arquitectura/datos/estrategia/roadmap).
Los marcados **[VIVO]** tienen changelog obligatorio: si tocás ese dominio y no
actualizaste su doc en el mismo commit, el trabajo está incompleto.

| Dominio / si vas a tocar… | Doc |
|---|---|
| Arquitectura, datos, roadmap | `ARQUITECTURA.md` (madre) |
| Qué vistas/tabs/endpoints/permisos hay (superficie completa) | `MAPA_APP.md` **[VIVO]** |
| Modelo SQL / schema | `SQL.md` + `SQL_MODELO.md` + `sql/schema.sql` |
| Programa de IA (gateway `core/ai`, briefing, triage) | `QUANTAI.md` **[VIVO]** |
| Copiloto de mesa (`api/services/copiloto.py`) | `COPILOTO.md` **[VIVO]** |
| Agregar una TOOL al asistente/copiloto | `TOOLS_IA.md` **[VIVO]** (auditoría de huecos + tandas) |
| Vista `/research` (1816, mail diario) | `VISTA_RESEARCH.md` **[VIVO]** |
| Research → tab BCRA / FRED | `RESEARCH_BCRA.md` · `RESEARCH_FRED.md` |
| Feed Eikon live / tab REUTERS (`eikon_*`) | `INTEGRACION_REUTERS.md` **[VIVO]** |
| Interbanking (bancos: cuentas, saldos, extractos, transferencias) | `INTERBANKING.md` **[VIVO]** |
| Renta fija / curvas | `RENTA_FIJA.md` · `SALUD_CURVAS.md` |
| Renta variable / scanner | `RENTA_VARIABLE.md` |
| Estrategia Quant (señal intradía, tab ESTRATEGIA de Trading) | `ESTRATEGIA_QUANT.md` **[VIVO]** |
| Vista `/aca` (resumen ejecutivo de la cartera propia) | `ACA.md` **[VIVO]** |
| Derivados · sintéticos · agro | `DERIVADOS.md` · `SINTETICOS.md` · `AGRO.md` |
| Valuaciones / PnL | `MOTOR_VALUACIONES.md` |
| MCP server / tools | `MCP.md` · `MCP_TOOLS.md` |
| Operación, incidentes, monitoreo | `RUNBOOK.md` · `OBSERVABILIDAD_ROBUSTEZ.md` |
| Seguridad / credenciales | `SECURITY.md` · `SECRETS.md` |
| Clientes / grupos / segmentación | `GRUPOS.md` · `SEGMENTACION_PATRIMONIAL.md` |
| API HTTP (contratos) | `API.md` |

Auto-generados (NO editar a mano): `HERRAMIENTAS.md`, `vault/`, `deploy/SISTEMA.md`.

## Mapa de la app — `docs/MAPA_APP.md` (LEER al empezar una sesión)

**Es el índice de TODA la superficie**: 20 vistas, sus tabs, sus filtros, qué endpoint
consume cada cosa, quién la ve y qué puede escribir. Leerlo AHORRA re-relevar la app
(que cuesta horas y cientos de miles de tokens). Si vas a tocar cualquier vista,
empezá por ahí.

**Se mantiene en DOS capas, a propósito:**

- **§0 AUTO-GENERADA** (inventario de endpoints + gate efectivo por router + matriz
  rol × módulo). La regenera un script determinista desde la app FastAPI montada —
  no puede mentir y no cuesta tokens:

  ```bash
  python -m scripts.gen_mapa_app          # regenera los bloques AUTOGEN
  python -m scripts.gen_mapa_app --check  # CI: falla si quedó desincronizado
  python -m scripts.gen_mapa_app --full   # las 400+ rutas, a stdout (no al doc)
  ```

  **CI corre `--check` y BLOQUEA el merge** si agregaste un router y no regeneraste.

- **El resto, A MANO.** Qué hace cada vista, sus tabs, sus filtros, las acciones de
  escritura y la sección de huecos/rarezas. Cambia despacio y ahí está el criterio.
  **Si agregás o cambiás una VISTA, una TAB o un FILTRO, actualizá su sección en el
  MISMO commit** — igual que los docs `[VIVO]`. Un endpoint nuevo lo detecta el
  script; una tab nueva no la detecta nadie.

> ⚠️ **Trampa de FastAPI que ya rompió al tooling de seguridad**: `app.routes` NO
> trae las rutas de los `include_router` — trae envoltorios `_IncludedRouter`, y las
> rutas reales cuelgan de `.original_router.routes`. Un `for r in app.routes` ingenuo
> ve **5 de 428** y no falla: devuelve poco, en silencio. Lo mismo con los gates: las
> `dependencies=` del include viven en `route.include_context`, no bajan a cada ruta.
> `scripts/gen_mapa_app.py` resuelve las dos cosas — copiá de ahí, no reinventes
> (`scripts/audit_rbac.py` y `tests/unit/test_rbac_superficie.py` están ciegos por esto).

## Plano del sistema — `deploy/SISTEMA.md`

Fuente de verdad de TODO lo que corre: servicios systemd, motores, crons y
cómo se conectan. **Si agregás / quitás / modificás un servicio systemd o un
cron** (tocás `deploy/systemd/*.service` o `deploy/crontab.txt`), en el MISMO
cambio regenerá el plano:

```bash
python -m scripts.gen_sistema          # regenera las tablas (no editar a mano entre marcadores AUTOGEN)
python -m scripts.gen_sistema --check  # falla si SISTEMA.md quedó desincronizado
```

El inventario (servicios/motores/crons) es auto-generado desde la fuente
real → no puede mentir. La narrativa (topología, flujo de datos, bases) se
mantiene a mano. Si cambió cómo se conectan los servicios, actualizá esa
parte también. Skill: `/sistema`.

## Cerebro Obsidian — `docs/vault/`

Grafo navegable de TODO el sistema (módulos, componentes, rutas, crons,
colecciones, vistas, services, libs) como vault de Obsidian. El generador
`scripts/gen_obsidian.py` es **determinista**: parsea el código y reconstruye
archivos, links y backlinks de cada nota. La sección _Qué hace_ se completa con
una pasada de enriquecimiento con IA; el resto NO se edita a mano.

```bash
python -m scripts.gen_obsidian          # regenera las notas
python -m scripts.gen_obsidian --check  # CI: falla si el vault quedó stale
```

Mismo contrato que `gen_sistema`: tras un cambio estructural (router/cron/
colección/componente nuevo) el vault queda desincronizado → regenerar en el
mismo cambio. Cómo abrirlo: `docs/vault/README.md`.

## Comandos

```bash
uvicorn api.main:app --reload --port 8000
python -m engines.<motor> | jobs.<job> | scripts.<cmd>
ruff check . [--fix]                           # line-length=100, py312
pytest -ra                                     # unit (pyproject ya excluye integration via addopts)
pytest tests/<path>::<test_name>               # single test
pytest -m integration                          # integration (requiere Postgres accesible)
python -m scripts.perf_scan [--strict]         # anti-patterns de queries
```

CI (`.github/workflows/ci.yml`): en cada push/PR a `main` corre `ruff check .` (bloqueante) + `perf_scan` (informativo, `continue-on-error`) + `pytest -ra` (solo unit). Python 3.12. No buildea el frontend.

## Frontend en repo hermano

`../acaquant-web/` (Next.js 16, deploy auto a Vercel sobre `main` — `src/proxy.ts`, no middleware). **No es submodule** — es checkout paralelo. Cambios de API con impacto en UI se editan ahí con rutas absolutas (`C:\...\acaquant-web\...`). Las routes de Next que consumen endpoints "live fallback" necesitan `dynamic = "force-dynamic"` + `revalidate = 0` + `Cache-Control: no-store` (ver `api/CLAUDE.md`).

## Tablero Comercial (lente por operador)

El Tablero Comercial se sirve SQL-only desde `api/services/comercial_sql.py` (el router `operaciones.py::_com_motor` siempre devuelve SQL; `comercial.py` quedó como helpers/funciones SQL — ver "Capa SQL"). Cruza todo por `id_cuenta`: QUIÉN (`clientes.comitentes` → operador + `nivel_1`), ACTIVIDAD (`operaciones.negocio_movimientos`/`operaciones.operaciones` → última op), TAMAÑO (`portafolio.tenencia`, `aum='si'`), operador↔usuario (`manager.manager_users`, para cuentas huérfanas). Estado comercial por días desde última op: ACTIVA ≤45 / ENFRIANDOSE 45-90 / DORMIDA / NUEVA. Agrega EN VIVO con índices (sin precompute — no se recrean rollups).

**DÍAS SIN OPERAR es auditable por fila** (2026-08-07, mismo patrón que el modal por celda de Tesorería → BANCOS): click en una fila de la tabla ESTADO COMERCIAL abre `GET /api/operaciones/comercial/analisis/detalle?id_cuenta&fecha` (`comercial_sql.detalle_ultima_op`) y muestra **cuál boleto** fija el número — todos los boletos de ese día, el historial reciente, y los que **NO** cuentan con su motivo (anulados, posteriores al corte en modo foto). El insumo es `operaciones.operaciones`: cuenta CUALQUIER boleto no anulado, sin filtro de tipo/mercado/etapa. Ese predicado vive UNA sola vez (`comercial_sql._ULT_OP_WHERE`) y lo comparten la tabla y el modal — el front no recalcula nada, así el detalle no puede contradecir al número.

## Operaciones — SQL (migrado de Mongo 2026-06-16, CRÍTICO no inferible)

`operaciones.operaciones` (SQL Postgres) es la fuente de la vista MOVIMIENTOS (`/api/operaciones/ops/*`) + Contrapartes (`/operaciones/flujo`). **`CashFlow.Operaciones` (Mongo) y el rollup `CashFlow.OpsSerieDiaria` fueron ELIMINADOS** — ver `docs/SQL.md`. Origen: `jobs.operaciones_informes` (API informes Aunesa) que escribe SQL directo vía `operaciones_informes.ingestar_filas_sql` (normaliza + enriquece inline `moneda`/`mercado`/`operacion`/`nivel_3`/`segmento`/`es_cierre`/`commodity`/`mep`). `jobs.fci_bilateral` escribe el FCI bilateral (campo `etapa`) — upsert por boleto que NO pisa el resto. El catálogo `tipos_operacion` vive en SQL (`operaciones.tipos_operacion`).

**Series: HOT/COLD (decisión 2026-08-04, revierte el "no precomputes").** Los días CERRADOS viven pre-agregados en `operaciones.ops_agregado_diario` (mantenida por `jobs/ops_agregado` cada hora en rueda — recomputa POR DÍA SUCIO vía `ingestado_en`, así los backfills históricos re-agregan su día solo y NO puede driftear como el viejo `ops_rollup`); HOY se agrega EN VIVO. `/ops/serie` sin filtros lee agregado+hoy; con filtros va 100% en vivo (`GROUP BY` + índices, `api/services/operaciones_sql.py`). `/ops/aranceles` sigue 100% en vivo (candidato a adoptar el agregado).

- **El arancel y el bruto NO comparten filtro de cierre**: para volumen `bruto` excluye `es_cierre=true`; para `arancel` se INCLUYEN los cierres (el **arancel de caución vive SOLO en el cierre**). `etapa <> 'solicitud'` siempre (la liquidación CL ya cuenta).
- **`es_cierre`** materializado (bool) separa volumen de arancel sin regex.
- El motor de PnL no usa esta tabla (cost-basis sale de `negocio_movimientos`); acá viven volumen/arancel comercial.
- Opciones mantiene su rollup propio (`jobs/options_rollup.py`) sobre tablas SQL.

## mercado.curvas — shape de flujos (CRÍTICO, no inferible)

- **CER**: porcentual. `amortizacion_pct` + `cupon_sobre_residual` YA resuelto (NO re-multiplicar por `residual_previo_pct`). `cupon_anual=0` si zero coupon. Requiere `cer_emision`.
- **tasa_fija**: absolutos. `amortizacion` + `interes`. Requiere `flujo_vencimiento`.
- **soberanos** (`tipo='globales'|'bonares'`): mismo shape que CER, `cupon_sobre_residual` ya en USD.

Agregar instrumento: fila en `mercado.curvas` (vía `core/curvas_sql.py`; `data` jsonb = doc completo) + fila en `portafolio.assets` con el MISMO `ticker`. Sin el segundo no aparece en AuM/Portfolios.

**Nombres de columna (renombre 2026-08-15).** En `mercado.curvas` la PK es **`ticker`** (`AL30` — el que joinea con `portafolio.assets.ticker`) e **`instrumento`** es el SÍMBOLO DE MERCADO (`MERV - XMEV - AL30 - 24hs`, lo que se le manda a Primary). Estaban invertidos: la PK se llamaba `ticker_corto` y `ticker` guardaba el símbolo. El eje bono/letra, que ocupaba el nombre `instrumento`, pasó a **`tipo_instrumento`**. ⚠️ **El blob `data` NO se renombró**: sus claves siguen siendo `ticker_corto`/`ticker` con el significado VIEJO, y son las que leen ~500 lugares vía `core/curvas_sql.py` (que hace `SELECT data`). Por eso los `SELECT` directos llevan alias (`instrumento AS ticker, ticker AS ticker_corto`): la base quedó correcta sin tocar una línea de lógica. Migrar el blob es el paso siguiente.

`config.TICKERS_EXTRA_PRECIOS`: tickers que `motor_rofex` suscribe pero `motor_curvas` ignora. Default `['MERV - XMEV - AL30C - 24hs']` para `/api/analitica/canje`.

## Fórmulas no inferibles

**AuM / tenencias** — fuente única SQL `portafolio.tenencia` (writer diario
`jobs/portafolio_backfill --diario`, 11:00 UTC L-V). Mongo `Valuaciones.AuM` fue
**ELIMINADA** (2026-06-15) junto con `jobs/aum.py::run` (queda solo de librería de
helpers Aunesa) y la tabla SQL `aum`. El divisor de la valuación lo decide la
**CARTERA** (no más `tipoTitulo`, que se quedaba NULL):
- Renta fija (cartera `HD / DL / ARS`, cotiza en paridad) → `cantidad × precio / 100`
- Cash (`MONEDAS`), `FCI`, `RENTA VARIABLE` → `cantidad × precio` (NUNCA ÷100)
- Futuros (`DERIVADOS`) → `(precio + 1) × cantidad`
- El motor de PnL (`pnl.py::_aplicar_normalizer`) usa la MISMA regla por cartera.

**Breakevens** (`engines/breakevens.py`, método Buscar Objetivo, cupón cero):
```
retorno_lecap = flujo_vto_lecap / precio_lecap − 1
X = [(1 + retorno_lecap) · (precio_cer · cer_emision) / (vn_cer · cer_actual)]^(1/meses) − 1
```
Match **mismo vto** Lecap↔CER (`MAX_DIFF_DIAS=20`). Anualización con `dias_cer` = vto − 10 hábiles. Filtro `mes_inflacion ≤ último IPC publicado`. Fallback Fisher si faltan datos.

**Forwards**: `((1 + TEA_B)^t_B / (1 + TEA_A)^t_A)^(1/(t_B − t_A)) − 1`. Lee última TEA por ticker desde `MarketSnapshot.metrics.TEA` (escrita por `motor_curvas` en cada update). Igual patrón usan `breakevens.py` y los services de portfolio/renta-fija. **No leer TimeSales agregado** — es estrictamente más caro y devuelve el mismo valor que el snapshot live.

**TC Breakeven** (`api/services/renta_fija.py::_tc_breakeven`, sólo tasa fija nativa o CER fijado): `TC_BE = MEP × (flujo_vencimiento / precio_actual)`. Lee `flujo_vencimiento` de `mercado.curvas`, `last_price` del trade más reciente y MEP de `get_ultimo_mep` (live, TTL 5s). Se calcula on-the-fly en `get_renta_fija` y `listar_curva` — no se persiste.

**AuM join chain**: `mercado.curvas` (campo `curva`) → `ticker` (era `ticker_corto`) → `portafolio.assets.ticker` → `unidad` → `portafolio.tenencia` (SQL, filtrar `aum='si'`).

**Enriquecimiento CER**: `motor_curvas` usa CER con settlement T-10 hábiles. Si un bono no opera un día, el último trade puede quedar con CER de ayer.

## Asistente legacy — ELIMINADO

`api/agent/` + `POST /api/chat` fueron **borrados del repo** (no existen más;
no documentar ni referenciar). El asistente con IA del producto es el MCP
server (sección siguiente).

## MCP server (Custom Connector)

`api/mcp/` montado en `https://api.acaquant.com/mcp` — asistente **100% de RENTA VARIABLE**: 13 tools de SOLO LECTURA sobre equities ARG (universo CEDEARs/ADRs, tablero live ARS+USD, time sales intradía, retornos/quant del subyacente USD, pivot points, day-trading lab, Mesa de Estrategia: correlación/trade_analysis/book_analysis). NO expone portfolio/operaciones/cuentas/AuM/manager (datos privados). Las tools de estrategia operan solo sobre posiciones que el usuario pasa por parámetro — no leen cuentas reales. Las tools registradas viven en `api/mcp/tools/renta_variable.py`; `server.py` es solo wiring. **Los dominios de mercado no-RV (renta fija, derivados, opciones, forwards, breakevens, cauciones, futuros DLR, MEP, macro) están PAUSADOS** en `api/mcp/tools/parked_mercado.py` (código intacto, no registrado — descomentar `register(mcp)` en `server.py` para reactivar). Cliente principal: Claude Desktop / claude.ai vía Custom Connector. Doc completo de cada tool: `docs/MCP_TOOLS.md`.

**Auth**: OAuth 2.1 + PKCE + DCR (RFC 7591), Cloudflare Access como IdP. Flow completo en `docs/MCP.md`. Env vars: `MCP_BEARER_TOKEN` (static fallback dev/curl), `MCP_JWT_SECRET` (firma OAuth JWTs), `MCP_OAUTH_ISSUER` (default `https://api.acaquant.com`). Sin ninguno, `/mcp` queda deshabilitado.

**Dos cosas críticas que rompen el connector** (se aprendieron a los golpes; doc completo en memoria `project_mcp_cf_access.md`):

1. **CF Access path scoping**. App `acaquant-mcp-bypass` (BYPASS + Everyone) cubre 5 paths: `/mcp`, `/oauth/token`, `/oauth/register`, `/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`. Si CF Access tapa `/mcp`, el cliente recibe HTML de login en vez de 401 → muere silencioso. `/oauth/authorize` SÍ debe estar protegido (ahí logea el user). 5/5 destinations al tope.
2. **`TransportSecuritySettings` en `api/mcp/server.py`** con `allowed_hosts` (`api.acaquant.com`) y `allowed_origins` (`https://claude.ai`, `https://claude.com`). El default del SDK MCP solo acepta localhost → 421 Misdirected Request. El smoke local NO replica esta condición.

## Capa SQL — Postgres/Supabase (ÚNICA base; Mongo decomisado 2026-06-29)

**Postgres/Supabase ES el sistema.** Todo lee y escribe SQL: motores, jobs, API,
MCP. Mongo fue decomisado por completo — no hay dual-run, ni flags de
engine, ni espejo. Doc de referencia del modelo: **`docs/SQL.md`** + `sql/schema.sql`.

Esquema: `sql/schema.sql` (OJO: NO siempre 100% aplicado en la DB real — algún
`CREATE TABLE`/columna del archivo puede no existir en Postgres todavía; `scripts/apply_schema.py`
las crea). Pool/conn: `core.postgres.get_pool` (lee `.env` propia).

- Convención: cada dominio tiene su módulo de lectura/escritura SQL (`*_sql.py`
  o helpers en `core/`): `curvas_sql`, `macro_sql`, `renta_fija_sql`, `comercial_sql`,
  `valuaciones_sql`, `pnl_sql`, `agro_sql`, `operaciones_sql`, `grupos_sql`, `roles_sql`,
  `market_snapshot`, `series_macro`, etc. Los selectores `_motor()`/`_engine` y los flags
  `*_SQL` quedaron obsoletos (ya no hay rama Mongo) — si ves uno, es vestigial.
- Escrituras SQL-native vía `core.pg_mirror` (`write_native`/`append_native`/`write_hist`).
- El motor de PnL (`pnl.py::_pnl_por_cuenta_core`) es lógica PURA sobre dicts inyectados
  desde SQL (`pnl_sql._deps_sql`) — no lee la base directo.

## Deploy

Push a `main` → Vercel auto-deploya acaquant-web. Backend, **un solo comando en el Droplet**: `cd /root/TradingAV && git pull && bash deploy/deploy.sh` (pull → `apply_schema` → restart de api + motores activos → smoke a `/api/health`, cortando al primer fallo; `--sin-schema` saltea el schema). También existe la skill `/deploy` como wrapper del procedimiento. Motores de mercado los controla cron (start/stop L-V). Cron fuente de verdad: `deploy/crontab.txt`.

> **Todo lee SQL.** Tenencias/catálogo en `portafolio.tenencia`/`portafolio.assets`;
> el join de instrumentos + normalización de assets lo hace `api/services/titulos_flujos.py`
> (lee `portafolio.assets`). Las viejas colecciones espejo `*API` y los syncs Mongo→Mongo
> (`sync_api_copies`, `api_migrate`) ya no existen.

Jobs críticos diarios: `jobs.bcra --today` (22 UTC L-V, pide hoy+21d para CER forward), `jobs.argentina_datos` (12 UTC, RiesgoPais/IPC/REM), `jobs.portafolio_backfill --diario` (11 UTC L-V, writer de tenencias SQL — reemplazó a `jobs.aum`/Mongo, eliminado), `jobs.assets_autofill` (11:40 UTC L-V, completa en `portafolio.assets` lo que se deriva de la `unidad` — FINANCIAMIENTO, FCI y el TICKER del resto del catálogo; solo campos vacíos, nunca pisa la carga manual. **Excepción heurística: `financiamiento_clase`** — el `clase_activo` HD/DL de los pagarés NO sale de la unidad sino del NOMINAL de la última tenencia (≤ **5.000.000** → HD, > → DL), porque la vista FINANCIAMIENTO no puede graficar juntas dos escalas tan distintas y clasificar 2.000 assets a mano no era viable. Es un bootstrap: se corrige en Manager → ASSETS y el job no vuelve a opinar sobre lo corregido. Backfill = el mismo comando, `--regla financiamiento_clase`. **Regla `herencia` (2026-08-12) — el REBAUTIZO de Aunesa**: un cambio normativo reemitió los instrumentos con otro id de especie, y como `unidad` es la PK de `assets` la renombrada (`[28902] CAFCI1910-6461 - …` donde antes decía `[6461] …`) entra como asset NUEVO: CARTERA y TICKER se derivan solos, pero EMISOR/CALIFICACIÓN/INSTRUMENTO/CLASE_ACTIVO/CÓDIGO CNV/FEE ADMIN —lo que carga la mesa a mano— nace VACÍO y la carga vieja queda pegada a una unidad que NO se puede borrar (la tenencia histórica la referencia). La regla copia esos campos entre unidades que son el MISMO instrumento, en las DOS direcciones. Identidad = **código CAFCI** (lo que el rebautizo no toca) y, de respaldo, el **nombre del fondo**. Tres cosas la hacen segura: los donantes tienen que ESTAR DE ACUERDO (dos valores distintos → no escribe, reporta la divergencia — eso es lo que sostiene la clave por nombre), nunca pisa lo cargado, y hoy corre **solo para FCI**: fuera de FCI la identidad sería el ticker, que todavía no está medido, así que el job CUENTA cuántos assets completaría (stat `herencia_no_fci_receptoras`) sin escribir hasta poner `HEREDAR_NO_FCI=True`. Ver qué copiaría: `--regla herencia --dry`. **Regla `especies` (2026-08-15) — los DOS símbolos de mercado**: `assets.instrumento` es lo que el motor de portfolio le SUSCRIBE a Primary (`engines/_universo_portfolio.py`), o sea de dónde sale el `last_price` de la tenencia, y se venía cargando A MANO — con lo cual el catálogo de market data quedó desparramado en tres lugares que se contradicen (assets, `mercado.curvas` y el universo real de Primary). Ahora `assets` es un DERIVADO de **`mercado.especies`**, el único lugar donde vive la relación ticker → sus patas: la regla relaciona por **TICKER** y baja las DOS, `instrumento` = pata en PESOS e **`instrumento_usd`** = pata en DÓLARES (MEP; el cable NO — es otra cosa y mezclarlo volvería a esconder cuál es cuál). Dentro de cada pata gana la `es_default` y después 24hs sobre CI, que es donde hay liquidez y por lo tanto precio. **No puede cambiar una valuación**: rige el invariante del job (nunca pisa), así que lo cargado hoy sigue igual y el motor suscribe exactamente lo mismo; un símbolo distinto al de especies se REPORTA como conflicto — que es justo el listado que no existía. Los dos campos siguen siendo editables en Manager → TÍTULOS · ASSETS porque el catálogo de Primary a veces está viejo y ahí manda la mesa), `jobs.cleanup_curvas` + `jobs.cleanup_futuros_dlr` (12:30 UTC L-V, antes de motores), `jobs.snapshot_cierre` (20:25 UTC L-V, post-cierre — lee `mercado.market_snapshot` y persiste cierre por bono en `mercado.snapshots_cierre`), `jobs.negocio_movimientos` (cada hora 15-22 UTC L-V, pega a Aunesa `consolidadosGenerales`, parsea/categoriza/agrupa por boleto y persiste idempotente en **SQL `operaciones.negocio_movimientos`** para la vista `/operaciones/negocio`), `jobs.guardrails` (20:45 UTC L-V, post-cierre — invariantes de sanidad de datos; report en el log + stat en `manager.job_runs`), **`jobs.validar_instrumentos` (23:00 UTC L-V, 2026-08-15)** — dos pasos en uno. **(1) VIGENCIA**: `portafolio.assets` no tenía forma de decir si un título sigue existiendo, y un papel que amortizó NO se puede borrar (la tenencia histórica lo referencia) → columna **`vigente`** + `vigencia_motivo`/`vigencia_at`. El job la apaga cuando la fecha de vencimiento ya pasó (de `assets.vencimiento` o `mercado.curvas.fecha_vencimiento`), con motivo `vencido`, y es REVERSIBLE: si la fecha estaba mal cargada y se corrige, el título vuelve solo. **Nunca pisa una marca humana** — tildar/destildar en Manager → TÍTULOS · ASSETS sella `vigencia_motivo='manual'` y el job deja de opinar sobre esa fila (sin ese sello se la daría vuelta esa misma noche). **(2) VALIDACIÓN**: cruza cada símbolo de `mercado.especies` contra el universo real de Primary — el que existe queda marcado **`validado`** con su fecha, y **el que no existe se BORRA**. Borrar es lo correcto porque una especie ES, por definición, una pata que cotiza: si Primary no la lista no es una pata, es basura que reaparece en cada reporte. Nada se pierde — `scripts/sembrar_especies` reconstruye la tabla desde el master + Primary, así que el día que exista de verdad vuelve sola. Y por eso mismo **el seeder ya NO siembra el símbolo del master cuando Primary no lo lista** (esa rama existía por la hipótesis de que el discovery quedaba viejo; el 2026-08-15 se lo refrescó y los faltantes quedaron idénticos — no era el catálogo atrasado): si siguiera, recrearía cada noche lo que el job acaba de borrar. **Lo que IMPIDE suscribirse a un símbolo muerto es otra cosa: `core/instrumentos_validos`, aplicado en `core/websocket.py::agregar_suscripciones`** — el ÚNICO punto por el que pasan todas las suscripciones de todos los motores, así que un motor nuevo lo hereda sin escribir una línea y no se puede saltear por olvido. Las dos leen la MISMA fuente (`manager.pyrofex_instruments`) para que no puedan contradecirse. **Degradación elegida a propósito**: si Postgres no responde o el catálogo tiene menos de 100 símbolos, `validos()` devuelve `None` y NO se filtra nada — filtrar de más deja la mesa sin precios, no filtrar deja pasar símbolos muertos como siempre; ante la duda, lo segundo. Por eso mismo el job ABORTA sin marcar si el catálogo está vacío. El orden de los dos pasos importa: sin el 1, cada bono amortizado quedaría marcado como inválido todos los días), `jobs.research_mail` (cada 30' 10-14 UTC L-V, ingesta el mail 1816 a `ia.research`), `jobs.mercado_1816_series` (22 UTC L-V, append diario a `research.mkt_1816_series`), `jobs.bcra_research` (12/16/20/23 UTC L-S).

Dólar oficial: única fuente live es `valuaciones.dolar_oficial_live` (feed MAE mayorista UST$T plazo 000, script local en PC oficina). Histórico/anchors (7d/MTD/YTD del watchlist `/argy`) deshabilitado hasta que MAE acumule histórico suficiente. Para series macro (`serie_macro` con `dolar_oficial`/`dolar_mayorista`) usar `macro.series_macro` clave DOLAR (BCRA A3500 fixing diario).
