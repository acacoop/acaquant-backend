# Grupos de acceso por cuenta

Scoping multi-tenant: restringir qué cuentas comitentes ve cada usuario.
El admin lo gestiona 100% desde `/manager → GRUPOS`.

## Modelo

Colección **`Manager.Grupos`**:

```js
{
  _id:        ObjectId,
  nombre:     "Mesa Rosario",
  emails:     ["user1@x.com", "user2@x.com"],   // lowercased
  id_cuentas: ["805", "1207", ...],             // id_cuenta (string)
  creado_por: "admin@x.com",
  creado_at:  ISODate,
  updated_at: ISODate,
  updated_por:"admin@x.com",
}
```

Un grupo agrupa **usuarios** (por email) + **cuentas** (por `id_cuenta`).
Las cuentas asignables son SOLO las que ya existen (último snapshot AuM) —
el selector del panel se puebla con `portfolio.listar_cuentas()`.

## Regla de visibilidad

`core/grupos.py::cuentas_visibles(email) -> set[str] | None`:

| Caso | Resultado |
|---|---|
| admin | `None` → ve TODO |
| usuario en NINGÚN grupo | `None` → ve TODO (transición: nada se rompe) |
| usuario en ≥1 grupo | `set` = unión de las `id_cuentas` de sus grupos |

`None` = sin restricción. Cache TTL 60s; `invalidate_cache()` tras cada
mutación. Fail-open ante error de DB (devuelve `None`) — los grupos no
deben tumbar la app; el peor caso es "ve de más", igual al estado actual.

## Plan en fases

- **Fase 1 (HECHA)** — modelo + CRUD.
  - `core/grupos.py`: resolver + CRUD + cache.
  - `api/routers/manager/grupos.py`: `GET/POST/PATCH/DELETE
    /api/manager/grupos` (admin-only, hereda el gate de `manager`).
  - Frontend: tab GRUPOS en el panel Manager (`grupos-panel.tsx`).
  - **Todavía NO enforcea** — solo se pueden crear/editar grupos.

- **Fase 2 (HECHA)** — enforcement backend para el namespace `id_cuenta`
  (valuaciones + portfolio/AuM + PnL).
  - `api/services/_grupos_scope.py`: dependencies `scope_cuentas`
    (inyecta `tuple[str,...] | None`) y `verificar_id_cuenta` (403).
  - Los services de `portfolio.py`, `pnl.py` y `valuaciones.py` aceptan
    `scope` y lo aplican al `$match` Mongo / al filtro de docs.
  - Routers `carteras.py` y `valuaciones.py` resuelven el scope y lo
    pasan; los endpoints `/{id_cuenta}/*` quedan gateados.
  - El cron (`pnl_todas_cuentas_compute`) corre sin scope.
  - **Pendiente Fase 2b**: el namespace `cuenta` (string "[N] NOMBRE")
    de `operaciones/negocio` — necesita mapear `id_cuenta → cuenta`.
    `operar` ya es admin-only, así que no urge.

- **Fase 3 (pendiente / casi cubierta)** — frontend. Los selectores de
  cuenta salen de `/api/portfolio/cuentas` y `listar_cuentas`, que ya
  vienen scopeados por Fase 2 → un usuario scopeado solo ve sus cuentas
  en los selectores sin cambios de UI. Queda revisar selectores que no
  consuman ese endpoint.

## Notas

- `operar` quedó admin-only (commit `7478405`) como medida previa —
  independiente de grupos.
- No es RBAC: RBAC (`core/roles.py`) decide qué **módulos** ve un usuario;
  grupos decide qué **cuentas** ve dentro de esos módulos.
