---
id: web.lib.cf-access
type: lib
layer: web-lib
repo: frontend
tags: [lib, web-lib, frontend]
path: src/lib/cf-access.ts
---

# web/lib/cf-access

**Archivo:** `src/lib/cf-access.ts`

## Qué hace
Valida el sello firmado de Cloudflare Access (header `Cf-Access-Jwt-Assertion`) para obtener el email de confianza del usuario sin riesgo de spoofing. El email en texto plano es falsificable si alguien llega al origin salteando Cloudflare (ej. la URL `*.vercel.app`); el JWT firmado por CF no. Se activa solo si están las env vars `CF_ACCESS_TEAM_DOMAIN` + `CF_ACCESS_AUD` (borrar una revierte al instante, sin tocar código); si no, cae al header de texto plano (dev local).

Conecta con: lo usan `web.lib.api`, `web.lib.me` y `web.lib.proxy-backend` vía `trustedEmail()` para resolver qué email mandarle al backend; verifica contra los certs JWKS de Cloudflare (`<TEAM>/cdn-cgi/access/certs`).

## Lo usan (backlinks) ←
- [[web.api.api.me]]  ·  _route_
- [[web.lib.proxy]]  ·  _lib_
