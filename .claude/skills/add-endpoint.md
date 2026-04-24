---
name: add-endpoint
description: Scaffolding end-to-end para un endpoint REST nuevo. Sigue el patrón services/ (lógica pura) + routers/ (thin HTTP wrapper) + proxy Next.js en acaquant-web. Incluye las decisiones de cache, gating de roles, y documentación.
---

# Agregar un endpoint REST

Se aplica cuando el usuario pide "crear un endpoint nuevo" o "exponer X al frontend". Respetar el patrón de capas del repo.

## 1. Decidir el módulo y el gating

Antes de escribir código, preguntá/inferí:

- **¿A qué módulo pertenece?** (`home`, `renta-fija`, `derivados`, `estrategia`, `operaciones`, `portfolios`, `asistente`, `manager`). Define el role requerido.
- **¿Es público o restringido?** Si cae en módulos restringidos (portfolios/operaciones/manager), el router tiene que usar `Depends(require_module("..."))`. Si es público (cotizaciones/analítica/home), basta `_PUBLIC`.
- **¿Tiene que ser consumido por el asistente?** Si sí, hace falta registrarlo en `api/agent/service_registry.py` + declarar la tool en `api/agent/tool_metadata.py`.

## 2. Escribir la lógica en `api/services/`

La función va en `api/services/<modulo>.py` (reusar el existente si hay; crear uno nuevo si es un dominio distinto). **Pura Python, sin FastAPI** — tiene que ser invocable tanto por el router como por el asistente.

Patrón:

```python
from api.cache import cached

@cached(ttl=60)  # ajustar TTL según frescura requerida
def nombre_funcion(param1: str, param2: int | None = None) -> dict:
    """Docstring con qué devuelve y por qué."""
    client = get_mongo_client_read()  # READ-ONLY para queries de API
    # ... pipeline / queries ...
    return resultado
```

**Reglas del service**:
- Siempre `get_mongo_client_read()` (no el rw).
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
- NO filtrar ni transformar — solo proxy + auth.

Referencia: ver `acaquant-web/src/app/api/analitica/[...path]/route.ts` como ejemplo completo GET+POST.

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

- **Smoke**: sumar al array de endpoints de `scripts/test_api.py` si es relevante.
- **Unit**: si la lógica tiene ramas interesantes, test en `tests/unit/test_<modulo>.py` mockeando `get_mongo_client_read()` o usando fixtures.

## Criterios de éxito

- ✓ Service es invocable standalone (sin uvicorn) — probar en REPL.
- ✓ Router responde con el shape correcto via `curl` o Swagger.
- ✓ `ruff check .` limpio.
- ✓ Proxy de acaquant-web devuelve el mismo JSON.
- ✓ Doc actualizado en `docs/API.md`.
