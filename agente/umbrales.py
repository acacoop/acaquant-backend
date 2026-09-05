"""`agente/umbrales.py` — **el único número que necesitan DOS lugares.**

⚠️ **Esto NO es «los umbrales del agente».** Lo fue en el rediseño, cuando los
números estaban desparramados en seis archivos; hoy cada habilidad declara los
suyos en `agente/catalogo.py` y el valor efectivo lo pisa
`agente.habilidades.umbrales` (jsonb, editable sin deploy). Un umbral que lo lee
UN solo lugar no necesita un módulo: vive donde se usa.

Acá queda lo que **dos lugares distintos tienen que leer igual**: la banda donde
una paridad es creíble. La usan el detector `precio_moneda` (por su fila del
catálogo) y la cadena de alta (`agente/alta.py`, para decidir si la referencia
de 1816 es absurda). Con dos copias del 40-160 no falla nada: el detector
marcaría un bono que el alta considera sano, cada mitad coherente consigo misma
(REGLA #9). Por eso el catálogo **importa de acá** en vez de repetir el número.

Lo que se borró de este archivo, y por qué: `PRECIO_VIEJO_MIN`, `LAT_*` y
`PROV_*` eran una copia muerta. Nadie los importaba —`agente/latencia.py` tiene
sus propias constantes y los de proveedores viven en la fila del catálogo—, así
que eran tres pares de números que se podían editar sin que cambiara nada, que
es peor que no tenerlos: se lee como si mandaran.
"""
from __future__ import annotations

# La banda donde una paridad es creíble. Fuera de acá, o el precio está en otra
# escala o el bono tiene un problema real.
PARIDAD_MIN, PARIDAD_MAX = 40.0, 160.0
