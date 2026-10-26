"""Consumibles: inventario por cantidad (tóner, cables, periféricos a granel)."""
from typing import List

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser, get_client_ip, require_admin, require_inventory, require_operativo, require_stock_catalog,
)
from app.api.idempotency import _IdempotencyGuard, idempotency_guard
from app.core.limiter import limiter
from app.core.ocultar_costos import descartar_costos
from app.db.session import get_db
from app.schemas.consumable import (
    CompatiblesUpdate, ConsumibleCreate, ConsumibleResponse, ConsumibleUpdate,
    MovimientoConsumibleResponse, StockAjusteCreate, StockMovimientoCreate,
)
from app.services.consumable import ConsumableService

router = APIRouter()

ADMIN = [Depends(require_admin)]
OPERATIVO = [Depends(require_operativo)]


def get_service(db: AsyncSession = Depends(get_db)) -> ConsumableService:
    return ConsumableService(db)


def _ctx(request: Request, user: CurrentUser) -> dict:
    return {"usuario_id": user.USU_Usuario, "ip": get_client_ip(request)}


# Catálogo y existencias: el inventario, Contabilidad (valores) y Compras
# (para elegir qué reabastecer en una orden). Los movimientos, solo el inventario.
@router.get("", response_model=List[ConsumibleResponse], dependencies=[Depends(require_stock_catalog)])
async def list_consumibles(
    bajo_stock: bool = Query(False, description="Solo consumibles en o por debajo del mínimo"),
    service: ConsumableService = Depends(get_service),
):
    return await service.list(solo_bajo_stock=bajo_stock)


@router.get("/{id}", response_model=ConsumibleResponse, dependencies=[Depends(require_stock_catalog)])
async def get_consumible(id: int, service: ConsumableService = Depends(get_service)):
    return await service.get(id)


@router.post("", response_model=ConsumibleResponse, status_code=201, dependencies=ADMIN)
async def create_consumible(
    schema: ConsumibleCreate, request: Request, current_user: CurrentUser,
    service: ConsumableService = Depends(get_service),
):
    return await service.create(schema, **_ctx(request, current_user))


@router.patch("/{id}", response_model=ConsumibleResponse, dependencies=ADMIN)
async def update_consumible(
    id: int, schema: ConsumibleUpdate, request: Request, current_user: CurrentUser,
    service: ConsumableService = Depends(get_service),
):
    return await service.update(id, schema, **_ctx(request, current_user))


@router.delete("/{id}", status_code=204, dependencies=ADMIN)
async def delete_consumible(
    id: int, request: Request, current_user: CurrentUser,
    service: ConsumableService = Depends(get_service),
):
    await service.delete(id, **_ctx(request, current_user))


# ------------------------------------------------------------------
# Movimientos de stock
# ------------------------------------------------------------------
@router.post("/{id}/entrada", response_model=MovimientoConsumibleResponse, status_code=201, dependencies=OPERATIVO)
@limiter.limit("60/minute")
async def registrar_entrada(
    id: int, schema: StockMovimientoCreate, request: Request, current_user: CurrentUser,
    service: ConsumableService = Depends(get_service),
    idempotency: _IdempotencyGuard = Depends(idempotency_guard),
):
    """Suma stock al consumible (compra, reabastecimiento). Sin precio, se
    mantiene el costo promedio (quien no ve costos no los registra)."""
    descartar_costos(schema, current_user.USU_Rol, "precio_unitario", "moneda")
    return await idempotency.execute(
        lambda: service.registrar_entrada(id, schema, **_ctx(request, current_user)),
        status_code=201,
    )


@router.post("/{id}/salida", response_model=MovimientoConsumibleResponse, status_code=201, dependencies=OPERATIVO)
@limiter.limit("60/minute")
async def registrar_salida(
    id: int, schema: StockMovimientoCreate, request: Request, current_user: CurrentUser,
    service: ConsumableService = Depends(get_service),
    idempotency: _IdempotencyGuard = Depends(idempotency_guard),
):
    """Descuenta stock (entrega a una persona). 409 si no hay stock suficiente."""
    return await idempotency.execute(
        lambda: service.registrar_salida(id, schema, **_ctx(request, current_user)),
        status_code=201,
    )


@router.post("/{id}/ajuste", status_code=201, dependencies=ADMIN)
@limiter.limit("30/minute")
async def registrar_ajuste(
    id: int, schema: StockAjusteCreate, request: Request, response: Response, current_user: CurrentUser,
    service: ConsumableService = Depends(get_service),
):
    """
    Fija la existencia real tras un conteo físico. Si sube, se aplica (201,
    movimiento); si baja, se solicita aprobación del Jefe de TI (202, solicitud).
    """
    from app.services.aprobaciones import AprobacionService
    obj = await service.get_or_404(id, "CONSUMABLE_NOT_FOUND")
    if schema.CON_Stock_Real < obj.CON_Stock_Actual:
        response.status_code = 202
        return await AprobacionService(service.db).solicitar_ajuste(id, schema, **_ctx(request, current_user))
    mov = await service.registrar_ajuste(id, schema, **_ctx(request, current_user))
    return MovimientoConsumibleResponse.model_validate(mov)


@router.get("/{id}/compatibles", dependencies=[Depends(require_stock_catalog)])
async def listar_compatibles(id: int, service: ConsumableService = Depends(get_service)):
    """Modelos de equipo con los que es compatible (tóner → impresoras)."""
    return await service.compatibles(id)


@router.put("/{id}/compatibles", dependencies=ADMIN)
async def fijar_compatibles(
    id: int, schema: CompatiblesUpdate, request: Request, current_user: CurrentUser,
    service: ConsumableService = Depends(get_service),
):
    return await service.fijar_compatibles(id, schema.modelos, **_ctx(request, current_user))


@router.get("/{id}/movimientos", response_model=List[MovimientoConsumibleResponse],
            dependencies=[Depends(require_inventory)])
async def list_movimientos(id: int, service: ConsumableService = Depends(get_service)):
    return await service.list_movimientos(id)
