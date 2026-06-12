# AGRO — mapa de datos (Mongo + SQL)

> **Qué es este documento.** Mapa verificado **desde el código** de la vista
> **AGRO** (`/agro` en acaquant-web): qué muestra, de qué colección Mongo sale
> cada dato, quién la llena, cómo se relacionan y qué está en SQL y qué no.
>
> **Método.** Cada afirmación fue verificada leyendo archivo:línea (routers,
> services, motores, `sql/schema.sql`). Lo no verificable por código se marca
> `⚠️ a verificar`. Lo que requiere medir en prod está al final. No se asumió nada.
>
> Relevamiento: **2026-06-12**.

---

## 1. Resumen ejecutivo

📋 **Qué es la vista:** `/agro` (componente `AgroShell`) con **3 pestañas**:
**Mercado** (futuros + opciones + simulador de cobertura + pizarra de pases),
**Mejoras Precio Dispo** (LECAPs para mejorar el precio disponible), y **Datos**
(carga manual de la Cámara Arbitral de Cereales).

📋 **De dónde sale todo:** Mongo, repartido en **3 bases**: `Trading`
(snapshots de mercado), **`Derivados`** (datos cargados a mano por la mesa) y
`CashFlow` (operaciones, para el market share que vive en otra vista).

📋 **Estado SQL (lo importante):** **AGRO está casi por completo AFUERA de la
migración SQL.** Ninguna de sus colecciones propias (`AgroSnapshot`,
`AgroOpcionesSnapshot`, `Derivados.*`, `FuturosDLRSnapshot`, `VolumenMercadoAgro`)
tiene tabla en SQL (verificado: 0 apariciones en `sql/schema.sql`). Solo lo
**compartido** (`Curvas`, `MarketSnapshot`) y el cierre histórico de futuros DLR
(`FuturosDLR` → `mercado_hist`) están espejados.

🔎 **Hallazgo:** la vista usa una base Mongo **`Derivados`** que **no figura en
el inventario de bases del `CLAUDE.md`**. Conviene agregarla a la doc raíz.

---

## 2. Las 3 pestañas y sus endpoints

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
| **Datos** | Cámara Arbitral (carga manual) | `GET /api/derivados/agro/camara` (poll 10s) + `PATCH /agro/camara/{cereal}` | `camara_cereales.py` |

Endpoints verificados en `api/routers/derivados_agro.py`.

> **Nota — Market share AGRO NO está en esta vista.** El gráfico de participación
> de mercado (`GET /api/operaciones/ops/agro`) pertenece a la vista
> **OPERACIONES** (`agro-view.tsx`, otro árbol de componentes), no a `/agro`. Se
> documenta en el doc de Operaciones. Acá solo se menciona la relación.

---

## 3. Colecciones Mongo — quién las lee y quién las llena (verificado)

| Colección | Base | Qué es | La lee | La llena (verificado) |
|---|---|---|---|---|
| **AgroSnapshot** | `Trading` | Futuros agro vivos (~24 docs) | derivados_agro, mejoras (indirecto) | **`engines/motor_agro.py`** (ReplaceOne cada ~5s) |
| **AgroOpcionesSnapshot** | `Trading` | Cadena de opciones agro viva | derivados_agro | **`engines/motor_agro_opciones.py`** (ReplaceOne cada ~5s) |
| **FuturosDLRSnapshot** | `Trading` | Futuros DLR vivos | mejoras_dispo | **`engines/futuros_dlr.py`** (limpieza: `jobs/cleanup_futuros_dlr.py`) |
| **FuturosDLR** | `Trading` | Futuros DLR de cierre (histórico) | (histórico) | `engines/futuros_dlr.py` |
| **Curvas** | `Trading` | Maestro de bonos (LECAPs para mejoras) | mejoras_dispo | maestro editable |
| **MarketSnapshot** | `Trading` | TEA viva de cada LECAP | mejoras_dispo | motores rofex + curvas |
| **AgroPizarra** | **`Derivados`** | Precio pizarra USD por commodity (3 docs) | derivados_agro | **MANUAL** (PATCH desde la mesa) |
| **AgroPizarraAudit** | **`Derivados`** | Log de cambios de la pizarra | — | escrito en cada PATCH |
| **CamaraCereales** | **`Derivados`** | Precios Cámara Rosario (5 cereales) | camara_cereales, mejoras_dispo, derivados_agro | **MANUAL** (PATCH desde la mesa) |
| **CamaraCerealesAudit** | **`Derivados`** | Log de cambios de la cámara | — | escrito en cada PATCH |

