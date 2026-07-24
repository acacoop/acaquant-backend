# AGRO — mapa de datos (SQL)

> **Qué es este documento.** Mapa verificado **desde el código** de la vista
> **AGRO** (`/agro` en acaquant-web): qué muestra, de qué tabla SQL sale
> cada dato, quién la llena, cómo se relacionan.
>
> **Método.** Cada afirmación fue verificada leyendo archivo:línea (routers,
> services, motores, `sql/schema.sql`). Lo no verificable por código se marca
> `⚠️ a verificar`. Lo que requiere medir en prod está al final. No se asumió nada.
>
> Relevamiento: **2026-06-12**. Actualizado **2026-06-29** (decomiso total de
> Mongo: AGRO lee/escribe SQL-native).

---

## 1. Resumen ejecutivo

📋 **Qué es la vista:** `/agro` (componente `AgroShell`) con **3 pestañas**:
**Mercado** (futuros + opciones + simulador de cobertura + pizarra de pases),
**Mejoras Precio Dispo** (LECAPs para mejorar el precio disponible), y **Datos**
(carga manual de la Cámara Arbitral de Cereales).

📋 **De dónde sale todo:** SQL (Postgres/Supabase), todo bajo el schema
**`mercado`**: snapshots de mercado (`agro_snapshot`, `agro_opciones_snapshot`,
`futuros_dlr_snapshot`), datos cargados a mano por la mesa (`agro_pizarra`,
`camara_cereales`) y el volumen agro (`volumen_mercado_agro`, denominador del
market share que vive en otra vista). El market share cruza además
`operaciones.operaciones` y `clientes.comitentes`.

📋 **Estado SQL (lo importante):** **AGRO migró por completo a SQL** (decomiso de
Mongo, 2026-06-29). Todas sus tablas propias (`mercado.agro_snapshot`,
`mercado.agro_opciones_snapshot`, `mercado.futuros_dlr_snapshot`,
`mercado.agro_pizarra`, `mercado.camara_cereales`, `mercado.volumen_mercado_agro`)
y las compartidas (`mercado.curvas`, `mercado.market_snapshot`) viven en SQL. Los
motores escriben SQL-native (vía `core.pg_mirror`) y los services leen SQL.

---

## 2. Las 4 pestañas y sus endpoints

> **Backend real:** los endpoints viven bajo el prefijo `/api/derivados/agro*`
> (router `api/routers/derivados_agro.py`). El front los llama vía proxy con el
> alias `/api/derivados-agro/*`. Acá uso la ruta **backend real**.

| Pestaña | Bloque | Endpoint backend | Service |
|---|---|---|---|
| **Mercado** | Futuros agro | `GET /api/derivados/agro` (poll 5s) | `derivados_agro.py::get_pase_agro` |
| **Mercado** | Pizarra de pases | `GET /api/derivados/agro` + `PATCH /agro/pizarra/{commodity}` | `derivados_agro.py` |
| **Mercado** | Cadena de opciones | `GET /api/derivados/agro/opciones/{commodity}` (poll 5s) | `derivados_agro.py::get_panel_opciones` |
| **Mercado** | Simulador cobertura | `POST /api/derivados/agro/estrategia/simular` | `derivados_agro.py::simular_estrategia` |
| **Mejoras Precio Dispo** | LECAPs por commodity | `GET /api/derivados/agro/mejoras-dispo` (poll 5s) | `mejoras_dispo.py::get_mejoras_dispo` |
| **Chicago** | Futuros CBOT (feed Eikon oficina) | `GET /api/derivados/agro/chicago` (poll 10s) | `core/eikon_chicago.py::tablero_chicago` |
| **Datos** | Cámara Arbitral (carga manual) | `GET /api/derivados/agro/camara` (poll 10s) + `PATCH /agro/camara/{cereal}` | `camara_cereales.py` |

> **Chicago (2026-07-24):** 5 familias CBOT (Soja `Sc1-5` / Aceite `BOc1-6` /
> Maíz `Cc1-5` / Trigo `Wc1-5` / Harina `SMc1-6`; Aceite y Harina saltean la
> posición 3), precios en **USD/tonelada** (factores server-side en
> `core/eikon_chicago.py::FAMILIAS`; la tabla `mercado.eikon_chicago_snapshot`
> guarda crudo ¢/bu·¢/lb·USD/st). Los datos entran SOLO cuando el user prende
> el feed Eikon de oficina (mismo script que la RV internacional — ver
> `docs/INTEGRACION_REUTERS.md`); con el feed apagado queda la última foto con
> su hora. La vista es una grilla de 5 tablas (Mes / Precio USD-t / Var),
> componente `agro-chicago.tsx`. Visible también para el invitado (mercado).

Endpoints verificados en `api/routers/derivados_agro.py`.

> **Nota — Market share AGRO NO está en esta vista.** El gráfico de participación
> de mercado (`GET /api/operaciones/ops/agro`) pertenece a la vista
> **OPERACIONES** (`agro-view.tsx`, otro árbol de componentes), no a `/agro`. Se
> documenta en el doc de Operaciones. Acá solo se menciona la relación.

---

## 3. Tablas SQL — quién las lee y quién las llena (verificado)

