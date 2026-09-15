"""EL ASISTENTE: conversar con los datos del negocio y del mercado, para un admin.

Mapa del paquete (un nodo del grafo = un módulo):
  agente.py      qué es un agente (el objeto) y la instrucción común
  agentes/       un archivo por agente: sus herramientas + su AGENTE; el registro AGENTES
  ruteo.py       a qué agentes les toca la pregunta: reglas primero, modelo después
  junta.py       cruza lo que contestaron varios agentes
  grafo.py       el grafo LangGraph que los enchufa: preparar → ruteo → agentes → junta → finalizar
  herramientas.py función → tool del modelo (docstring = descripción, firma = esquema)
  memoria.py     poda y achicado del historial; dicts del proveedor ⇄ mensajes
  estado.py      el foco (lo que se sabe, aparte de lo que se dijo)
  puerta.py      controles antes de ejecutar una herramienta
  control.py     control de números sobre la respuesta final
  esquema.py     la forma de la respuesta: {respuesta, falta}
  permitido.py   las cuentas habilitadas (ASISTENTE_CUENTAS, fail-closed)
  sesiones.py    conversaciones guardadas (ia.conversaciones)
  panel.py       gasto, tarifas y elección de modelo por tarea
Doc: docs/AvAgentAI.md.
"""
