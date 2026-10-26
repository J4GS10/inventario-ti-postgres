"""
Trazabilidad y controles del inventario:

- /kardex, /kardex.csv         kardex unificado (vida de un objeto, entradas y
                               salidas, reasignaciones) con filtros.
- /custodia, /custodia.csv     quién tenía qué equipo y dónde, a una fecha.
- /entradas-salidas            resumen por periodo con valores por moneda.
- /activos/{id}/hoja-de-vida.pdf
- /licencias.csv               uso, vencimiento y costo de las licencias.
- /atencion                    bandeja "Requiere su atención" del tablero.
- /tomas…                      toma de inventario físico (conciliación).

Lectura: cualquier rol del inventario. Exportes: require_export. Tomas:
operativos; anular: administradores.
"""

import uuid
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser, RoleChecker, get_client_ip, require_admin, require_export, require_finance_export, require_inventory,
    require_operativo, require_stock_read,
)
from app.core.limiter import limiter
from app.core.ocultar_costos import oculta_costos
from app.db.session import get_db, get_read_db
from app.schemas.core import BajaActivo
from app.schemas.kardex import (
    CustodiaItem, FaltanteLocalizado, FaltanteNota, KardexPagina, LecturaCreate, LecturaResultado, TomaCreate,
    TomaDetalle, TomaResponse,
)
from app.services.inventario_fisico import InventarioFisicoService
from app.services.reportes import ReportesService, csv_bytes

router = APIRouter()

# Lectura del inventario (kardex, custodia, tomas, hoja de vida).
INV = [Depends(require_inventory)]

OBJETOS = "^(ACTIVO|CONSUMIBLE|LICENCIA)$"
CATEGORIAS = "^(ENTRADAS|SALIDAS|ENTRADAS_SALIDAS|REASIGNACIONES)$"


def _csv(contenido: bytes, nombre: str) -> Response:
    return Response(
        content=contenido, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


def _filtros(
    objeto: Optional[str] = Query(None, pattern=OBJETOS),
    tipos: Optional[List[str]] = Query(None),
    categoria: Optional[str] = Query(None, pattern=CATEGORIAS),
    activo_id: Optional[uuid.UUID] = None,
    consumible_id: Optional[int] = None,
    licencia_id: Optional[int] = None,
    persona_id: Optional[uuid.UUID] = None,
    sede_id: Optional[int] = None,
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
    texto: Optional[str] = Query(None, max_length=64),
) -> dict:
    return {
        "objeto": objeto, "tipos": tipos, "categoria": categoria, "activo_id": activo_id,
        "consumible_id": consumible_id, "licencia_id": licencia_id, "persona_id": persona_id,
        "sede_id": sede_id, "desde": desde, "hasta": hasta, "texto": texto,
    }


def _hoy() -> str:
    from app.services.report_i18n import hoy_local
    return hoy_local().strftime("%Y%m%d")


# ================= KARDEX =================
@router.get("/kardex", response_model=KardexPagina, dependencies=INV)
async def kardex(
    filtros: dict = Depends(_filtros),
    limite: int = Query(50, ge=1, le=500),
    desplazamiento: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_read_db),
):
    return await ReportesService(db).kardex_pagina(filtros, limite=limite, desplazamiento=desplazamiento)


@router.get("/kardex/verificar", dependencies=[Depends(RoleChecker(["SUPER_ADMIN", "JEFE_TI", "AUDITOR"]))])
async def verificar_kardex(db: AsyncSession = Depends(get_db)):
    """
    Recalcula el sello encadenado de todo el kardex: confirma que ningún evento
    se alteró, borró ni intercaló. Devuelve el último sello (secuencia y hash)
    para anotarlo fuera del sistema en cada cierre.
    """
    from app.core.data_scope import elevated
    from app.db.kardex_integridad import verificar
    async with elevated(db):
        return await verificar(db)


@router.get("/kardex.csv", dependencies=[Depends(require_export)])
@limiter.limit("10/minute")
async def kardex_csv(
    request: Request,
    filtros: dict = Depends(_filtros),
    lang: str = Query("es", pattern="^(es|en|it)$"),
    db: AsyncSession = Depends(get_read_db),
):
    nombre = {"es": "kardex", "en": "inventory_ledger", "it": "registro_inventario"}[lang]
    return _csv(await ReportesService(db).kardex_csv(filtros, lang), f"{nombre}_{_hoy()}.csv")


# ================= CUSTODIA =================
@router.get("/custodia", response_model=List[CustodiaItem], dependencies=INV)
async def custodia(
    fecha: Optional[date] = None, persona_id: Optional[uuid.UUID] = None,
    area_id: Optional[int] = None, sede_id: Optional[int] = None,
    db: AsyncSession = Depends(get_read_db),
):
    return await ReportesService(db).custodia(fecha=fecha, persona_id=persona_id, area_id=area_id, sede_id=sede_id)


