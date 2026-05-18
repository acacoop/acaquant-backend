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

- **Fase 2 (pendiente)** — enforcement backend. Aplicar
  `cuentas_visibles(email)` en los endpoints que listan/exponen cuentas
  (portfolio/AuM, operaciones, valuaciones, PnL). Patrón: si devuelve un
  `set`, filtrar el `$match` Mongo por `id_cuenta ∈ set`; si `None`, no
  filtrar. Candidato a vivir junto a `_cuentas_filter.py`.

- **Fase 3 (pendiente)** — frontend. Los selectores de cuenta de cada
  vista se limitan a las cuentas visibles del usuario.

## Notas

- `operar` quedó admin-only (commit `7478405`) como medida previa —
  independiente de grupos.
- No es RBAC: RBAC (`core/roles.py`) decide qué **módulos** ve un usuario;
  grupos decide qué **cuentas** ve dentro de esos módulos.
