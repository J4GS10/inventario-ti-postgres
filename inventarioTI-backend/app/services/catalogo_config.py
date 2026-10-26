"""
Configuración que acompaña a los catálogos (plan de licencias y catálogos §4.1):

- Especificaciones que pide cada tipo de activo (INV_ESPECIFICACION_TIPO_ACTIVO):
  el formulario del activo las muestra y la calidad de datos avisa las que faltan.
- Kit estándar de puesto por cargo o departamento (INV_KIT_ESTANDAR): la
  entrega de puesto lo propone antes que la deducción por frecuencia.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.transactional import commit_or_409
from app.models.catalogo_estandar import EspecificacionTipoActivo, KitEstandar, KitEstandarItem
from app.models.catalogs import TipoActivo, TipoEspecificacion
from app.models.organization import Cargo, Departamento
from app.repositories.governance import GovernanceRepository


class CatalogoConfigService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.gov_repo = GovernanceRepository(db)

    # ── Especificaciones por tipo de activo ──────────────────────────────────
    @staticmethod
    def _eta(e: EspecificacionTipoActivo) -> dict:
        tes = e.tipo_especificacion
        return {
            "TES_Tipo_Especificacion": e.TES_Tipo_Especificacion,
            "nombre": tes.TES_Nombre if tes else None,
            "unidad": tes.TES_Unidad_Medida if tes else None,
            "obligatoria": e.ETA_Obligatoria,
            "valores": e.ETA_Valores or [],
            "orden": e.ETA_Orden,
        }

    async def especificaciones_de_tipo(self, tipo_id: int) -> list[dict]:
        filas = (await self.db.execute(
            select(EspecificacionTipoActivo)
            .options(selectinload(EspecificacionTipoActivo.tipo_especificacion))
            .where(EspecificacionTipoActivo.TAC_Tipo_Activo == tipo_id)
            .order_by(EspecificacionTipoActivo.ETA_Orden, EspecificacionTipoActivo.ETA_Especificacion_Tipo)
        )).scalars().all()
        return [self._eta(e) for e in filas]

    async def todas_las_especificaciones(self) -> dict[int, list[dict]]:
        filas = (await self.db.execute(
            select(EspecificacionTipoActivo)
            .options(selectinload(EspecificacionTipoActivo.tipo_especificacion))
            .order_by(EspecificacionTipoActivo.ETA_Orden)
        )).scalars().all()
        por_tipo: dict[int, list[dict]] = {}
        for e in filas:
            por_tipo.setdefault(e.TAC_Tipo_Activo, []).append(self._eta(e))
        return por_tipo

    async def fijar_especificaciones(self, tipo_id: int, items: list[dict], *, usuario_id=None, ip=None) -> list[dict]:
        """Reemplaza la lista de especificaciones que pide el tipo (en el orden recibido)."""
        if await self.db.get(TipoActivo, tipo_id) is None:
            raise HTTPException(404, "ASSET_TYPE_NOT_FOUND")
        ids = [i["TES_Tipo_Especificacion"] for i in items]
        if len(set(ids)) != len(ids):
            raise HTTPException(422, "DUPLICATED_SPECIFICATION")
        existentes = set((await self.db.execute(
            select(TipoEspecificacion.TES_Tipo_Especificacion).where(TipoEspecificacion.TES_Tipo_Especificacion.in_(ids))
        )).scalars()) if ids else set()
        if existentes != set(ids):
            raise HTTPException(404, "SPECIFICATION_TYPE_NOT_FOUND")
        await self.db.execute(delete(EspecificacionTipoActivo).where(EspecificacionTipoActivo.TAC_Tipo_Activo == tipo_id))
        for orden, item in enumerate(items, start=1):
            valores = [v.strip() for v in (item.get("valores") or []) if v and v.strip()]
            self.db.add(EspecificacionTipoActivo(
                TAC_Tipo_Activo=tipo_id, TES_Tipo_Especificacion=item["TES_Tipo_Especificacion"],
                ETA_Obligatoria=bool(item.get("obligatoria")), ETA_Valores=valores or None, ETA_Orden=orden * 10,
            ))
        await self.db.flush()
        await self.gov_repo.create_audit_log(
            "UPDATE", "INV_ESPECIFICACION_TIPO_ACTIVO", {"tipo": tipo_id, "especificaciones": items},
            usuario_id=usuario_id, ip_origen=ip,
        )
        await commit_or_409(self.db, where="CatalogoConfigService.fijar_especificaciones")
        return await self.especificaciones_de_tipo(tipo_id)

    # ── Kit estándar ─────────────────────────────────────────────────────────
    @staticmethod
    def _kit(k: KitEstandar) -> dict:
        return {
            "KIT_Kit": k.KIT_Kit, "KIT_Nombre": k.KIT_Nombre, "CAR_Cargo": k.CAR_Cargo,
            "DEP_Departamento": k.DEP_Departamento, "KIT_Activo": k.KIT_Activo,
            "items": [{"KIT_Item": i.KIT_Item, "TAC_Tipo_Activo": i.TAC_Tipo_Activo,
                       "KIT_Cantidad": i.KIT_Cantidad, "LIC_Licencia": i.LIC_Licencia} for i in k.items],
        }

    async def _cargar_kit(self, kit_id: int) -> KitEstandar:
        kit = (await self.db.execute(
            select(KitEstandar).options(selectinload(KitEstandar.items)).where(KitEstandar.KIT_Kit == kit_id)
        )).scalar_one_or_none()
        if kit is None:
            raise HTTPException(404, "STANDARD_KIT_NOT_FOUND")
        return kit

    async def listar_kits(self) -> list[dict]:
        kits = (await self.db.execute(
            select(KitEstandar).options(selectinload(KitEstandar.items)).order_by(KitEstandar.KIT_Nombre)
        )).scalars().all()
        return [self._kit(k) for k in kits]

    async def _validar_kit(self, datos: dict, excepto: int | None = None) -> None:
        cargo, departamento = datos.get("CAR_Cargo"), datos.get("DEP_Departamento")
        if (cargo is None) == (departamento is None):
            raise HTTPException(422, "STANDARD_KIT_NEEDS_POSITION_OR_DEPARTMENT")
        if cargo is not None and await self.db.get(Cargo, cargo) is None:
            raise HTTPException(404, "POSITION_NOT_FOUND")
        if departamento is not None and await self.db.get(Departamento, departamento) is None:
            raise HTTPException(404, "DEPARTMENT_NOT_FOUND")
        # Un solo kit activo por cargo o por departamento.
        stmt = select(KitEstandar.KIT_Kit).where(
            KitEstandar.KIT_Activo.is_(True),
            KitEstandar.CAR_Cargo == cargo if cargo is not None else KitEstandar.DEP_Departamento == departamento,
        )
        if excepto is not None:
            stmt = stmt.where(KitEstandar.KIT_Kit != excepto)
        if datos.get("KIT_Activo", True) and (await self.db.execute(stmt)).first():
            raise HTTPException(409, "STANDARD_KIT_ALREADY_EXISTS")
        if not datos.get("items"):
            raise HTTPException(422, "STANDARD_KIT_EMPTY")

    @staticmethod
    def _items(datos: dict) -> list[KitEstandarItem]:
        return [KitEstandarItem(TAC_Tipo_Activo=i.get("TAC_Tipo_Activo"), KIT_Cantidad=i.get("KIT_Cantidad") or 1,
                                LIC_Licencia=i.get("LIC_Licencia")) for i in datos["items"]]

    async def crear_kit(self, datos: dict, *, usuario_id=None, ip=None) -> dict:
        await self._validar_kit(datos)
        kit = KitEstandar(KIT_Nombre=datos["KIT_Nombre"], CAR_Cargo=datos.get("CAR_Cargo"),
                          DEP_Departamento=datos.get("DEP_Departamento"), KIT_Activo=datos.get("KIT_Activo", True),
                          items=self._items(datos))
        self.db.add(kit)
        await self.db.flush()
        await self.gov_repo.create_audit_log("CREATE", "INV_KIT_ESTANDAR", {"id": kit.KIT_Kit, **datos},
                                             usuario_id=usuario_id, ip_origen=ip)
        await commit_or_409(self.db, where="CatalogoConfigService.crear_kit")
        return self._kit(await self._cargar_kit(kit.KIT_Kit))

    async def editar_kit(self, kit_id: int, datos: dict, *, usuario_id=None, ip=None) -> dict:
        kit = await self._cargar_kit(kit_id)
        await self._validar_kit(datos, excepto=kit_id)
        kit.KIT_Nombre = datos["KIT_Nombre"]
        kit.CAR_Cargo, kit.DEP_Departamento = datos.get("CAR_Cargo"), datos.get("DEP_Departamento")
        kit.KIT_Activo = datos.get("KIT_Activo", True)
        kit.items = self._items(datos)
        await self.gov_repo.create_audit_log("UPDATE", "INV_KIT_ESTANDAR", {"id": kit_id, **datos},
                                             usuario_id=usuario_id, ip_origen=ip)
        await commit_or_409(self.db, where="CatalogoConfigService.editar_kit")
        self.db.expire(kit)
        return self._kit(await self._cargar_kit(kit_id))

    async def borrar_kit(self, kit_id: int, *, usuario_id=None, ip=None) -> None:
        kit = await self._cargar_kit(kit_id)
        await self.db.delete(kit)
        await self.gov_repo.create_audit_log("DELETE", "INV_KIT_ESTANDAR", {"id": kit_id, "nombre": kit.KIT_Nombre},
                                             usuario_id=usuario_id, ip_origen=ip)
        await commit_or_409(self.db, where="CatalogoConfigService.borrar_kit")

    async def kit_de_persona(self, cargo_id: int | None, departamento_id: int | None) -> KitEstandar | None:
        """Kit activo del cargo (prioridad) o, si no hay, el del departamento."""
        for columna, valor in ((KitEstandar.CAR_Cargo, cargo_id), (KitEstandar.DEP_Departamento, departamento_id)):
            if valor is None:
                continue
            kit = (await self.db.execute(
                select(KitEstandar).options(selectinload(KitEstandar.items))
                .where(columna == valor, KitEstandar.KIT_Activo.is_(True))
            )).scalars().first()
            if kit is not None:
                return kit
        return None
