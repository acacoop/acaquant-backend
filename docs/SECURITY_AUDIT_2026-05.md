# Auditoría de seguridad — 2026-05-23

Auditoría completa (TradingAV API + acaquant-web frontend) hecha con 4 agentes
paralelos (auth/gating, secretos, inyección/input, frontend/PII). Documento de
trabajo: ir tachando a medida que se remedia.

## Veredicto
Postura **sólida**. Sin críticos de implementación. El único punto rojo (C1)
es una **decisión de diseño** pendiente de definir, no un bug.

## Lo que está BIEN (no tocar)
- Auth en capas OK: CF Access → API_KEY → JWT CF validado cripto → RBAC → rate limit.
- Secretos: nada hardcodeado, `.env` nunca committeado, ningún `verify=False`, ningún token logueado.
- Inyección: regex con `re.escape`, Pydantic v2 corta operator-injection, `$set` con whitelist.
- **Feature CLIENTES (nuevo)**: admin-only confirmado (hereda `require_module("manager")`);
  bulk import castea a str + whitelist `_EDITABLE_FIELDS` + no crea cuentas (sin NoSQL inj);
  PII renderizada escapada; sin secretos en el bundle.

## HALLAZGOS (priorizados)

### 🔴 CRÍTICO — decisión de negocio
- [ ] **C1 · Órdenes sin scope de cuenta.** `/api/ordenes`, `/api/ordenes/fci`,
  `DELETE /{cl_ord_id}`, `/api/operar/bracket`, `/api/operativa` aceptan
  `account=<cualquiera>` y NO aplican `scope_cuentas`/`verificar_id_cuenta`
  (a diferencia de `/api/portfolio` y `/api/valuaciones`). Cualquier rol con
  módulo `operar` (admin/trader/**sales**) opera/cancela/ve órdenes de cualquier
  cuenta. `api/routers/ordenes.py`, `api/services/ordenes.py`.
  **Definir**: ¿operador scopeado a sus clientes (agregar scope) o todos los
  traders operan todo (entonces sacar `sales` de `operar`)?

### 🟠 ALTO
- [ ] **A2 · PATCH de derivados_agro sin gate de role.** `PATCH /agro/pizarra/{commodity}`
  y `/agro/camara/{cereal}` bajo `_PUBLIC`, solo `get_user_email` (audit). Un `sales`
  pisa precios agro de toda la mesa. El comentario en `api/main.py:165` dice que
  tiene gate inline — **es falso**. `api/routers/derivados_agro.py:59,175`.
  Fix: `Depends(require_module("agro"))` o gate trader+admin a los 2 PATCH.
- [ ] **A3 · Cache de PII compartido en `/api/contrapartes` (acaquant-web).**
  Devuelve denominaciones/cuentas/flujos con `s-maxage=300` sin keyear por user
  y fuera del matcher de `proxy.ts` → hit de CDN sirve PII a cualquiera.
  `src/app/api/contrapartes/route.ts:24-26,39-43`. Fix: `dynamic="force-dynamic"` +
  `revalidate=0` + `Cache-Control: no-store` + sumar al matcher de `proxy.ts`.

### 🟡 MEDIO
- [ ] **M1 · Sin rate-limit en mutaciones** de trading (`/api/ordenes`, `/api/operativa`,
  `/api/operar/bracket`) ni manager (`PATCH/POST /clientes`, `/clientes/bulk`, users/roles).
  Fix: `@limiter.limit(...)`.
- [ ] **M2 · `verify_api_key` compara con `!=`** (no constante-en-tiempo). `api/deps.py:30`.
  Fix: `secrets.compare_digest(authorization or "", f"Bearer {API_KEY}")`.
- [ ] **M3 · `POST /manager/jobs/run` arg-injection.** `args: list[str]` libre →
  `python -m jobs.X <args>`. Sin shell inj (es lista), admin-only. `api/routers/manager/jobs.py:51,60`.
  Fix: whitelistear args por job.
- [ ] **M4 · Proxies `[[...path]]` reenvían cualquier sub-path** sin allowlist de método.
  No es SSRF (prefijo fijo) pero expone endpoints mutantes nuevos automáticamente.
  Copiar el patrón de `cotizaciones/route.ts` (restringe a GET).

### 🟢 BAJO
- [ ] Fail-open dev sin alarma en prod: si falta `API_KEY`/`CF_ACCESS_*` la auth
  degrada silenciosa → loggear `error` fuerte al boot (o fail-closed si `ENV=prod`).
  Ver **EXT-AUTH1** (mismo issue, verificado en `api/deps.py:28`).
- [ ] `scripts/partner_user.py:41,75` imprime password (CLI, by design — solo cuidar scrollback).
- [ ] Verificar en Vercel/CF que el acceso directo a `*.vercel.app` (salteando CF Access) esté bloqueado.
  Ver **EXT-AUTH2** (es la única vía por la que el header `x-acaquant-user-email` sería spoofeable).

## Auditoría externa cruzada (verificada contra código · 2026-05-23)

Segunda auditoría recibida de un tercero. Verifiqué cada punto contra el código.
Numeración `EXT-*` para no chocar con C1/A2/A3 de arriba. Lo que sigue es **lo
que sobrevivió a la verificación** — los puntos refutados están al final.

### 🟠 ALTO (nuevos, válidos)
- [ ] **EXT-DEP1 · `requirements.txt` sin pins.** Cero `==` en ~28 deps → un
  release upstream incompatible rompe el boot al `restart` sin que toquemos nada
  (ya pasó: 502). Pinear exacto + lockfile.
- [ ] **EXT-TEST1 · Cero tests sobre paths críticos.** 169 tests existen, pero
  ninguno cubre RBAC (`require_module`/`has_access`/scope de cuenta), PnL
  (`api/services/pnl.py`), AuM (`jobs/aum.py`) ni órdenes. Un cambio rompe el
  cálculo de un cliente y nada lo detecta. Golden tests que congelen los números.
- [ ] **EXT-XLSX1 · `xlsx ^0.18.5` (acaquant-web) con CVEs.** Prototype pollution
  (CVE-2023-30533) + ReDoS (CVE-2024-22363), sin fix en la versión de npm.
  **Mitigado hoy**: solo se usa para exportar, no para leer uploads. Migrar a
  build oficial de SheetJS o `exceljs` ANTES de agregar import de Excel.

### 🟡 MEDIO (nuevos, válidos)
- [ ] **EXT-MONEY1 · Dinero en `float`, no `Decimal`.** 0 usos de `Decimal` en
  `aum.py`/`pnl.py`/`portfolio.py`; 35 casts a `float()`. Drift de redondeo
  sub-centavo acumulable en miles de boletos. Migrar a `Decimal` los TOTALES
  (no necesariamente cada cálculo intermedio).
- [ ] **EXT-ERR1 · 226 `except Exception` amplios** (0 bare `except:`; ~74 con
  `pass`/`continue`). Mezclan transitorio vs error real. Acotar a excepciones
  concretas en los paths de órdenes/escritura a Mongo; dejar el catch-all solo
  donde es deliberado, con log.
- [ ] **EXT-TEST2 · Frontend sin tests + archivos gigantes.** acaquant-web: 0
  tests, `manager-view.tsx` 2329 líneas, `aum-view.tsx` 1780. Tocar esos
  componentes es alto riesgo. Tests mínimos sobre `proxy.ts` + exportaciones.

### 🟢 BAJO (nuevos)
- [ ] **EXT-AUTH1 · Fail-open de `verify_api_key`** (`api/deps.py:28`): `if not
  API_KEY: return`. Igual que el BAJO de arriba. Fail-closed si `ENV=prod`.
- [ ] **EXT-MATCHER1 · `proxy.ts` matcher cubre 12/55 rutas** (acaquant-web).
  Falta valuaciones/contrapartes/scanner/aum-*/cashflow. **No es agujero**: el
  backend gatea con `require_module` (`api/main.py:174,188`). Es defensa-en-
  profundidad faltante — sumar al matcher por consistencia.

### Refutados (NO re-abrir)
- **SSRF en proxies** — FALSO. `api/routers/news.py:18 _is_safe_external_url`
  ya bloquea IPs privadas/loopback/link-local/metadata + puertos no-estándar,
  se invoca antes del fetch (línea 147) y hay rate-limit. Único residual: DNS
  rebinding (borde reconocido).
- **"Cualquiera se hace pasar por otro user" vía `x-acaquant-user-email`** —
  FALSO en el camino normal. `api/auth.py` valida el JWT de CF criptográficamente
  (JWKS/RS256/audience); el header solo se confía detrás de un service-token JWT
  verificado. Riesgo real reducido a EXT-AUTH2 (bypass de CF a nivel red).
- **Gate frontend "fail-open si falta config"** — DESACTUALIZADO. El fail-open por
  error de red ya es fail-closed (`proxy.ts:86-107`); solo pasa en dev local.

## Orden de remediación sugerido
1. Definir **C1** (modelo de scope) → implementar.
2. **A2** (gate agro) — 2 líneas.
3. **A3** (cache PII contrapartes) — 3 líneas.
4. **EXT-DEP1** (pinear deps) — cierra la clase de incidente "502 al restart".
5. **M1/M2** (rate-limit + compare_digest) + **EXT-AUTH1** (fail-closed prod).
6. **EXT-TEST1** (golden tests RBAC/PnL/AuM) — habilita tocar lo crítico sin miedo.
7. **EXT-XLSX1**, **EXT-MONEY1**, M3/M4, EXT-ERR1, EXT-TEST2, BAJO.

## Para retomar
Arrancar por **A2 + A3 + M2** (bajo riesgo, fix obvio, sin decisión de negocio)
mientras el user confirma el modelo de C1.
