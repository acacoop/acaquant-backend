"""`agente/umbrales.py` — los números, en UN lugar.

En el agente viejo estaban desparramados en SEIS archivos y ninguno se podía
tocar sin un deploy. Acá viven los DEFAULTS; el valor efectivo de cada habilidad
sale de `agente.habilidades.umbrales` (jsonb), que se edita sin deploy.

Estos módulos-constante existen solo para el código que todavía los importa
directo (la cadena de alta). Un detector NO los lee: recibe sus umbrales.
"""
from __future__ import annotations

# La banda donde una paridad es creíble. Fuera de acá, o el precio está en otra
# escala o el bono tiene un problema real.
PARIDAD_MIN, PARIDAD_MAX = 40.0, 160.0

# Cuántos minutos sin actualizarse para considerar viejo un precio. El motor
# reescribe el snapshot cada pocos segundos: 20 minutos es un papel que dejó de
# operar o una suscripción caída, no un instante sin trades.
PRECIO_VIEJO_MIN = 20

# LATENCIA. La referencia es la MEDIANA de las propias horas previas del
# endpoint, no el promedio de los otros: se compara contra sí mismo.
LAT_VENTANA_H, LAT_BASE_H = 2, 72
LAT_MIN_REQUESTS, LAT_MIN_HORAS_BASE = 20, 6
LAT_FACTOR, LAT_MIN_DELTA_MS, LAT_MIN_ERRORES = 2.5, 300, 3

# PROVEEDORES. La prueba activa solo corre si YA hay una falla en el rastro de
# las llamadas reales: nunca en el camino feliz, porque 1816 cobra por llamada.
PROV_VENTANA_S, PROV_MINIMO_FALLOS = 20 * 60, 1

# MOTORES. Los primeros minutos de rueda no son un motor caído: es un motor
# arrancando.
GRACIA_ARRANQUE_MIN = 30

DEFAULTS = {
    "paridad_min": PARIDAD_MIN, "paridad_max": PARIDAD_MAX,
    "precio_viejo_min": PRECIO_VIEJO_MIN,
}
