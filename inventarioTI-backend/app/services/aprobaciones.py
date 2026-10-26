"""
Aprobaciones del Jefe de TI (separación de funciones).

Flujo:
  solicitar → PENDIENTE → aprobar (ejecuta la operación tal como se pidió,
  revalidando las reglas) → APROBADA
                        → rechazar (con motivo) → RECHAZADA
                        → anular (solo quien la pidió) → ANULADA

Qué pasa por aprobación (toda salida o pérdida de inventario):
  BAJA                 baja de un activo (también la de un faltante por pérdida o robo)
  RECEPCION_SOBRANTE   recibir más de lo pedido en una orden
  AJUSTE_CONSUMIBLE    un conteo que DISMINUYE la existencia de un consumible
  CIERRE_FALTANTE      cerrar una orden parcial aceptando que lo pendiente no llegará
  CANCELACION_ORDEN    cancelar una orden de compra
  RECLASIFICACION      convertir los activos de un tipo «insumo» en consumible

Reglas:
- Aprueban JEFE_TI o SUPER_ADMIN (lo exige el endpoint).
- Nadie aprueba su propia solicitud (aquí y con un CHECK en la base).
- Si al aprobar la operación ya no es válida (el equipo se asignó, la orden
  cambió…), no se ejecuta nada y la solicitud sigue pendiente para rechazarla.
- Cada paso queda en la bitácora de auditoría; la operación ejecutada queda en
  el kardex con quién la solicitó y quién la aprobó.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.transactional import transactional
from app.models.aprobacion import Aprobacion
from app.models.organization import Persona, Usuario
from app.repositories.governance import GovernanceRepository


def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AprobacionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.gov_repo = GovernanceRepository(db)

    # ------------------------------------------------------------------
    # Solicitudes
    # ------------------------------------------------------------------
    async def crear(self, tipo: str, *, payload: dict, solicitante: uuid.UUID | None, resumen: str,
                    motivo: str | None = None, activo_id: uuid.UUID | None = None,
                    orden_id: int | None = None, consumible_id: int | None = None,
                    sede: int | None = None, ip=None) -> Aprobacion:
        if solicitante is None:
            raise HTTPException(400, "APPROVAL_REQUIRES_REQUESTER")
        solicitud = Aprobacion(
            APR_Tipo=tipo, APR_Estado="PENDIENTE", APR_Payload=payload, APR_Resumen=resumen[:500],
            APR_Motivo=(motivo or None) and motivo[:500], ACT_Activo=activo_id, OCO_Orden=orden_id,
            CON_Consumible=consumible_id, SED_Sede=sede, APR_Solicitante=solicitante,
        )
        self.db.add(solicitud)
        await self.db.flush()
        await self.gov_repo.create_audit_log(
            "APPROVAL_REQUEST", "INV_APROBACION",
            {"id": solicitud.APR_Aprobacion, "tipo": tipo, "resumen": resumen},
            usuario_id=solicitante, ip_origen=ip, sede_id=sede,
        )
        return solicitud

    @transactional
    async def solicitar_baja(self, activo_id: uuid.UUID, schema, usuario_id=None, ip=None) -> dict:
        """La baja se valida ahora y se ejecuta cuando el Jefe de TI la aprueba."""
        from app.services.core import CoreService
        activo, _, _ = await CoreService(self.db).validar_baja(activo_id, schema)
        pendiente = (await self.db.execute(select(Aprobacion.APR_Aprobacion).where(
            Aprobacion.ACT_Activo == activo_id, Aprobacion.APR_Tipo == "BAJA",
            Aprobacion.APR_Estado == "PENDIENTE"))).first()
        if pendiente:
            raise HTTPException(409, f"DECOMMISSION_ALREADY_REQUESTED:{pendiente[0]}")
        solicitud = await self.crear(
            "BAJA", payload=schema.model_dump(mode="json"), solicitante=usuario_id,
            resumen=f"Baja de {activo.ACT_Codigo_Interno} ({activo.ACT_Serie_Fabricante}): "
                    f"{schema.motivo} → {schema.destino}",
            motivo=schema.observacion, activo_id=activo_id, sede=activo.SED_Sede, ip=ip,
        )
        return await self._a_dict(solicitud)

    # ------------------------------------------------------------------
    # Consulta
    async def _sin_pendiente(self, tipo: str, *, orden_id=None, consumible_id=None, activo_id=None) -> None:
        q = select(Aprobacion.APR_Aprobacion).where(Aprobacion.APR_Tipo == tipo, Aprobacion.APR_Estado == "PENDIENTE")
        if orden_id is not None:
            q = q.where(Aprobacion.OCO_Orden == orden_id)
        if consumible_id is not None:
            q = q.where(Aprobacion.CON_Consumible == consumible_id)
        if activo_id is not None:
            q = q.where(Aprobacion.ACT_Activo == activo_id)
        pendiente = (await self.db.execute(q)).first()
        if pendiente:
            raise HTTPException(409, f"APPROVAL_ALREADY_PENDING:{pendiente[0]}")

    @transactional
    async def solicitar_ajuste(self, consumible_id: int, schema, usuario_id=None, ip=None) -> dict:
        """Un conteo que baja la existencia: se ejecuta cuando otra persona lo aprueba."""
        from app.models.consumable import Consumible
        obj = (await self.db.execute(select(Consumible).where(
            Consumible.CON_Consumible == consumible_id))).scalar_one_or_none()
        if obj is None:
            raise HTTPException(404, "CONSUMABLE_NOT_FOUND")
        if schema.CON_Stock_Real >= obj.CON_Stock_Actual:
            raise HTTPException(400, "STOCK_ADJUST_NOT_A_DECREASE")
        await self._sin_pendiente("AJUSTE_CONSUMIBLE", consumible_id=consumible_id)
        diferencia = obj.CON_Stock_Actual - schema.CON_Stock_Real
        solicitud = await self.crear(
            "AJUSTE_CONSUMIBLE",
            payload={**schema.model_dump(mode="json"), "stock_antes": obj.CON_Stock_Actual},
            solicitante=usuario_id,
            resumen=f"Ajuste de {obj.CON_Nombre}: {obj.CON_Stock_Actual} → {schema.CON_Stock_Real} "
                    f"(−{diferencia} {obj.CON_Unidad or ''})".strip(),
            motivo=schema.MOC_Motivo, consumible_id=consumible_id, sede=obj.SED_Sede, ip=ip,
        )
        return await self._a_dict(solicitud)

    async def _orden(self, orden_id: int):
        from app.models.procurement import OrdenCompra
        orden = (await self.db.execute(select(OrdenCompra).where(OrdenCompra.OCO_Orden == orden_id))).scalar_one_or_none()
        if orden is None:
            raise HTTPException(404, "PURCHASE_ORDER_NOT_FOUND")
        return orden

    @transactional
    async def solicitar_cierre_orden(self, orden_id: int, motivo: str, usuario_id=None, ip=None) -> dict:
        """Cerrar una orden parcial aceptando el faltante (lo pendiente no llegará)."""
        orden = await self._orden(orden_id)
        if orden.OCO_Estado != "PARCIAL":
            raise HTTPException(409, "ONLY_PARTIAL_ORDERS_CAN_BE_CLOSED")
        await self._sin_pendiente("CIERRE_FALTANTE", orden_id=orden_id)
        solicitud = await self.crear(
            "CIERRE_FALTANTE", payload={"motivo": motivo}, solicitante=usuario_id,
            resumen=f"Cerrar con faltante la orden {orden.OCO_Numero}", motivo=motivo,
            orden_id=orden_id, sede=orden.SED_Sede, ip=ip,
        )
        return await self._a_dict(solicitud)

    @transactional
    async def solicitar_cancelacion_orden(self, orden_id: int, motivo: str | None, usuario_id=None, ip=None) -> dict:
        orden = await self._orden(orden_id)
        if orden.OCO_Estado == "CANCELADA":
            raise HTTPException(409, "ORDER_ALREADY_CANCELLED")
        if orden.OCO_Estado == "PARCIAL":
            raise HTTPException(409, "ORDER_HAS_RECEIPTS_CLOSE_WITH_SHORTAGE")
        if orden.OCO_Estado != "BORRADOR":
            raise HTTPException(409, "ORDER_STATE_TRANSITION_NOT_ALLOWED")
        if not (motivo or "").strip():
            raise HTTPException(422, "CANCEL_REASON_REQUIRED")
        await self._sin_pendiente("CANCELACION_ORDEN", orden_id=orden_id)
        solicitud = await self.crear(
            "CANCELACION_ORDEN", payload={"motivo": motivo}, solicitante=usuario_id,
            resumen=f"Cancelar la orden {orden.OCO_Numero} ({orden.OCO_Total} {orden.OCO_Moneda})", motivo=motivo,
            orden_id=orden_id, sede=orden.SED_Sede, ip=ip,
        )
        return await self._a_dict(solicitud)

    @transactional
    async def solicitar_reclasificacion(self, tipo_id: int, schema, usuario_id=None, ip=None) -> dict:
        """Los activos de un tipo «insumo» pasan a un consumible (por sede) al aprobarse."""
        from app.services.reclasificacion import ReclasificacionService
        vista = await ReclasificacionService(self.db).vista_previa(tipo_id, schema.SED_Sede)
        if vista["bloqueados"]:
            raise HTTPException(409, "RECLASSIFY_ASSETS_IN_USE")
        if not vista["activos"]:
            raise HTTPException(409, "RECLASSIFY_NOTHING_TO_CONVERT")
        await self._sin_pendiente("RECLASIFICACION", consumible_id=None)
        solicitud = await self.crear(
            "RECLASIFICACION", payload={"tipo_id": tipo_id, **schema.model_dump(mode="json")}, solicitante=usuario_id,
            resumen=f"Reclasificar {vista['activos']} activos «{vista['tipo']}» como el consumible «{schema.nombre}»",
            motivo=schema.motivo, ip=ip,
        )
        return await self._a_dict(solicitud)

    # ------------------------------------------------------------------
    async def _nombres(self, ids: set) -> dict:
        ids = {i for i in ids if i}
        if not ids:
            return {}
        filas = (await self.db.execute(
            select(Usuario.USU_Usuario, Usuario.USU_Username, Persona.PER_Primer_Nombre, Persona.PER_Primer_Apellido)
            .join(Persona, Persona.PER_Persona == Usuario.PER_Persona, isouter=True)
            .where(Usuario.USU_Usuario.in_(ids))
        )).all()
        return {u: (f"{n} {a}" if n else user) for u, user, n, a in filas}

    async def _a_dict(self, a: Aprobacion, nombres: dict | None = None) -> dict:
        if nombres is None:
            nombres = await self._nombres({a.APR_Solicitante, a.APR_Aprobador})
        return {
            "APR_Aprobacion": a.APR_Aprobacion, "APR_Tipo": a.APR_Tipo, "APR_Estado": a.APR_Estado,
            "APR_Resumen": a.APR_Resumen, "APR_Motivo": a.APR_Motivo, "APR_Payload": a.APR_Payload,
            "ACT_Activo": a.ACT_Activo, "OCO_Orden": a.OCO_Orden, "CON_Consumible": a.CON_Consumible,
            "SED_Sede": a.SED_Sede,
            "APR_Solicitante": a.APR_Solicitante, "solicitante_nombre": nombres.get(a.APR_Solicitante),
            "APR_Fecha_Solicitud": a.APR_Fecha_Solicitud,
            "APR_Aprobador": a.APR_Aprobador, "aprobador_nombre": nombres.get(a.APR_Aprobador),
            "APR_Fecha_Resolucion": a.APR_Fecha_Resolucion, "APR_Comentario": a.APR_Comentario,
        }

    async def listar(self, estado: str | None = None, activo_id: uuid.UUID | None = None,
                     limite: int = 200) -> list[dict]:
        q = select(Aprobacion).order_by(Aprobacion.APR_Fecha_Solicitud.desc()).limit(limite)
        if estado:
            q = q.where(Aprobacion.APR_Estado == estado)
        if activo_id:
            q = q.where(Aprobacion.ACT_Activo == activo_id)
        filas = list((await self.db.execute(q)).scalars().all())
        nombres = await self._nombres({x.APR_Solicitante for x in filas} | {x.APR_Aprobador for x in filas})
        return [await self._a_dict(x, nombres) for x in filas]

    async def pendientes(self) -> int:
        from sqlalchemy import func
        return (await self.db.execute(select(func.count()).select_from(Aprobacion).where(
            Aprobacion.APR_Estado == "PENDIENTE"))).scalar_one()

    # ------------------------------------------------------------------
    # Resolución
    # ------------------------------------------------------------------
    async def _pendiente(self, aprobacion_id: int) -> Aprobacion:
        a = (await self.db.execute(
            select(Aprobacion).where(Aprobacion.APR_Aprobacion == aprobacion_id).with_for_update()
        )).scalar_one_or_none()
        if not a:
            raise HTTPException(404, "APPROVAL_NOT_FOUND")
        if a.APR_Estado != "PENDIENTE":
            raise HTTPException(409, f"APPROVAL_ALREADY_RESOLVED:{a.APR_Estado}")
        return a

    @transactional
    async def aprobar(self, aprobacion_id: int, aprobador_id: uuid.UUID, comentario: str | None = None,
                      ip=None) -> dict:
        a = await self._pendiente(aprobacion_id)
        if a.APR_Solicitante == aprobador_id:
            raise HTTPException(403, "CANNOT_APPROVE_OWN_REQUEST")
        traza = {"aprobacion": a.APR_Aprobacion, "solicitado_por": a.APR_Solicitante, "aprobado_por": aprobador_id}
        if a.APR_Tipo == "BAJA":
            from app.schemas.core import BajaActivo
            from app.services.core import CoreService
            await CoreService(self.db).dar_de_baja(
                a.ACT_Activo, BajaActivo(**a.APR_Payload), usuario_id=a.APR_Solicitante, aprobacion=traza,
            )
        elif a.APR_Tipo == "RECEPCION_SOBRANTE":
            from app.schemas.procurement import RecepcionOrden
            from app.services.procurement import ProcurementService
            resultado = await ProcurementService(self.db).recibir_orden(
                a.OCO_Orden, RecepcionOrden(**a.APR_Payload), usuario_id=a.APR_Solicitante, ip=ip, aprobacion=traza,
            )
            traza["recepcion"] = resultado.get("REC_Recepcion")
        elif a.APR_Tipo == "AJUSTE_CONSUMIBLE":
            from app.schemas.consumable import StockAjusteCreate
            from app.services.consumable import ConsumableService
            await ConsumableService(self.db).registrar_ajuste(
                a.CON_Consumible, StockAjusteCreate(**{k: v for k, v in a.APR_Payload.items() if k != "stock_antes"}),
                usuario_id=a.APR_Solicitante, ip=ip, aprobacion=traza,
                stock_esperado=a.APR_Payload.get("stock_antes"),
            )
        elif a.APR_Tipo == "CIERRE_FALTANTE":
            from app.services.procurement import ProcurementService
            await ProcurementService(self.db).cerrar_con_faltante(
                a.OCO_Orden, a.APR_Payload["motivo"], usuario_id=a.APR_Solicitante, ip=ip, aprobacion=traza)
        elif a.APR_Tipo == "CANCELACION_ORDEN":
            from app.services.procurement import ProcurementService
            await ProcurementService(self.db).cambiar_estado(
                a.OCO_Orden, "CANCELADA", usuario_id=a.APR_Solicitante, ip=ip, motivo=a.APR_Payload.get("motivo"),
                aprobacion=traza)
        elif a.APR_Tipo == "RECLASIFICACION":
            from app.services.reclasificacion import ReclasificacionService
            traza["resultado"] = await ReclasificacionService(self.db).ejecutar(
                a.APR_Payload, usuario_id=a.APR_Solicitante, ip=ip, aprobacion=traza)
        if a.APR_Tipo == "BAJA":
            # Un faltante de toma con la baja aprobada queda resuelto.
            from app.services.faltantes import FaltanteService
            await FaltanteService(self.db).al_resolver_baja(a.APR_Aprobacion, aprobada=True)
        a.APR_Estado = "APROBADA"
        a.APR_Aprobador = aprobador_id
        a.APR_Fecha_Resolucion = _ahora()
        a.APR_Comentario = comentario or None
        await self.gov_repo.create_audit_log(
            "APPROVAL_APPROVE", "INV_APROBACION",
            {"id": a.APR_Aprobacion, "tipo": a.APR_Tipo, "resumen": a.APR_Resumen,
             "solicitante": str(a.APR_Solicitante), "comentario": comentario},
            usuario_id=aprobador_id, ip_origen=ip, sede_id=a.SED_Sede,
        )
        await self.db.flush()
        return await self._a_dict(a)

    @transactional
    async def rechazar(self, aprobacion_id: int, aprobador_id: uuid.UUID, comentario: str, ip=None) -> dict:
        a = await self._pendiente(aprobacion_id)
        if a.APR_Solicitante == aprobador_id:
            raise HTTPException(403, "CANNOT_APPROVE_OWN_REQUEST")
        a.APR_Estado = "RECHAZADA"
        a.APR_Aprobador = aprobador_id
        if a.APR_Tipo == "BAJA":
            from app.services.faltantes import FaltanteService
            await FaltanteService(self.db).al_resolver_baja(a.APR_Aprobacion, aprobada=False)
        a.APR_Fecha_Resolucion = _ahora()
        a.APR_Comentario = comentario
        await self.gov_repo.create_audit_log(
            "APPROVAL_REJECT", "INV_APROBACION",
            {"id": a.APR_Aprobacion, "tipo": a.APR_Tipo, "resumen": a.APR_Resumen, "comentario": comentario},
            usuario_id=aprobador_id, ip_origen=ip, sede_id=a.SED_Sede,
        )
        await self.db.flush()
        return await self._a_dict(a)

    @transactional
    async def anular(self, aprobacion_id: int, usuario_id: uuid.UUID, ip=None) -> dict:
        a = await self._pendiente(aprobacion_id)
        if a.APR_Solicitante != usuario_id:
            raise HTTPException(403, "ONLY_REQUESTER_CAN_CANCEL")
        a.APR_Estado = "ANULADA"
        a.APR_Fecha_Resolucion = _ahora()
        if a.APR_Tipo == "BAJA":
            from app.services.faltantes import FaltanteService
            await FaltanteService(self.db).al_resolver_baja(a.APR_Aprobacion, aprobada=False)
        await self.gov_repo.create_audit_log(
            "APPROVAL_CANCEL", "INV_APROBACION", {"id": a.APR_Aprobacion, "tipo": a.APR_Tipo},
            usuario_id=usuario_id, ip_origen=ip, sede_id=a.SED_Sede,
        )
        await self.db.flush()
        return await self._a_dict(a)
