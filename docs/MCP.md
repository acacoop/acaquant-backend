# MCP — TradingAV

Servidor MCP que expone data **100% de mercado** al Claude Desktop /
Claude Code / claude.ai. Permite que Claude haga análisis razonando sobre
los números reales en lugar de adivinar o de que vos le pegues
screenshots.

**Hoy es un asistente 100% de RENTA VARIABLE** (equities ARG). El catálogo
completo de cada tool está en `docs/MCP_TOOLS.md`.

## Qué expone

Tools de SOLO LECTURA, **13 en total**, todas de renta variable. Cada una es
thin wrapper sobre un servicio puro de `api/services/{scanner_sql,day_trading,
rv_motor}.py`. El registro vive en `api/mcp/tools/renta_variable.py`.

| Categoría | Tools |
|---|---|
| Universo | `rv_universo` |
| Live de mercado | `cedears_scanner`, `ccl_live` |
| Time sales intradía | `cedears_tape`, `cedears_intraday` |
| Histórico + quant (USD) | `acciones_retornos`, `acciones_quant_stats`, `pivot_points` |
| Day-trading lab | `day_trading_scanner`, `day_trading_companeros` |
| Mesa de Estrategia | `correlacion_matriz`, `trade_analysis`, `book_analysis` |

**No expone (por diseño, REGLA #8)**: portfolio, operaciones, cuentas, AuM,
manager, clientes — datos privados, no de mercado. `trade_analysis` y
`book_analysis` operan SOLO sobre posiciones que el usuario describe como
parámetro; no leen ninguna cuenta real.

**No hay tools de otros dominios de mercado** (renta fija, derivados, opciones,
forwards, breakevens, cauciones, futuros DLR, MEP, macro). El código pausado que
las tenía se borró el 2026-08-28: nunca estuvo registrado, así que nunca fue
alcanzable.

## Auth — dos caminos

El middleware del MCP acepta dos tipos de bearer:

### A) OAuth (Claude Desktop / claude.ai / Claude Code via Custom Connector)

Flujo completo OAuth 2.1 con PKCE + Dynamic Client Registration. Login
delegado a **Cloudflare Access** — cuando Claude Desktop redirige al user
a `/oauth/authorize`, CF Access lo desafía con email + OTP, y nuestro
server lee la identidad del JWT validado por CF para emitir el access
token.

- `MCP_JWT_SECRET` en `.env` activa este path.
- Endpoints implementados:
  - `POST /oauth/register` (RFC 7591 — Dynamic Client Registration)
  - `GET  /oauth/authorize` (gated por CF Access)
  - `POST /oauth/token` (intercambio code → access_token con PKCE)
  - `GET  /.well-known/oauth-protected-resource` (RFC 9728)
  - `GET  /.well-known/oauth-authorization-server` (RFC 8414)
- Storage: SQL schema `mcp`, tablas `mcp.oauth_clients` / `mcp.oauth_codes` /
  `mcp.oauth_tokens` (conexión `core.postgres.get_pool()`). TTL automático
  (10min codes, 1h tokens).

### B) Static bearer token (curl, scripts, dev)

`MCP_BEARER_TOKEN` en `.env`. Cualquier request con
`Authorization: Bearer <token>` con ese valor pasa, sin OAuth. Útil para
smoke tests, scripts internos.

## Setup en el server

### 1) Generar secrets

```bash
# JWT secret (firma los access tokens OAuth)
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# (opcional) Static bearer token para dev
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 2) Sumar al `.env` del Droplet

```
MCP_JWT_SECRET=<el-secret-largo>
MCP_BEARER_TOKEN=<el-token-corto>             # opcional, fallback dev
MCP_OAUTH_ISSUER=https://api.acaquant.com     # default ya OK
```

### 3) Restart

```bash
systemctl restart api.service
```

En el log deberías ver:
```
MCP montado en /mcp + OAuth + discovery (JWT)
```

### 4) Cloudflare Access — múltiples destinos

La app de CF Access `acaquant-mcp-bypass` (la que creamos antes) tenía
SOLO `api.acaquant.com/mcp` como destino. Hay que sumar más destinos
para que los endpoints de discovery y token sean accesibles sin login:

En CF Zero Trust → Access → Applications → editar `acaquant-mcp-bypass` →
Destinations:

| Destino | ¿Por qué Bypass? |
|---|---|
| `api.acaquant.com/mcp` (ya existe) | Tráfico MCP, gated por nuestro middleware bearer |
| `api.acaquant.com/oauth/token` | Claude Desktop lo POSTea sin browser → no puede hacer login CF |
| `api.acaquant.com/oauth/register` | Idem (DCR llamada de máquina) |
| `api.acaquant.com/.well-known/oauth-protected-resource` | Discovery público |
| `api.acaquant.com/.well-known/oauth-authorization-server` | Discovery público |

**NO incluyas `api.acaquant.com/oauth/authorize`** — ese path SÍ tiene
que estar gateado por la app general de CF Access (es donde el user se
loguea).

## Setup en el cliente

### Claude Desktop

1. Abrí Claude Desktop → Settings → **Conectores**.
2. **Añadir conector personalizado**.
3. Pegá:
   - Nombre: `TradingAV`
   - URL: `https://api.acaquant.com/mcp/`
4. Sin OAuth Client ID ni Client Secret — los detecta solo via DCR.
5. Apretá **Conectar**. Se abre browser → CF Access te pide email + OTP →
   logueás → vuelve a Claude → conectado.
6. En el chat, las 13 tools de renta variable deberían aparecer al toque.

### Claude Code

```bash
claude mcp add tradingav https://api.acaquant.com/mcp/ --transport http
```

(reemplazar `--transport http` por la flag actual si cambia). El primer
uso abre browser con CF Access; flujo idéntico.

### claude.ai (web)

Settings → Connectors → Add custom connector → URL =
`https://api.acaquant.com/mcp/`. Mismo flujo.

## Cómo usarlo

Una vez conectado, podés pedirle a Claude cosas como:

- "Mostrame el tablero de CEDEARs ahora, ordenado por variación en USD."
- "¿Qué papeles de IA hicieron más vueltas de 0.5% hoy para scalpear?"
- "Quiero comprar USD 20k de NVDA, ¿cómo me cubro vs SPY?"
- "Analizá el riesgo de un book AAPL:10000, MSFT:8000, TSLA:-5000."
- "Dame los soportes y resistencias de YPF en los 4 timeframes."

Claude llama las tools, agrupa los datos, razona y te devuelve el insight.
Cada llamada paga ~50–300 ms. Cache TTL de 60–300s en muchos services.

## Smoke test desde el server (static bearer)

```bash
TOKEN=$(grep '^MCP_BEARER_TOKEN=' /root/TradingAV/.env | cut -d= -f2-)
curl -s -X POST http://localhost:8000/mcp/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
```

## Smoke test del discovery público

Sin auth (debería responder 200 con el JSON de metadata):

```bash
curl -s https://api.acaquant.com/.well-known/oauth-authorization-server | jq .
```

Si tira 403 o HTML de CF Access, falta agregar ese path como destino
Bypass en CF Access.