| Tabla | Schema | Qué es | La lee | La llena (verificado) |
|---|---|---|---|---|
| **agro_snapshot** | `mercado` | Futuros agro vivos (~24 filas) | derivados_agro, mejoras (indirecto) | **`engines/motor_agro.py`** (upsert cada ~5s) |
| **agro_opciones_snapshot** | `mercado` | Cadena de opciones agro viva | derivados_agro | **`engines/motor_agro_opciones.py`** (upsert cada ~5s) |
| **futuros_dlr_snapshot** | `mercado` | Futuros DLR vivos | mejoras_dispo | **`engines/futuros_dlr.py`** (limpieza: `jobs/cleanup_futuros_dlr.py`) |
| **curvas** | `mercado` | Maestro de bonos (LECAPs para mejoras) | mejoras_dispo | maestro editable |
| **market_snapshot** | `mercado` | TEA viva de cada LECAP | mejoras_dispo | motores rofex + curvas |
| **agro_pizarra** | `mercado` | Precio pizarra USD por commodity (3 filas) | derivados_agro | **MANUAL** (PATCH desde la mesa) |
| **agro_pizarra_audit** | `mercado` | Log de cambios de la pizarra | — | escrito en cada PATCH |
| **camara_cereales** | `mercado` | Precios Cámara Rosario (5 cereales) | camara_cereales, mejoras_dispo, derivados_agro | **MANUAL** (PATCH desde la mesa) |
| **camara_cereales_audit** | `mercado` | Log de cambios de la cámara | — | escrito en cada PATCH |

> **Dato clave de diseño:** la **Pizarra** y la **Cámara** NO las alimenta ningún
> motor — son **carga manual de la mesa** (PATCH con `require_module("agro")`),
> con tablas de auditoría (`*_audit`) que registran `updated_by` + `updated_at`.

---

## 4. Relaciones clave (verificado)

1. **Pase agro:** `get_pase_agro` cruza `mercado.agro_snapshot` (futuros vivos en
   USD) + `mercado.agro_pizarra` (precio pizarra manual) + `mercado.camara_cereales`
   (precio USD de cámara) para armar la pizarra de pases.

2. **Opción ↔ futuro:** se emparejan por **prefijo del ticker del futuro**, NO
   por fecha de vencimiento (las opciones agro vencen ~1 mes antes que el futuro
   subyacente). Verificado en `derivados_agro.py` (`_futuro_ticker_de_opcion`).

3. **Mejoras dispo:** `mercado.camara_cereales` (precio ARS spot) +
   `mercado.futuros_dlr_snapshot` (cobertura cambiaria) + `mercado.curvas` (LECAPs)
   + `mercado.market_snapshot` (TEA de cada LECAP) → tasa directa / valor final.

---

## 5. Estado SQL — migración completa

**Decomiso de Mongo terminado (2026-06-29): AGRO lee y escribe SQL-native.**

### 5.1. Tablas propias de AGRO (todas en SQL, schema `mercado`)
- `mercado.agro_snapshot` — futuros agro vivos
- `mercado.agro_opciones_snapshot` — cadena de opciones agro viva
- `mercado.futuros_dlr_snapshot` — futuros DLR (vivo + cierre)
- `mercado.agro_pizarra` (+ `agro_pizarra_audit`) — pizarra manual
- `mercado.camara_cereales` (+ `camara_cereales_audit`) — cámara manual
- `mercado.volumen_mercado_agro` — denominador del market share (**carga manual mensual**)

### 5.2. Compartidas (también SQL)
- `mercado.curvas` y `mercado.market_snapshot` (documentadas en RENTA_FIJA.md).
- (Market share, en la vista Operaciones) `operaciones.operaciones` y
  `clientes.comitentes`.

### 5.3. Conclusión de migración para AGRO
- **Lectura y escritura: 100% SQL.** Los services de agro leen vía los `*_sql.py`
  / helpers de `core`; los motores escriben con `core.pg_mirror`. Conexión
  `core.postgres.get_pool()`.
- No queda nada de AGRO en Mongo.

---

## 6. ⚠️ Pendiente de verificar / medir en prod (NO asumido)

1. **Conteos reales** (¿`mercado.agro_snapshot` tiene las ~24 filas?, ¿la pizarra
   tiene las 3?, etc.) — medir en SQL.
2. **`agro_pizarra`/`camara_cereales` creación inicial:** el código asume que
   existen; no se vio dónde se crean por primera vez. ⚠️ a verificar (¿seed manual?).

---

## 7. Archivos fuente

- **Frontend:** `acaquant-web/src/app/agro/page.tsx` + `agro-shell.tsx`,
  `derivados-agro-view.tsx`, `derivados-agro-futuros.tsx`,
  `derivados-agro-opciones.tsx`, `derivados-agro-pizarra.tsx`,
  `derivados-agro-estrategias.tsx`, `agro-mejoras-dispo.tsx`, `agro-datos.tsx`.
- **Router:** `api/routers/derivados_agro.py`.
- **Services:** `derivados_agro.py`, `camara_cereales.py`, `mejoras_dispo.py`.
- **Motores:** `engines/motor_agro.py`, `engines/motor_agro_opciones.py`,
  `engines/futuros_dlr.py` (limpieza: `jobs/cleanup_futuros_dlr.py`).
- **SQL:** `sql/schema.sql` (schema `mercado`: `agro_snapshot`,
  `agro_opciones_snapshot`, `futuros_dlr_snapshot`, `agro_pizarra`(+`_audit`),
  `camara_cereales`(+`_audit`), `volumen_mercado_agro`, `curvas`,
  `market_snapshot`; + `operaciones.operaciones` y `clientes.comitentes` para el
  market share). Conexión `core.postgres.get_pool()`, escritura vía
  `core.pg_mirror`.
