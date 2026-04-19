"""Agente conversacional para la mesa.

Arquitectura:
- provider.py: cliente HTTP hacia Gemini (swappable por Claude/OpenAI en el futuro).
- tools.py: declaración de herramientas (endpoints internos expuestos al LLM) + dispatch.
- prompt.py: system prompt con reglas de negocio.
- runner.py: bucle de tool-use (modelo pide tool → ejecutamos → le devolvemos respuesta).
"""
