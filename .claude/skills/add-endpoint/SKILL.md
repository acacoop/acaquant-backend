---
name: add-endpoint
description: Scaffolding end-to-end para un endpoint REST nuevo. Sigue el patrón services/ (lógica pura) + routers/ (thin HTTP wrapper) + proxy Next.js en acaquant-web. Incluye las decisiones de cache, gating de roles, y documentación.
---

# Agregar un endpoint REST

Se aplica cuando el usuario pide "crear un endpoint nuevo" o "exponer X al frontend". Respetar el patrón de capas del repo.

## 1. Decidir el módulo y el gating

Antes de escribir código:

- **¿A qué módulo pertenece?** (`home`, `renta-fija`, `derivados`, `estrategia`, `operaciones`, `portfolios`, `manager`, `ia`…). La lista viva es `core/roles.py::MODULES`.
- **¿Router existente o nuevo?** El gate RBAC está aplicado **a nivel de router** en `api/main.py` (constantes `_PUBLIC` / `_PORTFOLIOS` / `_OPERACIONES` / `_ASISTENTE` / `_MANAGER` con `Depends(require_module(...))`). Si agregás un endpoint a un router existente, hereda el gate; si creás router nuevo tenés que incluirlo con las deps correctas en `main.py`. **No se aplica `require_module` per-endpoint** — va al router entero.
- **¿Tiene que consumirlo el AV AGENT?** Si sí:
  1. Registrar la función en `api/agent/service_registry.py` (mapeo endpoint → callable).
  2. Declarar la tool en `api/agent/tool_metadata.py` con JSON schema.
  3. Confirmar que NO cae en `BLOCKED_PATH_PREFIXES` (portfolio/operaciones/cuentas/manager). Si el módulo es restringido, la tool queda bloqueada por policy y NO se declara — consultar antes de tocarlo.

## 2. Escribir la lógica en `api/services/`

La función va en `api/services/<modulo>.py` (reusar el existente si hay; crear uno nuevo si es un dominio distinto). **Pura Python, sin FastAPI** — tiene que ser invocable tanto por el router como por el AV AGENT.

Patrón:

```python
from api.cache import cached
from core.postgres import get_pool

@cached(ttl=60)  # ajustar TTL según frescura requerida
def nombre_funcion(param1: str, param2: int | None = None) -> dict:
    """Docstring con qué devuelve y por qué."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ...", (param1,))
        filas = cur.fetchall()
    return armar(filas)
```

**Reglas del service**:
- Pool singleton `core.postgres.get_pool()` — no cerrarlo. Parámetros siempre, nunca
  SQL concatenado.
- `api/services/` es PURO: no importa FastAPI. El `Depends`, el `HTTPException` y el
  status code viven en el router.
- Cache con `@cached(ttl=N)` — nunca escribir caches manuales.
- Inputs parseados/validados acá (no dejar para el router).
- Si hay mutación (raro), explicitar y NO cachear.

## 3. Router — thin wrapper

En `api/routers/<modulo>.py`:

```python
@router.get("/nueva-ruta")
def nueva_ruta(
    param1: str = Query(..., description="..."),
    param2: int = Query(10, ge=1, le=100),
):
    return svc.nombre_funcion(param1=param1, param2=param2)
```

**Reglas del router**:
- Solo FastAPI plumbing: Query params, Body, dependencies.
- Validación de rangos/enums via `Query(..., ge=, le=)` o `pydantic BaseModel`.
- Cero lógica de negocio. Si estás escribiendo un `for` o cálculo acá, mudalo al service.

## 4. Proxy en acaquant-web

En `acaquant-web/src/app/api/<modulo>/[...path]/route.ts` (si el catch-all ya existe) o archivo nuevo `src/app/api/<ruta>/route.ts`:

- GET: forward con `Bearer API_KEY` + `CF-Access-Client-{Id,Secret}` + `cache: "no-store"`.
- POST: agregar bloque separado, forward del body con `content-type: application/json`.
- Si el endpoint audita al actor (ej. `/api/manager/*`), propagar el header `cf-access-authenticated-user-email` — si no, el audit log registra "service:<cn>" en lugar del email real.
- NO filtrar ni transformar — solo proxy + auth.

**Si el endpoint es restringido (módulo no-público)**, sumar el path a `src/proxy.ts::PATH_MODULES` para que el frontend redirija al user con role equivocado en lugar de dejar que el backend le tire 403 feo. El mapping tiene que coincidir con `ENDPOINT_MODULE_PREFIXES` del backend (`api/auth.py`).

Referencia: `acaquant-web/src/app/api/analitica/[...path]/route.ts` (GET+POST) y `acaquant-web/src/app/api/manager/[...path]/route.ts` (todos los métodos + propagación de email).

## 5. Consumo en la vista

- **SSR** (vista inicial): `apiFetch()` en `src/lib/api.ts` con `next: { revalidate: N }` apropiado.
- **Live polling**: `usePoll()` para refetch periódico. Ajustar TTL coherente con el cache del service backend (no más frecuente que el TTL).

## 6. Docs

Sumar al catálogo de `docs/API.md` con:
- Método + ruta.
- Query/body params.
- Shape de response.
- Rate limit si aplica.

## 7. Test

- **Unit**: si la lógica tiene ramas interesantes, test en `tests/unit/test_<modulo>.py` usando fixtures.

## Criterios de éxito

- ✓ Service es invocable standalone (sin uvicorn) — probar en REPL.
- ✓ Router responde con el shape correcto via `curl` o Swagger.
- ✓ `ruff check .` limpio.
- ✓ Proxy de acaquant-web devuelve el mismo JSON.
- ✓ Doc actualizado en `docs/API.md`.
- ✓ **Si es restringido**: un role sin acceso al módulo recibe 403 del backend, y el frontend redirige al home sin pegarle al API (test: curl con `cf-access-authenticated-user-email` de un user en otro role).
