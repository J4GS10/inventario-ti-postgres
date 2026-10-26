# Matriz de control de acceso por rol (RBAC)

> Generado y verificado el 2026-06-05. Fuente de verdad: `app/api/deps.py`
> (`RoleChecker`) y las dependencias declaradas en cada router de
> `app/api/v1/endpoints/`. El router base aplica `Depends(get_current_user)`
> a todos los módulos de negocio (`org`, `geo`, `cat`, `core`, `trazabilidad`,
> `soft`, `mantenimiento`, `consumibles`, `adjuntos`, `compras`, `stats`,
> `export`); `login` y `gov` tienen reglas propias.

## Roles

> Actualizado el 2026-10-02 (rol JEFE_TI y aprobaciones); antes, 2026-09-25 (rol AUDITOR y alcance por sede). Fuente de verdad:
> `app/core/roles.py` y `app/api/deps.py`.

| Rol | Atajos que lo incluyen | Capacidad | Alcance de datos |
|---|---|---|---|
| `SUPER_ADMIN` | todos | Total: configuración, cualquier cuenta, alcance global | Siempre global |
| `ADMIN_SEGURIDAD` | `require_iam`, `require_audit_reader` | Identidades y accesos, bitácora. **Sin inventario** | Siempre global |
| `JEFE_TI` | `require_admin`, `require_operativo`, `require_export`, `require_aprobador` | Lo mismo que ADMIN_TI y, además, **aprueba bajas y sobrantes** solicitados por otra persona; verifica la integridad del kardex | Sus sedes o global |
| `ADMIN_TI` | `require_admin`, `require_operativo`, `require_export` | Inventario completo, catálogos, personas, compras | Sus sedes o global |
| `TECNICO` | `require_operativo` | Operación de campo: activos, asignaciones, mantenimientos | Sus sedes o global |
| `AUDITOR` | `require_business`, `require_audit_reader`, `require_export` | **Solo lectura** del inventario, bitácora y exportaciones | Sus sedes o global |
| `CONSULTA` | `require_inventory` | **Solo lectura** del inventario, sin costos, compras, licencias, aprobaciones ni datos personales (salvo el nombre del custodio) | Sus sedes o global |
| `COMPRAS` | `require_purchase`, `require_purchase_read` | Administración: proveedores, órdenes y factura. No recibe en bodega ni ve inventario, personas o licencias | Sus sedes o global |
| `CONTABILIDAD` | `require_stock_read`, `require_software`, `require_purchase_read`, `require_finance_export` | Administración: lectura de valores y exportes, sin datos personales ni escritura | Sus sedes o global |

Atajos (`app/api/deps.py`):
- `require_super_admin = ["SUPER_ADMIN"]`
- `require_iam = ["SUPER_ADMIN", "ADMIN_SEGURIDAD"]`
- `require_business = ["SUPER_ADMIN", "JEFE_TI", "ADMIN_TI", "TECNICO", "AUDITOR", "CONSULTA"]` (routers de inventario)
- `require_admin = ["SUPER_ADMIN", "JEFE_TI", "ADMIN_TI"]`
- `require_operativo = ["SUPER_ADMIN", "JEFE_TI", "ADMIN_TI", "TECNICO"]`
- `require_aprobador = ["SUPER_ADMIN", "JEFE_TI"]` (aprobar o rechazar; nunca lo propio)
- `require_audit_reader = ["SUPER_ADMIN", "ADMIN_SEGURIDAD", "AUDITOR"]`
- `require_export = ["SUPER_ADMIN", "JEFE_TI", "ADMIN_TI", "AUDITOR"]`

Reglas de gobierno (`app/core/roles.py`): nadie administra su propia cuenta;
`ADMIN_SEGURIDAD` no gestiona ni otorga `SUPER_ADMIN`/`ADMIN_SEGURIDAD`; **solo un
`SUPER_ADMIN` otorga el alcance global**; una cuenta de inventario debe tener al menos
una sede o alcance global.

## Matriz por módulo (2026-10-08)

> Actualizado el 2026-10-08: roles de Administración `COMPRAS` y `CONTABILIDAD`,
> permiso por módulo en el router (un rol sin el módulo recibe **403** y no ve ni
> indicios de sus registros) y costos ocultos a `TECNICO` y `CONSULTA`. Grupos en
> `app/core/roles.py`; verificado por `tests/test_matriz_roles.py`.

