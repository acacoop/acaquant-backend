---
description: Preview y aplicación del seed de Manager.Users / RoleMatrix
---

Corré `scripts.seed_roles` en dry-run, mostrá el preview, y ofrecé aplicar si el output es razonable.

Pasos:

1. `python -m scripts.seed_roles --dry` — capturá todo el output.
2. Resumí:
   - Cuántos roles va a seedear en `Manager.RoleMatrix` (o "ya existen, no sobrescribe").
   - Cuántos emails de `MANAGER_EMAILS` va a crear en `Manager.Users` como admin.
   - Cuántos ya existen (no los toca).
3. Validaciones:
   - Si `MANAGER_EMAILS` está vacío, advertí y detenete — no tiene sentido seedear sin admins.
   - Si hay errores de conexión a Mongo, reportá y pará (puede ser Atlas pausado entre 04:00–11:20 UTC).
4. Preguntale al usuario si quiere aplicar. Si dice sí, corré sin `--dry` y confirmá con un chequeo:
   - `python -c "from core.roles import list_users; print(len(list_users()))"` para confirmar que escribió.
5. Si hay admins que el usuario quería agregar y no estaban en `MANAGER_EMAILS`, recordale que puede usar `core.roles.upsert_user(email, role, actor='...')` o esperar a la Fase 2 (admin UI).

**No corras nunca `--force`** sin confirmación explícita del usuario — reescribe la matriz custom que él pudo haber editado.
