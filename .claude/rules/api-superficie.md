---
paths:
  - "api/**"
  - "scripts/gen_mapa_app.py"
  - "docs/MAPA_APP.md"
---
# Mapa de la app y superficie de la API

## Mapa de la app — `docs/MAPA_APP.md` (LEER al empezar una sesión)

**Es el índice de TODA la superficie**: 19 vistas, sus tabs, sus filtros, qué endpoint
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
> **RESUELTO 2026-08-19**: la técnica vive UNA sola vez en **`api/superficie.py`**
> y las tres herramientas delegan ahí. **No la reimplementes** — al medirlo,
> `audit_rbac` y `test_rbac_superficie` veían **37 de 541 rutas** y pasaban en
> verde (auditaban el 7% y afirmaban que estaba todo bien), y `gen_mapa_app`
> duplicaba el prefijo en **395 de 541 paths** (el `prefix` de un `APIRouter` ya
> viene aplicado a sus propias rutas). Ver `AGENT.md` §0.s.

