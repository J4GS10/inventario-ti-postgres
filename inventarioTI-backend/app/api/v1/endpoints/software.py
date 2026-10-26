from typing import List
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    get_client_ip,
    require_admin,
    require_operativo,
    require_people,
)
from app.api.idempotency import idempotency_guard, _IdempotencyGuard
from app.core.limiter import limiter
from app.db.session import get_db
from app.schemas.software import (
    CredencialesLote,
    CredencialRotar,
    CredencialUpdate,
    InstalacionCreate,
    InstalacionDetalleResponse,
    InstalacionResponse,
    LicenciaCreate,
    LicenciaClaveResponse,
    LicenciaResponse,
    LicenciaUpdate,
    SoftwareCreate,
    SoftwareResponse,
    SoftwareUpdate,
    TipoLicenciaCreate,
    TipoLicenciaResponse,
    TipoLicenciaUpdate,
)
import uuid as _uuid
from app.services.software import SoftwareService

router = APIRouter()


def get_service(db: AsyncSession = Depends(get_db)) -> SoftwareService:
    return SoftwareService(db)


# --- TIPOS LICENCIA ---
@router.post(
    "/tipos-licencia", response_model=TipoLicenciaResponse, status_code=201,
    dependencies=[Depends(require_admin)],
)
async def create_tipo_licencia(
    schema: TipoLicenciaCreate,
    request: Request,
    current_user: CurrentUser,
    service: SoftwareService = Depends(get_service),
):
    return await service.create_tipo_licencia(
        schema, usuario_id=current_user.USU_Usuario, ip=get_client_ip(request)
    )


@router.get("/tipos-licencia", response_model=List[TipoLicenciaResponse])
async def list_tipos_licencia(service: SoftwareService = Depends(get_service)):
    return await service.list_tipos_licencia()


@router.get("/tipos-licencia/{id}", response_model=TipoLicenciaResponse)
async def get_tipo_licencia(id: int, service: SoftwareService = Depends(get_service)):
    obj = await service.repo.get_tipo_licencia_by_id(id)
    if not obj:
        from fastapi import HTTPException
        raise HTTPException(404, "LICENSE_TYPE_NOT_FOUND")
    return obj


@router.patch(
    "/tipos-licencia/{id}", response_model=TipoLicenciaResponse,
    dependencies=[Depends(require_admin)],
)
async def update_tipo_licencia(
    id: int,
    schema: TipoLicenciaUpdate,
    request: Request,
    current_user: CurrentUser,
    service: SoftwareService = Depends(get_service),
):
    return await service.update_tipo_licencia(
        id, schema, usuario_id=current_user.USU_Usuario, ip=get_client_ip(request)
    )


@router.delete(
    "/tipos-licencia/{id}", status_code=204,
    dependencies=[Depends(require_admin)],
)
async def delete_tipo_licencia(
    id: int,
    request: Request,
    current_user: CurrentUser,
    service: SoftwareService = Depends(get_service),
):
    await service.delete_tipo_licencia(
        id, usuario_id=current_user.USU_Usuario, ip=get_client_ip(request)
    )


# --- SOFTWARE ---
@router.post(
    "/software", response_model=SoftwareResponse, status_code=201,
    dependencies=[Depends(require_admin)],
)
async def create_software(
    schema: SoftwareCreate,
    request: Request,
    current_user: CurrentUser,
    service: SoftwareService = Depends(get_service),
):
    return await service.create_software(
        schema, usuario_id=current_user.USU_Usuario, ip=get_client_ip(request)
    )


@router.get("/software", response_model=List[SoftwareResponse])
async def list_software(service: SoftwareService = Depends(get_service)):
    return await service.list_software()


@router.get("/software/{id}", response_model=SoftwareResponse)
async def get_software(id: int, service: SoftwareService = Depends(get_service)):
    obj = await service.repo.get_software_by_id(id)
    if not obj:
        from fastapi import HTTPException
        raise HTTPException(404, "SOFTWARE_NOT_FOUND")
    return obj


