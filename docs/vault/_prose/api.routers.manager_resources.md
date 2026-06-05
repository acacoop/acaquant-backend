Endpoints de monitoreo de recursos del Droplet para el Manager. `GET /api/manager/resources` da un snapshot vivo (CPU/RAM/swap/disco/load + RSS y CPU de los procesos clave: api, motores, cloudflared) y `GET /api/manager/resources/history` devuelve el buffer circular de hasta 180 muestras (3h a 1/min). El histórico lo llena `resources_sampler_loop`, un background task montado en el lifespan de la API.

Conecta con: lee métricas del SO vía `psutil` (no toca Mongo); el sampler lo arranca `api.main` en su lifespan; lo consume la vista de recursos del frontend Manager.
