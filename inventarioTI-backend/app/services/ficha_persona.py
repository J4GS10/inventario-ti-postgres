"""
Ficha 360 de una persona (plan de licencias y catálogos, bloque C §3): en una
sola consulta, sus datos, los equipos que tiene en custodia, sus licencias y
cuentas, los consumibles que recibió y su historial en el kardex.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalogs import TipoActivo
from app.models.consumable import Consumible, MovimientoConsumible
from app.models.core import Activo
from app.models.kardex import Kardex
from app.models.location import Area, Sede
from app.models.organization import Cargo, Departamento, Persona
from app.models.software import Instalacion, Licencia, LicenciaClave, Software
from app.models.traceability import Movimiento, TipoMovimiento


def _nombre(p: Persona | None) -> str | None:
    if p is None:
        return None
    return " ".join(x for x in (p.PER_Primer_Nombre, p.PER_Segundo_Nombre, p.PER_Primer_Apellido,
                                p.PER_Segundo_Apellido) if x)


class FichaPersonaService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def ficha(self, persona_id: uuid.UUID) -> dict:
        persona = await self.db.get(Persona, persona_id)
        if persona is None:
            raise HTTPException(404, "PERSON_NOT_FOUND")
        departamento = await self.db.get(Departamento, persona.DEP_Departamento) if persona.DEP_Departamento else None
        cargo = await self.db.get(Cargo, persona.CAR_Cargo) if persona.CAR_Cargo else None
        jefe = await self.db.get(Persona, persona.PER_Jefe) if persona.PER_Jefe else None
        sede = await self.db.get(Sede, persona.SED_Sede) if persona.SED_Sede else None

        equipos = [
            {"MOV_Movimiento": str(m.MOV_Movimiento), "ACT_Activo": str(a.ACT_Activo), "codigo": a.ACT_Codigo_Interno,
             "hostname": a.ACT_Hostname, "tipo": tipo, "movimiento": tmo, "area": area,
             "desde": m.MOV_Fecha_Asignacion, "devolucion_prevista": m.MOV_Fecha_Prevista_Devolucion}
            for m, a, tipo, tmo, area in (await self.db.execute(
                select(Movimiento, Activo, TipoActivo.TAC_Nombre, TipoMovimiento.TMO_Nombre, Area.ARE_Nombre)
                .join(Activo, Activo.ACT_Activo == Movimiento.ACT_Activo)
                .join(TipoActivo, TipoActivo.TAC_Tipo_Activo == Activo.TAC_Tipo_Activo, isouter=True)
                .join(TipoMovimiento, TipoMovimiento.TMO_Tipo_Movimiento == Movimiento.TMO_Tipo_Movimiento, isouter=True)
                .join(Area, Area.ARE_Area == Movimiento.ARE_Area, isouter=True)
                .where(Movimiento.PER_Persona == persona_id, Movimiento.MOV_Fecha_Devolucion.is_(None))
                .order_by(Movimiento.MOV_Fecha_Asignacion.desc())
            )).all()
        ]

        licencias = [
            {"INS_Instalacion": i.INS_Instalacion, "LIC_Licencia": i.LIC_Licencia,
             "software": " ".join(x for x in (sw.SOF_Nombre, sw.SOF_Version) if x),
             "cuenta": cred.LCL_Usuario if cred else None, "LCL_Licencia_Clave": i.LCL_Licencia_Clave,
             "desde": i.INS_Fecha_Instalacion, "vence": lic.LIC_Fecha_Vencimiento}
            for i, lic, sw, cred in (await self.db.execute(
                select(Instalacion, Licencia, Software, LicenciaClave)
                .join(Licencia, Licencia.LIC_Licencia == Instalacion.LIC_Licencia)
                .join(Software, Software.SOF_Software == Licencia.SOF_Software)
                .join(LicenciaClave, LicenciaClave.LCL_Licencia_Clave == Instalacion.LCL_Licencia_Clave, isouter=True)
                .where(Instalacion.PER_Persona == persona_id, Instalacion.INS_Estado.is_(True))
                .order_by(Software.SOF_Nombre)
            )).all()
        ]

        consumibles = [
            {"MOC_Movimiento": m.MOC_Movimiento, "nombre": nombre, "cantidad": m.MOC_Cantidad, "unidad": unidad,
             "fecha": m.MOC_Fecha, "motivo": m.MOC_Motivo}
            for m, nombre, unidad in (await self.db.execute(
                select(MovimientoConsumible, Consumible.CON_Nombre, Consumible.CON_Unidad)
                .join(Consumible, Consumible.CON_Consumible == MovimientoConsumible.CON_Consumible)
                .where(MovimientoConsumible.PER_Persona == persona_id, MovimientoConsumible.MOC_Tipo == "SALIDA")
                .order_by(MovimientoConsumible.MOC_Fecha.desc()).limit(50)
            )).all()
        ]

        historial = [
            {"KDX_Evento": k.KDX_Evento, "fecha": k.KDX_Fecha, "tipo": k.KDX_Tipo, "objeto": k.KDX_Objeto,
             "referencia": k.KDX_Referencia, "motivo": k.KDX_Motivo,
             "sentido": "RECIBE" if k.KDX_Destino_Persona == persona_id else "ENTREGA"}
            for k in (await self.db.execute(
                select(Kardex).where(or_(Kardex.KDX_Destino_Persona == persona_id, Kardex.KDX_Origen_Persona == persona_id))
                .order_by(Kardex.KDX_Fecha.desc(), Kardex.KDX_Evento.desc()).limit(100)
            )).scalars()
        ]

        return {
            "persona": {
                "PER_Persona": str(persona.PER_Persona), "nombre": _nombre(persona),
                "PER_Email_Corporativo": persona.PER_Email_Corporativo, "PER_Telefono": persona.PER_Telefono,
                "PER_Estado": persona.PER_Estado, "departamento": departamento.DEP_Nombre if departamento else None,
                "cargo": cargo.CAR_Nombre if cargo else None, "jefe": _nombre(jefe),
                "PER_Jefe": str(persona.PER_Jefe) if persona.PER_Jefe else None,
                "sede": sede.SED_Nombre if sede else None, "SED_Sede": persona.SED_Sede,
            },
            "equipos": equipos, "licencias": licencias, "consumibles": consumibles, "historial": historial,
        }
