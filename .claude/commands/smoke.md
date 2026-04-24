---
description: Corre los smoke tests del API y del asistente contra localhost
---

Corré los smoke tests del backend para validar que los endpoints principales y el asistente responden. Uvicorn tiene que estar arriba en `localhost:8000` antes de empezar.

Pasos:

1. Chequeá que uvicorn esté corriendo: `curl -s http://localhost:8000/api/health`. Si no responde, pedile al usuario que lo levante con `uvicorn api.main:app --reload --port 8000`.
2. Corré `python -m scripts.test_api`. Reportá:
   - Endpoints que respondieron OK.
   - Endpoints que devolvieron error (status + mensaje).
3. Corré `python -m scripts.test_chat`. Reportá si el asistente respondió texto válido o si hubo error de provider/auth.
4. Resumen final: ✓ o ✗ por cada suite. Si hay errores, sugerí el próximo paso a investigar (ej. "el 502 en `/api/cotizaciones/mep` suele ser Atlas pausado entre 04:00–11:20 UTC").

No re-ejecutes pasos que fallaron — reportá y dejá que el usuario decida cómo seguir.
