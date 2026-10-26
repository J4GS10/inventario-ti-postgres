from fastapi import APIRouter, Depends
from app.api.v1.endpoints import (
    login,
    organization,
    location,
    catalogs,
    catalogos,
    core,
    traceability,
    software,
    governance,
    stats,
    maintenance,
    export,
    consumable,
    attachment,
    procurement,
    twofactor,
    directory,
    inventario,
    aprobaciones,
    productividad,
)
from app.api import deps

api_router = APIRouter()

# 1. Auth (Público)
api_router.include_router(login.router, tags=["Autenticación"])
# 1b. 2FA del usuario autenticado (/me/2fa/*) — JWT requerido en cada endpoint.
api_router.include_router(twofactor.router, tags=["2FA"])

# 2. Gobierno (GET público para cargar tema visual antes del login)
api_router.include_router(governance.router, prefix="/gov", tags=["Gobierno"])

# 3. Módulos de Negocio (Requieren autenticación JWT)
_auth = [Depends(deps.get_current_user)]
# Cada módulo exige su grupo de roles (app/core/roles.py) en el router, no solo
# ocultando menús: un rol sin el módulo recibe 403 y no ve ni indicios de sus
# registros. El administrador de seguridad no accede al inventario (separación
# de funciones). Los endpoints afinan más (escritura, personas, exportes).
_app = [Depends(deps.require_app)]
_inventory = [Depends(deps.require_inventory)]
_stock = [Depends(deps.require_stock_read)]

api_router.include_router(
    organization.router, prefix="/org", tags=["Organización"],
    dependencies=_auth
)
api_router.include_router(
    location.router, prefix="/geo", tags=["Ubicación Geográfica"],
    dependencies=_app
)
api_router.include_router(
    catalogs.router, prefix="/cat", tags=["Catálogos Técnicos"],
    dependencies=_app
)
api_router.include_router(
    catalogos.router, prefix="/catalogos", tags=["Centro de catálogos"],
    dependencies=_app
)
api_router.include_router(
    core.router, prefix="/core", tags=["Core Inventario"],
    dependencies=_stock
)
api_router.include_router(
    traceability.router, prefix="/trazabilidad", tags=["Trazabilidad"],
    dependencies=_inventory
)
api_router.include_router(
    software.router, prefix="/soft", tags=["Software y Licencias"],
    dependencies=[Depends(deps.require_software)]
)
api_router.include_router(
    maintenance.router, prefix="/mantenimiento", tags=["Mantenimiento y Soporte"],
    dependencies=_inventory
)
api_router.include_router(
    consumable.router, prefix="/consumibles", tags=["Consumibles"],
    dependencies=_app
)
api_router.include_router(
    attachment.router, prefix="/adjuntos", tags=["Adjuntos"],
    dependencies=_app
)
api_router.include_router(
    procurement.router, prefix="/compras", tags=["Compras y Garantías"],
    dependencies=[Depends(deps.require_purchase_read)]
)
api_router.include_router(
    inventario.router, prefix="/inventario", tags=["Trazabilidad e inventario"],
    dependencies=_app
)
api_router.include_router(
    aprobaciones.router, prefix="/aprobaciones", tags=["Aprobaciones del Jefe de TI"],
    dependencies=[Depends(deps.require_control_read)]
)

# 4. Dashboard (Requiere autenticación)
api_router.include_router(
    stats.router, prefix="/stats", tags=["Dashboard y Métricas"],
    dependencies=_stock
)

# Productividad: búsqueda global, kit sugerido, calidad de datos e importación
api_router.include_router(
    productividad.router, prefix="/productividad", tags=["Productividad"],
    dependencies=_app,
)

# Exportes CSV
api_router.include_router(
    export.router, prefix="/export", tags=["Exportación CSV"],
    # Cada exporte exige su rol (valores, compras, auditoría para seguridad).
    dependencies=_auth,
)

# Active Directory + reglas de notificación (roles validados por endpoint)
api_router.include_router(
    directory.router, prefix="/directorio", tags=["Directorio Activo y Notificaciones"],
    dependencies=_auth,
)
