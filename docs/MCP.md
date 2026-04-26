# MCP — TradingAV

Servidor MCP (Model Context Protocol) que expone data **100% de mercado** al
Claude Desktop / Claude Code. Permite que Claude haga análisis razonando
sobre los números reales en lugar de adivinar o de que vos le pegues
screenshots.

## Qué expone

Tools de SOLO LECTURA, ~25 en total. Cada una es thin wrapper sobre un
servicio puro de `api/services/*`.

| Categoría | Tools |
|---|---|
| Curvas y bonos | `listar_curva`, `snapshot_curva_historico`, `pendiente_curva`, `liquidez_secundario`, `historico_trades`, `historico_curva` |
| Atribución (Lecap/Boncap) | `descomposicion_retorno`, `rolldown_esperado` |
| Sensibilidad | `sensibilidad_retorno` |
| Forwards / Breakevens | `forwards_live`, `forwards_historico`, `breakevens_live`, `breakevens_historico` |
| Cauciones / Futuros DLR / MEP | `cauciones_live`, `cauciones_historico`, `futuros_dlr_live`, `futuros_dlr_historico`, `mep_actual`, `mep_historico` |
| Cross-asset | `canje`, `carry_trade` |
| Macro (BCRA / INDEC / scrapings) | `serie_macro`, `clasificar_nivel`, `rem_expectativas` |
| Opciones | `opciones_chain`, `opciones_meta`, `opciones_historico` |

**No expone (por diseño)**: portfolio, operaciones, cuentas, AuM, manager,
intel — son datos privados, no de mercado. Si en el futuro se agregan
tools al asistente, NO copiarlas ciegamente al MCP — respetar la
separación.

## Setup en el server

1. Generar un token bearer (cualquier secreto random, ~32 chars):

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. Agregarlo al `.env` del Droplet:

   ```
   MCP_BEARER_TOKEN=<el-token-de-arriba>
   ```

3. Restart del API:

   ```bash
   systemctl restart api.service
   ```

   En el log debería aparecer `MCP montado en /mcp (Streamable HTTP, bearer auth)`.
   Si en cambio dice `MCP_BEARER_TOKEN no configurado — /mcp deshabilitado`,
   revisar que el `.env` haya cargado.

4. **Cloudflare Access**: el path `/mcp/*` queda detrás de la misma policy
   de CF que el resto del API. Para que Claude Desktop / Code llegue desde
   afuera necesitás un **service token de CF Access** O exponer el path sin
   gate de CF (no recomendado).

   Opción mínima para empezar: agregar el path `/mcp/*` a una application
   de CF Access con policy "Service Auth" usando un service token, o
   directamente "bypass" si el bearer interno es suficiente para vos.

## Setup en el cliente

### Claude Desktop

`~/.claude/claude_desktop_config.json` (Mac/Linux) o `%APPDATA%/Claude/claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "tradingav": {
      "url": "https://api.acaquant.com/mcp",
      "auth": {
        "type": "bearer",
        "token": "<MCP_BEARER_TOKEN del .env>"
      }
    }
  }
}
```

### Claude Code

`~/.claude/settings.json` (global) o `.claude/settings.json` (por proyecto):

```json
{
  "mcpServers": {
    "tradingav": {
      "url": "https://api.acaquant.com/mcp",
      "auth": {
        "type": "bearer",
        "token": "<MCP_BEARER_TOKEN del .env>"
      }
    }
  }
}
```

Después de guardar el config, reiniciar Claude Desktop / Code para que
descubra el server. En la UI de Claude debería aparecer el server
`tradingav` con sus tools listadas.

## Cómo usarlo

Una vez conectado, podés pedirle a Claude cosas como:

- "Mostrame las Lecap rankeadas por rolldown esperado a 60 días."
- "Comparame la curva CER de hoy vs hace 30 días, qué tramo se movió más."
- "Hay dislocaciones de breakevens vs el último REM publicado?"
- "¿Qué bonos del Bonar tienen más upside si el rendimiento de mercado de
  GD30 baja al 8%?"
- "Mostrame la chain de calls de Galicia cerca del ATM con sus IV."

Claude llama las tools, agrupa los datos, razona, y te devuelve el insight.
Todas las llamadas pagan ~50–300 ms (Mongo + cómputo) y pueden estar hasta
~5 min vieja por el cache TTL del API. Para Q&A casual está bien; para
timing de orden no.

## Smoke test desde el server

El endpoint MCP usa Streamable HTTP. Probar con curl que esté arriba:

```bash
TOKEN=$(grep -E '^MCP_BEARER_TOKEN=' /root/TradingAV/.env | cut -d= -f2-)

# Esto debería devolver JSON-RPC initialize response (no 401)
curl -s -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -H "Accept: application/json, text/event-stream" \
     -X POST http://localhost:8000/mcp/ \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
```

Sin el header de Authorization devolvería 401.
