# Herramientas de QA

Scripts usados para validar el sistema de punta a punta. Requieren `pip install playwright pyotp httpx`
y `python -m playwright install chromium`.

| Script | Qué hace | Uso |
|---|---|---|
| `levantar_revision.py` | Levanta el stack de producción aislado (`inv_ui`, https://localhost:8443) con el inventario real | `python levantar_revision.py` |
| `shots.py` | Captura todas las rutas en escritorio/tablet/móvil y detecta desbordes y errores de consola | `python shots.py <salida> "<clave sa>" es [desk,tab,mob]` |
| `palette_check.py` | Aplica una paleta vía API, captura y mide el contraste real (WCAG) | `python palette_check.py <salida> "<clave>" "#bg" "#pri" "#sec" <etiqueta>` |
| `iam_flow.py` | Flujos de seguridad en navegador: primer ingreso con MFA obligatorio, contraseña temporal, admin de seguridad | `python iam_flow.py <salida> "<clave sa>"` (sa sin MFA) |
| `alcance_flow.py` | Alcance por sede, rol AUDITOR, autocompletado de sede y sesiones activas (navegador + API) | `python alcance_flow.py <salida> "<clave sa>"` (sa sin MFA) |
| `adjuntos_check.py` | Sube un PDF a un activo y una factura a una orden, los descarga (SHA-256) y confirma que quedan en MinIO | `python adjuntos_check.py <usuario> "<clave>" [secreto_totp]` |
| `e2e_check.py` | Sesiones, proxy, cabeceras, cola de correos, rate limit contra el stack | `python e2e_check.py <archivo .env del stack>` |
| `verify.py` | Verifica las traducciones: mismas claves en es/en/it, claves usadas existen, sin huérfanas ni corrupción | ejecutar desde `inventarioTI-frontend/` |
| `i18n_set.py` | Agrega/modifica/borra claves en los 3 idiomas conservando formato | `python i18n_set.py spec.json` |

Pruebas del backend (solo PostgreSQL 16, el motor de producción; la suite rechaza SQLite). Atajo que levanta la base y ejecuta todo: `python pruebas_backend.py`. Manualmente:

```bash
docker run -d --rm --name inv_pgtest -e POSTGRES_USER=t -e POSTGRES_PASSWORD=t -e POSTGRES_DB=t -p 55433:5432 postgres:16-alpine
TEST_DATABASE_URL="postgresql+psycopg://t:t@localhost:55433/t" python -m pytest tests/ -q
```
