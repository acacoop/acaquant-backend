Guard anti doble-orden. Cada intención de orden lleva un `client_order_id`: el primer envío reserva la clave y manda al broker; un reenvío con la MISMA clave no manda otra orden, devuelve el resultado del primero. Sin clave, no se invoca (retrocompatible). Degradación segura: ante error de infra prefiere MANDAR antes que tragar la orden.

Conecta con: usa `Operaciones.OrdenesIdempotency` (índice único en `key` para atomicidad ante doble submit simultáneo + TTL 1 día); lo invocan los routers operar/operativa al envolver el envío real (`ejecutar_idempotente`).
