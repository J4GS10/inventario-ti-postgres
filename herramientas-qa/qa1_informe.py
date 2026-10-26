"""
QA1 — Genera salidas/qa1_informe.md a partir de salidas/qa1/api_resultados.json
(qa1_crud.py) y de las comprobaciones en navegador (qa1_ui.py).

Uso (cwd = herramientas-qa):  PYTHONIOENCODING=utf-8 python qa1_informe.py
"""
import json
import os

AQUI = os.path.dirname(os.path.abspath(__file__))
API = json.load(open(os.path.join(AQUI, "salidas", "qa1", "api_resultados.json"), encoding="utf-8"))

# Ajustes tras revisar evidencias y código: (estado, severidad, nota)
AJUSTES = {
    # Duplicados rechazados por la BD pero con el mensaje genérico de integridad
    **{k: ("DEFECTO", "baja", "Se rechaza, pero con el mensaje genérico de integridad: no dice que el nombre ya existe")
       for k in ("CAT-09", "CAT-12", "CAT-15", "CAT-26", "CAT-31", "GEO-02", "GEO-03", "ORG-03", "ORG-07",
                 "SOF-02", "SOF-04", "CON-07", "COM-05", "ESP-02")},
    "ORG-17": ("DEFECTO", "media", "La BD lo impide, pero el servicio no valida el correo al editar y la UI muestra el mensaje genérico de integridad"),
    **{k: ("DEFECTO", "baja", "Referencia inexistente: 409 INTEGRITY genérico en vez de 404 *_NOT_FOUND") for k in ("CAT-20", "GEO-06", "ACT-25")},
    **{k: ("DEFECTO", "baja", "Se bloquea bien, pero el código no tiene traducción (no hay pantalla para esta entidad; solo se ve por API)")
       for k in ("CAT-27", "CAT-28", "CAT-37", "ESP-05", "ORG-20")},
    **{k: ("DEFECTO", "media", "Se bloquea bien, pero el código no tiene traducción: la UI muestra 'No se pudo guardar el registro' (ver UI-22)")
       for k in ("GEO-16", "GEO-17", "GEO-18", "GEO-19", "GEO-20", "GEO-21")},
    "SOF-16": ("DEFECTO", "media", "Se bloquea bien (CANNOT_REDUCE_BELOW_USED:2), pero el código no tiene traducción: la UI muestra un mensaje genérico"),
    "SOF-19": ("DEFECTO", "media", "Se bloquea bien (CANNOT_DELETE_LICENSE_WITH_ACTIVE_INSTALLATIONS:2), pero el código no tiene traducción"),
    "ACT-16": ("DEFECTO", "baja", "404 correcto, pero MODEL_NOT_FOUND (y todos los *_NOT_FOUND) no tienen traducción"),
    "CAT-10": ("OK", "-", "404 correcto (BRAND_NOT_FOUND sin traducción; se agrupa con ACT-16)"),
    "CAT-04": ("DEFECTO", "media", "Acepta 'qa1-… marca' cuando existe 'QA1-… Marca' (unicidad sensible a mayúsculas)"),
    "CAT-06": ("DEFECTO", "media", "Crea una marca con nombre '     ' (min_length cuenta los espacios)"),
    "CAT-16": ("DEFECTO", "baja", "Acepta el prefijo 'a b-%': los códigos automáticos salen con espacios y símbolos"),
    "CAT-19": ("DEFECTO", "media", "Crea un segundo modelo con el mismo nombre en la misma marca"),
    "CAT-36": ("DEFECTO", "media", "Borra un tipo de conexión en uso (204) y deja los modelos con TCN_Tipo_Conexion=null en silencio"),
    "GEO-04": ("DEFECTO", "baja", "Acepta el código ISO '1$'"),
    "GEO-13": ("DEFECTO", "baja", "ARE_Tipo_Acceso de 300 caracteres → 500 DATABASE_ERROR (columna varchar(50) sin cota en el schema)"),
    "ORG-11": ("DEFECTO", "alta", "Acepta QA1.JEFE.…@lombardi.local cuando existe qa1.jefe.…@lombardi.local: dos personas con el mismo buzón"),
    "ORG-13": ("DEFECTO", "baja", "Acepta el teléfono 'abc-xyz'"),
    "ORG-16": ("DEFECTO", "baja", "Permite el ciclo de jefatura A↔B"),
    "ORG-18": ("DEFECTO", "baja", "Permite asignar una persona a un departamento desactivado"),
    "ACT-09": ("OBSERVACIÓN", "baja", "Acepta fecha de compra a 400 días en el futuro"),
    "ACT-11": ("DEFECTO", "media", "Acepta la misma serie en minúsculas"),
    "ACT-12": ("DEFECTO", "media", "Acepta la misma serie con espacios alrededor y la guarda sin recortar"),
    "ACT-18": ("DEFECTO", "alta", "500 'Internal Server Error' (texto plano) para CUALQUIER activo, también los reales: MissingGreenlet al serializar modelo/tipo_activo/estado_operativo"),
    "ACT-23": ("DEFECTO", "media", "500 DATABASE_ERROR: ActivoUpdate.ACT_Costo no tiene max_digits (sí lo tiene el alta)"),
    "ACT-24": ("DEFECTO", "media", "Responde 500 (texto plano) pero GUARDA 12.35: el usuario ve error y el cambio queda aplicado"),
    "ACT-28": ("DEFECTO", "media", "PATCH con SED_Sede=null deja el activo SIN sede (el formulario envía null si el campo queda vacío); solo lo verán cuentas globales"),
    "SOF-17": ("OBSERVACIÓN", "baja", "Total=10 con 3 claves individuales; no hay endpoint para añadir claves a una licencia existente"),
    "SOF-18": ("DEFECTO", "baja", "La edición no aplica la validación LICENSE_EXPIRATION_IN_THE_PAST del alta"),
    "CON-03": ("DEFECTO", "baja", "Acepta 'QA1-… TÓNER' cuando existe 'QA1-… Tóner'"),
    "CON-12": ("DEFECTO", "baja", "Permite salidas de stock de un consumible desactivado"),
    "COM-04": ("OBSERVACIÓN", "baja", "Dos proveedores con el mismo NIT"),
    "COM-15": ("DEFECTO", "media", "500 DATABASE_ERROR (numeric overflow): LineaCreate.OCL_Precio_Unitario sin max_digits/decimal_places"),
    "COM-16": ("DEFECTO", "baja", "Crea una orden sin líneas con total 0"),
    "COM-17": ("DEFECTO", "baja", "Permite una orden a un proveedor desactivado"),
    "COM-18": ("OBSERVACIÓN", "baja", "Acepta fecha de orden a 500 días"),
    "COM-27": ("DEFECTO", "alta", "PATCH /estado permite RECIBIDA → BORRADOR (no hay máquina de estados; solo se bloquea CANCELADA)"),
    "COM-28": ("DEFECTO", "alta", "Tras reabrirla, la orden se recibe otra vez: el stock del consumible pasa de 3 a 6 (doble entrada)"),
    "DEL-03": ("OK", "-", "409 correcto: el proveedor B tiene la orden creada en COM-17 (efecto de ese defecto, no un fallo nuevo)"),
}

