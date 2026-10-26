"""
Edición en lote de activos (plan de licencias y catálogos, bloque C §2).

Cambia para una selección de activos la ubicación (área), el costo y su
moneda, el fin de garantía, el proveedor y la factura, y sus
especificaciones. Todo en una transacción: si un activo no admite el cambio,
nada se aplica y se informa cuál lo impidió (BATCH_ITEM_FAILED:<código>:<motivo>).

Cada activo deja su evento en el kardex (CAMBIO_ESTADO para los datos,
ESPECIFICACION para las especificaciones) y la respuesta trae los valores
anteriores: con ellos la interfaz ofrece «Deshacer» (POST …/lote/restaurar).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import area_sede_id
from app.core.transactional import transactional
from app.models.catalogo_estandar import EspecificacionTipoActivo
from app.models.catalogs import TipoEspecificacion
from app.models.core import Activo, Especificacion
from app.models.location import Area
from app.models.procurement import Proveedor
from app.models.traceability import Movimiento
from app.repositories.governance import GovernanceRepository
from app.schemas.core import CambiosLote
from app.services.kardex import KardexService

CAMPOS = ("ARE_Area_Ubicacion", "ACT_Costo", "ACT_Moneda", "ACT_Fin_Garantia", "PRV_Proveedor", "ACT_Factura")


def _serializable(valor):
    if isinstance(valor, Decimal):
        return str(valor)
    if hasattr(valor, "isoformat"):
        return valor.isoformat()
    return valor


class EdicionLoteService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.gov_repo = GovernanceRepository(db)

    async def _validar(self, activo: Activo, cambios: CambiosLote, campos: set[str]) -> None:
        if "ARE_Area_Ubicacion" in campos and cambios.ARE_Area_Ubicacion is not None:
            if await self.db.get(Area, cambios.ARE_Area_Ubicacion) is None:
                raise HTTPException(404, "AREA_NOT_FOUND")
            if activo.SED_Sede is not None and await area_sede_id(self.db, cambios.ARE_Area_Ubicacion) != activo.SED_Sede:
                raise HTTPException(400, "ASSET_AREA_OTHER_SITE")
            if (await self.db.execute(select(Movimiento.MOV_Movimiento).where(
                    Movimiento.ACT_Activo == activo.ACT_Activo, Movimiento.MOV_Fecha_Devolucion.is_(None)))).first():
                # Asignado: su ubicación la da la custodia (se cambia transfiriéndolo).
                raise HTTPException(400, "ASSET_LOCATION_FOLLOWS_CUSTODY")
        if "ACT_Fin_Garantia" in campos and cambios.ACT_Fin_Garantia and activo.ACT_Fecha_Compra \
                and cambios.ACT_Fin_Garantia < activo.ACT_Fecha_Compra:
            raise HTTPException(400, "WARRANTY_DATE_CANNOT_BE_BEFORE_PURCHASE_DATE")
        if "PRV_Proveedor" in campos and cambios.PRV_Proveedor is not None \
                and await self.db.get(Proveedor, cambios.PRV_Proveedor) is None:
            raise HTTPException(404, "SUPPLIER_NOT_FOUND")

    async def _especificaciones(self, activo: Activo, cambios: CambiosLote, kardex: KardexService,
                                usuario_id) -> list[dict]:
        """Aplica las especificaciones y devuelve sus valores anteriores."""
        if not cambios.especificaciones:
            return []
        permitidos = {
            e.TES_Tipo_Especificacion: e.ETA_Valores or [] for e in (await self.db.execute(
                select(EspecificacionTipoActivo).where(EspecificacionTipoActivo.TAC_Tipo_Activo == activo.TAC_Tipo_Activo)
            )).scalars()
        }
        anteriores, detalle = [], []
        for esp in cambios.especificaciones:
            if await self.db.get(TipoEspecificacion, esp.TES_Tipo_Especificacion) is None:
                raise HTTPException(404, "SPECIFICATION_TYPE_NOT_FOUND")
            valor = (esp.valor or "").strip() or None
            lista = permitidos.get(esp.TES_Tipo_Especificacion) or []
            if valor and lista and valor not in lista:
                raise HTTPException(422, "SPECIFICATION_VALUE_NOT_ALLOWED")
            actual = (await self.db.execute(select(Especificacion).where(
                Especificacion.ACT_Activo == activo.ACT_Activo,
                Especificacion.TES_Tipo_Especificacion == esp.TES_Tipo_Especificacion,
            ))).scalar_one_or_none()
            antes = actual.ESP_Valor if actual else None
            if antes == valor:
                continue
            anteriores.append({"TES_Tipo_Especificacion": esp.TES_Tipo_Especificacion, "valor": antes})
            detalle.append({"tipo": esp.TES_Tipo_Especificacion, "antes": antes, "despues": valor})
            if valor is None and actual is not None:
                await self.db.delete(actual)
            elif actual is not None:
                actual.ESP_Valor = valor
            elif valor is not None:
                self.db.add(Especificacion(ACT_Activo=activo.ACT_Activo,
                                           TES_Tipo_Especificacion=esp.TES_Tipo_Especificacion, ESP_Valor=valor))
        if detalle:
            await self.db.flush()
            await kardex.registrar("ESPECIFICACION", activo=activo, usuario_id=usuario_id,
                                   motivo="Edición en lote", detalle={"cambios": detalle})
        return anteriores

    @transactional
    async def aplicar(self, items: list[tuple[uuid.UUID, CambiosLote]], *, usuario_id=None, ip=None,
                      accion: str = "UPDATE_BATCH") -> dict:
        kardex = KardexService(self.db)
        anteriores: list[dict] = []
        codigos: list[str] = []
        for activo_id, cambios in items:
            activo = await self.db.get(Activo, activo_id)
            if activo is None:
                raise HTTPException(404, f"BATCH_ITEM_FAILED:{activo_id}:ASSET_NOT_FOUND")
            campos = cambios.model_fields_set & set(CAMPOS)
            try:
                await self._validar(activo, cambios, campos)
                previos = {c: getattr(activo, c) for c in campos}
                diferencias = {c: [_serializable(previos[c]), _serializable(getattr(cambios, c))]
                               for c in campos if previos[c] != getattr(cambios, c)}
                for c in campos:
                    setattr(activo, c, getattr(cambios, c))
                if diferencias:
                    await self.db.flush()
                    await kardex.registrar("CAMBIO_ESTADO", activo=activo, usuario_id=usuario_id,
                                           motivo="Edición en lote", detalle={"campos": diferencias})
                specs_previas = await self._especificaciones(activo, cambios, kardex, usuario_id)
            except HTTPException as exc:
                raise HTTPException(
                    exc.status_code, f"BATCH_ITEM_FAILED:{activo.ACT_Codigo_Interno}:{str(exc.detail).split(':')[0]}",
                ) from exc
            codigos.append(activo.ACT_Codigo_Interno)
            previo = {c: _serializable(v) for c, v in previos.items() if c in diferencias}
            if specs_previas:
                previo["especificaciones"] = specs_previas
            anteriores.append({"ACT_Activo": str(activo_id), "codigo": activo.ACT_Codigo_Interno, "cambios": previo})
        await self.gov_repo.create_audit_log(
            accion, "INV_ACTIVO",
            {"activos": codigos, "campos": sorted({c for _i, ch in items for c in ch.model_fields_set})},
            usuario_id=usuario_id, ip_origen=ip,
        )
        return {"total": len(codigos), "anteriores": anteriores}
