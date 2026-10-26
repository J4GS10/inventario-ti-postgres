from __future__ import annotations

from fastapi import HTTPException, status
from app.core.transactional import transactional
from app.services.notification import NotificationService
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import uuid
from datetime import datetime, timezone

from app.core.data_scope import elevated, require_sede
from app.services.base import BaseService
from app.services.kardex import KardexService
from app.repositories.core import CoreRepository
from app.repositories.catalogs import CatalogRepository
from app.repositories.governance import GovernanceRepository
from app.repositories.traceability import TraceabilityRepository

from app.models.organization import Persona, Usuario
from app.models.traceability import Movimiento
from app.models.catalogs import EstadoOperativo

from app.schemas.core import ActivoCreate, ActivoUpdate, ActivoFilter
from app.models.enums import EstadoOperativoEnum
from app.services.state_machine import AssetStateMachine, ESTADOS_DE_ALMACEN, estado_enum_de_nombre


class CoreService(BaseService[CoreRepository]):
    repo_class = CoreRepository

    def __init__(self, db: AsyncSession):
        super().__init__(db)
        self.cat_repo = CatalogRepository(db)
        self.gov_repo = GovernanceRepository(db)
        self.trace_repo = TraceabilityRepository(db)

    # =================================================================
    # CREATE — ACID con auditoría
    # =================================================================
    @transactional
    async def create_activo(self, schema: ActivoCreate, usuario_id: uuid.UUID | None = None):
        # 1. VALIDACIONES DE FK
        tipo_activo = await self.cat_repo.get_tipo_activo_by_id(schema.TAC_Tipo_Activo)
        if not tipo_activo:
            raise HTTPException(status_code=404, detail="ASSET_TYPE_NOT_FOUND")
        # Los insumos se registran como consumibles (por cantidad), no como activos.
        from app.services.clases import exigir_no_insumo_tipo
        await exigir_no_insumo_tipo(self.db, schema.TAC_Tipo_Activo)

        modelo = await self.cat_repo.get_modelo_by_id(schema.MOD_Modelo)
        if not modelo:
            raise HTTPException(status_code=404, detail="MODEL_NOT_FOUND")

        estado = await self.cat_repo.get_estado_operativo_by_id(schema.EOP_Estado_Operativo)
        if not estado:
            raise HTTPException(status_code=404, detail="OPERATIONAL_STATUS_NOT_FOUND")
        # Un activo nuevo entra al almacén: "Asignado", "En Reparación" o "Baja"
        # solo se alcanzan con su operación (asignar, abrir ticket, dar de baja).
        if estado_enum_de_nombre(estado.EOP_Nombre) not in ESTADOS_DE_ALMACEN:
            raise HTTPException(status_code=400, detail="INITIAL_STATE_MUST_BE_AVAILABLE_OR_STORAGE")

        # Sede dentro del alcance del usuario (su única sede si no la indica).
        schema.SED_Sede = require_sede(schema.SED_Sede, required=False)
        if schema.ACT_Activo_Padre and not await self.repo.get_by_id_simple(schema.ACT_Activo_Padre):
            raise HTTPException(status_code=404, detail="PARENT_ASSET_NOT_FOUND")

        # 2. LÓGICA DE SECUENCIAS (Código Interno automático)
        if not schema.ACT_Codigo_Interno:
            if not tipo_activo.TAC_Prefijo:
                raise HTTPException(
                    status_code=400,
                    detail="ASSET_TYPE_HAS_NO_PREFIX_CONFIGURED_FOR_AUTO_GENERATION"
                )
            contexto_secuencia = f"ASSET_{tipo_activo.TAC_Prefijo}"
            nuevo_codigo = await self.gov_repo.get_next_code(
                contexto=contexto_secuencia,
                prefijo=tipo_activo.TAC_Prefijo
            )
            schema.ACT_Codigo_Interno = nuevo_codigo

        # 3. VALIDACIONES DE UNICIDAD (en toda la empresa, no solo en el alcance)
        async with elevated(self.db):
            codigo_existe = await self.repo.get_by_codigo_interno(schema.ACT_Codigo_Interno)
            serie_existe = await self.repo.get_by_serie(schema.ACT_Serie_Fabricante)
        if codigo_existe:
            raise HTTPException(status_code=400, detail="ASSET_CODE_ALREADY_EXISTS")
        if serie_existe:
            raise HTTPException(status_code=400, detail="SERIAL_NUMBER_ALREADY_EXISTS")

        # 4. VALIDACIÓN DE NEGOCIO: Garantía >= Fecha Compra
        if schema.ACT_Fin_Garantia and schema.ACT_Fin_Garantia < schema.ACT_Fecha_Compra:
            raise HTTPException(
                status_code=400,
                detail="WARRANTY_DATE_CANNOT_BE_BEFORE_PURCHASE_DATE"
            )

        # 5. CREACIÓN
        nuevo_activo = await self.repo.create_activo(schema)
        kardex = KardexService(self.db)
        await kardex.registrar(
            "ALTA", activo=nuevo_activo, usuario_id=usuario_id, destino={"sede": schema.SED_Sede},
            costo=schema.ACT_Costo, moneda=schema.ACT_Moneda, motivo="Alta manual en el inventario",
        )
        if schema.ACT_Activo_Padre:
            await kardex.registrar(
                "COMPONENTE_INSTALADO", activo=nuevo_activo, usuario_id=usuario_id,
                destino={"activo": schema.ACT_Activo_Padre, "sede": schema.SED_Sede},
                motivo="Registrado como componente al darlo de alta",
            )

        # Nota: no creamos un movimiento "automático" de ingreso. El alta
        # del activo NO implica asignación. Si se desea registrar quién
        # recibe el activo en bodega, se hace explícitamente con
        # POST /trazabilidad/movimientos.

        # 7. AUDITORÍA
        await self.gov_repo.create_audit_log(
            accion="CREATE",
            entidad="INV_ACTIVO",
            snapshot={"codigo": schema.ACT_Codigo_Interno, "serie": schema.ACT_Serie_Fabricante,
                      "sede": schema.SED_Sede},
            usuario_id=usuario_id, sede_id=schema.SED_Sede,
        )

        # 8. COMMIT ATÓMICO
        return nuevo_activo

    # =================================================================
    # UPDATE — Con validaciones de colisión
    # =================================================================
    @transactional
    async def update_activo(self, activo_id: uuid.UUID, schema: ActivoUpdate, usuario_id: uuid.UUID | None = None):
        activo_actual = await self.get_or_404(activo_id, "ASSET")

        # Validar FK de Modelo si se envía
        if schema.MOD_Modelo is not None:
            modelo = await self.cat_repo.get_modelo_by_id(schema.MOD_Modelo)
            if not modelo:
                raise HTTPException(status_code=404, detail="MODEL_NOT_FOUND")

        if schema.TAC_Tipo_Activo is not None and not await self.cat_repo.get_tipo_activo_by_id(schema.TAC_Tipo_Activo):
            raise HTTPException(status_code=404, detail="ASSET_TYPE_NOT_FOUND")

        # El estado solo se edita a mano entre "Disponible" y "En Bodega", y sin
        # custodia ni ticket abiertos. El resto de transiciones tiene su
        # operación (asignar, devolver, mantenimiento, baja), que valida la
        # máquina de estados y mantiene coherentes custodias y tablero.
        if schema.EOP_Estado_Operativo is not None and schema.EOP_Estado_Operativo != activo_actual.EOP_Estado_Operativo:
            estado = await self.cat_repo.get_estado_operativo_by_id(schema.EOP_Estado_Operativo)
            if not estado:
                raise HTTPException(status_code=404, detail="OPERATIONAL_STATUS_NOT_FOUND")
            actual = await self.cat_repo.get_estado_operativo_by_id(activo_actual.EOP_Estado_Operativo)
            if (estado_enum_de_nombre(estado.EOP_Nombre) not in ESTADOS_DE_ALMACEN
                    or estado_enum_de_nombre(actual.EOP_Nombre if actual else None) not in ESTADOS_DE_ALMACEN):
                raise HTTPException(status_code=400, detail="STATE_CHANGE_REQUIRES_OPERATION")
            if await self.trace_repo.get_movimiento_vigente(activo_id):
                raise HTTPException(status_code=400, detail="STATE_CHANGE_REQUIRES_OPERATION")

        # Fechas: se validan con los valores finales (los nuevos o los actuales).
        compra = schema.ACT_Fecha_Compra or activo_actual.ACT_Fecha_Compra
        garantia = (schema.ACT_Fin_Garantia if "ACT_Fin_Garantia" in schema.model_fields_set
                    else activo_actual.ACT_Fin_Garantia)
        if "ACT_Fecha_Compra" in schema.model_fields_set and schema.ACT_Fecha_Compra is None:
            raise HTTPException(status_code=400, detail="PURCHASE_DATE_REQUIRED")
        if garantia and compra and garantia < compra:
            raise HTTPException(status_code=400, detail="WARRANTY_DATE_CANNOT_BE_BEFORE_PURCHASE_DATE")

        # Cambio de sede: debe quedar en el alcance y el activo no puede estar
        # asignado (mientras lo está, su sede es la del área de la asignación).
        sede_final = activo_actual.SED_Sede
        if "SED_Sede" in schema.model_fields_set and schema.SED_Sede is None and activo_actual.SED_Sede is not None:
            # Un activo sin sede deja de verse para las cuentas con alcance por sede.
            raise HTTPException(status_code=400, detail="SEDE_REQUIRED")
        if "SED_Sede" in schema.model_fields_set and schema.SED_Sede != activo_actual.SED_Sede:
            schema.SED_Sede = require_sede(schema.SED_Sede, required=False)
            if await self.trace_repo.get_movimiento_vigente(activo_id):
                raise HTTPException(status_code=400, detail="CANNOT_CHANGE_SEDE_OF_ASSIGNED_ASSET")
            sede_final = schema.SED_Sede

        # Validar colisión de Código
        if schema.ACT_Codigo_Interno and schema.ACT_Codigo_Interno != activo_actual.ACT_Codigo_Interno:
            async with elevated(self.db):
                existe = await self.repo.get_by_codigo_interno(schema.ACT_Codigo_Interno)
            if existe:
                raise HTTPException(status_code=400, detail="NEW_ASSET_CODE_ALREADY_EXISTS")

        # Validar colisión de Serie
        if schema.ACT_Serie_Fabricante and schema.ACT_Serie_Fabricante != activo_actual.ACT_Serie_Fabricante:
            async with elevated(self.db):
                existe = await self.repo.get_by_serie(schema.ACT_Serie_Fabricante)
            if existe:
                raise HTTPException(status_code=400, detail="NEW_SERIAL_NUMBER_ALREADY_EXISTS")

        estado_antes, sede_antes = activo_actual.EOP_Estado_Operativo, activo_actual.SED_Sede
        resultado = await self.repo.update_activo(activo_id, schema)
        kardex = KardexService(self.db)
        if schema.EOP_Estado_Operativo is not None and schema.EOP_Estado_Operativo != estado_antes:
            await kardex.registrar(
                "CAMBIO_ESTADO", activo=resultado, usuario_id=usuario_id,
                detalle={"estado_antes": estado_antes, "estado_despues": schema.EOP_Estado_Operativo},
                motivo="Cambio de estado de almacén",
            )
        if sede_final != sede_antes:
            await kardex.registrar(
                "TRASLADO_SEDE", activo=resultado, usuario_id=usuario_id,
                origen={"sede": sede_antes}, destino={"sede": sede_final}, motivo="Cambio de sede",
            )

        # Auditoría
        await self.gov_repo.create_audit_log(
            accion="UPDATE",
            entidad="INV_ACTIVO",
            snapshot={"activo_id": str(activo_id), "cambios": schema.model_dump(exclude_unset=True)},
            usuario_id=usuario_id, sede_id=sede_final,
        )
        return resultado

    # =================================================================
    # READ
    # =================================================================
    async def list_activos(self, skip: int, limit: int):
        return await self.repo.get_all(skip, limit)

    async def get_activo_detail(self, activo_id: uuid.UUID):
        # get_by_id carga modelo, marca, tipo, estado y especificaciones: la
        # respuesta anidada no dispara cargas diferidas fuera del contexto async.
        activo = await self.repo.get_by_id(activo_id)
        if not activo:
            raise HTTPException(status_code=404, detail="ASSET_NOT_FOUND")
        return activo

    async def get_activo_by_codigo(self, codigo: str):
        activo = await self.repo.get_by_codigo_interno(codigo.strip())
        if not activo:
            raise HTTPException(status_code=404, detail="ASSET_NOT_FOUND")
        return await self.repo.get_by_id(activo.ACT_Activo)

    async def search_activos(self, filters: ActivoFilter):
        return await self.repo.search_activos(filters)

    # =================================================================
    # DELETE (Baja Lógica) — ACID con validaciones
    # =================================================================
    async def delete_activo(self, activo_id: uuid.UUID, usuario_id: uuid.UUID | None = None):
        """
        DELETE /core/activos/{id} ya no da de baja: toda salida del inventario
        exige motivo, destino y la aprobación del Jefe de TI (solicitud de baja).
        """
        raise HTTPException(status_code=409, detail="USE_DECOMMISSION_REQUEST")

    async def validar_baja(self, activo_id: uuid.UUID, schema, al_aprobar: bool = False):
        """Reglas de la baja (al solicitarla y otra vez al aprobarla). Devuelve (activo, estado_baja, componentes)."""
        from app.models.traceability import Mantenimiento

        from app.services.catalogo_generico import CatalogoGenericoService
        catalogos = CatalogoGenericoService(self.db)
        # Al aprobar se acepta un motivo o destino desactivado después de la
        # solicitud: era válido cuando se pidió (solo se exige que exista).
        motivo = await catalogos.obtener_por_codigo("motivos-baja", schema.motivo, solo_activos=not al_aprobar)
        if motivo is None:
            raise HTTPException(status_code=422, detail="DECOMMISSION_REASON_NOT_FOUND")
        destino = await catalogos.obtener_por_codigo("destinos-baja", schema.destino, solo_activos=not al_aprobar)
        if destino is None:
            raise HTTPException(status_code=422, detail="DECOMMISSION_DESTINATION_NOT_FOUND")
        if (motivo.MBA_Pide_Detalle or destino.DBA_Pide_Detalle) and not (schema.observacion or "").strip():
            raise HTTPException(status_code=422, detail="OBSERVATION_REQUIRED_FOR_OTHER")

        activo = await self.repo.get_by_id_simple(activo_id)
        if not activo:
            raise HTTPException(status_code=404, detail="ASSET_NOT_FOUND")
        if (await self.db.execute(select(Movimiento).where(
                Movimiento.ACT_Activo == activo_id, Movimiento.MOV_Fecha_Devolucion.is_(None)))).first():
            raise HTTPException(status_code=400, detail="CANNOT_DELETE_ASSIGNED_ASSET_RETURN_IT_FIRST")
        if (await self.db.execute(select(Mantenimiento.MAN_Mantenimiento).where(
                Mantenimiento.ACT_Activo == activo_id, Mantenimiento.MAN_Fecha_Cierre.is_(None)))).first():
            raise HTTPException(status_code=409, detail="ASSET_HAS_OPEN_MAINTENANCE")
        if activo.ACT_Activo_Padre is not None:
            raise HTTPException(status_code=409, detail="ASSET_INSTALLED_AS_COMPONENT")

        estado_baja = (await self.db.execute(
            select(EstadoOperativo).where(EstadoOperativo.EOP_Nombre.ilike("Baja")))).scalar_one_or_none()
        if not estado_baja:
            raise HTTPException(status_code=500, detail="SYSTEM_CONFIGURATION_ERROR_MISSING_STATUS_BAJA")
        actual = await self.cat_repo.get_estado_operativo_by_id(activo.EOP_Estado_Operativo)
        estado_actual = estado_enum_de_nombre(actual.EOP_Nombre if actual else None)
        if estado_actual == EstadoOperativoEnum.BAJA:
            raise HTTPException(status_code=409, detail="ASSET_ALREADY_DECOMMISSIONED")
        if estado_actual is not None and not AssetStateMachine.is_transition_allowed(
                estado_actual, EstadoOperativoEnum.BAJA):
            raise HTTPException(status_code=400, detail="INVALID_STATE_TRANSITION")
        componentes = await self._componentes_de(activo_id)
        if componentes and not schema.retirar_componentes:
            raise HTTPException(status_code=409, detail=f"ASSET_HAS_COMPONENTS:{len(componentes)}")
        return activo, estado_baja, componentes

    @transactional
    async def dar_de_baja(self, activo_id: uuid.UUID, schema, usuario_id: uuid.UUID | None = None,
                          aprobacion: dict | None = None):
        """
        Ejecuta una baja APROBADA (ver services/aprobaciones.py): motivo, destino,
        fecha y documento de respaldo. Revalida las reglas, retira (si se pidió)
        los componentes, libera las licencias y deja todo en el kardex como
        SALIDA del inventario, con quién la solicitó y quién la aprobó.
        """
        from datetime import date as _date
        from sqlalchemy import update as _upd
        from app.models.core import Activo as _Act
        from app.repositories.software import SoftwareRepository

        if not aprobacion:
            # Separación de funciones: la baja solo se ejecuta desde una aprobación.
            raise HTTPException(status_code=409, detail="USE_DECOMMISSION_REQUEST")
        activo, estado_baja, componentes = await self.validar_baja(activo_id, schema, al_aprobar=True)
        kardex = KardexService(self.db)
        for comp in componentes:
            await self._retirar(activo, comp, "Retiro por baja del equipo", "En Bodega", usuario_id, kardex)

        await self.db.execute(
            _upd(_Act).where(_Act.ACT_Activo == activo_id).values(
                EOP_Estado_Operativo=estado_baja.EOP_Estado_Operativo,
                ACT_Baja_Motivo=schema.motivo, ACT_Baja_Destino=schema.destino,
                ACT_Baja_Fecha=schema.fecha or _date.today(),
                ACT_Baja_Documento=schema.documento, ACT_Baja_Observacion=schema.observacion,
                ARE_Area_Ubicacion=None,
            ).execution_options(synchronize_session="fetch")
        )
        sw_repo = SoftwareRepository(self.db)
        await sw_repo.liberar_instalaciones_por_destino(
            activos_ids=[activo_id], persona_id=None, motivo="Baja del equipo",
        )
        for lic_id, act_id, per_id, clave_id in getattr(sw_repo, "ultimas_liberadas", []):
            await kardex.registrar(
                "LICENCIA_RETIRADA", licencia_id=lic_id, clave_id=clave_id, referencia=f"Licencia #{lic_id}",
                origen={"activo": act_id, "sede": activo.SED_Sede}, cantidad=1,
                motivo="Baja del equipo", usuario_id=usuario_id,
            )
        await kardex.registrar(
            "BAJA", activo=activo, usuario_id=usuario_id, origen={"sede": activo.SED_Sede},
            costo=activo.ACT_Costo, moneda=activo.ACT_Moneda,
            documento=("BAJA", schema.documento) if schema.documento else None,
            motivo=schema.observacion,
            detalle={"motivo": schema.motivo, "destino": schema.destino, "fecha": schema.fecha or _date.today(),
                     **aprobacion},
        )
        await self.gov_repo.create_audit_log(
            accion="DELETE_LOGIC", entidad="INV_ACTIVO",
            snapshot={"activo_id": str(activo_id), "codigo": activo.ACT_Codigo_Interno,
                      "motivo": schema.motivo, "destino": schema.destino, "documento": schema.documento,
                      **{k: str(v) for k, v in aprobacion.items()}},
            usuario_id=usuario_id, sede_id=activo.SED_Sede,
        )
        op_name, op_role, op_email = await self._operator_info(usuario_id)
        NotificationService.schedule_baja(
            activo_codigo=activo.ACT_Codigo_Interno,
            activo_serie=activo.ACT_Serie_Fabricante,
            fecha_iso=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            operador_nombre=op_name,
            operador_email=op_email,
            operador_rol=op_role,
        )
        return await self.repo.get_by_id(activo_id)

    # =================================================================
    # COMPONENTES (disco, memoria, monitor… instalados en un equipo)
    # =================================================================
    async def _componentes_de(self, equipo_id: uuid.UUID) -> list:
        from app.models.core import Activo as _Act
        return list((await self.db.execute(
            select(_Act).where(_Act.ACT_Activo_Padre == equipo_id).order_by(_Act.ACT_Codigo_Interno)
        )).scalars().all())

    async def listar_componentes(self, equipo_id: uuid.UUID) -> dict:
        equipo = await self.repo.get_by_id_simple(equipo_id)
        if not equipo:
            raise HTTPException(404, "ASSET_NOT_FOUND")
        from app.models.kardex import Kardex
        historial = (await self.db.execute(
            select(Kardex).where(
                Kardex.KDX_Tipo.in_(("COMPONENTE_INSTALADO", "COMPONENTE_RETIRADO")),
                (Kardex.KDX_Destino_Activo == equipo_id) | (Kardex.KDX_Origen_Activo == equipo_id),
            ).order_by(Kardex.KDX_Fecha.desc(), Kardex.KDX_Evento.desc())
        )).scalars().all()
        return {
            "equipo": equipo_id,
            "instalado_en": equipo.ACT_Activo_Padre,
            "actuales": await self._componentes_de(equipo_id),
            "historial": historial,
        }

    @transactional
    async def instalar_componente(self, equipo_id: uuid.UUID, schema, usuario_id=None):
        from sqlalchemy import update as _upd
        from app.models.core import Activo as _Act
        from app.models.traceability import Mantenimiento
        equipo = await self.repo.get_by_id_simple(equipo_id)
        comp = await self.repo.get_by_id_simple(schema.ACT_Componente)
        if not equipo or not comp:
            raise HTTPException(404, "ASSET_NOT_FOUND")
        if comp.ACT_Activo == equipo.ACT_Activo:
            raise HTTPException(400, "COMPONENT_CANNOT_BE_ITSELF")
        # Una impresora no lleva RAM: solo los componentes que admite el tipo de equipo.
        from app.services.clases import exigir_componente_valido
        await exigir_componente_valido(self.db, equipo, comp)
        if comp.ACT_Activo_Padre is not None:
            raise HTTPException(409, "COMPONENT_ALREADY_INSTALLED")
        # Sin ciclos: el equipo no puede estar instalado (directa o indirectamente) en el componente.
        padre = equipo.ACT_Activo_Padre
        while padre is not None:
            if padre == comp.ACT_Activo:
                raise HTTPException(400, "COMPONENT_CYCLE")
            padre = (await self.db.execute(select(_Act.ACT_Activo_Padre).where(_Act.ACT_Activo == padre))).scalar()
        for estado in (comp, equipo):
            nombre = await self.cat_repo.get_estado_operativo_by_id(estado.EOP_Estado_Operativo)
            if estado_enum_de_nombre(nombre.EOP_Nombre if nombre else None) == EstadoOperativoEnum.BAJA:
                raise HTTPException(400, "ASSET_DECOMMISSIONED")
        if await self.trace_repo.get_movimiento_vigente(comp.ACT_Activo):
            raise HTTPException(400, "COMPONENT_IS_ASSIGNED_RETURN_IT_FIRST")
        if (await self.db.execute(select(Mantenimiento.MAN_Mantenimiento).where(
                Mantenimiento.ACT_Activo == comp.ACT_Activo, Mantenimiento.MAN_Fecha_Cierre.is_(None)))).first():
            raise HTTPException(409, "ASSET_IN_MAINTENANCE")
        if comp.SED_Sede is not None and equipo.SED_Sede is not None and comp.SED_Sede != equipo.SED_Sede:
            raise HTTPException(400, "COMPONENT_IN_OTHER_SEDE")
        await self.db.execute(
            _upd(_Act).where(_Act.ACT_Activo == comp.ACT_Activo)
            .values(ACT_Activo_Padre=equipo.ACT_Activo, SED_Sede=equipo.SED_Sede,
                    ARE_Area_Ubicacion=equipo.ARE_Area_Ubicacion)
            .execution_options(synchronize_session="fetch")
        )
        await KardexService(self.db).registrar(
            "COMPONENTE_INSTALADO", activo=comp, usuario_id=usuario_id,
            destino={"activo": equipo.ACT_Activo, "sede": equipo.SED_Sede, "area": equipo.ARE_Area_Ubicacion},
            motivo=schema.motivo, detalle={"equipo": equipo.ACT_Codigo_Interno},
        )
        await self.gov_repo.create_audit_log(
            "COMPONENT_INSTALL", "INV_ACTIVO",
            {"equipo": equipo.ACT_Codigo_Interno, "componente": comp.ACT_Codigo_Interno, "motivo": schema.motivo},
            usuario_id=usuario_id, sede_id=equipo.SED_Sede,
        )
        return await self.listar_componentes(equipo_id)

    async def _retirar(self, equipo, comp, motivo, estado_destino, usuario_id, kardex) -> None:
        from sqlalchemy import update as _upd
        from app.models.core import Activo as _Act
        estado = (await self.db.execute(
            select(EstadoOperativo).where(EstadoOperativo.EOP_Nombre.ilike(estado_destino)))).scalar_one_or_none()
        valores = {"ACT_Activo_Padre": None}
        if estado is not None:
            valores["EOP_Estado_Operativo"] = estado.EOP_Estado_Operativo
        await self.db.execute(
            _upd(_Act).where(_Act.ACT_Activo == comp.ACT_Activo).values(**valores)
            .execution_options(synchronize_session="fetch")
        )
        await kardex.registrar(
            "COMPONENTE_RETIRADO", activo=comp, usuario_id=usuario_id,
            origen={"activo": equipo.ACT_Activo, "sede": equipo.SED_Sede},
            destino={"sede": comp.SED_Sede}, motivo=motivo,
            detalle={"equipo": equipo.ACT_Codigo_Interno, "estado_destino": estado_destino},
        )

    @transactional
    async def retirar_componente(self, equipo_id: uuid.UUID, componente_id: uuid.UUID, schema, usuario_id=None):
        equipo = await self.repo.get_by_id_simple(equipo_id)
        comp = await self.repo.get_by_id_simple(componente_id)
        if not equipo or not comp:
            raise HTTPException(404, "ASSET_NOT_FOUND")
        if comp.ACT_Activo_Padre != equipo.ACT_Activo:
            raise HTTPException(400, "COMPONENT_NOT_INSTALLED_IN_ASSET")
        await self._retirar(equipo, comp, schema.motivo, schema.estado_destino, usuario_id, KardexService(self.db))
        await self.gov_repo.create_audit_log(
            "COMPONENT_REMOVE", "INV_ACTIVO",
            {"equipo": equipo.ACT_Codigo_Interno, "componente": comp.ACT_Codigo_Interno, "motivo": schema.motivo},
            usuario_id=usuario_id, sede_id=equipo.SED_Sede,
        )
        return await self.listar_componentes(equipo_id)

    # =================================================================
    # HELPERS PRIVADOS
    # =================================================================
    async def _operator_info(self, usuario_id: uuid.UUID | None) -> tuple[str, str, str | None]:
        """Resuelve (nombre_completo, rol, email) del operador para notificaciones."""
        if not usuario_id:
            return ("Sistema", "", None)
        row = (await self.db.execute(
            select(
                Usuario.USU_Username, Usuario.USU_Rol,
                Persona.PER_Primer_Nombre, Persona.PER_Primer_Apellido,
                Persona.PER_Email_Corporativo,
            )
            .join(Persona, Persona.PER_Persona == Usuario.PER_Persona, isouter=True)
            .where(Usuario.USU_Usuario == usuario_id)
        )).first()
        if not row:
            return ("Sistema", "", None)
        username, rol, nombre, apellido, email = row
        if nombre:
            return (f"{nombre} {apellido}", rol or "", email)
        return (username, rol or "", None)

    # =================================================================
    # ESPECIFICACIONES (características del equipo: RAM, disco, batería…)
    # =================================================================
    async def list_especificaciones(self, activo_id: uuid.UUID):
        if not await self.repo.get_by_id_simple(activo_id):
            raise HTTPException(404, "ASSET_NOT_FOUND")
        return await self.repo.list_especificaciones(activo_id)

    @transactional
    async def add_especificacion(self, activo_id, tes_id, valor, usuario_id=None, ip=None, motivo=None):
        activo = await self.repo.get_by_id_simple(activo_id)
        if not activo:
            raise HTTPException(404, "ASSET_NOT_FOUND")
        if not await self.cat_repo.get_tipo_especificacion_by_id(tes_id):
            raise HTTPException(404, "SPECIFICATION_TYPE_NOT_FOUND")
        obj = await self.repo.add_especificacion(activo_id, tes_id, valor)
        await KardexService(self.db).registrar(
            "ESPECIFICACION", activo=activo, usuario_id=usuario_id, motivo=motivo,
            detalle={"tipo": tes_id, "antes": None, "despues": valor[:120]},
        )
        await self.gov_repo.create_audit_log(
            "CREATE", "INV_ESPECIFICACION",
            {"activo": str(activo_id), "tipo": tes_id, "valor": valor[:120]},
            usuario_id=usuario_id, ip_origen=ip, sede_id=activo.SED_Sede,
        )
        return obj

    @transactional
    async def update_especificacion(self, esp_id, valor, usuario_id=None, ip=None, motivo=None):
        esp = await self.repo.get_especificacion(esp_id)
        if not esp:
            raise HTTPException(404, "SPECIFICATION_NOT_FOUND")
        antes = esp.ESP_Valor
        activo = await self.repo.get_by_id_simple(esp.ACT_Activo)
        await self.repo.update_especificacion(esp_id, valor)
        if antes != valor:
            await KardexService(self.db).registrar(
                "ESPECIFICACION", activo=activo, usuario_id=usuario_id, motivo=motivo,
                detalle={"tipo": esp.TES_Tipo_Especificacion, "antes": (antes or "")[:120], "despues": valor[:120]},
            )
        await self.gov_repo.create_audit_log(
            "UPDATE", "INV_ESPECIFICACION", {"id": esp_id, "valor": valor[:120]},
            usuario_id=usuario_id, ip_origen=ip,
        )

    @transactional
    async def delete_especificacion(self, esp_id, usuario_id=None, ip=None, motivo=None):
        obj = await self.repo.get_especificacion(esp_id)
        if not obj:
            raise HTTPException(404, "SPECIFICATION_NOT_FOUND")
        activo = await self.repo.get_by_id_simple(obj.ACT_Activo)
        await self.repo.delete_especificacion(esp_id)
        await KardexService(self.db).registrar(
            "ESPECIFICACION", activo=activo, usuario_id=usuario_id, motivo=motivo,
            detalle={"tipo": obj.TES_Tipo_Especificacion, "antes": (obj.ESP_Valor or "")[:120], "despues": None},
        )
        await self.gov_repo.create_audit_log(
            "DELETE", "INV_ESPECIFICACION", {"id": esp_id},
            usuario_id=usuario_id, ip_origen=ip,
        )
