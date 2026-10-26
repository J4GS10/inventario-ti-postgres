"""
Aprobaciones del Jefe de TI: bajas de activos y sobrantes de compras.

Lectura: roles del inventario (cada uno ve su alcance y sus propias
solicitudes). Aprobar o rechazar: JEFE_TI o SUPER_ADMIN, nunca la misma
persona que solicitó. Anular: quien solicitó, mientras siga pendiente.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_client_ip, require_admin, require_aprobador
from app.db.session import get_db
from app.schemas.common import Esquema
from app.services.aprobaciones import AprobacionService

router = APIRouter()


class Resolucion(Esquema):
    comentario: Optional[str] = Field(None, max_length=500)


class Rechazo(Esquema):
    comentario: str = Field(..., min_length=5, max_length=500)


def get_service(db: AsyncSession = Depends(get_db)) -> AprobacionService:
    return AprobacionService(db)


@router.get("")
async def listar(
    current_user: CurrentUser,
    estado: Optional[str] = Query(None, pattern="^(PENDIENTE|APROBADA|RECHAZADA|ANULADA)$"),
    activo_id: Optional[uuid.UUID] = None,
    service: AprobacionService = Depends(get_service),
):
    filas = await service.listar(estado=estado, activo_id=activo_id)
    # La interfaz decide qué botones mostrar (nadie resuelve lo propio).
    for f in filas:
        f["es_mia"] = f["APR_Solicitante"] == current_user.USU_Usuario
    return filas


@router.get("/pendientes")
async def total_pendientes(service: AprobacionService = Depends(get_service)):
    return {"total": await service.pendientes()}


@router.post("/{aprobacion_id}/aprobar", dependencies=[Depends(require_aprobador)])
async def aprobar(aprobacion_id: int, body: Resolucion, request: Request, current_user: CurrentUser,
                  service: AprobacionService = Depends(get_service)):
    return await service.aprobar(aprobacion_id, current_user.USU_Usuario, body.comentario,
                                 ip=get_client_ip(request))


@router.post("/{aprobacion_id}/rechazar", dependencies=[Depends(require_aprobador)])
async def rechazar(aprobacion_id: int, body: Rechazo, request: Request, current_user: CurrentUser,
                   service: AprobacionService = Depends(get_service)):
    return await service.rechazar(aprobacion_id, current_user.USU_Usuario, body.comentario,
                                  ip=get_client_ip(request))


@router.post("/{aprobacion_id}/anular", dependencies=[Depends(require_admin)])
async def anular(aprobacion_id: int, request: Request, current_user: CurrentUser,
                 service: AprobacionService = Depends(get_service)):
    return await service.anular(aprobacion_id, current_user.USU_Usuario, ip=get_client_ip(request))
