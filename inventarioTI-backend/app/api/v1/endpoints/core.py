"""Activos (Core). Lectura: cualquier autenticado. Mutación: TECNICO+."""
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser, PaginationParams, get_client_ip, require_admin, require_inventory, require_operativo, require_people,
)
from app.api.idempotency import _IdempotencyGuard, idempotency_guard
from app.core.limiter import limiter
from app.core.ocultar_costos import descartar_costos
from app.db.session import get_db
from app.schemas.common import PaginatedResponse
from app.schemas.core import (
    ActivoCreate, ActivoDetailResponse, ActivoFilter, ActivoResponse, ActivoUpdate, ReclasificacionCreate,
    BajaActivo, ComponenteInstalar, ComponenteRetirar, EdicionLote, RestaurarLote,
    EspecificacionCreate, EspecificacionDetalle, EspecificacionValorUpdate,
    EtiquetasActivosRequest,
)
from app.services.core import CoreService
from app.services.suggestions import SuggestionService
from app.services.labels import LabelService

router = APIRouter()

OPERATIVO = [Depends(require_operativo)]
ADMIN = [Depends(require_admin)]


def get_service(db: AsyncSession = Depends(get_db)) -> CoreService:
    return CoreService(db)


def _ctx(request: Request, user: CurrentUser) -> dict:
    return {"usuario_id": user.USU_Usuario, "ip": get_client_ip(request)}


# =====================================================================
# ESPECIFICACIONES (características del equipo: RAM, disco, batería, etc.)
# =====================================================================
@router.get("/activos/{activo_id}/especificaciones", response_model=List[EspecificacionDetalle])
async def list_especificaciones(activo_id: uuid.UUID, service: CoreService = Depends(get_service)):
    return await service.list_especificaciones(activo_id)


@router.post("/activos/{activo_id}/especificaciones", response_model=EspecificacionDetalle,
             status_code=201, dependencies=OPERATIVO)
async def add_especificacion(
    activo_id: uuid.UUID, schema: EspecificacionCreate, request: Request, current_user: CurrentUser,
    service: CoreService = Depends(get_service),
):
    await service.add_especificacion(
        activo_id, schema.TES_Tipo_Especificacion, schema.ESP_Valor, motivo=schema.motivo,
        **_ctx(request, current_user)
    )
    # Devolvemos la fila enriquecida recién creada.
    specs = await service.list_especificaciones(activo_id)
    return next((s for s in specs if s.TES_Tipo_Especificacion == schema.TES_Tipo_Especificacion), specs[-1])


@router.patch("/especificaciones/{esp_id}", status_code=204, dependencies=OPERATIVO)
async def update_especificacion(
    esp_id: int, schema: EspecificacionValorUpdate, request: Request, current_user: CurrentUser,
    service: CoreService = Depends(get_service),
):
    await service.update_especificacion(esp_id, schema.ESP_Valor, motivo=schema.motivo,
                                        **_ctx(request, current_user))


@router.delete("/especificaciones/{esp_id}", status_code=204, dependencies=OPERATIVO)
async def delete_especificacion(
    esp_id: int, request: Request, current_user: CurrentUser,
    service: CoreService = Depends(get_service),
):
    await service.delete_especificacion(esp_id, **_ctx(request, current_user))


@router.post("/activos", response_model=ActivoResponse, status_code=201, dependencies=OPERATIVO)
async def create_activo(
    schema: ActivoCreate, request: Request, current_user: CurrentUser,
    service: CoreService = Depends(get_service),
    idempotency: _IdempotencyGuard = Depends(idempotency_guard),
):
    descartar_costos(schema, current_user.USU_Rol, "ACT_Costo")
    return await idempotency.execute(
        lambda: service.create_activo(schema, usuario_id=current_user.USU_Usuario),
        status_code=201,
    )


