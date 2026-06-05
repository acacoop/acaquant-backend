Router `/api/back-office`: sección Back Office. Por ahora expone solo `titulos-mercado` — qué títulos hay que enviar y recibir hoy contra el mercado, calculando el settlement (ops del día con plazo CI/Inmediato + ops del día hábil anterior a 24hs). Si la fecha no es día hábil devuelve estructura vacía con `mercado_cerrado`.

Conecta con: delega en `api.services.back_office_titulos.get_titulos_mercado` (deriva de `CashFlow.NegocioMovimientos`); requiere identidad vía `api.auth.get_user_email`; lo monta `api.main`.