@router.patch(
    "/software/{id}", response_model=SoftwareResponse,
    dependencies=[Depends(require_admin)],
)
async def update_software(
    id: int,
    schema: SoftwareUpdate,
    request: Request,
    current_user: CurrentUser,
    service: SoftwareService = Depends(get_service),
):
    return await service.update_software(
        id, schema, usuario_id=current_user.USU_Usuario, ip=get_client_ip(request)
    )


@router.delete(
    "/software/{id}", status_code=204,
    dependencies=[Depends(require_admin)],
)
async def delete_software(
    id: int,
    request: Request,
    current_user: CurrentUser,
    service: SoftwareService = Depends(get_service),
):
    await service.delete_software(
        id, usuario_id=current_user.USU_Usuario, ip=get_client_ip(request)
    )


# --- LICENCIAS ---
@router.post(
    "/licencias", response_model=LicenciaResponse, status_code=201,
    dependencies=[Depends(require_admin)],
)
async def create_licencia(
    schema: LicenciaCreate,
    request: Request,
    current_user: CurrentUser,
    service: SoftwareService = Depends(get_service),
):
    return await service.create_licencia(
        schema, usuario_id=current_user.USU_Usuario, ip=get_client_ip(request)
    )


@router.get("/licencias", response_model=List[LicenciaResponse])
async def list_licencias(
    software_id: int = Query(...),
    service: SoftwareService = Depends(get_service),
):
    return await service.list_licencias(software_id)


@router.get("/licencias-disponibles")
async def licencias_disponibles(
    forma: str | None = Query(None, pattern="^(EQUIPO|PERSONA|CONCURRENTE|SITIO)$"),
    todas: bool = Query(False, description="Incluir las sin cupo libre o vencidas (para configurar kits)"),
    service: SoftwareService = Depends(get_service),
):
    """Licencias con cupo libre (o por sitio), filtradas por forma de conteo."""
    return await service.licencias_disponibles(forma, todas)


@router.get("/licencias/{id}", response_model=LicenciaResponse)
async def get_licencia(id: int, service: SoftwareService = Depends(get_service)):
    return await service.get_licencia(id)


@router.get("/licencias/{id}/historial", dependencies=[Depends(require_people)])
async def historial_licencia(id: int, service: SoftwareService = Depends(get_service)):
    """Asignaciones de la licencia, vigentes y retiradas (fecha y motivo del retiro)."""
    return await service.historial_licencia(id)


@router.get(
    "/licencias/{id}/claves",
    response_model=List[LicenciaClaveResponse],
    dependencies=[Depends(require_admin)],
)
async def list_claves_licencia(
    id: int,
    request: Request,
    current_user: CurrentUser,
    service: SoftwareService = Depends(get_service),
):
    return await service.list_claves_licencia(
        id,
        usuario_id=current_user.USU_Usuario,
        ip=get_client_ip(request),
    )


@router.patch("/licencias/{id}", response_model=LicenciaResponse, dependencies=[Depends(require_admin)])
async def update_licencia(
    id: int,
    schema: LicenciaUpdate,
    request: Request, current_user: CurrentUser,
    service: SoftwareService = Depends(get_service),
):
    return await service.update_licencia(
        id, schema, usuario_id=current_user.USU_Usuario, ip=get_client_ip(request)
    )


@router.delete("/licencias/{id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_licencia(
    id: int,
    request: Request, current_user: CurrentUser,
    service: SoftwareService = Depends(get_service),
):
    await service.delete_licencia(
        id, usuario_id=current_user.USU_Usuario, ip=get_client_ip(request)
    )


# --- LICENCIAS INSTALADAS EN UN ACTIVO ---
@router.get(
    "/activos/{activo_id}/instalaciones",
    response_model=List[InstalacionDetalleResponse], dependencies=[Depends(require_people)],
)
async def list_instalaciones_activo(
    activo_id: _uuid.UUID,
    solo_activas: bool = True,
    service: SoftwareService = Depends(get_service),
):
    """Lista las licencias instaladas en el activo (por defecto solo activas)."""
    return await service.list_instalaciones_by_activo(activo_id, solo_activas=solo_activas)