| Módulo (API) | SA / JEFE / ADMIN | TECNICO | AUDITOR | CONSULTA | COMPRAS | CONTABILIDAD | ADMIN_SEG |
|---|---|---|---|---|---|---|---|
| Activos y consumibles (`/core`, `/consumibles`) | total | opera | lee | lee | catálogo de consumibles | lee (con valores) | — |
| Movimientos, trazabilidad, mantenimiento, tomas, kardex | total | opera | lee | lee (custodio por nombre) | — | — | — |
| Personas, departamentos, fichas, asignaciones por persona | total | lee | lee | — | — | — | lee (identidades) |
| Software y licencias (`/soft`) | total | lee / instala | lee | — | — | lee (valores) | — |
| Credenciales de licencias | revelar auditado | — | — | — | — | — | — |
| Compras: proveedores, órdenes, factura | total | — | lee | — | crea y edita | lee | — |
| Recibir en bodega, cerrar con faltante, borrar proveedor | total | — | — | — | — | — | — |
| Aprobaciones | aprueba (JEFE) | — | lee | — | — | — | — |
| Tablero (`/stats`) | total | sin costos | total | sin costos ni licencias | — | total (con valores) | — |
| Bandeja de atención | todo | lo suyo | lo suyo | nada | órdenes, garantías, stock bajo | datos incompletos | — |
| Exportes de valores (activos, consumibles, licencias) | sí | — | sí | — | — | sí | — |
| Exportes de compras (proveedores, órdenes) | sí | — | sí | — | sí | sí | — |
| Exporte de movimientos / kardex / custodia | sí | — | sí | — | — | — | — |

Atajos nuevos (`app/api/deps.py`): `require_app`, `require_inventory`,
`require_stock_read`, `require_people`, `require_people_or_iam`, `require_software`,
`require_purchase`, `require_purchase_read`, `require_control_read`,
`require_finance_export`, `require_purchase_export`, `require_stock_catalog`.

**Costos**: el middleware `app/core/ocultar_costos.py` quita de toda respuesta JSON
las llaves con dinero (`ACT_Costo`, `CON_Costo_Unitario`, `LIC_Costo`, `KDX_Costo`,
`KDX_Saldo_Valor`, `OCL_Precio_Unitario`, `RCL_Precio_Real`, valoraciones por moneda)
para `TECNICO` y `CONSULTA`; la hoja de vida PDF omite el costo y, si envían un costo
al editar un activo o registrar una entrada, se ignora (`descartar_costos`). El costo
de un mantenimiento sigue visible (lo registra el técnico).

**Adjuntos**: heredan el permiso de su dueño (activo → inventario; orden → compras;
licencia → administradores). Descargar uno ajeno responde 404.

## Separación de funciones (aprobaciones)

| Operación | Solicita | Aprueba | Regla |
|---|---|---|---|
| Baja de activo (`POST /core/activos/{id}/baja` → 202) | `require_admin` | `require_aprobador` (`POST /aprobaciones/{id}/aprobar`) | Otra persona; el equipo queda congelado mientras está pendiente |
| Sobrante en una recepción de compra | `require_admin` | `require_aprobador` | Otra persona; el inventario no cambia hasta aprobarse |
| Ajuste de consumible que **baja** la existencia (`POST /consumibles/{id}/ajuste` → 202) | `require_admin` | `require_aprobador` | Si la existencia cambió desde la solicitud, no se aplica (`STOCK_CHANGED_SINCE_REQUEST`); los aumentos se aplican directo |
| Cerrar una orden con faltante (`POST /compras/ordenes/{id}/cerrar` → 202) | `require_admin` | `require_aprobador` | — |
| Cancelar una orden (`PATCH /compras/ordenes/{id}/estado` → 202) | `require_purchase` | `require_aprobador` | Con motivo |
| Reclasificar insumos a consumible (`POST /core/tipos-activo/{id}/reclasificar` → 202) | `require_admin` | `require_aprobador` | Por sede; los activos quedan de baja «Reclasificado» |
| Baja por pérdida o robo de un faltante (`POST /inventario/faltantes/{id}/baja` → 202) | `require_admin` | `require_aprobador` | Al aprobarse, el faltante queda «Dado de baja»; si se rechaza, vuelve a investigación |
| Anular una solicitud pendiente | quien la pidió | — | — |
| Verificar el kardex (`GET /inventario/kardex/verificar`) | `SUPER_ADMIN`, `JEFE_TI`, `AUDITOR` | — | Solo lectura |

`DELETE /core/activos/{id}` ya no da de baja (409 `USE_DECOMMISSION_REQUEST`).
La base también impide que el aprobador sea el solicitante (`ck_aprobacion_otra_persona`).

## Alcance de datos por sede (RLS)

El rol decide **qué operaciones** puede hacer una cuenta; el alcance decide **sobre
qué filas**. Se aplica en dos capas (ver `app/core/data_scope.py` y `app/db/rls.py`):

| Tabla | Visible si… | Escritura |
|---|---|---|
| `INV_ACTIVO`, `INV_ORDEN_COMPRA`, `INV_CONSUMIBLE` | su sede está en el alcance | solo en sedes del alcance |
| `INV_PERSONA` | su sede está en el alcance, o está vinculada a un registro visible (custodia, mantenimiento, instalación, consumo), o es la propia | solo personas de sedes del alcance |
| Movimientos, mantenimientos, especificaciones, instalaciones, adjuntos, evidencias, líneas de compra, movimientos de consumibles | su activo / orden / consumible es visible | ídem; una asignación nueva exige un área de una sede del alcance |
| `INV_AUDITORIA_SISTEMA` | evento de una sede del alcance, o acción propia | solo inserción (nunca UPDATE/DELETE desde peticiones) |
| Catálogos (incluidos los del Centro de catálogos), geografía, departamentos, cargos, software y licencias | siempre (vocabulario compartido) | país/estado/municipio/sede solo con alcance global |
| Archivos de licencia (`INV_ADJUNTO` con `LIC_Licencia`) | siempre (la licencia no tiene sede); la descarga exige rol admin | ídem |

