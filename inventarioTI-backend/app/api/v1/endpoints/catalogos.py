"""
Centro de catálogos: CRUD genérico, reorden y fusión de duplicados.
Lectura: roles de negocio. Escritura: ADMIN+ (la fusión, además, alcance global).
"""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_client_ip, require_admin
from app.db.session import get_db
from app.schemas.catalogo_generico import (
    CatalogoItemCreate, CatalogoItemUpdate, CatalogoReorden, EspecificacionesTipo, KitEstandarIn,
)
from app.services.catalogo_config import CatalogoConfigService
from app.services.catalogo_generico import CATALOGOS, CatalogoGenericoService

router = APIRouter()

ADMIN = [Depends(require_admin)]


def get_service(db: AsyncSession = Depends(get_db)) -> CatalogoGenericoService:
    return CatalogoGenericoService(db)


@router.get("")
async def listar_catalogos():
    """Catálogos administrables, con su grupo y sus campos propios."""
    return [CatalogoGenericoService.describir(d) for d in CATALOGOS.values()]


# ── Especificaciones por tipo de activo y kit estándar (antes de /{clave}) ──
@router.get("/especificaciones-tipo")
async def especificaciones_por_tipo(db: AsyncSession = Depends(get_db)):
    """Especificaciones que pide cada tipo de activo: {TAC_Tipo_Activo: [...]}."""
    return await CatalogoConfigService(db).todas_las_especificaciones()


@router.get("/especificaciones-tipo/{tipo_id}")
async def especificaciones_de_tipo(tipo_id: int, db: AsyncSession = Depends(get_db)):
    return await CatalogoConfigService(db).especificaciones_de_tipo(tipo_id)


@router.put("/especificaciones-tipo/{tipo_id}", dependencies=ADMIN)
async def fijar_especificaciones(
    tipo_id: int, schema: EspecificacionesTipo, request: Request, current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    return await CatalogoConfigService(db).fijar_especificaciones(
        tipo_id, [i.model_dump() for i in schema.items], usuario_id=current_user.USU_Usuario,
        ip=get_client_ip(request))


@router.get("/kits")
async def listar_kits(db: AsyncSession = Depends(get_db)):
    return await CatalogoConfigService(db).listar_kits()


@router.post("/kits", status_code=201, dependencies=ADMIN)
async def crear_kit(schema: KitEstandarIn, request: Request, current_user: CurrentUser,
                    db: AsyncSession = Depends(get_db)):
    return await CatalogoConfigService(db).crear_kit(schema.model_dump(), usuario_id=current_user.USU_Usuario,
                                                     ip=get_client_ip(request))


@router.put("/kits/{kit_id}", dependencies=ADMIN)
async def editar_kit(kit_id: int, schema: KitEstandarIn, request: Request, current_user: CurrentUser,
                     db: AsyncSession = Depends(get_db)):
    return await CatalogoConfigService(db).editar_kit(kit_id, schema.model_dump(),
                                                      usuario_id=current_user.USU_Usuario, ip=get_client_ip(request))


@router.delete("/kits/{kit_id}", status_code=204, dependencies=ADMIN)
async def borrar_kit(kit_id: int, request: Request, current_user: CurrentUser, db: AsyncSession = Depends(get_db)):
    await CatalogoConfigService(db).borrar_kit(kit_id, usuario_id=current_user.USU_Usuario, ip=get_client_ip(request))


# ── Catálogos estándar ────────────────────────────────────────────────────────
@router.get("/{clave}")
async def listar_items(
    clave: str, inactivos: bool = Query(False),
    service: CatalogoGenericoService = Depends(get_service),
):
    return await service.listar(clave, inactivos=inactivos)


@router.post("/{clave}", status_code=201, dependencies=ADMIN)
async def crear_item(
    clave: str, schema: CatalogoItemCreate, request: Request, current_user: CurrentUser,
    service: CatalogoGenericoService = Depends(get_service),
):
    return await service.crear(clave, schema.model_dump(), usuario_id=current_user.USU_Usuario,
                               ip=get_client_ip(request))


@router.patch("/{clave}/{item_id}", dependencies=ADMIN)
async def editar_item(
    clave: str, item_id: int, schema: CatalogoItemUpdate, request: Request, current_user: CurrentUser,
    service: CatalogoGenericoService = Depends(get_service),
):
    return await service.editar(clave, item_id, schema.model_dump(exclude_unset=True),
                                usuario_id=current_user.USU_Usuario, ip=get_client_ip(request))


@router.post("/{clave}/reordenar", status_code=204, dependencies=ADMIN)
async def reordenar(
    clave: str, schema: CatalogoReorden, request: Request, current_user: CurrentUser,
    service: CatalogoGenericoService = Depends(get_service),
):
    await service.reordenar(clave, schema.ids, usuario_id=current_user.USU_Usuario, ip=get_client_ip(request))


@router.get("/{clave}/{origen}/fusionar/{destino}", dependencies=ADMIN)
async def previa_fusion(
    clave: str, origen: int, destino: int, service: CatalogoGenericoService = Depends(get_service),
):
    """Cuántos registros se moverán si se fusiona `origen` en `destino`."""
    return await service.previa_fusion(clave, origen, destino)


@router.post("/{clave}/{origen}/fusionar/{destino}", dependencies=ADMIN)
async def fusionar(
    clave: str, origen: int, destino: int, request: Request, current_user: CurrentUser,
    service: CatalogoGenericoService = Depends(get_service),
):
    return await service.fusionar(clave, origen, destino, usuario_id=current_user.USU_Usuario,
                                  ip=get_client_ip(request))
