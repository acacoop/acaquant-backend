Contenedor del módulo Agro (/agro): orquesta el selector único de commodity (Trigo/Maíz/Soja) y arma el layout — pizarra arriba, futuros + cadena de opciones a la izquierda, estrategias a la derecha. Comparte el commodity elegido entre los sub-paneles.

Conecta con: compone `web.cmp.derivados-agro-pizarra`, `-futuros`, `-opciones` y `-estrategias`; todos consumen los endpoints `/api/derivados/agro` del backend.
