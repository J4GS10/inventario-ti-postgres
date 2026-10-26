"""
Faltantes de la toma de inventario: lo que no apareció se investiga y se resuelve.

  PENDIENTE ──nota──▶ EN_INVESTIGACION
      │                    │
      ├──localizado───────▶ LOCALIZADO (apareció; también si se lee en otra toma)
      └──baja por pérdida─▶ BAJA_SOLICITADA ──aprobada──▶ DADO_DE_BAJA
                                      └──rechazada/anulada──▶ EN_INVESTIGACION

Cada paso queda en el seguimiento del faltante (quién, cuándo, qué), en la
bitácora y, si cambia la situación del equipo, en su kardex.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.transactional import transactional
from app.models.core import Activo
from app.models.inventario_fisico import Faltante
from app.models.location import Area
from app.models.organization import Usuario
from app.repositories.governance import GovernanceRepository
from app.services.kardex import KardexService

ABIERTOS = ("PENDIENTE", "EN_INVESTIGACION", "BAJA_SOLICITADA")


def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FaltanteService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.gov_repo = GovernanceRepository(db)

    async def _nota(self, f: Faltante, usuario_id, texto: str, accion: str) -> None:
        nombre = (await self.db.execute(select(Usuario.USU_Username).where(
            Usuario.USU_Usuario == usuario_id))).scalar() if usuario_id else None
        f.FAL_Seguimiento = [*(f.FAL_Seguimiento or []),
                             {"fecha": _ahora().isoformat(timespec="seconds"), "usuario": nombre,
                              "accion": accion, "nota": texto}]

    async def _get(self, faltante_id: int, *, lock: bool = True) -> Faltante:
        q = select(Faltante).where(Faltante.FAL_Faltante == faltante_id)
        f = (await self.db.execute(q.with_for_update() if lock else q)).scalar_one_or_none()
        if f is None:
            raise HTTPException(404, "MISSING_ITEM_NOT_FOUND")
        return f

    # ------------------------------------------------------------------
    async def registrar_de_toma(self, toma, faltantes: list[dict]) -> int:
        """Al cerrar una toma: un faltante abierto por equipo (no se duplica)."""
        abiertos = set((await self.db.execute(select(Faltante.ACT_Activo).where(
            Faltante.FAL_Estado.in_(ABIERTOS)))).scalars())
        nuevos = 0
        for item in faltantes:
            activo_id = uuid.UUID(item["activo"])
            if activo_id in abiertos:
                continue
            self.db.add(Faltante(TOM_Toma=toma.TOM_Toma, ACT_Activo=activo_id, SED_Sede=toma.SED_Sede,
                                 FAL_Estado="PENDIENTE", FAL_Fecha=_ahora()))
            nuevos += 1
        return nuevos

    async def resolver_por_lectura(self, activo_id: uuid.UUID, toma_id: int, usuario_id=None) -> None:
        """Un faltante que aparece en otra toma queda localizado."""
        for f in (await self.db.execute(select(Faltante).where(
                Faltante.ACT_Activo == activo_id, Faltante.FAL_Estado.in_(("PENDIENTE", "EN_INVESTIGACION"))))).scalars():
            f.FAL_Estado = "LOCALIZADO"
            f.FAL_Fecha_Resolucion = _ahora()
            f.FAL_Resolucion = f"Leído en la toma {toma_id}"
            await self._nota(f, usuario_id, f.FAL_Resolucion, "LOCALIZADO")

    async def listar(self, estado: str | None = None, abiertos: bool = False,
                     faltante_id: int | None = None) -> list[dict]:
        from app.models.catalogs import Modelo, TipoActivo
        q = (select(Faltante, Activo.ACT_Codigo_Interno, Activo.ACT_Serie_Fabricante, TipoActivo.TAC_Nombre,
                    Modelo.MOD_Nombre, Area.ARE_Nombre)
             .join(Activo, Activo.ACT_Activo == Faltante.ACT_Activo)
             .join(TipoActivo, TipoActivo.TAC_Tipo_Activo == Activo.TAC_Tipo_Activo)
             .join(Modelo, Modelo.MOD_Modelo == Activo.MOD_Modelo)
             .outerjoin(Area, Area.ARE_Area == Activo.ARE_Area_Ubicacion)
             .order_by(Faltante.FAL_Fecha.desc()))
        if faltante_id is not None:
            q = q.where(Faltante.FAL_Faltante == faltante_id)
        if estado:
            q = q.where(Faltante.FAL_Estado == estado)
        elif abiertos:
            q = q.where(Faltante.FAL_Estado.in_(ABIERTOS))
        return [{
            "FAL_Faltante": f.FAL_Faltante, "TOM_Toma": f.TOM_Toma, "ACT_Activo": str(f.ACT_Activo),
            "codigo": codigo, "serie": serie, "tipo": tipo, "modelo": modelo, "area": area,
            "FAL_Estado": f.FAL_Estado, "FAL_Fecha": f.FAL_Fecha, "FAL_Seguimiento": f.FAL_Seguimiento or [],
            "FAL_Resolucion": f.FAL_Resolucion, "FAL_Fecha_Resolucion": f.FAL_Fecha_Resolucion,
            "APR_Aprobacion": f.APR_Aprobacion, "SED_Sede": f.SED_Sede,
        } for f, codigo, serie, tipo, modelo, area in (await self.db.execute(q.limit(500))).all()]

    # ------------------------------------------------------------------
    @transactional
    async def seguimiento(self, faltante_id: int, nota: str, usuario_id=None, ip=None) -> dict:
        f = await self._get(faltante_id)
        if f.FAL_Estado not in ("PENDIENTE", "EN_INVESTIGACION"):
            raise HTTPException(409, "MISSING_ITEM_ALREADY_RESOLVED")
        f.FAL_Estado = "EN_INVESTIGACION"
        await self._nota(f, usuario_id, nota, "NOTA")
        await self.gov_repo.create_audit_log(
            "MISSING_ITEM_NOTE", "INV_FALTANTE", {"id": faltante_id, "nota": nota},
            usuario_id=usuario_id, ip_origen=ip, sede_id=f.SED_Sede)
        await self.db.flush()
        return (await self.listar_uno(faltante_id))

    @transactional
    async def localizar(self, faltante_id: int, nota: str, area_id: int | None, usuario_id=None, ip=None) -> dict:
        f = await self._get(faltante_id)
        if f.FAL_Estado not in ("PENDIENTE", "EN_INVESTIGACION"):
            raise HTTPException(409, "MISSING_ITEM_ALREADY_RESOLVED")
        activo = (await self.db.execute(select(Activo).where(Activo.ACT_Activo == f.ACT_Activo))).scalar_one()
        if area_id is not None:
            area = await self.db.get(Area, area_id)
            if area is None:
                raise HTTPException(404, "AREA_NOT_FOUND")
        origen_area = activo.ARE_Area_Ubicacion
        if area_id is not None and area_id != origen_area:
            await self.db.execute(update(Activo).where(Activo.ACT_Activo == activo.ACT_Activo).values(
                ARE_Area_Ubicacion=area_id).execution_options(synchronize_session="fetch"))
        await KardexService(self.db).registrar(
            "INVENTARIO_FISICO", activo=activo, usuario_id=usuario_id,
            origen={"sede": activo.SED_Sede, "area": origen_area},
            destino={"sede": activo.SED_Sede, "area": area_id if area_id is not None else origen_area},
            documento=("TOMA_INVENTARIO", str(f.TOM_Toma)) if f.TOM_Toma else None,
            motivo=f"Faltante localizado: {nota}", detalle={"resultado": "LOCALIZADO", "faltante": faltante_id},
        )
        f.FAL_Estado = "LOCALIZADO"
        f.FAL_Fecha_Resolucion = _ahora()
        f.FAL_Resolucion = nota
        await self._nota(f, usuario_id, nota, "LOCALIZADO")
        await self.gov_repo.create_audit_log(
            "MISSING_ITEM_FOUND", "INV_FALTANTE",
            {"id": faltante_id, "codigo": activo.ACT_Codigo_Interno, "nota": nota, "area": area_id},
            usuario_id=usuario_id, ip_origen=ip, sede_id=f.SED_Sede)
        await self.db.flush()
        return await self.listar_uno(faltante_id)

    @transactional
    async def solicitar_baja(self, faltante_id: int, schema, usuario_id=None, ip=None) -> dict:
        """Baja por pérdida o robo: va a aprobación como cualquier baja."""
        from app.services.aprobaciones import AprobacionService
        f = await self._get(faltante_id)
        if f.FAL_Estado not in ("PENDIENTE", "EN_INVESTIGACION"):
            raise HTTPException(409, "MISSING_ITEM_ALREADY_RESOLVED")
        if schema.motivo not in ("PERDIDA", "ROBO"):
            raise HTTPException(422, "MISSING_ITEM_DECOMMISSION_REASON")
        solicitud = await AprobacionService(self.db).solicitar_baja(f.ACT_Activo, schema, usuario_id=usuario_id, ip=ip)
        f.FAL_Estado = "BAJA_SOLICITADA"
        f.APR_Aprobacion = solicitud["APR_Aprobacion"]
        await self._nota(f, usuario_id, schema.observacion or schema.motivo, "BAJA_SOLICITADA")
        await self.db.flush()
        return await self.listar_uno(faltante_id)

    async def al_resolver_baja(self, aprobacion_id: int, *, aprobada: bool) -> None:
        for f in (await self.db.execute(select(Faltante).where(
                Faltante.APR_Aprobacion == aprobacion_id, Faltante.FAL_Estado == "BAJA_SOLICITADA"))).scalars():
            if aprobada:
                f.FAL_Estado = "DADO_DE_BAJA"
                f.FAL_Fecha_Resolucion = _ahora()
                f.FAL_Resolucion = f"Baja aprobada (solicitud {aprobacion_id})"
            else:
                f.FAL_Estado = "EN_INVESTIGACION"
                f.APR_Aprobacion = None
            await self._nota(f, None, f"Solicitud de baja {aprobacion_id} {'aprobada' if aprobada else 'no aprobada'}",
                             "DADO_DE_BAJA" if aprobada else "BAJA_NO_APROBADA")

    async def listar_uno(self, faltante_id: int) -> dict:
        filas = await self.listar(faltante_id=faltante_id)
        if not filas:
            raise HTTPException(404, "MISSING_ITEM_NOT_FOUND")
        return filas[0]