UI = [
    ("UI-00", "Inicio de sesión en la UI con MFA (código vía codigo_totp_ui)", "Login usuario/clave + TOTP", "Entra al panel", "Entra al panel", "salidas/qa1/ui/00_login.png", "-", "OK"),
    ("UI-01…12", "Recorrido de 12 pantallas CRUD (marcas, tipos, modelos, ubicaciones, departamentos, empleados, activos, software, consumibles, proveedores, órdenes, garantías)",
     "Abrir cada ruta y capturar", "Carga sin errores", "11/12 sin errores; Activos falló en la 1.ª pasada (ver UI-07)", "salidas/qa1/ui/01_marcas.png … 12_garantias.png", "-", "OK"),
    ("UI-07", "Pantalla Activos bajo carga concurrente", "Abrir /activos mientras otros agentes usan la API",
     "Listado cargado, o aviso claro si hay límite", "Tabla vacía '0 activos' + 'No se pudieron cargar los datos'. Log: 13× 429 en /core/activos/search (límite 30/min por IP, todos los clientes llegan como 172.20.0.1). En la 2.ª pasada cargó bien",
     "salidas/qa1/ui/07_activos.png; docker logs inv_ui-backend-1: 'ratelimit 30 per 1 minute (172.20.0.1) exceeded'", "media", "DEFECTO"),
    ("UI-20", "Marca duplicada desde la UI", "Nueva marca con el nombre de una existente", "Mensaje traducido", "'Ya existe una marca con ese nombre'", "salidas/qa1/ui/20_marca_duplicada.png", "-", "OK"),
    ("UI-21", "Marca duplicada en MAYÚSCULAS desde la UI", "Nueva marca 'QA1-… MARCA EDITADA'", "Rechazo por duplicado",
     "Se creó (MAR_Marca=10, comprobado por API); el aviso capturado era el de UI-20", "salidas/qa1/ui/21_marca_mayusculas.png; GET /cat/marcas", "media", "DEFECTO"),
    ("UI-22", "Borrar un país con estados desde Ubicaciones", "Eliminar → confirmar", "Mensaje claro: tiene estados",
     "'No se pudo guardar el registro' (genérico y además dice 'guardar' en un borrado)", "salidas/qa1/ui/22_borrar_pais_con_hijos.png", "media", "DEFECTO"),
    ("UI-23", "Diálogo de edición de activo", "Editar activo QA1", "Título 'Editar activo', costo con selector de moneda",
     "Título correcto; costo 'Q 12.35' con selector GTQ", "salidas/qa1/ui/23_editar_activo.png", "-", "OK"),
    ("UI-24", "Editar la fecha de compra desde el formulario", "Cambiar a 01/06/2026, Guardar, reabrir", "Fecha guardada",
     "Aviso 'Registro actualizado correctamente', pero al reabrir sigue 29/08/2026", "salidas/qa1/ui/24_fecha_compra_no_guardada.png", "alta", "DEFECTO"),
    ("UI-25", "Moneda en el listado de órdenes", "Abrir /compras/ordenes", "Cada orden con su moneda", "Se ven USD, CHF y Q (GTQ)", "salidas/qa1/ui/25_ordenes_monedas.png", "-", "OK"),
    ("UI-26", "Pantallas para estados operativos, tipos de especificación, tipos de conexión y cargos", "Revisar el menú y /config",
     "Existe CRUD en la UI", "No existen: esos catálogos solo se gestionan por API (catalogService.ts tiene las funciones, ninguna pantalla las usa)",
     "salidas/qa1/ui/26_config.png", "media", "OBSERVACIÓN"),
]


