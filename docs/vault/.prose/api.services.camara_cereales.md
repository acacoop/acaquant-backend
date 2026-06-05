Single source of truth de los precios disponibles de granos (5 cereales: TRIGO, MAIZ, GIRASOL, SOJA, SORGO) que reporta la Cámara Arbitral de Cereales de Rosario. El trader los carga a mano en ARS y USD por separado (no hay fórmula entre uno y otro) y la app los reutiliza en varias vistas. No confundir con la pizarra de pase del agro.

Conecta con: lee/escribe `Derivados.CamaraCereales` (5 docs, _id = cereal) con audit en `Derivados.CamaraCerealesAudit`; lo consumen el router de agro y `api.services.mejoras_dispo`.
