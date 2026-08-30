# 🧠 acaquant — el cerebro del sistema

Mapa vivo y navegable de TODO: backend + frontend + base + deploy.
Generado por `scripts/gen_obsidian.py` (no editar a mano).

Abrí el **graph view** (Ctrl/Cmd+G) para ver cómo conecta todo con todo.

**448 nodos** en total.

## Mapas por capa

- [[_core|🧱 core — infraestructura]]  (59)
- [[_quant|📐 quant — cálculo puro]]  (11)
- [[_engines|⚙️ engines — motores WS→Mongo]]  (19)
- [[_jobs|⏱️ jobs — batch / cron]]  (67)
- [[_api|🌐 api — services · routers]]  (194)
- [[_config|⚙️ config]]  (1)
- [[_db|🗄️ base — colecciones Mongo]]  (26)
- [[_deploy|🚀 deploy — servicios + crons]]  (71)

## Flujo de datos (alto nivel)

```
pyRofex WS → engines/ → Trading.MarketSnapshot → api/services → api/routers
                                                       ↓
        acaquant-web (vistas) ← rutas API (proxy) ← FastAPI
```
