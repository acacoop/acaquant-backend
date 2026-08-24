"""`agente/` — EL AV AGENT. Doc: `docs/AGENT_2.0.md`.

Reemplaza a los 37 `api/services/av_agent_*` y a los 4 relojes del agente
viejo. La forma es una sola y no hay dónde equivocarse:

    catalogo.py    LAS HABILIDADES. Una fila por cosa que el agente sabe hacer,
                   con su ritmo y su ventana. Sumar una habilidad es una fila.
    registro.py    LA PUERTA ÚNICA. Es el único módulo que escribe hallazgos.
    motor.py       LA AGENDA. Un solo reloj: pregunta a quién le toca y lo corre.
    detectores/    Funciones PURAS o casi: miran y devuelven hallazgos.
    arreglos.py    Lo que ESCRIBE en el sistema. Un arreglo que no escribe no
                   es un arreglo.
    vista.py       El read model: AHORA · ENCONTRÓ · HISTORIAL.

Regla de capas: este paquete usa `core/` y `api/services/` de lectura (salud,
diagnóstico), igual que `engines/` y `jobs/`. Nadie de `core/` lo importa.
"""
