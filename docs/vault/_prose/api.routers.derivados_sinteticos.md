Router HTTP `/api/derivados/sinteticos` — un único GET de solo lectura que devuelve las dos tablas de sintéticos (long-LECAP y short-DLK) armadas matcheando cada instrumento contra un futuro DLR vigente por año-mes de vencimiento. Thin wrapper: toda la lógica vive en el service. Acceso por el gate genérico de `/api/derivados/*` (abierto a los 3 roles).

Conecta con: service `api.services.sinteticos::get_sinteticos`; auth `get_user_email`. Lo consume la vista de derivados/sintéticos en acaquant-web.