@router.get("/custodia.csv", dependencies=[Depends(require_export)])
@limiter.limit("10/minute")
async def custodia_csv(
    request: Request,
    fecha: Optional[date] = None, persona_id: Optional[uuid.UUID] = None,
    area_id: Optional[int] = None, sede_id: Optional[int] = None,
    lang: str = Query("es", pattern="^(es|en|it)$"),
    db: AsyncSession = Depends(get_read_db),
):
    nombre = {"es": "custodia", "en": "custody", "it": "custodia"}[lang]
    contenido = await ReportesService(db).custodia_csv(
        lang, fecha=fecha, persona_id=persona_id, area_id=area_id, sede_id=sede_id)
    return _csv(contenido, f"{nombre}_{(fecha.strftime('%Y%m%d') if fecha else _hoy())}.csv")


# ================= ENTRADAS Y SALIDAS =================
@router.get("/entradas-salidas", dependencies=[Depends(require_stock_read)])
async def entradas_salidas(
    desde: Optional[date] = None, hasta: Optional[date] = None, sede_id: Optional[int] = None,
    db: AsyncSession = Depends(get_read_db),
):
    return await ReportesService(db).entradas_salidas(desde=desde, hasta=hasta, sede_id=sede_id)


@router.get("/resumen-motivos", dependencies=INV)
async def resumen_motivos(
    desde: Optional[date] = None, hasta: Optional[date] = None, sede_id: Optional[int] = None,
    db: AsyncSession = Depends(get_read_db),
):
    """Movimientos por motivo, mantenimientos por falla y acción, y bajas por motivo y destino."""
    return await ReportesService(db).resumen_motivos(desde=desde, hasta=hasta, sede_id=sede_id)


