# 🧠 acaquant — el cerebro del sistema

Mapa vivo y navegable de TODO: backend + frontend + base + deploy.
Generado por `scripts/gen_obsidian.py` (no editar a mano).

Abrí el **graph view** (Ctrl/Cmd+G) para ver cómo conecta todo con todo.

**512 nodos** en total.

## Mapas por capa

- [[_core|🧱 core — infraestructura]]  (31)
- [[_quant|📐 quant — cálculo puro]]  (8)
- [[_engines|⚙️ engines — motores WS→Mongo]]  (17)
- [[_jobs|⏱️ jobs — batch / cron]]  (43)
- [[_api|🌐 api — services · routers · mcp]]  (140)
- [[_partner_api|🤝 partner_api]]  (10)
- [[_config|⚙️ config]]  (1)
- [[_db|🗄️ base — colecciones Mongo]]  (29)
- [[_deploy|🚀 deploy — servicios + crons]]  (49)
- [[_web-view|🖥️ web — vistas]]  (20)
- [[_web-component|🧩 web — componentes]]  (97)
- [[_web-api|🔌 web — rutas API (proxy)]]  (53)
- [[_web-lib|📚 web — lib]]  (14)

## Flujo de datos (alto nivel)

```
pyRofex WS → engines/ → Trading.MarketSnapshot → api/services → api/routers
                                                       ↓
        acaquant-web (vistas) ← rutas API (proxy) ← FastAPI
```