> **Dato clave de diseño:** la **Pizarra** y la **Cámara** NO las alimenta ningún
> motor — son **carga manual de la mesa** (PATCH con `require_module("agro")`),
> con colecciones de auditoría (`*Audit`) que registran `updated_by` + `updated_at`.

---

## 4. Relaciones clave (verificado)

1. **Pase agro:** `get_pase_agro` cruza `Trading.AgroSnapshot` (futuros vivos en
   USD) + `Derivados.AgroPizarra` (precio pizarra manual) + `Derivados.CamaraCereales`
   (precio USD de cámara) para armar la pizarra de pases.

2. **Opción ↔ futuro:** se emparejan por **prefijo del ticker del futuro**, NO
   por fecha de vencimiento (las opciones agro vencen ~1 mes antes que el futuro
   subyacente). Verificado en `derivados_agro.py` (`_futuro_ticker_de_opcion`).

3. **Mejoras dispo:** `Derivados.CamaraCereales` (precio ARS spot) +
   `Trading.FuturosDLRSnapshot` (cobertura cambiaria) + `Trading.Curvas` (LECAPs)
   + `Trading.MarketSnapshot` (TEA de cada LECAP) → tasa directa / valor final.

---

## 5. Estado SQL — qué está espejado y qué no

**Verificado contra `sql/schema.sql` (búsqueda directa por nombre):**

### 5.1. SIN espejo en SQL (0 apariciones en schema.sql)
- `Trading.AgroSnapshot` ❌
- `Trading.AgroOpcionesSnapshot` ❌
- `Trading.FuturosDLRSnapshot` ❌ (el **vivo**)
- `Derivados.AgroPizarra` (+ Audit) ❌
- `Derivados.CamaraCereales` (+ Audit) ❌
- `CashFlow.VolumenMercadoAgro` ❌ (denominador del market share, **carga manual mensual**)

### 5.2. CON espejo en SQL
- `Trading.FuturosDLR` (cierre histórico) → tabla `mercado_hist` (vía
  `sync_mercado_hist`, batch). El **vivo** (Snapshot) no.
- `Trading.Curvas` → `curvas`; `Trading.MarketSnapshot` → `market_snapshot`
  (compartidas, ya documentadas en RENTA_FIJA.md).
- (Market share, en la vista Operaciones) `CashFlow.Operaciones` → tabla
  `operaciones` con dual-run (`OPERACIONES_SQL`); `Clientes.Comitentes` →
  `comitentes`. `VolumenMercadoAgro` sigue siendo solo Mongo.

### 5.3. Conclusión de migración para AGRO
- **Lectura: 100% Mongo.** Ningún service de agro lee de SQL.
- **El núcleo de la vista (futuros, opciones, pizarra, cámara, mejoras) NO tiene
  ningún espejo en SQL.** Es el módulo de MERCADOS **más fuera** de la migración.
- Si se quisiera llevar AGRO a SQL, hay que crear tablas para `AgroSnapshot`,
  `AgroOpcionesSnapshot`, `FuturosDLRSnapshot` y las dos colecciones manuales de
  `Derivados` — hoy **no existen**.

---

## 6. ⚠️ Pendiente de verificar / medir en prod (NO asumido)

1. **Base `Derivados` no inventariada** en `CLAUDE.md` — corregir la doc raíz
   (existe y la usan 3 services de agro, verificado).
2. **Conteos reales** (¿`AgroSnapshot` tiene los ~24 docs?, ¿la pizarra tiene los
   3?, etc.) — medir con `python -m scripts.diag_inventario_mongo_sql`.
3. **`AgroPizarra`/`CamaraCereales` creación inicial:** el código asume que
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
- **SQL:** `sql/schema.sql` (solo `curvas`, `market_snapshot`, `mercado_hist`,
  `operaciones`, `comitentes` tocan tangencialmente a agro).
