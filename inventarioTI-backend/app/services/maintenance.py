"""Servicio de mantenimientos."""
from __future__ import annotations

from app.core.errors import utcnow_naive
from app.core.transactional import schedule_post_commit, transactional

import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email import send_notification
from app.models.catalogs import EstadoOperativo
from app.models.organization import Persona
from app.models.traceability import Mantenimiento
from app.repositories.core import CoreRepository
from app.repositories.governance import GovernanceRepository
from app.repositories.maintenance import MaintenanceRepository
from app.services.base import BaseService
from app.schemas.maintenance import (
    DetalleCreate,
    MantenimientoCierre,
    MantenimientoCreate,
    TipoMantenimientoCreate,
    TipoMantenimientoUpdate,
)




def _con_prefijo(nombre: str, texto: str) -> str:
    """«Nombre del catálogo: detalle», sin repetir el nombre si el texto ya lo trae (edición)."""
    if not texto or texto == nombre:
        return nombre
    return texto if texto.startswith(f"{nombre}:") else f"{nombre}: {texto}"

class MaintenanceService(BaseService[MaintenanceRepository]):
    repo_class = MaintenanceRepository

    def __init__(self, db: AsyncSession):
        super().__init__(db)
        self.gov_repo = GovernanceRepository(db)
        self.core_repo = CoreRepository(db)

    def _schedule_notification(self, template: str, context: dict, **kwargs) -> None:
        schedule_post_commit(
            self,
            lambda: send_notification(template, context, **kwargs),
        )

    # =====================================================================
    # TIPOS
    # =====================================================================
    @transactional
    async def create_tipo(self, schema: TipoMantenimientoCreate, usuario_id=None, ip=None):
        obj = await self.repo.create_tipo(schema)
        await self.gov_repo.create_audit_log(
            "CREATE", "INV_TIPO_MANTENIMIENTO",
            {"nombre": schema.TMA_Nombre},
            usuario_id=usuario_id, ip_origen=ip,
        )
        return obj

    async def list_tipos(self):
        return await self.repo.get_tipos()

    async def get_tipo(self, id: int):
        obj = await self.repo.get_tipo_by_id(id)
        if not obj:
            raise HTTPException(404, "MAINTENANCE_TYPE_NOT_FOUND")
        return obj

    @transactional
    async def update_tipo(self, id: int, schema: TipoMantenimientoUpdate, usuario_id=None, ip=None):
        await self.get_tipo(id)
        obj = await self.repo.update_tipo(id, schema)
        await self.gov_repo.create_audit_log(
            "UPDATE", "INV_TIPO_MANTENIMIENTO",
            {"id": id, "cambios": schema.model_dump(exclude_unset=True)},
            usuario_id=usuario_id, ip_origen=ip,
        )
        return obj

    @transactional
    async def delete_tipo(self, id: int, usuario_id=None, ip=None):
        tipo = await self.get_tipo(id)
        if await self.repo.count_mantenimientos_by_tipo(id) > 0:
            raise HTTPException(409, "CANNOT_DELETE_TYPE_HAS_TICKETS")
        await self.repo.delete_tipo(id)
        await self.gov_repo.create_audit_log(
            "DELETE", "INV_TIPO_MANTENIMIENTO",
            {"id": id, "nombre": tipo.TMA_Nombre},
            usuario_id=usuario_id, ip_origen=ip,
        )

    # =====================================================================
    # MANTENIMIENTOS
    # =====================================================================
    async def list_mantenimientos(self, skip: int = 0, limit: int = 50):
        return await self.repo.get_all(skip, limit)

    async def get_mantenimiento(self, id: uuid.UUID):
        obj = await self.repo.get_by_id(id)
        if not obj:
            raise HTTPException(404, "MAINTENANCE_TICKET_NOT_FOUND")
        return obj

    @transactional
    async def registrar_mantenimiento(
        self, schema: MantenimientoCreate, usuario_id: uuid.UUID | None = None, ip: str | None = None,
    ):
        try:
            activo = await self.core_repo.get_by_id_simple(schema.ACT_Activo)
            if not activo:
                raise HTTPException(404, "ASSET_NOT_FOUND")

            persona = (await self.db.execute(
                select(Persona).where(Persona.PER_Persona == schema.PER_Persona_Solicita)
            )).scalar_one_or_none()
            if not persona:
                raise HTTPException(404, "REQUESTING_PERSON_NOT_FOUND")
            if not persona.PER_Estado:
                raise HTTPException(400, "PERSON_INACTIVE")

            abierto = (await self.db.execute(
                select(Mantenimiento).where(
                    Mantenimiento.ACT_Activo == schema.ACT_Activo,
                    Mantenimiento.MAN_Fecha_Cierre.is_(None),
                )
            )).scalar_one_or_none()
            if abierto:
                raise HTTPException(409, "ASSET_ALREADY_HAS_OPEN_MAINTENANCE_TICKET")

            estado_reparacion = (await self.db.execute(
                select(EstadoOperativo).where(EstadoOperativo.EOP_Nombre.ilike("%Reparación%"))
            )).scalar_one_or_none()
            if estado_reparacion is None:
                raise HTTPException(500, "SYSTEM_CONFIG_ERROR_MISSING_OPERATIONAL_STATE")
            # Solo entra a reparación lo que la máquina de estados permite: un
            # equipo de baja o en tránsito no abre ticket.
            from app.services.state_machine import AssetStateMachine, estado_enum_de_nombre
            from app.models.enums import EstadoOperativoEnum
            nombre_actual = (await self.db.execute(
                select(EstadoOperativo.EOP_Nombre)
                .where(EstadoOperativo.EOP_Estado_Operativo == activo.EOP_Estado_Operativo)
            )).scalar_one_or_none()
            estado_actual = estado_enum_de_nombre(nombre_actual)
            if estado_actual is not None and not AssetStateMachine.is_transition_allowed(
                    estado_actual, EstadoOperativoEnum.REPARACION):
                raise HTTPException(400, "ASSET_STATE_DOES_NOT_ALLOW_MAINTENANCE")
            activo.EOP_Estado_Operativo = estado_reparacion.EOP_Estado_Operativo

            await self._textos_de_catalogo(schema)
            mantenimiento = await self.repo.create_mantenimiento(schema)
            from app.services.kardex import KardexService
            await KardexService(self.db).registrar(
                "MANTENIMIENTO_ENTRADA", activo=activo, usuario_id=usuario_id,
                origen={"sede": activo.SED_Sede}, destino={"sede": activo.SED_Sede},
                documento=("TICKET", str(mantenimiento.MAN_Mantenimiento)),
                motivo=schema.MAN_Descripcion_Falla[:500],
                detalle={"solicitante": schema.PER_Persona_Solicita, "tipo": schema.TMA_Tipo_Mantenimiento},
            )
            await self.gov_repo.create_audit_log(
                "CREATE", "INV_MANTENIMIENTO",
                {"activo": str(schema.ACT_Activo), "falla": schema.MAN_Descripcion_Falla[:200]},
                usuario_id=usuario_id, ip_origen=ip, sede_id=activo.SED_Sede,
            )
            # Notificación post-commit
            try:
                from app.core.email import send_notification
                from app.models.organization import Usuario
                from app.models.traceability import TipoMantenimiento as _TM
                tm = (await self.db.execute(
                    select(_TM).where(_TM.TMA_Tipo_Mantenimiento == schema.TMA_Tipo_Mantenimiento)
                )).scalar_one_or_none()
                tipo_nombre = tm.TMA_Nombre if tm else ""
                # Resolver operador
                op_name, op_role, op_email = "Sistema", "", None
                if usuario_id:
                    usu = (await self.db.execute(
                        select(Usuario).where(Usuario.USU_Usuario == usuario_id)
                    )).scalar_one_or_none()
                    if usu:
                        op_role = usu.USU_Rol or ""
                        from app.models.organization import Persona as _Per
                        per_op = (await self.db.execute(
                            select(_Per).where(_Per.PER_Persona == usu.PER_Persona)
                        )).scalar_one_or_none()
                        if per_op:
                            op_name = f"{per_op.PER_Primer_Nombre} {per_op.PER_Primer_Apellido}"
                            op_email = per_op.PER_Email_Corporativo
                self._schedule_notification(
                    "mantenimiento_abierto",
                    {
                        "codigo": activo.ACT_Codigo_Interno if activo else "",
                        "tipo": tipo_nombre,
                        "persona_nombre": f"{persona.PER_Primer_Nombre} {persona.PER_Primer_Apellido}" if persona else "",
                        "descripcion": schema.MAN_Descripcion_Falla[:300],
                        "fecha": mantenimiento.MAN_Fecha_Ingreso.isoformat() if mantenimiento.MAN_Fecha_Ingreso else "",
                    },
                    to=[persona.PER_Email_Corporativo] if persona and persona.PER_Email_Corporativo else (),
                    reply_to=op_email,
                    operator_name=op_name,
                    operator_role=op_role,
                )
            except Exception:  # noqa: BLE001
                pass

            return mantenimiento
        except IntegrityError:
            # Choque del índice único parcial bajo concurrencia: otro request
            # abrió un ticket para el mismo activo entre el SELECT y el INSERT.
            raise HTTPException(409, "ASSET_ALREADY_HAS_OPEN_MAINTENANCE_TICKET")

    async def _detalle_de_catalogo(self, detalle) -> None:
        """Acción del catálogo + detalle escrito (obligatorio si la acción lo pide o no hay acción)."""
        from app.services.catalogo_generico import CatalogoGenericoService
        texto = (detalle.DMA_Accion_Realizada or "").strip()
        if detalle.AMA_Accion is None:
            if len(texto) < 5:
                raise HTTPException(422, "MAINTENANCE_ACTION_REQUIRED")
            return
        nombre = await CatalogoGenericoService(self.db).nombre_vigente("acciones-mantenimiento", detalle.AMA_Accion)
        from app.models.catalogo_estandar import AccionMantenimiento
        if (await self.db.get(AccionMantenimiento, detalle.AMA_Accion)).AMA_Pide_Detalle and not texto:
            raise HTTPException(422, "MAINTENANCE_ACTION_DETAIL_REQUIRED")
        detalle.DMA_Accion_Realizada = _con_prefijo(nombre, texto)[:255]

    async def _textos_de_catalogo(self, schema) -> None:
        """Tipo de falla del catálogo + descripción (obligatoria sin tipo o si el tipo la pide)."""
        from app.services.catalogo_generico import CatalogoGenericoService
        from app.models.catalogo_estandar import TipoFalla
        texto = (schema.MAN_Descripcion_Falla or "").strip()
        if schema.TFA_Tipo_Falla is None:
            if not texto:
                raise HTTPException(422, "FAULT_DESCRIPTION_REQUIRED")
        else:
            nombre = await CatalogoGenericoService(self.db).nombre_vigente("tipos-falla", schema.TFA_Tipo_Falla)
            if (await self.db.get(TipoFalla, schema.TFA_Tipo_Falla)).TFA_Pide_Detalle and not texto:
                raise HTTPException(422, "FAULT_DESCRIPTION_REQUIRED")
            schema.MAN_Descripcion_Falla = _con_prefijo(nombre, texto)[:2000]
        for detalle in schema.detalles:
            await self._detalle_de_catalogo(detalle)

    @transactional
    async def agregar_detalle(
        self, mantenimiento_id: uuid.UUID, schema: DetalleCreate, usuario_id=None, ip=None,
    ):
        mant = await self.get_mantenimiento(mantenimiento_id)
        if mant.MAN_Fecha_Cierre:
            raise HTTPException(400, "CANNOT_ADD_DETAILS_TO_CLOSED_TICKET")
        await self._detalle_de_catalogo(schema)
        obj = await self.repo.add_detalle(mantenimiento_id, schema)
        await self.gov_repo.create_audit_log(
            "CREATE", "INV_DETALLE_MANT",
            {"mantenimiento_id": str(mantenimiento_id), "accion": schema.DMA_Accion_Realizada[:200]},
            usuario_id=usuario_id, ip_origen=ip,
        )
        return obj

    async def list_detalles(self, mantenimiento_id: uuid.UUID):
        await self.get_mantenimiento(mantenimiento_id)
        return await self.repo.list_detalles(mantenimiento_id)

    @transactional
    async def update_detalle(self, detalle_id: int, schema: DetalleCreate, usuario_id=None, ip=None):
        det = await self.repo.get_detalle_by_id(detalle_id)
        if not det:
            raise HTTPException(404, "MAINTENANCE_DETAIL_NOT_FOUND")
        mant = await self.repo.get_by_id(det.MAN_Mantenimiento)
        if mant and mant.MAN_Fecha_Cierre:
            raise HTTPException(400, "CANNOT_MODIFY_DETAILS_OF_CLOSED_TICKET")
        await self._detalle_de_catalogo(schema)
        obj = await self.repo.update_detalle(detalle_id, schema)
        await self.gov_repo.create_audit_log(
            "UPDATE", "INV_DETALLE_MANT",
            {"detalle_id": detalle_id, "cambios": schema.model_dump(exclude_unset=True)},
            usuario_id=usuario_id, ip_origen=ip,
        )
        return obj

    @transactional
    async def delete_detalle(self, detalle_id: int, usuario_id=None, ip=None):
        det = await self.repo.get_detalle_by_id(detalle_id)
        if not det:
            raise HTTPException(404, "MAINTENANCE_DETAIL_NOT_FOUND")
        mant = await self.repo.get_by_id(det.MAN_Mantenimiento)
        if mant and mant.MAN_Fecha_Cierre:
            raise HTTPException(400, "CANNOT_DELETE_DETAILS_OF_CLOSED_TICKET")
        await self.repo.delete_detalle(detalle_id)
        await self.gov_repo.create_audit_log(
            "DELETE", "INV_DETALLE_MANT",
            {"detalle_id": detalle_id},
            usuario_id=usuario_id, ip_origen=ip,
        )

    @transactional
    async def cerrar_mantenimiento(
        self, mantenimiento_id: uuid.UUID, schema: MantenimientoCierre,
        usuario_id: uuid.UUID | None = None, ip: str | None = None,
    ):
        try:
            mant = await self.get_mantenimiento(mantenimiento_id)
            if mant.MAN_Fecha_Cierre:
                raise HTTPException(400, "TICKET_ALREADY_CLOSED")

            fecha = schema.MAN_Fecha_Cierre or utcnow_naive()
            if fecha < mant.MAN_Fecha_Ingreso:
                raise HTTPException(400, "CLOSE_DATE_CANNOT_BE_BEFORE_ENTRY_DATE")
            if fecha > utcnow_naive() + timedelta(minutes=5):
                raise HTTPException(400, "CLOSE_DATE_CANNOT_BE_IN_THE_FUTURE")

            moneda = schema.MAN_Moneda or mant.MAN_Moneda
            # UPDATE condicional (WHERE sin cierre): dos cierres simultáneos
            # no pueden ganar ambos.
            if not await self.repo.close_mantenimiento(
                mantenimiento_id, fecha_cierre=fecha, costo_total=schema.MAN_Costo_Total,
                moneda=moneda,
            ):
                raise HTTPException(400, "TICKET_ALREADY_CLOSED")

            # Transición correcta al cerrar mantenimiento:
            #   - Si el activo tiene un movimiento vigente (alguien lo tiene
            #     en custodia) → vuelve a 'Asignado' (no a 'Disponible', que
            #     dejaría inconsistencia entre INV_MOVIMIENTO e INV_ACTIVO).
            #   - Si no tiene movimiento abierto → vuelve a 'En Bodega' o
            #     'Disponible' según el sistema. Usamos 'En Bodega' por defecto
            #     (caso típico: el activo entró a reparación tras un retorno).
            from app.models.traceability import Movimiento as _Mov
            mov_vigente = (await self.db.execute(
                select(_Mov).where(
                    _Mov.ACT_Activo == mant.ACT_Activo,
                    _Mov.MOV_Fecha_Devolucion.is_(None),
                )
            )).scalar_one_or_none()
            activo_previo = await self.core_repo.get_by_id_simple(mant.ACT_Activo)
            # Un equipo compartido vuelve a su área («Instalado»).
            instalado = bool(activo_previo and activo_previo.ACT_Instalado and not mov_vigente)
            nuevo_nombre = "Asignado" if mov_vigente else "Instalado" if instalado else "%Bodega%"
            estado_nuevo = (await self.db.execute(
                select(EstadoOperativo).where(EstadoOperativo.EOP_Nombre.ilike(nuevo_nombre))
            )).scalar_one_or_none()
            if estado_nuevo is None:
                raise HTTPException(500, "SYSTEM_CONFIG_ERROR_MISSING_OPERATIONAL_STATE")
            activo = await self.core_repo.get_by_id_simple(mant.ACT_Activo)
            # Un equipo dado de baja mientras estaba en taller no "resucita".
            estado_baja = (await self.db.execute(
                select(EstadoOperativo.EOP_Estado_Operativo).where(EstadoOperativo.EOP_Nombre.ilike("Baja"))
            )).scalar_one_or_none()
            if activo and activo.EOP_Estado_Operativo != estado_baja:
                activo.EOP_Estado_Operativo = estado_nuevo.EOP_Estado_Operativo

            from app.services.kardex import KardexService
            await KardexService(self.db).registrar(
                "MANTENIMIENTO_SALIDA", activo=activo, activo_id=mant.ACT_Activo, usuario_id=usuario_id,
                origen={"sede": activo.SED_Sede if activo else None},
                destino={"sede": activo.SED_Sede if activo else None},
                costo=schema.MAN_Costo_Total, moneda=moneda,
                documento=("TICKET", str(mantenimiento_id)),
                detalle={"estado_final": "Asignado" if mov_vigente else "Instalado" if instalado else "En Bodega"},
            )
            await self.gov_repo.create_audit_log(
                "CLOSE", "INV_MANTENIMIENTO",
                {"ticket_id": str(mantenimiento_id), "costo_total": str(schema.MAN_Costo_Total),
                 "moneda": moneda},
                usuario_id=usuario_id, ip_origen=ip, sede_id=activo.SED_Sede if activo else None,
            )
            # Notificación post-commit
            try:
                from app.core.email import send_notification
                from app.models.organization import Persona as _Per, Usuario
                activo = await self.core_repo.get_by_id_simple(mant.ACT_Activo)
                estado_final = "Asignado" if mov_vigente else "En Bodega"
                # Resolver operador
                op_name, op_role, op_email = "Sistema", "", None
                if usuario_id:
                    usu = (await self.db.execute(
                        select(Usuario).where(Usuario.USU_Usuario == usuario_id)
                    )).scalar_one_or_none()
                    if usu:
                        op_role = usu.USU_Rol or ""
                        per_op = (await self.db.execute(
                            select(_Per).where(_Per.PER_Persona == usu.PER_Persona)
                        )).scalar_one_or_none()
                        if per_op:
                            op_name = f"{per_op.PER_Primer_Nombre} {per_op.PER_Primer_Apellido}"
                            op_email = per_op.PER_Email_Corporativo
                # Afectado = quien tiene el activo en custodia (para reglas
                # de notificación: "notificar afectado" / "notificar jefe").
                custodio = await self.db.get(_Per, mov_vigente.PER_Persona) if mov_vigente else None
                self._schedule_notification(
                    "mantenimiento_cerrado",
                    {
                        "codigo": activo.ACT_Codigo_Interno if activo else "",
                        "costo": f"{schema.MAN_Costo_Total} {moneda}",
                        "fecha": fecha.isoformat(),
                        "estado_final": estado_final,
                    },
                    to=(),  # solo admins
                    affected=[custodio.PER_Email_Corporativo] if custodio else (),
                    reply_to=op_email,
                    operator_name=op_name,
                    operator_role=op_role,
                )
            except Exception:  # noqa: BLE001
                pass

            return await self.repo.get_by_id(mantenimiento_id)
        except Exception:
            raise

    @transactional
    async def delete_mantenimiento(self, mantenimiento_id: uuid.UUID, usuario_id=None, ip=None):
        mant = await self.get_mantenimiento(mantenimiento_id)
        if not mant.MAN_Fecha_Cierre:
            raise HTTPException(400, "CANNOT_DELETE_OPEN_TICKET")
        await self.repo.delete_mantenimiento(mantenimiento_id)
        await self.gov_repo.create_audit_log(
            "DELETE", "INV_MANTENIMIENTO",
            {"ticket_id": str(mantenimiento_id)},
            usuario_id=usuario_id, ip_origen=ip,
        )
