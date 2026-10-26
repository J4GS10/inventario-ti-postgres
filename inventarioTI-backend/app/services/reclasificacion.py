"""
Reclasificación de insumos registrados como activos (aire comprimido, pasta
térmica, limpia contactos…) a un consumible controlado por cantidad.

Se hace por tipo y sede, con aprobación (AprobacionService.solicitar_reclasificacion):
  1. Vista previa: cuántos activos se convierten y cuáles lo impiden (asignados,
     instalados, en mantenimiento o dentro de otro equipo).
  2. Al aprobarse: cada activo queda de baja con motivo RECLASIFICACION y
     destino CONSUMIBLES (evento RECLASIFICACION en su kardex) y el consumible
     recibe una entrada por la misma cantidad, valorizada al costo promedio de
     los activos si todos tienen costo en la misma moneda.
Nada se borra: la historia de cada activo sigue consultable.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clases_activo import INSUMO, reglas
from app.core.transactional import transactional
from app.models.catalogs import EstadoOperativo, TipoActivo
from app.models.consumable import Consumible
from app.models.core import Activo
from app.models.location import Sede
from app.models.traceability import Mantenimiento, Movimiento
from app.repositories.governance import GovernanceRepository
from app.services.kardex import KardexService


class ReclasificacionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.gov_repo = GovernanceRepository(db)

    async def _baja_id(self) -> int | None:
        return (await self.db.execute(select(EstadoOperativo.EOP_Estado_Operativo).where(
            EstadoOperativo.EOP_Nombre.ilike("Baja")))).scalar()

    async def _activos(self, tipo_id: int, sede: int | None = None) -> list[Activo]:
        q = select(Activo).where(Activo.TAC_Tipo_Activo == tipo_id, Activo.EOP_Estado_Operativo != await self._baja_id())
        if sede is not None:
            q = q.where(Activo.SED_Sede == sede)
        return list((await self.db.execute(q.order_by(Activo.ACT_Codigo_Interno))).scalars())

    async def _bloqueo(self, activo: Activo) -> str | None:
        if activo.ACT_Activo_Padre is not None:
            return "INSTALADO_EN_EQUIPO"
        if activo.ACT_Instalado:
            return "INSTALADO_EN_AREA"
        if (await self.db.execute(select(Movimiento.MOV_Movimiento).where(
                Movimiento.ACT_Activo == activo.ACT_Activo, Movimiento.MOV_Fecha_Devolucion.is_(None)))).first():
            return "ASIGNADO"
        if (await self.db.execute(select(Mantenimiento.MAN_Mantenimiento).where(
                Mantenimiento.ACT_Activo == activo.ACT_Activo, Mantenimiento.MAN_Fecha_Cierre.is_(None)))).first():
            return "EN_MANTENIMIENTO"
        return None

    async def vista_previa(self, tipo_id: int, sede: int | None = None) -> dict:
        tipo = await self.db.get(TipoActivo, tipo_id)
        if tipo is None:
            raise HTTPException(404, "ASSET_TYPE_NOT_FOUND")
        if reglas(tipo.TAC_Clase)["uso"] != INSUMO:
            raise HTTPException(409, "ASSET_TYPE_IS_NOT_SUPPLY")
        activos = await self._activos(tipo_id, sede)
        bloqueados, por_sede = [], {}
        for a in activos:
            motivo = await self._bloqueo(a)
            if motivo:
                bloqueados.append({"codigo": a.ACT_Codigo_Interno, "motivo": motivo})
            por_sede[a.SED_Sede] = por_sede.get(a.SED_Sede, 0) + 1
        nombres = dict((await self.db.execute(select(Sede.SED_Sede, Sede.SED_Nombre).where(
            Sede.SED_Sede.in_([s for s in por_sede if s is not None])))).all()) if por_sede else {}
        return {
            "tipo": tipo.TAC_Nombre, "activos": len(activos), "bloqueados": bloqueados,
            "por_sede": [{"SED_Sede": s, "sede": nombres.get(s), "activos": n} for s, n in por_sede.items()],
        }

    @transactional
    async def ejecutar(self, payload: dict, *, usuario_id=None, ip=None, aprobacion: dict | None = None) -> dict:
        from app.schemas.consumable import StockMovimientoCreate
        from app.services.consumable import ConsumableService
        if aprobacion is None:
            raise HTTPException(409, "RECLASSIFY_REQUIRES_APPROVAL")
        tipo_id, sede = payload["tipo_id"], payload["SED_Sede"]
        tipo = await self.db.get(TipoActivo, tipo_id)
        activos = await self._activos(tipo_id, sede)
        if not activos:
            raise HTTPException(409, "RECLASSIFY_NOTHING_TO_CONVERT")
        for a in activos:
            if await self._bloqueo(a):
                raise HTTPException(409, "RECLASSIFY_ASSETS_IN_USE")

        # Consumible destino: el que ya existe con ese nombre en la sede, o uno nuevo.
        nombre = payload["nombre"].strip()
        consumible = (await self.db.execute(select(Consumible).where(
            func.lower(Consumible.CON_Nombre) == nombre.lower()))).scalar_one_or_none()
        if consumible is not None and consumible.SED_Sede != sede:
            raise HTTPException(409, "CONSUMABLE_NAME_IN_OTHER_SEDE")
        if consumible is None:
            consumible = Consumible(
                CON_Nombre=nombre, CON_Unidad=payload.get("CON_Unidad") or "unidad", CON_Stock_Actual=0,
                CON_Stock_Minimo=payload.get("CON_Stock_Minimo") or 0, CCO_Categoria=payload.get("CCO_Categoria"),
                UME_Unidad=payload.get("UME_Unidad"), SED_Sede=sede, CON_Activo=True,
            )
            self.db.add(consumible)
            await self.db.flush()
            await self.gov_repo.create_audit_log(
                "CREATE", "INV_CONSUMIBLE", {"id": consumible.CON_Consumible, "nombre": nombre, "origen": "RECLASIFICACION"},
                usuario_id=usuario_id, ip_origen=ip, sede_id=sede,
            )

        # Valor de la entrada: costo promedio si todos tienen costo en la misma moneda.
        costos = [(a.ACT_Costo, a.ACT_Moneda) for a in activos]
        monedas = {m for c, m in costos if c is not None}
        precio = moneda = None
        if all(c is not None for c, _ in costos) and len(monedas) == 1:
            precio = (sum((Decimal(c) for c, _ in costos), Decimal(0)) / len(costos)).quantize(Decimal("0.01"))
            moneda = monedas.pop()
        if consumible.CON_Moneda and moneda and moneda != consumible.CON_Moneda:
            precio = moneda = None  # otra moneda: entra sin valorizar (no se convierte)

        baja = await self._baja_id()
        documento = f"APR-{aprobacion.get('aprobacion')}"
        kardex = KardexService(self.db)
        for a in activos:
            await self.db.execute(update(Activo).where(Activo.ACT_Activo == a.ACT_Activo).values(
                EOP_Estado_Operativo=baja, ACT_Baja_Motivo="RECLASIFICACION", ACT_Baja_Destino="CONSUMIBLES",
                ACT_Baja_Fecha=date.today(), ACT_Baja_Documento=documento,
                ACT_Baja_Observacion=f"Pasa al consumible «{nombre}»",
            ).execution_options(synchronize_session="fetch"))
            await kardex.registrar(
                "RECLASIFICACION", activo=a, usuario_id=usuario_id, origen={"sede": a.SED_Sede, "area": a.ARE_Area_Ubicacion},
                costo=a.ACT_Costo, moneda=a.ACT_Moneda, documento=("APROBACION", str(aprobacion.get("aprobacion"))),
                motivo=f"Reclasificado como consumible «{nombre}»: {payload.get('motivo')}",
                detalle={"consumible": consumible.CON_Consumible,
                         "aprobacion": {k: str(v) for k, v in aprobacion.items()}},
            )
        await ConsumableService(self.db).registrar_entrada(
            consumible.CON_Consumible,
            StockMovimientoCreate(MOC_Cantidad=len(activos), precio_unitario=precio, moneda=moneda,
                                  MOC_Motivo=f"Reclasificación de {len(activos)} activos «{tipo.TAC_Nombre}»"),
            usuario_id=usuario_id, ip=ip,
        )
        await self.gov_repo.create_audit_log(
            "RECLASSIFY_ASSETS", "INV_ACTIVO",
            {"tipo": tipo.TAC_Nombre, "consumible": nombre, "cantidad": len(activos),
             "codigos": [a.ACT_Codigo_Interno for a in activos][:200],
             "aprobacion": {k: str(v) for k, v in aprobacion.items()}},
            usuario_id=usuario_id, ip_origen=ip, sede_id=sede,
        )
        return {"consumible": consumible.CON_Consumible, "cantidad": len(activos)}
