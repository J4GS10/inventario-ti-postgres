"""Servicio de Software/Licencias/Instalaciones."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import encrypt_field, fingerprint_field
from app.core.transactional import transactional
from app.models.core import Activo
from app.models.organization import Persona
from app.models.software import Instalacion, Licencia, Software as SoftwareModel
from app.repositories.governance import GovernanceRepository
from app.repositories.software import SoftwareRepository
from app.services.base import BaseService
from app.services.kardex import KardexService
from app.services.licencias import _mascara
from app.schemas.software import (
    InstalacionCreate,
    LicenciaCreate,
    LicenciaClaveCreate,
    LicenciaClaveResponse,
    LicenciaUpdate,
    SoftwareCreate,
    SoftwareUpdate,
    TipoLicenciaCreate,
    TipoLicenciaUpdate,
)


class SoftwareService(BaseService[SoftwareRepository]):
    repo_class = SoftwareRepository

    def __init__(self, db: AsyncSession):
        super().__init__(db)
        self.gov_repo = GovernanceRepository(db)

    # =====================================================================
    # TIPO LICENCIA
    # =====================================================================
    @transactional
    async def create_tipo_licencia(self, schema: TipoLicenciaCreate, usuario_id=None, ip=None):
        obj = await self.repo.create_tipo_licencia(schema)
        await self.gov_repo.create_audit_log(
            "CREATE", "INV_TIPO_LICENCIA", {"nombre": schema.TLI_Nombre},
            usuario_id=usuario_id, ip_origen=ip,
        )
        return obj

    async def list_tipos_licencia(self):
        return await self.repo.get_tipos_licencia()

    @transactional
    async def update_tipo_licencia(self, id: int, schema: TipoLicenciaUpdate, usuario_id=None, ip=None):
        tipo = await self.repo.get_tipo_licencia_by_id(id)
        if not tipo:
            raise HTTPException(404, detail="LICENSE_TYPE_NOT_FOUND")
        obj = await self.repo.update_tipo_licencia(id, schema)
        await self.gov_repo.create_audit_log(
            "UPDATE", "INV_TIPO_LICENCIA",
            {"id": id, "cambios": schema.model_dump(exclude_unset=True)},
            usuario_id=usuario_id, ip_origen=ip,
        )
        return obj

    @transactional
    async def delete_tipo_licencia(self, id: int, usuario_id=None, ip=None):
        tipo = await self.repo.get_tipo_licencia_by_id(id)
        if not tipo:
            raise HTTPException(404, detail="LICENSE_TYPE_NOT_FOUND")
        count = await self.repo.count_licencias_by_tipo(id)
        if count > 0:
            raise HTTPException(409, detail="CANNOT_DELETE_TYPE_HAS_LICENSES")
        await self.repo.delete_tipo_licencia(id)
        await self.gov_repo.create_audit_log(
            "DELETE", "INV_TIPO_LICENCIA",
            {"id": id, "nombre": tipo.TLI_Nombre},
            usuario_id=usuario_id, ip_origen=ip,
        )

    # =====================================================================
    # SOFTWARE
    # =====================================================================
    async def _fabricante(self, schema) -> None:
        """
        La marca del catálogo fija el texto del fabricante. Sin marca, el texto
        se enlaza con la marca del mismo nombre si existe.
        """
        from app.models.catalogs import Marca
        from app.services.catalogo_generico import CatalogoGenericoService
        if schema.MAR_Marca is not None:
            schema.SOF_Fabricante = await CatalogoGenericoService(self.db).nombre_vigente("marcas", schema.MAR_Marca)
        elif schema.SOF_Fabricante:
            schema.MAR_Marca = (await self.db.execute(
                select(Marca.MAR_Marca).where(func.lower(func.trim(Marca.MAR_Nombre)) == schema.SOF_Fabricante.lower(),
                                              Marca.MAR_Activo.is_(True))
            )).scalar_one_or_none()
        if "SOF_Fabricante" in schema.model_fields_set or isinstance(schema, SoftwareCreate):
            if len((schema.SOF_Fabricante or "").strip()) < 2:
                raise HTTPException(422, detail="SOFTWARE_MANUFACTURER_REQUIRED")

    @transactional
    async def create_software(self, schema: SoftwareCreate, usuario_id=None, ip=None):
        await self._fabricante(schema)
        obj = await self.repo.create_software(schema)
        await self.gov_repo.create_audit_log(
            "CREATE", "INV_SOFTWARE",
            {"nombre": schema.SOF_Nombre, "version": schema.SOF_Version},
            usuario_id=usuario_id, ip_origen=ip,
        )
        return obj

    async def list_software(self):
        return await self.repo.get_software_all()

    @transactional
    async def update_software(self, id: int, schema: SoftwareUpdate, usuario_id=None, ip=None):
        sw = await self.repo.get_software_by_id(id)
        if not sw:
            raise HTTPException(404, detail="SOFTWARE_NOT_FOUND")
        await self._fabricante(schema)
        obj = await self.repo.update_software(id, schema)
        await self.gov_repo.create_audit_log(
            "UPDATE", "INV_SOFTWARE",
            {"id": id, "cambios": schema.model_dump(exclude_unset=True)},
            usuario_id=usuario_id, ip_origen=ip,
        )
        return obj

    @transactional
    async def delete_software(self, id: int, usuario_id=None, ip=None):
        sw = await self.repo.get_software_by_id(id)
        if not sw:
            raise HTTPException(404, detail="SOFTWARE_NOT_FOUND")
        count = await self.repo.count_licencias_by_software(id)
        if count > 0:
            raise HTTPException(409, detail="CANNOT_DELETE_SOFTWARE_HAS_LICENSES")
        await self.repo.delete_software(id)
        await self.gov_repo.create_audit_log(
            "DELETE", "INV_SOFTWARE",
            {"id": id, "nombre": sw.SOF_Nombre},
            usuario_id=usuario_id, ip_origen=ip,
        )

    # =====================================================================
    # LICENCIAS
    # =====================================================================
    async def _license_key_counts(self, obj) -> dict[str, int]:
        estados = await self.repo.count_claves_by_estado(obj.LIC_Licencia)
        total = sum(estados.values())
        if total == 0 and obj.LIC_Clave_Activacion:
            assigned = 1 if obj.LIC_Cantidad_Usada else 0
            return {
                "total": 1,
                "disponibles": 0 if assigned else 1,
                "asignadas": assigned,
            }
        return {
            "total": total,
            "disponibles": estados.get("DISPONIBLE", 0),
            "asignadas": estados.get("ASIGNADA", 0),
        }

    async def _decorate_licencia_counts(self, obj):
        counts = await self._license_key_counts(obj)
        obj.LIC_Claves_Total = counts["total"]
        obj.LIC_Claves_Disponibles = counts["disponibles"]
        obj.LIC_Claves_Asignadas = counts["asignadas"]
        return obj

    async def _licencia_public_response(self, obj) -> dict:
        counts = await self._license_key_counts(obj)
        return {
            "LIC_Licencia": obj.LIC_Licencia,
            # License keys are only exposed by list_claves_licencia(), which is
            # admin-only and creates a VIEW_KEYS audit event.
            "LIC_Clave_Activacion": None,
            "LIC_Fecha_Vencimiento": obj.LIC_Fecha_Vencimiento,
            "LIC_Cantidad_Total": obj.LIC_Cantidad_Total,
            "LIC_Cantidad_Usada": obj.LIC_Cantidad_Usada,
            "SOF_Software": obj.SOF_Software,
            "TLI_Tipo_Licencia": obj.TLI_Tipo_Licencia,
            "LIC_Costo": obj.LIC_Costo,
            "LIC_Moneda": obj.LIC_Moneda,
            "MCO_Modelo_Comercial": obj.MCO_Modelo_Comercial,
            "MAC_Metodo_Activacion": obj.MAC_Metodo_Activacion,
            "FCO_Forma_Conteo": obj.FCO_Forma_Conteo,
            "LIC_Proveedor": obj.LIC_Proveedor,
            "LIC_Fecha_Inicio": obj.LIC_Fecha_Inicio,
            "LIC_Renovacion_Automatica": obj.LIC_Renovacion_Automatica,
            "LIC_Notas": obj.LIC_Notas,
            "LIC_Claves_Total": counts["total"],
            "LIC_Claves_Disponibles": counts["disponibles"],
            "LIC_Claves_Asignadas": counts["asignadas"],
        }

    async def _validar_catalogos(self, schema) -> None:
        """Modelo comercial, método y forma de conteo: registros activos del catálogo."""
        from app.services.catalogo_generico import CatalogoGenericoService
        catalogos = CatalogoGenericoService(self.db)
        for campo, clave in (("MCO_Modelo_Comercial", "modelos-comerciales"),
                             ("MAC_Metodo_Activacion", "metodos-activacion"), ("FCO_Forma_Conteo", "formas-conteo")):
            valor = getattr(schema, campo, None)
            if valor is not None:
                await catalogos.nombre_vigente(clave, valor)
        proveedor = getattr(schema, "LIC_Proveedor", None)
        if proveedor is not None:
            from app.models.procurement import Proveedor
            if await self.db.get(Proveedor, proveedor) is None:
                raise HTTPException(404, detail="SUPPLIER_NOT_FOUND")

    async def create_licencia_in_transaction(
        self,
        schema: LicenciaCreate,
        usuario_id=None,
        ip=None,
        origen: str | None = None,
        documento: tuple[str, str | None] | None = None,
        sede: int | None = None,
    ):
        sw = await self.db.execute(
            select(SoftwareModel).where(SoftwareModel.SOF_Software == schema.SOF_Software)
        )
        software = sw.scalar_one_or_none()
        if not software:
            raise HTTPException(404, detail="SOFTWARE_NOT_FOUND")
        if schema.TLI_Tipo_Licencia is not None and not await self.repo.get_tipo_licencia_by_id(schema.TLI_Tipo_Licencia):
            raise HTTPException(404, detail="LICENSE_TYPE_NOT_FOUND")
        await self._validar_catalogos(schema)
        if schema.LIC_Fecha_Vencimiento and schema.LIC_Fecha_Vencimiento < date.today():
            raise HTTPException(400, detail="LICENSE_EXPIRATION_IN_THE_PAST")

        claves = list(schema.LIC_Claves or [])
        if not claves and schema.LIC_Clave_Activacion and schema.LIC_Cantidad_Total == 1:
            claves = [LicenciaClaveCreate(LCL_Clave_Activacion=schema.LIC_Clave_Activacion)]

        fingerprints = [fingerprint_field(k.LCL_Clave_Activacion) for k in claves]
        if len(fingerprints) != len(set(fingerprints)):
            raise HTTPException(400, detail="DUPLICATE_LICENSE_KEY_IN_REQUEST")
        if await self.repo.count_claves_by_hashes(fingerprints) > 0:
            raise HTTPException(409, detail="LICENSE_KEY_ALREADY_EXISTS")

        payload = schema.model_copy(update={
            "LIC_Clave_Activacion": encrypt_field(schema.LIC_Clave_Activacion),
            "LIC_Claves": [],
        })
        obj = await self.repo.create_licencia(payload)

        for item, key_hash in zip(claves, fingerprints):
            await self.repo.create_licencia_clave(
                obj.LIC_Licencia,
                clave_cifrada=encrypt_field(item.LCL_Clave_Activacion),
                clave_hash=key_hash,
                referencia=item.LCL_Referencia,
            )
        if schema.LIC_Credenciales:
            # Credenciales según el método (cuentas, seriales…); quedan en el
            # mismo evento de alta del kardex.
            from app.services.licencias import CredencialService
            await CredencialService(self.db).crear(
                obj.LIC_Licencia, [c.model_dump() for c in schema.LIC_Credenciales],
                usuario_id=usuario_id, ip=ip, registrar_kardex=False,
            )

        await self.gov_repo.create_audit_log(
            "CREATE", "INV_LICENCIA",
            {
                "software_id": schema.SOF_Software,
                "cantidad_total": schema.LIC_Cantidad_Total,
                "claves_individuales": len(claves),
                "credenciales": len(schema.LIC_Credenciales),
                "origen": origen,
            },
            usuario_id=usuario_id, ip_origen=ip,
        )
        await self.db.flush()
        await KardexService(self.db).registrar(
            "LICENCIA_ALTA", licencia_id=obj.LIC_Licencia, referencia=_ref_software(software),
            cantidad=schema.LIC_Cantidad_Total, saldo=0, costo=schema.LIC_Costo, moneda=schema.LIC_Moneda,
            documento=documento, motivo=origen, usuario_id=usuario_id, sede=sede,
            detalle={"claves_individuales": len(claves) + len(schema.LIC_Credenciales),
                     "vencimiento": schema.LIC_Fecha_Vencimiento},
        )
        return await self._decorate_licencia_counts(obj)

    @transactional
    async def create_licencia(self, schema: LicenciaCreate, usuario_id=None, ip=None):
        obj = await self.create_licencia_in_transaction(
            schema,
            usuario_id=usuario_id,
            ip=ip,
        )
        return await self._licencia_public_response(obj)

    async def list_licencias(self, software_id: int):
        items = await self.repo.get_licencias_by_software(software_id)
        return [await self._licencia_public_response(it) for it in items]

    async def get_licencia(self, id: int):
        obj = await self.repo.get_licencia_by_id(id)
        if not obj:
            raise HTTPException(404, "LICENSE_NOT_FOUND")
        return await self._licencia_public_response(obj)

    @transactional
    async def list_claves_licencia(self, licencia_id: int, usuario_id=None, ip=None):
        licencia = await self.repo.get_licencia_by_id(licencia_id)
        if not licencia:
            raise HTTPException(404, "LICENSE_NOT_FOUND")
        claves = await self.repo.get_claves_by_licencia(licencia_id)
        response: list[LicenciaClaveResponse] = []
        for clave in claves:
            instalacion_activa = next(
                (inst for inst in clave.instalaciones if inst.INS_Estado),
                None,
            )
            destino_tipo = None
            destino_id = None
            destino_nombre = None
            destino_detalle = None
            asignada_en = None

            if instalacion_activa:
                asignada_en = instalacion_activa.INS_Fecha_Instalacion
                if instalacion_activa.activo:
                    activo = instalacion_activa.activo
                    destino_tipo = "ACTIVO"
                    destino_id = str(instalacion_activa.ACT_Activo)
                    destino_nombre = activo.ACT_Codigo_Interno
                    destino_detalle = " / ".join(
                        part for part in (activo.ACT_Hostname, activo.ACT_Serie_Fabricante) if part
                    ) or None
                elif instalacion_activa.persona:
                    persona = instalacion_activa.persona
                    destino_tipo = "PERSONA"
                    destino_id = str(instalacion_activa.PER_Persona)
                    destino_nombre = " ".join(
                        part for part in (
                            persona.PER_Primer_Nombre,
                            persona.PER_Segundo_Nombre,
                            persona.PER_Primer_Apellido,
                            persona.PER_Segundo_Apellido,
                        ) if part
                    )
                    destino_detalle = persona.PER_Email_Corporativo

            response.append(
                LicenciaClaveResponse(
                    LCL_Licencia_Clave=clave.LCL_Licencia_Clave,
                    LIC_Licencia=clave.LIC_Licencia,
                    # Nunca la key completa en un listado: se revela una por una (auditado).
                    LCL_Clave_Activacion=_mascara(clave.LCL_Clave_Activacion) or "",
                    LCL_Referencia=clave.LCL_Referencia,
                    LCL_Estado=clave.LCL_Estado,
                    LCL_Creado_En=clave.created_at,
                    LCL_Asignada_En=asignada_en,
                    LCL_Destino_Tipo=destino_tipo,
                    LCL_Destino_Id=destino_id,
                    LCL_Destino_Nombre=destino_nombre,
                    LCL_Destino_Detalle=destino_detalle,
                )
            )

        if not response and licencia.LIC_Clave_Activacion:
            legacy_key = _mascara(licencia.LIC_Clave_Activacion) or ""
            if legacy_key:
                response.append(
                    LicenciaClaveResponse(
                        LCL_Licencia_Clave=-licencia.LIC_Licencia,
                        LIC_Licencia=licencia.LIC_Licencia,
                        LCL_Clave_Activacion=legacy_key,
                        LCL_Referencia="Clave legada",
                        LCL_Estado="ASIGNADA" if licencia.LIC_Cantidad_Usada else "DISPONIBLE",
                        LCL_Creado_En=None,
                        LCL_Asignada_En=None,
                        LCL_Destino_Tipo=None,
                        LCL_Destino_Id=None,
                        LCL_Destino_Nombre=None,
                        LCL_Destino_Detalle=None,
                    )
                )

        await self.gov_repo.create_audit_log(
            "VIEW_KEYS",
            "INV_LICENCIA_CLAVE",
            {
                "licencia_id": licencia_id,
                "claves_consultadas": len(response),
            },
            usuario_id=usuario_id,
            ip_origen=ip,
        )
        return response

    @transactional
    async def update_licencia(self, id: int, schema: LicenciaUpdate, usuario_id=None, ip=None):
        obj = await self.repo.get_licencia_by_id(id)
        if not obj:
            raise HTTPException(404, "LICENSE_NOT_FOUND")

        await self._validar_catalogos(schema)
        # El método decide qué guarda cada credencial: no cambia si ya hay credenciales.
        if (schema.MAC_Metodo_Activacion is not None and schema.MAC_Metodo_Activacion != obj.MAC_Metodo_Activacion
                and await self.repo.count_claves_by_licencia(id) > 0):
            raise HTTPException(409, "LICENSE_METHOD_LOCKED")
        # Validar que LIC_Cantidad_Total no quede por debajo del uso actual
        if schema.LIC_Cantidad_Total is not None and schema.LIC_Cantidad_Total < obj.LIC_Cantidad_Usada:
            raise HTTPException(
                400, f"CANNOT_REDUCE_BELOW_USED:{obj.LIC_Cantidad_Usada}"
            )

        # Cifrar clave si viene
        data = schema.model_copy()
        if data.LIC_Clave_Activacion is not None:
            data = data.model_copy(update={"LIC_Clave_Activacion": encrypt_field(data.LIC_Clave_Activacion)})

        total_antes = obj.LIC_Cantidad_Total
        updated = await self.repo.update_licencia(id, data)
        if schema.LIC_Cantidad_Total is not None and schema.LIC_Cantidad_Total != total_antes:
            await KardexService(self.db).registrar(
                "LICENCIA_AJUSTE", licencia_id=id, referencia=await self._ref_licencia(id),
                cantidad=schema.LIC_Cantidad_Total - total_antes, saldo=updated.LIC_Cantidad_Usada,
                usuario_id=usuario_id,
                detalle={"total_antes": total_antes, "total_despues": schema.LIC_Cantidad_Total},
            )
        await self.gov_repo.create_audit_log(
            "UPDATE", "INV_LICENCIA",
            {"id": id, "cambios": schema.model_dump(exclude_unset=True, exclude={"LIC_Clave_Activacion"})},
            usuario_id=usuario_id, ip_origen=ip,
        )
        return await self._licencia_public_response(updated)

    @transactional
    async def delete_licencia(self, id: int, usuario_id=None, ip=None):
        obj = await self.repo.get_licencia_by_id(id)
        if not obj:
            raise HTTPException(404, "LICENSE_NOT_FOUND")

        # Si hay instalaciones activas, no se puede eliminar
        activas = await self.repo.count_instalaciones_activas_de_licencia(id)
        if activas > 0:
            raise HTTPException(409, f"CANNOT_DELETE_LICENSE_WITH_ACTIVE_INSTALLATIONS:{activas}")

        try:
            # Salida de la licencia del inventario (el kardex conserva su historia).
            await KardexService(self.db).registrar(
                "LICENCIA_BAJA", licencia_id=id, referencia=_ref_software(await self.db.get(SoftwareModel, obj.SOF_Software)),
                cantidad=obj.LIC_Cantidad_Total, saldo=0, costo=obj.LIC_Costo, moneda=obj.LIC_Moneda,
                motivo="Licencia eliminada del inventario", usuario_id=usuario_id,
            )
            await self.repo.delete_licencia(id)
            await self.gov_repo.create_audit_log(
                "DELETE", "INV_LICENCIA",
                {"id": id, "software_id": obj.SOF_Software, "total": obj.LIC_Cantidad_Total},
                usuario_id=usuario_id, ip_origen=ip,
            )
        except IntegrityError:
            raise HTTPException(409, "CANNOT_DELETE_LICENSE_IN_USE")

    async def licencias_disponibles(self, forma: str | None = None, todas: bool = False) -> list[dict]:
        """
        Licencias que se pueden asignar (con cupo libre, o por sitio), con su
        forma de conteo: la entrega de puesto ofrece las «por persona» y la
        ficha del equipo las «por equipo».
        """
        from app.models.catalogo_estandar import FormaConteo, MetodoActivacion
        stmt = (
            select(Licencia, SoftwareModel, FormaConteo.FCO_Codigo, MetodoActivacion.MAC_Codigo)
            .join(SoftwareModel, SoftwareModel.SOF_Software == Licencia.SOF_Software)
            .join(FormaConteo, FormaConteo.FCO_Forma == Licencia.FCO_Forma_Conteo, isouter=True)
            .join(MetodoActivacion, MetodoActivacion.MAC_Metodo == Licencia.MAC_Metodo_Activacion, isouter=True)
            .order_by(SoftwareModel.SOF_Nombre, Licencia.LIC_Licencia)
        )
        if forma:
            stmt = stmt.where(FormaConteo.FCO_Codigo == forma)
        hoy = date.today()
        salida = []
        for lic, sw, fco, mac in (await self.db.execute(stmt)).all():
            libres = None if fco == "SITIO" else lic.LIC_Cantidad_Total - lic.LIC_Cantidad_Usada
            # `todas`: para configurar (kits) se listan también las sin cupo o vencidas.
            if not todas and libres is not None and libres <= 0:
                continue
            if not todas and lic.LIC_Fecha_Vencimiento and lic.LIC_Fecha_Vencimiento < hoy:
                continue
            salida.append({
                "LIC_Licencia": lic.LIC_Licencia, "SOF_Software": sw.SOF_Software, "software": _ref_software(sw),
                "forma": fco, "metodo": mac, "total": lic.LIC_Cantidad_Total, "usadas": lic.LIC_Cantidad_Usada,
                "libres": libres, "vence": lic.LIC_Fecha_Vencimiento,
            })
        return salida

    async def list_instalaciones_by_activo(self, activo_id: uuid.UUID, solo_activas: bool = True):
        """Lista las instalaciones (con software/tipo_licencia poblados) de un activo."""
        return await self.repo.get_instalaciones_by_activo(activo_id, solo_activas=solo_activas)

    async def list_instalaciones_by_persona(self, persona_id: uuid.UUID, solo_activas: bool = True):
        persona = (await self.db.execute(
            select(Persona).where(Persona.PER_Persona == persona_id)
        )).scalar_one_or_none()
        if not persona:
            raise HTTPException(404, "PERSON_NOT_FOUND")
        return await self.repo.get_instalaciones_by_persona(persona_id, solo_activas=solo_activas)

    # =====================================================================
    # INSTALACIONES — Operación ACID con UPDATE atómico
    # =====================================================================
    @transactional
    async def registrar_instalacion(
        self,
        schema: InstalacionCreate,
        usuario_id: uuid.UUID | None = None,
        ip: str | None = None,
    ):
        """
        Flujo:
        1. Reservar cupo de licencia con UPDATE atómico condicional.
           Si no hay cupos, 409 directo (sin race condition).
        2. Validar duplicidad (no instalada ya).
        3. Crear Instalacion.
        4. Auditoría.
        5. Commit.
        """
        if schema.ACT_Activo is not None:
            activo = (await self.db.execute(
                select(Activo).where(Activo.ACT_Activo == schema.ACT_Activo)
            )).scalar_one_or_none()
            if activo is not None:
                # Software y licencias por equipo: solo en equipos que lo llevan (no en un monitor).
                from app.services.clases import exigir_software
                await exigir_software(self.db, activo.ACT_Activo)
            if not activo:
                raise HTTPException(404, detail="ASSET_NOT_FOUND")
        if schema.PER_Persona is not None:
            persona = (await self.db.execute(
                select(Persona).where(Persona.PER_Persona == schema.PER_Persona)
            )).scalar_one_or_none()
            if not persona:
                raise HTTPException(404, detail="PERSON_NOT_FOUND")
            if not persona.PER_Estado:
                raise HTTPException(400, detail="PERSON_INACTIVE")

        existe = await self.repo.get_instalacion_activa(
            schema.LIC_Licencia,
            activo_id=schema.ACT_Activo,
            persona_id=schema.PER_Persona,
        )
        if existe:
            raise HTTPException(400, detail="LICENSE_ALREADY_ASSIGNED_TO_TARGET")

        # Forma de conteo: por equipo exige un activo, por persona una persona;
        # concurrente comparte la credencial (no se reserva una) y por sitio
        # registra el uso sin descontar cupos.
        licencia = await self.repo.get_licencia_by_id(schema.LIC_Licencia)
        if not licencia:
            raise HTTPException(404, detail="LICENSE_NOT_FOUND")
        forma = licencia.forma_conteo.FCO_Codigo if licencia.forma_conteo else None
        if forma == "EQUIPO" and schema.ACT_Activo is None:
            raise HTTPException(400, detail="LICENSE_COUNTS_BY_DEVICE")
        if forma == "PERSONA" and schema.PER_Persona is None:
            raise HTTPException(400, detail="LICENSE_COUNTS_BY_PERSON")
        reserva_credencial = forma not in ("CONCURRENTE", "SITIO")
        consume_cupo = forma != "SITIO"

        clave_reservada_id = schema.LCL_Licencia_Clave if reserva_credencial else None
        total_claves = await self.repo.count_claves_by_licencia(schema.LIC_Licencia) if reserva_credencial else 0
        if clave_reservada_id is not None:
            clave = await self.repo.get_clave_by_id(clave_reservada_id)
            if not clave or clave.LIC_Licencia != schema.LIC_Licencia:
                raise HTTPException(404, detail="LICENSE_KEY_NOT_FOUND")
            if not await self.repo.reservar_clave_licencia(schema.LIC_Licencia, clave_reservada_id):
                raise HTTPException(409, detail="LICENSE_KEY_NOT_AVAILABLE")
        elif total_claves > 0:
            clave = await self.repo.reservar_clave_disponible(schema.LIC_Licencia)
            if not clave:
                raise HTTPException(409, detail="NO_LICENSE_KEYS_AVAILABLE")
            clave_reservada_id = clave.LCL_Licencia_Clave

        if consume_cupo and not await self.repo.reservar_cupo_licencia(schema.LIC_Licencia):
            raise HTTPException(409, detail="NO_LICENSE_SEATS_AVAILABLE")

        schema_to_create = schema.model_copy(update={"LCL_Licencia_Clave": clave_reservada_id})
        instalacion = await self.repo.create_instalacion(schema_to_create, consume_cupo=consume_cupo)
        licencia_actual = await self.repo.get_licencia_by_id(schema.LIC_Licencia)
        await KardexService(self.db).registrar(
            "LICENCIA_ASIGNADA", licencia_id=schema.LIC_Licencia, clave_id=clave_reservada_id,
            referencia=await self._ref_licencia(schema.LIC_Licencia),
            destino={"persona": schema.PER_Persona, "activo": schema.ACT_Activo,
                     "sede": await self._sede_destino_instalacion(schema)},
            cantidad=1, saldo=licencia_actual.LIC_Cantidad_Usada if licencia_actual else None,
            usuario_id=usuario_id,
        )
        await self.gov_repo.create_audit_log(
            "INSTALL", "INV_INSTALACION",
            {
                "activo": str(schema.ACT_Activo) if schema.ACT_Activo else None,
                "persona": str(schema.PER_Persona) if schema.PER_Persona else None,
                "licencia": schema.LIC_Licencia,
                "licencia_clave": clave_reservada_id,
            },
            usuario_id=usuario_id, ip_origen=ip,
        )
        return instalacion

    @transactional
    async def desinstalar_software(
        self,
        schema: InstalacionCreate,
        usuario_id: uuid.UUID | None = None,
        ip: str | None = None,
    ):
        """
        UPDATE condicional para que doble-desinstalación no decremente dos veces.
        """
        instalacion_actual = await self.repo.get_instalacion_activa(
            schema.LIC_Licencia,
            activo_id=schema.ACT_Activo,
            persona_id=schema.PER_Persona,
        )
        if not instalacion_actual:
            raise HTTPException(404, detail="INSTALLATION_NOT_FOUND_OR_ALREADY_REMOVED")

        desactivado = await self.repo.desactivar_instalacion_atomico(
            schema.LIC_Licencia,
            activo_id=schema.ACT_Activo,
            persona_id=schema.PER_Persona,
            motivo=schema.motivo,
        )
        if not desactivado:
            raise HTTPException(404, detail="INSTALLATION_NOT_FOUND_OR_ALREADY_REMOVED")

        if instalacion_actual.INS_Consume_Cupo:
            await self.repo.liberar_cupo_licencia(schema.LIC_Licencia)
        if instalacion_actual.LCL_Licencia_Clave is not None:
            await self.repo.liberar_clave_licencia(instalacion_actual.LCL_Licencia_Clave)
        licencia_actual = await self.repo.get_licencia_by_id(schema.LIC_Licencia)
        await KardexService(self.db).registrar(
            "LICENCIA_RETIRADA", licencia_id=schema.LIC_Licencia,
            clave_id=instalacion_actual.LCL_Licencia_Clave,
            referencia=await self._ref_licencia(schema.LIC_Licencia),
            origen={"persona": schema.PER_Persona, "activo": schema.ACT_Activo,
                    "sede": await self._sede_destino_instalacion(schema)},
            cantidad=1, saldo=licencia_actual.LIC_Cantidad_Usada if licencia_actual else None,
            motivo=schema.motivo, usuario_id=usuario_id,
        )

        await self.gov_repo.create_audit_log(
            "UNINSTALL", "INV_INSTALACION",
            {
                "activo": str(schema.ACT_Activo) if schema.ACT_Activo else None,
                "persona": str(schema.PER_Persona) if schema.PER_Persona else None,
                "licencia": schema.LIC_Licencia,
                "licencia_clave": instalacion_actual.LCL_Licencia_Clave,
            },
            usuario_id=usuario_id, ip_origen=ip,
        )
        return {"status": "success", "message": "SOFTWARE_UNINSTALLED_SUCCESSFULLY"}

    # =====================================================================
    # HISTORIAL Y REFERENCIAS
    # =====================================================================
    async def _ref_licencia(self, licencia_id: int) -> str | None:
        row = (await self.db.execute(
            select(SoftwareModel).join(Licencia, Licencia.SOF_Software == SoftwareModel.SOF_Software)
            .where(Licencia.LIC_Licencia == licencia_id)
        )).scalar_one_or_none()
        return f"{_ref_software(row)} · licencia #{licencia_id}" if row else f"Licencia #{licencia_id}"

    async def _sede_destino_instalacion(self, schema) -> int | None:
        if getattr(schema, "ACT_Activo", None):
            return (await self.db.execute(
                select(Activo.SED_Sede).where(Activo.ACT_Activo == schema.ACT_Activo)
            )).scalar_one_or_none()
        if getattr(schema, "PER_Persona", None):
            return (await self.db.execute(
                select(Persona.SED_Sede).where(Persona.PER_Persona == schema.PER_Persona)
            )).scalar_one_or_none()
        return None

    async def historial_licencia(self, licencia_id: int) -> list[dict]:
        """Todas las asignaciones de la licencia (vigentes y retiradas), en orden."""
        if not await self.repo.get_licencia_by_id(licencia_id):
            raise HTTPException(404, "LICENSE_NOT_FOUND")
        from sqlalchemy.orm import aliased
        filas = (await self.db.execute(
            select(Instalacion, Activo.ACT_Codigo_Interno, Persona.PER_Primer_Nombre, Persona.PER_Primer_Apellido)
            .join(Activo, Activo.ACT_Activo == Instalacion.ACT_Activo, isouter=True)
            .join(Persona, Persona.PER_Persona == Instalacion.PER_Persona, isouter=True)
            .where(Instalacion.LIC_Licencia == licencia_id)
            .order_by(Instalacion.INS_Fecha_Instalacion.desc(), Instalacion.INS_Instalacion.desc())
        )).all()
        return [
            {
                "INS_Instalacion": ins.INS_Instalacion,
                "destino": "ACTIVO" if ins.ACT_Activo else "PERSONA",
                "ACT_Activo": ins.ACT_Activo, "activo_codigo": codigo,
                "PER_Persona": ins.PER_Persona,
                "persona_nombre": f"{nombre} {apellido}" if nombre else None,
                "LCL_Licencia_Clave": ins.LCL_Licencia_Clave,
                "INS_Fecha_Instalacion": ins.INS_Fecha_Instalacion,
                "INS_Estado": ins.INS_Estado,
                "INS_Fecha_Retiro": ins.INS_Fecha_Retiro,
                "INS_Motivo_Retiro": ins.INS_Motivo_Retiro,
            }
            for ins, codigo, nombre, apellido in filas
        ]


def _ref_software(software) -> str:
    if software is None:
        return "—"
    partes = [software.SOF_Nombre, software.SOF_Version]
    return " ".join(p for p in partes if p)[:150]
