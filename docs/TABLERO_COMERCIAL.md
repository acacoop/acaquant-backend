# Tablero de Control Comercial

> Documento vivo. Se va actualizando a medida que avanzamos. El **LOG DE
> AVANCES** (al final) es append-only con fecha.

## Objetivo

Un **tablero de control comercial** dentro de acaquant-web. Permite ver y
gestionar la cartera de **clientes (cuentas comitentes)** de la mesa,
segmentados, con su **operador** asignado, métricas comerciales, etc.

El tablero es **lo último** que se construye. Antes hay que armar la base:
clientes que figuren automáticamente + segmentación + operadores.

## De qué depende (cadena de dependencias, build bottom-up)

```
[5] Tablero de control comercial (vista en acaquant-web)   ← ÚLTIMO
        ▲
[4] Operadores ↔ usuarios de la página (Manager.Users)
        ▲
[3] Segmentación de clientes (campos categóricos, patrón Assets)
        ▲
[2] Master de clientes (DB/colección propia) + alta automática
        ▲
[1] Integración nuevas APIs Aunesa (cuentas comitentes)
```

## Idea clave: es el patrón "Assets" pero para clientes

Hoy `Valuaciones.Assets` es el **master categórico de instrumentos**: cada
asset tiene su `TICKER` + campos (emisor, vencimiento, etc.) y desde ahí se
joinean AuM/Portfolios. Queremos **lo mismo pero para clientes**: un master
de **cuentas comitentes** con campos propios de segmentación.

- Clave de cuenta = **mismo formato que AuM / NegocioMovimientos** (número de
  comitente / `id_cuenta`). Así joinea con todo lo que ya tenemos.
- Database y colección **propias** (no metemos esto en Valuaciones/Manager).
- Los campos de segmentación los **define el usuario** (TBD — ver abajo).

## Modelo de datos (propuesto — TBD confirmar nombres)

**DB nueva: `Clientes`** · colección master: **`Clientes.Comitentes`**

```jsonc
{
  "id_cuenta": "1114",            // clave, mismo formato que AuM/movimientos
  "nombre": "…",                  // razón social / titular
  // ── Segmentación (campos propios, los define el usuario) ──
  "segmento": "…",                // TBD
  "operador": "…",                // TBD — ver Operadores
  // … más campos categóricos a definir (análogo a emisor/vto en Assets)
  "activo": true,
  "origen": "aunesa",
  "created_at": "…",
  "updated_at": "…"
}
```

- **Idempotente por `id_cuenta`** (upsert), igual que `descubrir_cuentas`.
- Si más adelante el frontend la consume con RBAC, evaluar copia derivada
  `*API.*API` (patrón `jobs/sync_api_copies.py`, como `CuentasAPI`).

## Piezas existentes que reusamos (no reinventar)

| Necesidad | Pieza existente a usar de modelo | Archivo |
|---|---|---|
| Cliente Aunesa (auth + requests) | `AunesaApiManager` (auth `Irmo/api/login`, `config.AUNESA_*`) | `jobs/aunesa_client.py` |
| Job diario de alta de cuentas | discovery idempotente por cuenta, cron diario, upsert | `jobs/descubrir_cuentas.py` |
| Ingesta Aunesa recurrente | pega a Aunesa, parsea, persiste idempotente | `jobs/negocio_movimientos.py` |
| Master categórico (modelo) | asset + campos editables | `Valuaciones.Assets` |
| Segmentación | reglas de segmento sobre cuentas | `jobs/segmento_contrapartes.py` |
| Operadores = usuarios | usuarios + roles de la página | `Manager.Users`, `core/roles.py` |
| Copia derivada para frontend | sync de colecciones `*API` | `jobs/sync_api_copies.py` |

## [1] Integración Aunesa (arrancamos por acá)

- El usuario pasa las **nuevas APIs de Aunesa** (endpoints de cuentas
  comitentes / altas). Las sumamos como métodos en `AunesaApiManager`
  (`jobs/aunesa_client.py`) — ya tiene el patrón de auth con token.
- Primero un **script de prueba** (`scripts/diag_aunesa_comitentes.py`,
  read-only) para ver el shape real de la respuesta antes de codear el job.

## [2] Master de clientes + alta automática

- Job (ej. `jobs/sync_comitentes.py`) que corre **1×/día** (cron, como
  `descubrir_cuentas`): trae comitentes de Aunesa → upsert en
  `Clientes.Comitentes` por `id_cuenta`. Cuentas nuevas se agregan solas;
  las que desaparecen no se borran (quedan con `updated_at` viejo).

## [3] Segmentación

- Campos categóricos propios (los define el usuario), editables — análogo a
  cómo se editan los Assets desde `/manager`. Definir si la segmentación es
  manual (editás el campo) o por reglas (como `segmento_contrapartes`).

## [4] Operadores ↔ usuarios

- Cada cuenta tiene un **operador**. El operador es un **usuario de la página**
  (`Manager.Users`). Definir: ¿el operador se guarda como campo en la cuenta
  (`operador` = email/id del user) o en una colección de relación aparte?
- Esto habilita filtrar el tablero por operador (cada uno ve su cartera).

## [5] Tablero de control comercial (vista) — ÚLTIMO

- Vista nueva en acaquant-web. Probable **módulo RBAC nuevo** (sumar a
  `core/roles.py::MODULES` + `ENDPOINT_MODULE_PREFIXES` + matriz). Definir
  quién lo ve (¿rol comercial nuevo? ¿admin/operador?).

## Decisiones abiertas (las vamos cerrando)

- [ ] Nombre definitivo DB + colección (propuesto: `Clientes.Comitentes`).
- [ ] APIs Aunesa concretas (las pasa el usuario).
- [ ] Campos de segmentación (los define el usuario).
- [ ] Operador: campo en la cuenta vs colección de relación.
- [ ] ¿Copia derivada `*API` para el frontend?
- [ ] Módulo RBAC del tablero + quién lo ve.
- [ ] Métricas/columnas del tablero comercial.

## LOG DE AVANCES

- **2026-05-22** — Creado este documento. Definida la arquitectura (cadena de
  dependencias, patrón Assets para clientes, piezas a reusar). Pendiente: el
  usuario pasa las APIs de Aunesa para arrancar por la integración [1].
