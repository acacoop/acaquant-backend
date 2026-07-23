---
description: Chequeo de salud del asistente/copiloto en un solo lugar — ruteo de privacidad, tools que traen datos, contexto de las vistas y no-leak de PII
---

Junta en un comando las 4 verificaciones de salud de la IA que si no, se corren
sueltas. Todo read-only o casi (solo `eval_asistente` gasta pocos tokens).

**Importante:** estos scripts corren EN el Droplet (necesitan la DB y las
credenciales reales del `.env`). Claude NO tiene acceso al Droplet (REGLA #0):
este comando **arma el plan y te dice qué correr**; vos los corrés allá (o con
`!` en la sesión) y me pegás la salida, que yo interpreto.

## Las 4 verificaciones (0 tokens salvo la última)

```bash
# 1. RUTEO DE PRIVACIDAD — a qué proveedor va cada tarea y si su credencial está.
#    La tarea del asistente de negocio DEBE ir a un proveedor con 'no entrena = sí'.
python -m scripts.smoke_asistente --ruteo

# 2. TOOLS DEL ASISTENTE — ¿cada tool trae datos contra la DB real? ✓ / ? (vacía) / ✗ (rota)
#    Con EVAL_EMAIL de un usuario con Control Comercial se prueban también las gateadas (🔒).
EVAL_EMAIL=<un_email_con_control_comercial> python -m scripts.smoke_asistente --tools

# 3. CONTEXTO DEL COPILOTO — ¿cada vista de mercado arma su contexto? bloques por vista.
python -m scripts.smoke_copiloto --contexto

# 4. NO-LEAK DE PII (gasta pocos tokens) — un cliente REAL, ninguna identidad sale al proveedor.
python -m scripts.eval_asistente
```

## Cómo interpretar la salida (lo que reporto cuando me la pegás)

1. **Ruteo**: si la tarea `asistente_negocio` NO va a un proveedor con no-retención,
   o su credencial dice FALTA → el asistente se apaga (fail-closed, NO cae al otro).
   Eso es correcto pero hay que resolver la credencial. Cualquier tarea de MERCADO
   en un proveedor que entrena es esperable (datos públicos).
2. **Tools**: `✓` = trae datos. `?` = vacía — puede ser legítimo (no hay
   anomalías, no hubo movimientos) o una tool MUDA (lee una clave que el service
   no emite). Ante un `?` en algo que debería tener datos, verificar contra la
   vista equivalente de la web. `✗` = rota (falla o no existe el handler) → bug.
   `🔒` = gateada por Control Comercial, el usuario del smoke no tiene el permiso
   (correr con `EVAL_EMAIL` de alguien que sí).
3. **Contexto**: `✗ 0 filas` en una vista con dataset global = el copiloto
   responde "datos_no_disponibles". Un conteo de bloques más bajo del esperado =
   un service cambió de shape y un bloque quedó mudo. `trading` con 0 filas sin
   parámetros es esperado (su contexto sale de las tarjetas del trader).
4. **No-leak**: tiene que salir `2/2 OK` (o los casos que haya). Un FAIL es un
   bug de SEGURIDAD (una identidad salió del perímetro) → prioridad máxima,
   aplicar el skill `tocar-aduana`.

## Cuándo correrlo

- Después de un deploy que tocó IA.
- Después de tocar la aduana (complementa el skill `tocar-aduana`).
- Cuando "el asistente contesta raro" y no sabés si es una tool muda, un ruteo
  mal, o un contexto vacío — esto lo separa en un vistazo.

Herramientas relacionadas: `diag_ia_trazas --full` (ver las últimas N llamadas
enteras — usalo para el skill `triage-conversacion`), `diag_pii_matcher --frase`
(calibrar la aduana).
