# 🧠 acaquant — el cerebro del sistema

Mapa vivo y navegable de TODO: backend + frontend + base + deploy.
Generado por `scripts/gen_obsidian.py` (no editar a mano).

Abrí el **graph view** (Ctrl/Cmd+G) para ver cómo conecta todo con todo.

**598 nodos** en total.

## Mapas por capa

- [[_core|🧱 core — infraestructura]]  (24)
- [[_quant|📐 quant — cálculo puro]]  (7)
- [[_engines|⚙️ engines — motores WS→Mongo]]  (17)
- [[_jobs|⏱️ jobs — batch / cron]]  (47)
- [[_api|🌐 api — services · routers · mcp]]  (95)
- [[_partner_api|🤝 partner_api]]  (8)
- [[_config|⚙️ config]]  (1)
- [[_scripts|🔧 scripts — one-shot · migraciones · diag]]  (107)
- [[_tests|🧪 tests — red de seguridad]]  (26)
- [[_db|🗄️ base — colecciones Mongo]]  (29)
- [[_deploy|🚀 deploy — servicios + crons]]  (46)
- [[_web-view|🖥️ web — vistas]]  (17)
- [[_web-component|🧩 web — componentes]]  (107)
- [[_web-api|🔌 web — rutas API (proxy)]]  (54)
- [[_web-lib|📚 web — lib]]  (13)

## Flujo de datos (alto nivel)

```
pyRofex WS → engines/ → Trading.MarketSnapshot → api/services → api/routers
                                                       ↓
        acaquant-web (vistas) ← rutas API (proxy) ← FastAPI
```