## Invariante verificado

**Cero endpoints mutadores (POST/PUT/PATCH/DELETE) sin guard de rol**, fuera del
autoservicio (`/me/password`, `/login/logout`, `/me/2fa/*`, reset de contraseña),
donde la identidad del propio usuario en el JWT es el control de acceso.
Verificación automatizada incluida en `scripts/check_rbac.py` (escanea los
decoradores y falla si algún mutador carece de `require_*`).

> Nota: `POST /core/activos/search` usa POST por llevar cuerpo de filtros, pero
> es de **lectura**; por diseño solo requiere autenticación.

## Matriz por módulo (operaciones de escritura)

| Módulo (prefijo) | Crear/Editar/Borrar catálogos y maestros | Operación de negocio | Borrado/decomiso | Solo SUPER_ADMIN |
|---|---|---|---|---|
| Catálogos (`/cat`) | `require_admin` | — | `require_admin` | — |
| Centro de catálogos (`/catalogos`) | `require_admin` (crear, editar, desactivar, reordenar; especificaciones por tipo; kits) | — | — (se desactiva, no se borra) | Fusionar: `require_admin` **y alcance global** (`CATALOG_MERGE_REQUIRES_GLOBAL_SCOPE`) |
| Ubicación (`/geo`) | `require_admin` | — | `require_admin` | — |
| Organización (`/org`) | `require_admin` (deptos, cargos, personas, usuarios) | — | `require_admin` | — |
| Core inventario (`/core`) | `require_operativo` (activos, especificaciones) | `require_operativo`; **`require_admin`** para la edición en lote (`PATCH /activos/lote`) y su «deshacer» (`POST /activos/lote/restaurar`) | **`require_admin`** (baja lógica de activo) | — |
| Trazabilidad (`/trazabilidad`) | `require_admin` (tipos) | `require_operativo` (movimiento, devolución, transferencia, actas) | **`require_admin`** (offboarding) | — |
| Software (`/soft`) | `require_admin` (software, licencias, tipos, credenciales: alta, edición, rotación) | `require_operativo` (instalar/desinstalar) | `require_admin` | — |
| Credenciales de licencias (`/soft/credenciales/{id}/revelar`) | — | **`require_admin`** (SUPER_ADMIN, JEFE_TI, ADMIN_TI), límite 10/min, auditado `LICENSE_SECRET_VIEWED` + kardex `LICENCIA_SECRETO_VISTO` | — | — |
| Mantenimiento (`/mantenimiento`) | `require_admin` (tipos) | `require_operativo` (registrar, cerrar, detalles) | `require_admin` | — |
| Consumibles (`/consumibles`) | `require_admin` (alta/edición) | `require_operativo` (entrada/salida de stock) | `require_admin` | — |
| Adjuntos (`/adjuntos`) | `require_operativo` (subir a activo) / `require_admin` (subir a orden o a licencia) | — | `require_admin` | — |
| Compras (`/compras`) | `require_admin` (proveedores, órdenes) | `require_admin` (recibir, cambiar estado, notificar garantías) | `require_admin` | — |
| Exportación (`/export`) | — (solo GET) | `require_admin` (CSVs) | — | **`require_super_admin`** (`auditoria.csv`) |
| Gobierno (`/gov`) | — | — | — | **`require_super_admin`** (config, auditoría, purga) |

## Lecturas accesibles a `CONSULTA` (cualquier autenticado)

Los siguientes GET no exigen `require_*`, por lo que un usuario `CONSULTA`
(solo lectura) puede consultarlos. **Es una decisión de política de negocio**,
no un defecto: define qué información puede ver el rol de solo-consulta.

| Endpoint | Dato expuesto | ¿Restringir a `require_operativo`? |
|---|---|---|
| `GET /org/personas`, `/org/personas/disponibles` | PII (nombres, emails, teléfonos) | Decisión de negocio |
| `GET /compras/proveedores`, `/compras/ordenes` | Datos comerciales (montos, proveedores) | Decisión de negocio |
| `GET /trazabilidad/movimientos`, `/trazabilidad/persona/{id}/asignaciones` | Historial de asignaciones por persona | Decisión de negocio |
| `GET /consumibles/{id}/movimientos` | Historial de stock | Decisión de negocio |
| `GET /adjuntos/{id}/download` | Descarga de adjuntos (facturas, actas). Los **archivos de licencia** solo los descargan SUPER_ADMIN, JEFE_TI y ADMIN_TI | Decisión de negocio |
| `GET /productividad/personas/{id}/ficha` | Ficha 360 (equipos, licencias, consumibles, historial) dentro del alcance | Decisión de negocio |

> Maestros y catálogos (`/cat/*`, `/geo/*`, departamentos, cargos) son
> vocabularios controlados y se consideran lectura abierta a cualquier
> autenticado por diseño.