# Edición en lote (antes de /activos/{activo_id}: «lote» no es un id).
@router.patch("/activos/lote", dependencies=ADMIN)
async def editar_lote(schema: EdicionLote, request: Request, current_user: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Ubicación, costo y moneda, garantía, proveedor/factura y especificaciones para varios activos a la vez."""
    from app.services.edicion_lote import EdicionLoteService
    return await EdicionLoteService(db).aplicar([(a, schema.cambios) for a in schema.activos], **_ctx(request, current_user))


@router.post("/activos/lote/restaurar", dependencies=ADMIN)
async def restaurar_lote(schema: RestaurarLote, request: Request, current_user: CurrentUser,
                         db: AsyncSession = Depends(get_db)):
    """Deshacer una edición en lote: cada activo vuelve a sus valores anteriores (con su evento en el kardex)."""
    from app.services.edicion_lote import EdicionLoteService
    return await EdicionLoteService(db).aplicar([(i.ACT_Activo, i.cambios) for i in schema.items],
                                                accion="UPDATE_BATCH_UNDO", **_ctx(request, current_user))


@router.patch("/activos/{activo_id}", response_model=ActivoResponse, dependencies=OPERATIVO)
async def update_activo(
    activo_id: uuid.UUID, schema: ActivoUpdate, request: Request, current_user: CurrentUser,
    service: CoreService = Depends(get_service),
):
    descartar_costos(schema, current_user.USU_Rol, "ACT_Costo", "ACT_Moneda")
    return await service.update_activo(activo_id, schema, usuario_id=current_user.USU_Usuario)


@router.get("/activos", response_model=List[ActivoResponse])
async def list_activos(
    pagination: PaginationParams = Depends(),
    service: CoreService = Depends(get_service),
):
    return await service.list_activos(pagination.skip, pagination.limit)


@router.get("/activos/by-code/{codigo}", response_model=ActivoDetailResponse)
async def get_activo_by_codigo(
    codigo: str,
    service: CoreService = Depends(get_service),
):
    return await service.get_activo_by_codigo(codigo)


@router.post("/activos/etiquetas.pdf", response_class=StreamingResponse, dependencies=OPERATIVO)
async def generar_etiquetas_activos(
    payload: EtiquetasActivosRequest,
    lang: str = Query("es", pattern="^(es|en|it)$", description="Idioma de las etiquetas"),
    db: AsyncSession = Depends(get_db),
):
    from app.services.labels import label_filename
    buffer = await LabelService(db).generar_pdf_etiquetas(
        activo_ids=payload.activos_ids,
        codigos=payload.codigos,
        lang=lang,
    )
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{label_filename(lang)}"'},
    )


@router.get("/activos/{activo_id}", response_model=ActivoDetailResponse)
async def get_activo_detail(
    activo_id: uuid.UUID,
    service: CoreService = Depends(get_service),
):
    return await service.get_activo_detail(activo_id)


@router.delete("/activos/{activo_id}", status_code=204, dependencies=ADMIN)
async def delete_activo(
    activo_id: uuid.UUID, request: Request, current_user: CurrentUser,
    service: CoreService = Depends(get_service),
):
    """Baja lógica. Solo ADMIN+."""
    await service.delete_activo(activo_id, usuario_id=current_user.USU_Usuario)


@router.post("/activos/search", response_model=PaginatedResponse[ActivoResponse])
@limiter.limit("120/minute")  # por usuario autenticado (ver rate_limit_key)
async def search_activos(
    request: Request,
    filters: ActivoFilter,
    service: CoreService = Depends(get_service),
):
    """
    Búsqueda paginada server-side. Devuelve {items, total, page, per_page}.
    El frontend usa `total` para calcular el número de páginas y mostrar el contador.
    """
    activos, total = await service.search_activos(filters)
    return PaginatedResponse[ActivoResponse](
        items=activos,
        total=total,
        page=filters.page,
        per_page=filters.per_page,
    )


# =====================================================================
# BAJA FORMAL
# =====================================================================
@router.post("/activos/{activo_id}/baja", status_code=202, dependencies=ADMIN)
async def solicitar_baja(
    activo_id: uuid.UUID, schema: BajaActivo, request: Request, current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """
    Solicita la salida del inventario (motivo, destino, fecha y documento). Se
    valida ahora y se ejecuta cuando el Jefe de TI la aprueba
    (POST /aprobaciones/{id}/aprobar). Devuelve la solicitud PENDIENTE.
    """
    from app.services.aprobaciones import AprobacionService
    return await AprobacionService(db).solicitar_baja(activo_id, schema, **_ctx(request, current_user))


# =====================================================================
# COMPONENTES
# =====================================================================
@router.get("/activos/{activo_id}/componentes")
async def listar_componentes(activo_id: uuid.UUID, service: CoreService = Depends(get_service)):
    """Componentes instalados hoy y su historial de instalaciones y retiros."""
    data = await service.listar_componentes(activo_id)
    return _componentes_json(data)


@router.post("/activos/{activo_id}/componentes", dependencies=OPERATIVO)
async def instalar_componente(
    activo_id: uuid.UUID, schema: ComponenteInstalar, request: Request, current_user: CurrentUser,
    service: CoreService = Depends(get_service),
):
    data = await service.instalar_componente(activo_id, schema, usuario_id=current_user.USU_Usuario)
    return _componentes_json(data)


@router.post("/activos/{activo_id}/componentes/{componente_id}/retirar", dependencies=OPERATIVO)
async def retirar_componente(
    activo_id: uuid.UUID, componente_id: uuid.UUID, schema: ComponenteRetirar, request: Request,
    current_user: CurrentUser, service: CoreService = Depends(get_service),
):
    data = await service.retirar_componente(activo_id, componente_id, schema, usuario_id=current_user.USU_Usuario)
    return _componentes_json(data)


def _componentes_json(data: dict) -> dict:
    from app.schemas.kardex import KardexEvento
    return {
        "equipo": str(data["equipo"]),
        "instalado_en": str(data["instalado_en"]) if data["instalado_en"] else None,
        "actuales": [
            {"ACT_Activo": str(c.ACT_Activo), "ACT_Codigo_Interno": c.ACT_Codigo_Interno,
             "ACT_Serie_Fabricante": c.ACT_Serie_Fabricante, "TAC_Tipo_Activo": c.TAC_Tipo_Activo,
             "MOD_Modelo": c.MOD_Modelo}
            for c in data["actuales"]
        ],
        "historial": [KardexEvento.model_validate(e).model_dump(mode="json") for e in data["historial"]],
    }


@router.get("/activos/{id}/consumibles", dependencies=[Depends(require_inventory)])
async def consumibles_del_activo(id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Consumibles instalados en el equipo (tóner de una impresora) y los compatibles con su modelo."""
    from app.services.consumable import ConsumableService
    return await ConsumableService(db).consumo_de_equipo(id)


@router.get("/tipos-activo/{tipo_id}/reclasificar", dependencies=ADMIN)
async def vista_reclasificacion(tipo_id: int, sede: Optional[int] = None, db: AsyncSession = Depends(get_db)):
    """Cuántos activos de un tipo «insumo» pasarían a consumible y cuáles lo impiden."""
    from app.services.reclasificacion import ReclasificacionService
    return await ReclasificacionService(db).vista_previa(tipo_id, sede)


@router.post("/tipos-activo/{tipo_id}/reclasificar", status_code=202, dependencies=ADMIN)
async def solicitar_reclasificacion(
    tipo_id: int, schema: ReclasificacionCreate, request: Request, current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Solicita convertir los activos de un tipo «insumo» en un consumible (se ejecuta al aprobarse)."""
    from app.services.aprobaciones import AprobacionService
    return await AprobacionService(db).solicitar_reclasificacion(tipo_id, schema, **_ctx(request, current_user))


@router.get("/activos/{id}/contexto", dependencies=[Depends(require_people)])
async def activo_contexto(id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Custodio y área actuales del activo y el tipo de movimiento lógico."""
    return await SuggestionService(db).activo(id)
