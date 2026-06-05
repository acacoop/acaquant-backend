Valida la idempotencia del envío de órdenes (anti doble-orden por reintento o doble-click), mockeando motor y store. Congela la lógica del wrapper `send_order`: sin clave manda directo, clave nueva manda una vez y guarda el resultado, clave duplicada NO manda y devuelve el resultado del primer envío, y un error se registra y se relanza. Garantía dura: un duplicado nunca llega al broker.

Conecta con: importa `api.services.ordenes` y `api.services._idempotencia`; red de seguridad del flujo de órdenes LIVE contra ROFEX.
