"""Búsqueda global, kit sugerido, calidad de datos e importación desde Excel."""
import uuid

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_client_ip, require_admin, require_control_read, require_operativo, require_people
from app.core.limiter import limiter
from app.db.session import get_db
from app.services.importacion_activos import MAX_BYTES, ImportacionActivosService
from app.services.productividad import ProductividadService

router = APIRouter()

ADMIN = [Depends(require_admin)]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/buscar")
@limiter.limit("120/minute")
async def buscar(
    request: Request, current_user: CurrentUser,
    q: str = Query(..., min_length=2, max_length=100),
    db: AsyncSession = Depends(get_db),
):
    """Busca activos (código, serie, hostname, modelo), personas y órdenes de compra a la vez."""
    return await ProductividadService(db).buscar(q, current_user.USU_Rol)


@router.get("/kit-sugerido/{persona_id}", dependencies=[Depends(require_operativo)])
async def kit_sugerido(persona_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Tipos de equipo que suelen tener las personas de su departamento, con disponibilidad."""
    return await ProductividadService(db).kit_sugerido(persona_id)


@router.get("/personas/{persona_id}/ficha", dependencies=[Depends(require_people)])
async def ficha_persona(persona_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Ficha 360: datos, equipos en custodia, licencias y cuentas, consumibles e historial."""
    from app.services.ficha_persona import FichaPersonaService
    return await FichaPersonaService(db).ficha(persona_id)


@router.get("/calidad-datos", dependencies=[Depends(require_control_read)])
async def calidad_datos(db: AsyncSession = Depends(get_db)):
    """Verificaciones de datos incompletos o inconsistentes, con los registros afectados."""
    return await ProductividadService(db).calidad_datos()


async def _leer(archivo: UploadFile) -> bytes:
    contenido = await archivo.read(MAX_BYTES + 1)
    if len(contenido) > MAX_BYTES:
        from fastapi import HTTPException
        raise HTTPException(413, detail="FILE_TOO_LARGE")
    return contenido


@router.get("/importar/activos/plantilla", dependencies=ADMIN)
async def plantilla_importacion(db: AsyncSession = Depends(get_db)):
    """Plantilla .xlsx con los encabezados y las listas de categorías, ubicaciones y marcas."""
    contenido = await ImportacionActivosService(db).plantilla()
    return Response(contenido, media_type=XLSX,
                    headers={"Content-Disposition": 'attachment; filename="plantilla_activos.xlsx"'})


@router.post("/importar/activos/previsualizar", dependencies=ADMIN)
@limiter.limit("30/minute")
async def previsualizar_importacion(
    request: Request, archivo: UploadFile = File(...), db: AsyncSession = Depends(get_db),
):
    """Valida el archivo sin guardar nada y devuelve el resultado fila por fila."""
    return await ImportacionActivosService(db).previsualizar(await _leer(archivo))


@router.post("/importar/activos/confirmar", status_code=201, dependencies=ADMIN)
@limiter.limit("10/minute")
async def confirmar_importacion(
    request: Request, current_user: CurrentUser,
    archivo: UploadFile = File(...),
    omitir_errores: bool = Query(False, description="Importa las filas válidas y omite las que tienen error"),
    db: AsyncSession = Depends(get_db),
):
    """Vuelve a validar y crea los activos en una sola transacción."""
    return await ImportacionActivosService(db).confirmar(
        await _leer(archivo), omitir_errores=omitir_errores,
        usuario_id=current_user.USU_Usuario, ip=get_client_ip(request),
    )
