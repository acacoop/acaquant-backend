# 🧠 acaquant — el cerebro del sistema

Mapa vivo y navegable de TODO: backend + frontend + base + deploy.
Generado por `scripts/gen_obsidian.py` (no editar a mano).

Abrí el **graph view** (Ctrl/Cmd+G) para ver cómo conecta todo con todo.

**502 nodos** en total.

## Mapas por capa

- [[_core|🧱 core — infraestructura]]  (30)
- [[_quant|📐 quant — cálculo puro]]  (8)
- [[_engines|⚙️ engines — motores WS→Mongo]]  (17)
- [[_jobs|⏱️ jobs — batch / cron]]  (52)
- [[_api|🌐 api — services · routers · mcp]]  (118)
- [[_partner_api|🤝 partner_api]]  (9)
- [[_config|⚙️ config]]  (1)
- [[_db|🗄️ base — colecciones Mongo]]  (29)
- [[_deploy|🚀 deploy — servicios + crons]]  (53)
- [[_web-view|🖥️ web — vistas]]  (19)
- [[_web-component|🧩 web — componentes]]  (99)
- [[_web-api|🔌 web — rutas API (proxy)]]  (53)
- [[_web-lib|📚 web — lib]]  (14)

## Flujo de datos (alto nivel)

```
pyRofex WS → engines/ → Trading.MarketSnapshot → api/services → api/routers
                                                       ↓
        acaquant-web (vistas) ← rutas API (proxy) ← FastAPI
```