@router.get(
    "/personas/{persona_id}/instalaciones",
    response_model=List[InstalacionDetalleResponse], dependencies=[Depends(require_people)],
)
async def list_instalaciones_persona(
    persona_id: _uuid.UUID,
    solo_activas: bool = True,
    service: SoftwareService = Depends(get_service),
):
    """Lista las licencias asignadas directamente a una persona."""
    return await service.list_instalaciones_by_persona(persona_id, solo_activas=solo_activas)


# --- INSTALACIONES ---
@router.post(
    "/instalaciones", response_model=InstalacionResponse, status_code=201,
    dependencies=[Depends(require_operativo)],
)
@limiter.limit("30/minute")
async def registrar_instalacion(
    schema: InstalacionCreate,
    request: Request,
    current_user: CurrentUser,
    service: SoftwareService = Depends(get_service),
    idempotency: _IdempotencyGuard = Depends(idempotency_guard),
):
    """
    Asigna una licencia a un activo. Reserva el cupo atómicamente.
    Soporta cabecera 'Idempotency-Key' para evitar duplicados por reintentos.
    """
    return await idempotency.execute(
        lambda: service.registrar_instalacion(
            schema, usuario_id=current_user.USU_Usuario, ip=get_client_ip(request)
        ),
        status_code=201,
    )


@router.post(
    "/instalaciones/desinstalar", status_code=200,
    dependencies=[Depends(require_operativo)],
)
async def desinstalar_software(
    schema: InstalacionCreate,
    request: Request,
    current_user: CurrentUser,
    service: SoftwareService = Depends(get_service),
):
    """Libera el cupo de la licencia (idempotente)."""
    return await service.desinstalar_software(
        schema, usuario_id=current_user.USU_Usuario, ip=get_client_ip(request)
    )


# =====================================================================
# CREDENCIALES / CUPOS (activación variable)
# =====================================================================
@router.get("/licencias/{id}/credenciales", dependencies=[Depends(require_admin)])
async def listar_credenciales(id: int, db: AsyncSession = Depends(get_db)):
    """Credenciales de la licencia, sin secretos ni keys completas."""
    from app.services.licencias import CredencialService
    return await CredencialService(db).listar(id)


@router.post("/licencias/{id}/credenciales", status_code=201, dependencies=[Depends(require_admin)])
async def crear_credenciales(
    id: int, schema: CredencialesLote, request: Request, current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Una o varias credenciales (p. ej. pegadas desde una lista, una por línea)."""
    from app.services.licencias import CredencialService
    service = CredencialService(db)
    await service.crear(id, [c.model_dump() for c in schema.credenciales],
                        usuario_id=current_user.USU_Usuario, ip=get_client_ip(request))
    return await service.listar(id)


@router.patch("/credenciales/{id}", status_code=204, dependencies=[Depends(require_admin)])
async def editar_credencial(
    id: int, schema: CredencialUpdate, request: Request, current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    from app.services.licencias import CredencialService
    await CredencialService(db).editar(id, schema.model_dump(exclude_unset=True),
                                       usuario_id=current_user.USU_Usuario, ip=get_client_ip(request))


@router.post("/credenciales/{id}/revelar", dependencies=[Depends(require_admin)])
@limiter.limit("10/minute")
async def revelar_credencial(id: int, request: Request, current_user: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Devuelve el secreto o la key; queda registrado (bitácora y kardex). La UI lo oculta a los 30 s."""
    from app.services.licencias import CredencialService
    return await CredencialService(db).revelar(id, usuario_id=current_user.USU_Usuario, ip=get_client_ip(request))


@router.post("/credenciales/{id}/rotar", status_code=204, dependencies=[Depends(require_admin)])
async def rotar_credencial(
    id: int, schema: CredencialRotar, request: Request, current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    from app.services.licencias import CredencialService
    await CredencialService(db).rotar(id, schema.model_dump(exclude_none=True),
                                      usuario_id=current_user.USU_Usuario, ip=get_client_ip(request))