# ================= HOJA DE VIDA =================
@router.get("/activos/{activo_id}/hoja-de-vida.pdf", dependencies=INV)
@limiter.limit("20/minute")
async def hoja_de_vida(
    request: Request, activo_id: uuid.UUID, current_user: CurrentUser,
    lang: str = Query("es", pattern="^(es|en|it)$"),
    db: AsyncSession = Depends(get_read_db),
):
    contenido, nombre = await ReportesService(db).hoja_de_vida_pdf(
        activo_id, lang, con_costo=not oculta_costos(current_user.USU_Rol))
    return Response(content=contenido, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


# ================= LICENCIAS =================
@router.get("/licencias.csv", dependencies=[Depends(require_finance_export)])
@limiter.limit("10/minute")
async def licencias_csv(
    request: Request, lang: str = Query("es", pattern="^(es|en|it)$"),
    db: AsyncSession = Depends(get_read_db),
):
    nombre = {"es": "licencias", "en": "licenses", "it": "licenze"}[lang]
    return _csv(await ReportesService(db).licencias_csv(lang), f"{nombre}_{_hoy()}.csv")


# ================= BANDEJA DE ATENCIÓN =================
@router.get("/atencion")
async def atencion(current_user: CurrentUser, db: AsyncSession = Depends(get_read_db)):
    """Solo los pendientes que el rol puede atender o consultar (sin indicios del resto)."""
    return await ReportesService(db).atencion(current_user.USU_Rol)


# ================= CIERRE MENSUAL (Contabilidad) =================
@router.get("/cierre-mensual", dependencies=[Depends(require_finance_export)])
async def cierre_mensual(
    anio: int = Query(..., ge=2000, le=2100), mes: int = Query(..., ge=1, le=12), sede_id: Optional[int] = None,
    db: AsyncSession = Depends(get_read_db),
):
    """Valor del inventario al cierre del mes y lo que cambió en el mes, por sede y moneda."""
    from app.services.cierre_mensual import CierreMensualService
    return await CierreMensualService(db).cierre(anio, mes, sede_id)


@router.get("/cierre-mensual.xlsx", dependencies=[Depends(require_finance_export)])
@limiter.limit("10/minute")
async def cierre_mensual_xlsx(
    request: Request, anio: int = Query(..., ge=2000, le=2100), mes: int = Query(..., ge=1, le=12),
    sede_id: Optional[int] = None, lang: str = Query("es", pattern="^(es|en|it)$"),
    db: AsyncSession = Depends(get_read_db),
):
    from app.services.cierre_mensual import CierreMensualService
    from app.services.cierre_textos import TEXTOS
    contenido = await CierreMensualService(db).cierre_xlsx(anio, mes, sede_id, lang)
    return Response(content=contenido,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{TEXTOS[lang]["archivo"]}_{anio}{mes:02d}.xlsx"'})


# ================= FALTANTES DE LA TOMA =================
@router.get("/faltantes", dependencies=INV)
async def listar_faltantes(
    estado: Optional[str] = Query(None, pattern="^(PENDIENTE|EN_INVESTIGACION|LOCALIZADO|BAJA_SOLICITADA|DADO_DE_BAJA)$"),
    abiertos: bool = False, db: AsyncSession = Depends(get_db),
):
    from app.services.faltantes import FaltanteService
    return await FaltanteService(db).listar(estado, abiertos)


@router.post("/faltantes/{faltante_id}/seguimiento", dependencies=[Depends(require_operativo)])
async def nota_faltante(faltante_id: int, schema: FaltanteNota, request: Request, current_user: CurrentUser,
                        db: AsyncSession = Depends(get_db)):
    """Registra lo que se investigó (con quién se habló, dónde se buscó)."""
    from app.services.faltantes import FaltanteService
    return await FaltanteService(db).seguimiento(faltante_id, schema.nota, **_ctx(request, current_user))


@router.post("/faltantes/{faltante_id}/localizado", dependencies=[Depends(require_operativo)])
async def faltante_localizado(faltante_id: int, schema: FaltanteLocalizado, request: Request,
                              current_user: CurrentUser, db: AsyncSession = Depends(get_db)):
    from app.services.faltantes import FaltanteService
    return await FaltanteService(db).localizar(faltante_id, schema.nota, schema.ARE_Area, **_ctx(request, current_user))


@router.post("/faltantes/{faltante_id}/baja", status_code=202, dependencies=[Depends(require_admin)])
async def faltante_baja(faltante_id: int, schema: BajaActivo, request: Request, current_user: CurrentUser,
                        db: AsyncSession = Depends(get_db)):
    """Baja por pérdida o robo de un faltante: va a aprobación del Jefe de TI."""
    from app.services.faltantes import FaltanteService
    return await FaltanteService(db).solicitar_baja(faltante_id, schema, **_ctx(request, current_user))


# ================= TOMA DE INVENTARIO =================
def _ctx(request: Request, user: CurrentUser) -> dict:
    return {"usuario_id": user.USU_Usuario, "ip": get_client_ip(request)}


@router.get("/tomas", response_model=List[TomaResponse], dependencies=INV)
async def listar_tomas(db: AsyncSession = Depends(get_db)):
    return await InventarioFisicoService(db).listar()


@router.post("/tomas", response_model=TomaResponse, status_code=201, dependencies=[Depends(require_operativo)])
async def crear_toma(schema: TomaCreate, request: Request, current_user: CurrentUser, db: AsyncSession = Depends(get_db)):
    return await InventarioFisicoService(db).crear(schema, **_ctx(request, current_user))


@router.get("/tomas/{toma_id}", response_model=TomaDetalle, dependencies=INV)
async def detalle_toma(toma_id: int, db: AsyncSession = Depends(get_db)):
    return await InventarioFisicoService(db).detalle(toma_id)


@router.post("/tomas/{toma_id}/lecturas", response_model=LecturaResultado, status_code=201,
             dependencies=[Depends(require_operativo)])
@limiter.limit("600/minute")
async def leer(toma_id: int, schema: LecturaCreate, request: Request, current_user: CurrentUser,
               db: AsyncSession = Depends(get_db)):
    return await InventarioFisicoService(db).leer(toma_id, schema, usuario_id=current_user.USU_Usuario)


@router.post("/tomas/{toma_id}/cerrar", response_model=TomaDetalle, dependencies=[Depends(require_operativo)])
async def cerrar_toma(
    toma_id: int, request: Request, current_user: CurrentUser,
    actualizar_ubicacion: bool = Query(False, description="Corrige el área de los encontrados en otra ubicación"),
    db: AsyncSession = Depends(get_db),
):
    return await InventarioFisicoService(db).cerrar(
        toma_id, actualizar_ubicacion=actualizar_ubicacion, **_ctx(request, current_user))


@router.post("/tomas/{toma_id}/anular", response_model=TomaResponse, dependencies=[Depends(require_admin)])
async def anular_toma(toma_id: int, request: Request, current_user: CurrentUser, db: AsyncSession = Depends(get_db)):
    return await InventarioFisicoService(db).anular(toma_id, **_ctx(request, current_user))


@router.get("/tomas/{toma_id}/reporte.csv", dependencies=[Depends(require_export)])
async def reporte_toma(
    toma_id: int, lang: str = Query("es", pattern="^(es|en|it)$"), db: AsyncSession = Depends(get_db),
):
    detalle = await InventarioFisicoService(db).detalle(toma_id)
    r = detalle["resumen"]
    etiquetas = {
        "es": ("Resultado", "Código", "Área", "FALTANTE", "OTRA UBICACIÓN", "OTRA SEDE", "DE BAJA", "NO REGISTRADO"),
        "en": ("Result", "Code", "Area", "MISSING", "OTHER LOCATION", "OTHER SITE", "DECOMMISSIONED", "NOT REGISTERED"),
        "it": ("Esito", "Codice", "Area", "MANCANTE", "ALTRA POSIZIONE", "ALTRA SEDE", "DISMESSO", "NON REGISTRATO"),
    }[lang]
    filas = []
    for clave, etiqueta in zip(("faltantes", "otra_ubicacion", "otra_sede", "de_baja", "no_registrados"), etiquetas[3:]):
        filas += [[etiqueta, i["codigo"], i.get("area") or ""] for i in r[clave]]
    nombre = {"es": "toma_inventario", "en": "inventory_count", "it": "inventario_fisico"}[lang]
    return _csv(csv_bytes(list(etiquetas[:3]), filas), f"{nombre}_{toma_id}.csv")
