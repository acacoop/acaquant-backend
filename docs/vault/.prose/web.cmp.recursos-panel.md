Panel de monitoreo de recursos del servidor (Droplet): CPU, memoria, swap, disco, load average y uptime del sistema, más el consumo (RSS/CPU) por proceso de cada motor/servicio. Grafica la historia reciente con áreas y poltea cada 60s. Es una vista de salud para el Manager.

Conecta con: pega al endpoint de recursos del servidor (api.routers.manager_resources), que muestrea el host con psutil. Solo para roles con acceso a Manager.
