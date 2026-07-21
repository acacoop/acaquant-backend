# 🧠 acaquant — el cerebro del sistema

Mapa vivo y navegable de TODO: backend + frontend + base + deploy.
Generado por `scripts/gen_obsidian.py` (no editar a mano).

Abrí el **graph view** (Ctrl/Cmd+G) para ver cómo conecta todo con todo.

**639 nodos** en total.

## Mapas por capa

- [[_core|🧱 core — infraestructura]]  (41)
- [[_quant|📐 quant — cálculo puro]]  (8)
- [[_engines|⚙️ engines — motores WS→Mongo]]  (17)
- [[_jobs|⏱️ jobs — batch / cron]]  (54)
- [[_api|🌐 api — services · routers · mcp]]  (193)
- [[_partner_api|🤝 partner_api]]  (10)
- [[_config|⚙️ config]]  (1)
- [[_db|🗄️ base — colecciones Mongo]]  (29)
- [[_deploy|🚀 deploy — servicios + crons]]  (59)
- [[_web-view|🖥️ web — vistas]]  (22)
- [[_web-component|🧩 web — componentes]]  (124)
- [[_web-api|🔌 web — rutas API (proxy)]]  (66)
- [[_web-lib|📚 web — lib]]  (15)

## Flujo de datos (alto nivel)

```
pyRofex WS → engines/ → Trading.MarketSnapshot → api/services → api/routers
                                                       ↓
        acaquant-web (vistas) ← rutas API (proxy) ← FastAPI
```