def esc(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


filas, cont = [], {"OK": 0, "DEFECTO": 0, "OBSERVACIÓN": 0}
sev_cont = {}
for c in API["casos"]:
    estado, sev, nota = c["estado"], c["severidad"], ""
    if c["id"] in AJUSTES:
        estado, sev, nota = AJUSTES[c["id"]]
    if sev == "obs":
        sev = "baja"
    obtenido = c["obtenido"] + (f". {nota}" if nota else "")
    evid = f"salidas/qa1/{c['id']}.json — {c['evidencia'][:230]}"
    filas.append((c["id"], c["caso"], c["pasos"], c["esperado"], obtenido, evid, sev if estado != "OK" else "-", estado))
for u in UI:
    filas.append(u)
for f in filas:
    cont[f[7]] = cont.get(f[7], 0) + 1
    if f[7] != "OK":
        sev_cont[f[6]] = sev_cont.get(f[6], 0) + 1

md = [f"# QA1 — Informe de pruebas CRUD (InventarioTI)\n",
      f"Fecha: 2026-09-28 · Entorno: stack de producción `inv_ui` (https://localhost:8443) · Usuario: `sa` (SUPER_ADMIN, MFA) · Datos de prueba con prefijo `{API['prefijo']}`\n",
      "Scripts: `herramientas-qa/qa1_crud.py` (API), `qa1_ui.py` (navegador), `qa1_sesion.py` (sesión de sa reutilizada con refresh), `qa1_informe.py` (este informe). "
      "Evidencias: `herramientas-qa/salidas/qa1/<ID>.json` (petición, código y cuerpo de cada caso) y `salidas/qa1/ui/*.png`.\n",
      "## Resumen\n",
      f"- Casos ejecutados: **{len(filas)}** ({len(API['casos'])} por API + {len(UI)} en navegador)",
      f"- OK: **{cont['OK']}** · Defectos: **{cont['DEFECTO']}** · Observaciones: **{cont['OBSERVACIÓN']}**",
      "- Defectos + observaciones por severidad: " + ", ".join(f"{k}: {v}" for k, v in sorted(sev_cont.items(), key=lambda x: ["bloqueante", "alta", "media", "baja"].index(x[0]) if x[0] in ["bloqueante", "alta", "media", "baja"] else 9)),
      "- La columna «UI(es)» de la evidencia indica el texto que mostraría la interfaz en español para el código de error (clave `api_errors.<CÓDIGO>` de `es.json`), o si no tiene traducción y cae al mensaje genérico.\n",
      "### Notas de ejecución\n",
      "- La primera ejecución de `qa1_crud.py` (prefijo `QA1-134239`) se cortó en ACT-22 por el 500 de ACT-18; la ejecución completa y válida es `QA1-134335`. Quedan datos de ambas con prefijo QA1-.",
      "- **Observación de entorno:** durante la sesión, el login de `sa` devolvió 429 unos 6 minutos seguidos (cuota compartida entre agentes) y la cuenta llegó a bloquearse por códigos TOTP repetidos entre procesos. Tras la corrección de `qa_login.py`, QA1 hace un único login por API (token y refresh guardados en `salidas/qa1/.sesion_sa.json`, renovados con `/login/refresh`) y un login por cada ejecución en navegador, sin reintentos de 2FA.",
      "- No se borraron ni alteraron datos ajenos. Los borrados «en uso» se probaron solo contra entidades QA1.\n",
      "## Casos\n",
      "| ID | Caso | Pasos | Resultado esperado | Resultado obtenido | Evidencia | Severidad | Estado |",
      "|---|---|---|---|---|---|---|---|"]
for f in filas:
    md.append("| " + " | ".join(esc(x) for x in f) + " |")

md.append("""
## Defectos ordenados por severidad

Rutas relativas a `inventarioTI-backend/app/` salvo que se indique `frontend/` (= `inventarioTI-frontend/src/`).

### Alta

1. **El detalle de activo por id responde 500 para todos los activos** (ACT-18). `GET /core/activos/{id}` falla también con activos del inventario real.
   - *Causa:* `services/core.py:167-168`: `get_activo_detail` usa `get_or_404` → `BaseRepository.get_by_pk` (`repositories/base.py:59`), que no carga `modelo`, `tipo_activo` ni `estado_operativo`. Al serializar `ActivoDetailResponse` se produce una carga perezosa en contexto async (MissingGreenlet → `ResponseValidationError`). `by-code` sí funciona porque usa `repo.get_by_id` con `selectinload`.
   - *Corrección:* usar `self.repo.get_by_id(activo_id)` (con los `selectinload` de `repositories/core.py:112-118`) y 404 si no existe. Añadir un test del endpoint. `frontend/features/assets/assetService.ts:103` (`getActivo`) no se usa hoy, pero cualquier integración que lo llame recibe 500.
2. **Al editar un activo, las fechas de compra y de garantía se pierden sin avisar** (ACT-22, UI-24). El formulario envía `ACT_Fecha_Compra` y `ACT_Fin_Garantia`, la API responde 200 y la UI muestra «Registro actualizado correctamente», pero las fechas no cambian.
   - *Causa:* `schemas/core.py:95` (`ActivoUpdate`) no declara `ACT_Fecha_Compra` ni `ACT_Fin_Garantia`, y Pydantic descarta los campos extra en silencio.
   - *Corrección:* añadir ambos campos a `ActivoUpdate`. En `update_activo`, validar garantía ≥ compra usando los valores finales (nuevos o actuales). Plantearse `extra="forbid"` en los schemas de edición.
3. **Una orden RECIBIDA se puede reabrir y recibir otra vez, y el stock se duplica** (COM-27, COM-28). `PATCH /compras/ordenes/{id}/estado` acepta RECIBIDA → BORRADOR; después, `/recibir` vuelve a sumar el stock (3 → 6). También acepta BORRADOR → RECIBIDA sin pasar por la recepción.
   - *Causa:* `services/procurement.py:151-159` (`cambiar_estado`) solo rechaza el origen CANCELADA. No hay máquina de estados.
   - *Corrección:* permitir solo BORRADOR → CANCELADA desde este endpoint. RECIBIDA solo debe alcanzarse vía `/recibir` y ser un estado final. Si hace falta una recepción parcial, llevar la cantidad recibida por línea.
4. **Correo corporativo duplicado si solo cambian las mayúsculas** (ORG-11). Se crean dos personas con el mismo buzón (`qa1.jefe…` y `QA1.JEFE…`). Esto rompe la sincronización con AD, la sugerencia de usuario y los avisos por correo.
   - *Causa:* `schemas/common.py:50` pasa a minúsculas solo el dominio. `per_repo.get_by_email` y el índice único comparan sensible a mayúsculas.
   - *Corrección:* normalizar el correo completo con `lower()` (o comparar con `func.lower` y un índice único sobre `lower(PER_Email_Corporativo)`), con una migración que detecte los duplicados existentes.

### Media

5. **Códigos de error sin traducción en la UI.** El usuario ve «No se pudo guardar el registro» u otro texto genérico (GEO-16…21, UI-22, SOF-16, SOF-19, más CAT-27/28/37, ESP-05 y ORG-20, que hoy solo se ven por API). Faltan en `frontend/locales/{es,en,it}.json` → `api_errors`: `CANNOT_DELETE_COUNTRY_HAS_STATES`, `CANNOT_DELETE_STATE_HAS_MUNICIPALITIES`, `CANNOT_DELETE_MUNICIPALITY_HAS_SITES`, `CANNOT_DELETE_SITE_HAS_BUILDINGS`, `CANNOT_DELETE_BUILDING_HAS_LEVELS`, `CANNOT_DELETE_LEVEL_HAS_AREAS`, `CANNOT_DELETE_POSITION_HAS_PERSONS`, `CANNOT_DELETE_SPECIFICATION_TYPE_IN_USE`, `CANNOT_DELETE_OPERATIONAL_STATUS_IN_USE`, `CANNOT_DELETE_CONNECTION_TYPE_IN_USE`, `CANNOT_DELETE_SYSTEM_OPERATIONAL_STATUS`, `CANNOT_MODIFY_SYSTEM_OPERATIONAL_STATUS`, `CANNOT_REDUCE_BELOW_USED`, `CANNOT_DELETE_LICENSE_WITH_ACTIVE_INSTALLATIONS`, `LICENSE_QUANTITY_REQUIRED`, `LINE_NOT_IN_ORDER`, `INSTALLATION_TARGET_XOR_REQUIRED` y todos los `*_NOT_FOUND` (`MODEL_NOT_FOUND`, `BRAND_NOT_FOUND`, `COUNTRY_NOT_FOUND`, `SUPPLIER_NOT_FOUND`, `LICENSE_NOT_FOUND`…). Además, `frontend/features/locations/LocationsPage.tsx:200` usa `common.error_save` como texto de reserva también al **borrar**.
   - *Corrección:* añadir las claves en los 3 idiomas con `i18n_set.py`, con plural o parámetro en los códigos `:N`, como ya se hace con `PASSWORD_REUSED` en `frontend/api/errors.ts`. Usar un texto de reserva de borrado en `handleDelete`. Añadir a `verify.py` una comprobación de que cada `detail` que emite el backend tiene su clave.
6. **Validación de duplicados incompleta: sin chequeo, o con el mensaje genérico de integridad.**
   - *Duplicados aceptados:* modelo repetido en la misma marca (CAT-19); marca, serie y consumible que solo difieren en mayúsculas o espacios (CAT-04, UI-21, ACT-11, ACT-12, CON-03).
   - *Rechazados por la BD, pero con «La operación no puede completarse porque afectaría la integridad de los datos»:* tipos de conexión, estados, tipos de especificación, países (nombre e ISO), cargos, software, tipos de licencia y prefijo de tipo de activo (CAT-12/15/26/31, GEO-02/03, ORG-07, SOF-02/04); ediciones de marca, departamento, persona (correo), consumible y proveedor (CAT-09, ORG-03, ORG-17, CON-07, COM-05); especificación repetida en un activo (ESP-02).
   - *Causa:* `services/catalogs.py` (`create_tipo_conexion`, `create_modelo`, `create_estado_operativo`, `create_tipo_especificacion`), `services/location.py`, `services/organization.py:263` (cargo), `services/software.py:44/87` y todos los `update_*` (`catalogs.py:117`, `organization.py:230/331`, `consumable.py:70`, `procurement.py:59`) no consultan si el valor ya existe. Los que sí lo hacen comparan de forma sensible a mayúsculas y sin recortar espacios.
   - *Corrección:* un helper común de unicidad (recortar espacios, comparar con `lower()`, excluir el propio id en la edición) que devuelva `*_ALREADY_EXISTS` traducido, e índices únicos funcionales `lower(trim(x))`. Para `ACT_Serie_Fabricante`, normalizar con `strip()`.
7. **Nombres formados solo por espacios** (CAT-06). `min_length` cuenta los espacios, así que una marca `'     '` es válida. Afecta a todos los schemas con `str`.
   - *Corrección:* `model_config = ConfigDict(str_strip_whitespace=True)` en una base común de schemas.
8. **Errores 500 por cotas que faltan en los schemas de entrada** (ACT-23, ACT-24, COM-15, GEO-13).
   - `schemas/core.py:99`: `ActivoUpdate.ACT_Costo` no tiene `max_digits=12, decimal_places=2`. Un monto grande da 500 DATABASE_ERROR. Con 3 decimales, la BD redondea y **guarda**, pero la respuesta falla (500 en texto plano) y el usuario cree que no se guardó.
   - `schemas/procurement.py:50`: `OCL_Precio_Unitario` no tiene `max_digits`/`decimal_places` (columna `Numeric(14,2)`, `models/procurement.py:106`).
   - `schemas/location.py:118/127`: `ARE_Tipo_Acceso` no tiene `max_length` (columna `varchar(50)`) ni lista de valores.
   - *Corrección:* copiar las cotas del alta a la edición. Además, `main.py` debería devolver un JSON `{"detail":"INTERNAL_ERROR"}` también para `ResponseValidationError`/`Exception`, no texto plano.
9. **Borrar un tipo de conexión en uso lo borra igualmente** (CAT-36). Responde 204 y los modelos quedan con `TCN_Tipo_Conexion = NULL`.
   - *Causa:* `services/catalogs.py:176-190` espera un `IntegrityError`, pero la FK es `ondelete="SET NULL"` (`models/catalogs.py:62`), así que el mensaje `CANNOT_DELETE_CONNECTION_TYPE_IN_USE` nunca se emite.
   - *Corrección:* contar los modelos que lo usan antes de borrar, como ya se hace con marca y modelo.
10. **Un activo puede quedarse sin sede** (ACT-28). Un `PATCH` con `SED_Sede=null` la borra, y `frontend/features/assets/AssetDialog.tsx:182` envía `null` cuando el campo queda vacío. El activo deja de verse para las cuentas con alcance por sede.
    - *Causa:* `services/core.py:129-135`: `require_sede(None, required=False)` devuelve `None` para cuentas globales.
    - *Corrección:* en la edición, rechazar `SED_Sede=null` (o no enviarlo si no cambió) y hacer el campo obligatorio en el formulario.
11. **La pantalla Activos queda vacía, sin explicación, cuando salta el límite de peticiones** (UI-07). `POST /core/activos/search` tiene `@limiter.limit("30/minute")` (`api/v1/endpoints/core.py:148`) por IP, y detrás del proxy todos los clientes comparten la clave (172.20.0.1). La UI muestra «0 activos» y «No se pudieron cargar los datos» (`frontend/features/assets/AssetsPage.tsx:111`) en lugar de `RATE_LIMIT_EXCEEDED`.
    - *Corrección:* subir el límite para lecturas paginadas o aplicarlo por usuario. Verificar que la clave use la IP real (`X-Forwarded-For` de confianza desde Caddy/nginx). Mostrar el mensaje del 429 con `getApiErrorMessage` y no poner el contador a 0.
12. **Sin pantallas para varios catálogos** (UI-26, observación). Estados operativos, tipos de especificación, tipos de conexión y cargos solo se gestionan por API. `catalogService.ts` tiene las funciones, pero ninguna pantalla las usa. Añadir pantallas en Configuración con el patrón `PageHeader + DataTableShell`.

### Baja

13. **Referencias inexistentes: 409 INTEGRITY genérico en lugar de 404** (CAT-20, GEO-06, ACT-25). Afecta a `create_modelo` (marca), `create_estado` (país) y `update_activo` (tipo de activo): no validan la FK antes de insertar. Validarla como en `create_activo`.
14. **Formatos libres:**
    - prefijo de tipo de activo `'a b-%'` (CAT-16): acotar a `^[A-Z0-9]{1,10}$`;
    - ISO `'1$'` (GEO-04): `^[A-Z]{2,3}$`;
    - teléfono `'abc-xyz'` (ORG-13): patrón `^[0-9 +()-]{6,20}$`.
15. **Reglas de negocio que faltan:**
    - ciclo de jefatura A↔B (ORG-16): recorrer la cadena de `PER_Jefe` en `services/organization.py:331`;
    - persona asignada a un departamento inactivo (ORG-18);
    - salidas de stock de un consumible desactivado (CON-12): comprobar `CON_Activo` en `services/consumable.py:114`;
    - orden sin líneas (COM-16) y orden a un proveedor inactivo (COM-17): `services/procurement.py:96`;
    - vencimiento de licencia en el pasado al editar (SOF-18): `services/software.py:333` no repite la validación del alta.
16. **Observaciones:**
    - la cantidad total de una licencia con claves individuales se puede subir sin claves nuevas, y no existe endpoint para añadir claves a una licencia existente (SOF-17);
    - se aceptan fechas de compra o de orden muy futuras (ACT-09, COM-18);
    - NIT de proveedor repetido (COM-04);
    - `LICENSE_KEY_COUNT_MISMATCH` llega dentro de `errors[].msg` de un 422 y la UI solo ve `VALIDATION_ERROR` (SOF-08);
    - rango de fechas invertido en la búsqueda devuelve una lista vacía sin aviso (ACT-35).

## Lo que funciona bien (resumen)

- **CRUD básico:** alta, lectura, edición y baja en todas las entidades.
- **Validaciones de schema:** longitudes, vacíos, negativos, año de modelo, 3 decimales y desbordamiento al **crear** activos.
- **Duplicados exactos:** marca, tipo de activo, departamento, correo, serie, código, consumible, proveedor, número de orden y clave de licencia, con mensajes traducidos.
- **Borrados en uso bloqueados:** marca, modelo, tipo de activo, departamento, software, tipo de licencia, consumible con movimientos, proveedor con órdenes, estados del sistema.
- **Moneda:**
  - GTQ/USD/CHF al crear y editar, normalización de minúsculas y rechazo de EUR;
  - el activo recibido hereda la moneda de la orden (USD, COM-23);
  - la UI muestra el costo con su moneda (UI-23) y el listado de órdenes la moneda de cada una (UI-25).
- **Licencias:** claves individuales (creación, visualización auditada, duplicados en la petición y globales, conteo coherente), instalación que consume claves, bloqueo al reducir por debajo del uso.
- **Búsqueda de activos:** paginación, filtros combinados y escape de comodines SQL.
- **Personas:** jefe inexistente o propio rechazado; contexto de persona con jefe sugerido.
- **Título del diálogo de activo:** ya dice «Editar activo» / «Nuevo activo» (pendiente de CONTINUAR.md resuelto).
""")

out = os.path.join(AQUI, "salidas", "qa1_informe.md")
open(out, "w", encoding="utf-8").write("\n".join(md))
print(out, len(filas), cont, sev_cont)
