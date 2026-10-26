"""
Equipos compartidos instalados en un área (impresora, switch, UPS, proyector).

No son custodia de una persona: se instalan en un área con un responsable
opcional (quien reporta fallas o recibe el tóner). Flujo:

  bodega/disponible ──instalar──▶ Instalado (área + responsable)
  Instalado ──instalar en otra área──▶ Instalado (reubicación)
  Instalado ──retirar──▶ En Bodega (área de bodega)

Cada paso exige un motivo del catálogo (aplica a INSTALACION o RETIRO), queda
en el kardex (INSTALACION_AREA / RETIRO_AREA) y en la bitácora. Un equipo que
entra a mantenimiento vuelve a «Instalado» al cerrar el ticket.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.transactional import transactional
from app.models.catalogs import EstadoOperativo
from app.models.core import Activo
from app.models.enums import EstadoOperativoEnum
from app.models.location import Area
from app.models.organization import Persona
from app.repositories.governance import GovernanceRepository
from app.services.clases import exigir_instalable_en_area
from app.services.kardex import KardexService
from app.services.motivos import con_motivo
from app.services.state_machine import AssetStateMachine, estado_enum_de_nombre


def _nombre(p: Persona | None) -> str | None:
    return f"{p.PER_Primer_Nombre} {p.PER_Primer_Apellido}" if p else None


class InstalacionAreaService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.gov_repo = GovernanceRepository(db)

    async def _estado(self, estado: EstadoOperativoEnum) -> int:
        eid = (await self.db.execute(select(EstadoOperativo.EOP_Estado_Operativo).where(
            EstadoOperativo.EOP_Nombre.ilike(estado.value)))).scalar()
        if eid is None:
            raise HTTPException(500, detail="SYSTEM_CONFIG_ERROR_MISSING_OPERATIONAL_STATE")
        return eid

    async def _activo(self, activo_id: uuid.UUID) -> tuple[Activo, EstadoOperativoEnum | None]:
        activo = (await self.db.execute(
            select(Activo).where(Activo.ACT_Activo == activo_id).with_for_update())).scalar_one_or_none()
        if activo is None:
            raise HTTPException(404, detail="ASSET_NOT_FOUND")
        nombre = (await self.db.execute(select(EstadoOperativo.EOP_Nombre).where(
            EstadoOperativo.EOP_Estado_Operativo == activo.EOP_Estado_Operativo))).scalar()
        return activo, estado_enum_de_nombre(nombre)

    @transactional
    async def instalar(self, schema, usuario_id=None, ip=None) -> dict:
        from app.services.traceability import TraceabilityService
        trazas = TraceabilityService(self.db)
        activo, estado = await self._activo(schema.ACT_Activo)
        await exigir_instalable_en_area(self.db, activo)
        if estado not in (EstadoOperativoEnum.DISPONIBLE, EstadoOperativoEnum.BODEGA, EstadoOperativoEnum.INSTALADO):
            raise HTTPException(409, detail="ASSET_NOT_AVAILABLE_FOR_INSTALLATION")
        AssetStateMachine.validate_transition(estado, EstadoOperativoEnum.INSTALADO)
        await trazas._exigir_activo_operable(activo)
        if await trazas.repo.get_movimiento_vigente(activo.ACT_Activo):
            raise HTTPException(409, detail="ASSET_HAS_CUSTODIAN_RETURN_FIRST")
        area = await self.db.get(Area, schema.ARE_Area)
        if area is None:
            raise HTTPException(404, detail="AREA_NOT_FOUND")
        if estado == EstadoOperativoEnum.INSTALADO and activo.ARE_Area_Ubicacion == area.ARE_Area \
                and activo.ACT_Responsable == schema.PER_Responsable:
            raise HTTPException(409, detail="ASSET_ALREADY_INSTALLED_THERE")
        responsable = None
        if schema.PER_Responsable is not None:
            responsable = await self.db.get(Persona, schema.PER_Responsable)
            if responsable is None:
                raise HTTPException(404, detail="PERSON_NOT_FOUND")
            trazas._exigir_persona_activa(responsable)
        schema = await con_motivo(self.db, schema, "INSTALACION")
        sede_destino = await trazas._sede_de_destino(area.ARE_Area)
        await trazas._ubicar_en_sede(activo, None, sede_destino, usuario_id=usuario_id, ip=ip)

        origen = {"area": activo.ARE_Area_Ubicacion, "sede": activo.SED_Sede,
                  "persona": activo.ACT_Responsable}
        reubicacion = estado == EstadoOperativoEnum.INSTALADO
        await self.db.execute(update(Activo).where(Activo.ACT_Activo == activo.ACT_Activo).values(
            EOP_Estado_Operativo=await self._estado(EstadoOperativoEnum.INSTALADO), ARE_Area_Ubicacion=area.ARE_Area,
            ACT_Instalado=True, ACT_Responsable=schema.PER_Responsable,
        ).execution_options(synchronize_session="fetch"))
        # Los componentes instalados en el equipo lo siguen.
        await self.db.execute(update(Activo).where(Activo.ACT_Activo_Padre == activo.ACT_Activo).values(
            ARE_Area_Ubicacion=area.ARE_Area, SED_Sede=sede_destino).execution_options(synchronize_session="fetch"))
        await KardexService(self.db).registrar(
            "INSTALACION_AREA", activo=activo, usuario_id=usuario_id,
            origen=origen if reubicacion else {"area": origen["area"], "sede": origen["sede"]},
            destino={"area": area.ARE_Area, "sede": sede_destino, "persona": schema.PER_Responsable},
            motivo=schema.MOV_Motivo,
            detalle={"responsable": _nombre(responsable), "reubicacion": reubicacion},
        )
        await self.gov_repo.create_audit_log(
            "ASSET_INSTALL_AREA", "INV_ACTIVO",
            {"activo": str(activo.ACT_Activo), "codigo": activo.ACT_Codigo_Interno, "area": area.ARE_Nombre,
             "responsable": _nombre(responsable), "motivo": schema.MOV_Motivo, "reubicacion": reubicacion},
            usuario_id=usuario_id, ip_origen=ip, sede_id=sede_destino,
        )
        await self.db.flush()
        return await self.estado(activo.ACT_Activo)

    @transactional
    async def retirar(self, schema, usuario_id=None, ip=None) -> dict:
        from app.services.traceability import TraceabilityService
        trazas = TraceabilityService(self.db)
        activo, estado = await self._activo(schema.ACT_Activo)
        if estado != EstadoOperativoEnum.INSTALADO:
            raise HTTPException(409, detail="ASSET_NOT_INSTALLED")
        await trazas._exigir_activo_operable(activo)
        destino_area = schema.ARE_Area_Destino
        if destino_area is not None:
            if await self.db.get(Area, destino_area) is None:
                raise HTTPException(404, detail="AREA_NOT_FOUND")
            sede_destino = await trazas._sede_de_destino(destino_area)
            await trazas._ubicar_en_sede(activo, None, sede_destino, usuario_id=usuario_id, ip=ip)
        else:
            sede_destino = activo.SED_Sede
        schema = await con_motivo(self.db, schema, "RETIRO")
        origen = {"area": activo.ARE_Area_Ubicacion, "sede": activo.SED_Sede, "persona": activo.ACT_Responsable}
        await self.db.execute(update(Activo).where(Activo.ACT_Activo == activo.ACT_Activo).values(
            EOP_Estado_Operativo=await self._estado(EstadoOperativoEnum.BODEGA),
            ARE_Area_Ubicacion=destino_area if destino_area is not None else activo.ARE_Area_Ubicacion,
            ACT_Instalado=False, ACT_Responsable=None,
        ).execution_options(synchronize_session="fetch"))
        await KardexService(self.db).registrar(
            "RETIRO_AREA", activo=activo, usuario_id=usuario_id, origen=origen,
            destino={"area": destino_area, "sede": sede_destino}, motivo=schema.MOV_Motivo,
            detalle={"condicion": schema.condicion} if schema.condicion else None,
        )
        await self.gov_repo.create_audit_log(
            "ASSET_REMOVE_AREA", "INV_ACTIVO",
            {"activo": str(activo.ACT_Activo), "codigo": activo.ACT_Codigo_Interno, "motivo": schema.MOV_Motivo,
             "condicion": schema.condicion},
            usuario_id=usuario_id, ip_origen=ip, sede_id=activo.SED_Sede,
        )
        await self.db.flush()
        return await self.estado(activo.ACT_Activo)

    async def estado(self, activo_id: uuid.UUID) -> dict:
        fila = (await self.db.execute(
            select(Activo, Area.ARE_Nombre, Persona)
            .outerjoin(Area, Area.ARE_Area == Activo.ARE_Area_Ubicacion)
            .outerjoin(Persona, Persona.PER_Persona == Activo.ACT_Responsable)
            .where(Activo.ACT_Activo == activo_id)
        )).first()
        if fila is None:
            raise HTTPException(404, detail="ASSET_NOT_FOUND")
        activo, area, responsable = fila
        return {
            "ACT_Activo": str(activo.ACT_Activo), "ACT_Codigo_Interno": activo.ACT_Codigo_Interno,
            "ACT_Instalado": activo.ACT_Instalado, "ARE_Area": activo.ARE_Area_Ubicacion, "area": area,
            "PER_Responsable": str(activo.ACT_Responsable) if activo.ACT_Responsable else None,
            "responsable": _nombre(responsable),
        }
