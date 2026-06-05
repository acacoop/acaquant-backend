Router de "operativas" — wrappers de alto nivel sobre /api/ordenes que empaquetan una operación de mesa en sus 2 órdenes atómicas. Hoy solo dólar MEP: compra (BUY AL30 + SELL AL30D) y venta (camino inverso USD→ARS), más cotizaciones live, serie MEP por minuto y listado/detalle de operativas del día. Pensado para crecer con CCL, canjes, etc.

Conecta con: delega en `api.services.operativa_mep`; aplica scope de grupos + idempotencia (`client_order_id`); hereda RBAC del módulo `operaciones`; lo consume la tab MEP del frontend.
