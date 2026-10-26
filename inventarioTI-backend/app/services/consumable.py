"""Servicio de Consumibles (inventario por cantidad)."""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.transactional import schedule_post_commit, transactional
from app.models.consumable import MovimientoConsumible
from app.repositories.consumable import ConsumibleRepository
from app.repositories.governance import GovernanceRepository
from app.services.base import BaseService
from app.services import valoracion
from app.services.kardex import KardexService
from app.schemas.consumable import (
    ConsumibleCreate, ConsumibleUpdate, StockMovimientoCreate,
)


def _con_to_response(con) -> dict:
    """Serializa un Consumible añadiendo el flag calculado bajo_stock."""
    return {
        "CON_Consumible": con.CON_Consumible,
        "CON_Nombre": con.CON_Nombre,
        "CON_Descripcion": con.CON_Descripcion,
        "CON_Categoria": con.CON_Categoria,
        "CON_Unidad": con.CON_Unidad,
        "CCO_Categoria": con.CCO_Categoria,
        "UME_Unidad": con.UME_Unidad,
        "CON_Stock_Actual": con.CON_Stock_Actual,
        "CON_Stock_Minimo": con.CON_Stock_Minimo,
        "CON_Activo": con.CON_Activo,
        "SED_Sede": con.SED_Sede,
        "CON_Costo_Unitario": con.CON_Costo_Unitario,
        "CON_Moneda": con.CON_Moneda,
        "bajo_stock": con.CON_Stock_Minimo > 0 and con.CON_Stock_Actual <= con.CON_Stock_Minimo,
    }


class ConsumableService(BaseService[ConsumibleRepository]):
    repo_class = ConsumibleRepository

    def __init__(self, db: AsyncSession):
        super().__init__(db)
        self.gov_repo = GovernanceRepository(db)

    async def list(self, solo_bajo_stock: bool = False):
        items = await self.repo.get_all(solo_bajo_stock=solo_bajo_stock)
        return [_con_to_response(c) for c in items]

    async def get(self, id: int):
        obj = await self.get_or_404(id, "CONSUMABLE_NOT_FOUND")
        return _con_to_response(obj)

    async def _textos_de_catalogo(self, schema) -> None:
        """Categoría y unidad del catálogo: su nombre queda también en el texto legible."""
        from app.services.catalogo_generico import CatalogoGenericoService
        catalogos = CatalogoGenericoService(self.db)
        if schema.CCO_Categoria is not None:
            schema.CON_Categoria = (await catalogos.nombre_vigente("categorias-consumible", schema.CCO_Categoria))[:50]
        if schema.UME_Unidad is not None:
            schema.CON_Unidad = (await catalogos.nombre_vigente("unidades-medida", schema.UME_Unidad))[:20].lower()

    @transactional
    async def create(self, schema: ConsumibleCreate, usuario_id=None, ip=None):
        from app.core.data_scope import elevated, require_sede
        schema.SED_Sede = require_sede(schema.SED_Sede, required=False)
        # El nombre es único en toda la empresa, no solo en el alcance.
        async with elevated(self.db):
            existe = await self.repo.get_by_name(schema.CON_Nombre)
        if existe:
            raise HTTPException(409, "CONSUMABLE_ALREADY_EXISTS")
        if schema.CON_Costo_Unitario is not None and not schema.CON_Moneda:
            schema.CON_Moneda = "GTQ"
        await self._textos_de_catalogo(schema)
        obj = await self.repo.create(schema)
        if schema.CON_Stock_Actual > 0:
            # Saldo inicial: el kardex del consumible cuadra desde cero.
            await KardexService(self.db).registrar(
                "CONSUMIBLE_ENTRADA", consumible_id=obj.CON_Consumible, referencia=schema.CON_Nombre,
                cantidad=schema.CON_Stock_Actual, saldo=schema.CON_Stock_Actual,
                costo=schema.CON_Costo_Unitario, moneda=schema.CON_Moneda,
                saldo_valor=valoracion.valor_saldo(schema.CON_Stock_Actual, schema.CON_Costo_Unitario),
                destino={"sede": schema.SED_Sede}, motivo="Saldo inicial", usuario_id=usuario_id,
            )
        await self.gov_repo.create_audit_log(
            "CREATE", "INV_CONSUMIBLE",
            {"nombre": schema.CON_Nombre, "stock_inicial": schema.CON_Stock_Actual,
             "sede": schema.SED_Sede},
            usuario_id=usuario_id, ip_origen=ip, sede_id=schema.SED_Sede,
        )
        await self.db.refresh(obj)
        return _con_to_response(obj)

    @transactional
    async def update(self, id: int, schema: ConsumibleUpdate, usuario_id=None, ip=None):
        obj = await self.get_or_404(id, "CONSUMABLE_NOT_FOUND")
        sede_final = obj.SED_Sede
        if "SED_Sede" in schema.model_fields_set and schema.SED_Sede != obj.SED_Sede:
            from app.core.data_scope import require_sede
            schema.SED_Sede = require_sede(schema.SED_Sede, required=False)
            sede_final = schema.SED_Sede
        sede_antes = obj.SED_Sede
        stock, promedio, moneda_val, nombre = obj.CON_Stock_Actual, obj.CON_Costo_Unitario, obj.CON_Moneda, obj.CON_Nombre
        await self._textos_de_catalogo(schema)
        updated = await self.repo.update(id, schema)
        if sede_final != sede_antes:
            # Traslado de bodega: la existencia sale de una sede y entra en otra.
            await KardexService(self.db).registrar(
                "CONSUMIBLE_TRASLADO", consumible_id=id, referencia=nombre,
                cantidad=stock, saldo=stock, costo=promedio, moneda=moneda_val,
                saldo_valor=valoracion.valor_saldo(stock, promedio),
                origen={"sede": sede_antes}, destino={"sede": sede_final}, usuario_id=usuario_id,
            )
        await self.gov_repo.create_audit_log(
            "UPDATE", "INV_CONSUMIBLE",
            {"id": id, "cambios": schema.model_dump(exclude_unset=True)},
            usuario_id=usuario_id, ip_origen=ip, sede_id=sede_final,
        )
        return _con_to_response(updated)

    @transactional
    async def delete(self, id: int, usuario_id=None, ip=None):
        obj = await self.get_or_404(id, "CONSUMABLE_NOT_FOUND")
        # Si tiene historial de movimientos, no se borra (preserva trazabilidad).
        if await self.repo.count_movimientos(id) > 0:
            raise HTTPException(409, "CANNOT_DELETE_CONSUMABLE_HAS_MOVEMENTS")
        await self.repo.delete(id)
        await self.gov_repo.create_audit_log(
            "DELETE", "INV_CONSUMIBLE",
            {"id": id, "nombre": obj.CON_Nombre},
            usuario_id=usuario_id, ip_origen=ip, sede_id=obj.SED_Sede,
        )

    # ------------------------------------------------------------------
    # Stock
    # ------------------------------------------------------------------
    async def registrar_entrada(
        self, id: int, schema: StockMovimientoCreate,
        usuario_id: uuid.UUID | None = None, ip=None,
    ):
        return await self._mover_stock(id, schema, "ENTRADA", usuario_id, ip)

    async def registrar_salida(
        self, id: int, schema: StockMovimientoCreate,
        usuario_id: uuid.UUID | None = None, ip=None,
    ):
        return await self._mover_stock(id, schema, "SALIDA", usuario_id, ip)

    @transactional
    async def _mover_stock(self, id: int, schema, tipo: str, usuario_id, ip):
        obj = await self.get_or_404(id, "CONSUMABLE_NOT_FOUND")
        sede = obj.SED_Sede
        if not obj.CON_Activo:
            raise HTTPException(400, "CONSUMABLE_INACTIVE")
        if schema.PER_Persona is not None:
            from app.models.organization import Persona as _Persona
            if not await self.db.get(_Persona, schema.PER_Persona):
                raise HTTPException(404, "PERSON_NOT_FOUND")
        equipo = None
        if getattr(schema, "ACT_Activo", None) is not None:
            if tipo != "SALIDA":
                raise HTTPException(422, "ASSET_ONLY_ON_STOCK_OUT")
            equipo = await self._equipo_para_consumo(schema.ACT_Activo)

        # Capturar metadatos ANTES del UPDATE (que sincroniza el objeto en sesión).
        stock_antes = obj.CON_Stock_Actual
        nombre, minimo, unidad, categoria = (
            obj.CON_Nombre, obj.CON_Stock_Minimo, obj.CON_Unidad, obj.CON_Categoria,
        )
        # Costo promedio ponderado: se calcula antes de mover el stock (una
        # entrada en otra moneda se rechaza sin tocar nada).
        promedio, moneda_val, costo = obj.CON_Costo_Unitario, obj.CON_Moneda, obj.CON_Costo_Unitario
        if tipo == "ENTRADA":
            promedio, moneda_val, costo = valoracion.entrada(
                stock_antes, promedio, moneda_val, schema.MOC_Cantidad,
                getattr(schema, "precio_unitario", None), getattr(schema, "moneda", None),
            )

        if tipo == "ENTRADA":
            ok = await self.repo.incrementar_stock(id, schema.MOC_Cantidad)
        else:  # SALIDA — atómico, falla si no hay stock suficiente
            ok = await self.repo.decrementar_stock(id, schema.MOC_Cantidad)
            if not ok:
                raise HTTPException(409, "INSUFFICIENT_STOCK")
        if not ok:
            raise HTTPException(409, "STOCK_OPERATION_FAILED")

        # Releer el stock resultante para el snapshot del movimiento.
        refreshed = await self.repo.get_by_id(id)
        stock_despues = refreshed.CON_Stock_Actual
        mov = MovimientoConsumible(
            CON_Consumible=id,
            MOC_Tipo=tipo,
            MOC_Cantidad=schema.MOC_Cantidad,
            MOC_Stock_Resultante=stock_despues,
            MOC_Motivo=schema.MOC_Motivo,
            PER_Persona=schema.PER_Persona,
            ACT_Activo=equipo.ACT_Activo if equipo else None,
            USU_Usuario=usuario_id,
        )
        await self.repo.add_movimiento(mov)
        refreshed.CON_Costo_Unitario = promedio
        refreshed.CON_Moneda = moneda_val
        await KardexService(self.db).registrar(
            "CONSUMIBLE_ENTRADA" if tipo == "ENTRADA" else "CONSUMIBLE_SALIDA",
            consumible_id=id, referencia=nombre, cantidad=schema.MOC_Cantidad, saldo=stock_despues,
            costo=costo, moneda=moneda_val, saldo_valor=valoracion.valor_saldo(stock_despues, promedio),
            destino=({"persona": schema.PER_Persona, "sede": sede,
                      "activo": equipo.ACT_Activo if equipo else None, "area": equipo.ARE_Area_Ubicacion if equipo else None}
                     if tipo == "SALIDA" else {"sede": sede}),
            origen={"sede": sede} if tipo == "SALIDA" else None,
            motivo=schema.MOC_Motivo, usuario_id=usuario_id,
        )

        await self.gov_repo.create_audit_log(
            "STOCK_IN" if tipo == "ENTRADA" else "STOCK_OUT", "INV_CONSUMIBLE",
            {
                "id": id, "nombre": nombre, "cantidad": schema.MOC_Cantidad,
                "stock_resultante": stock_despues, "motivo": schema.MOC_Motivo,
                "equipo": equipo.ACT_Codigo_Interno if equipo else None,
            },
            usuario_id=usuario_id, ip_origen=ip, sede_id=sede,
        )
        await self.db.refresh(mov)

        # Alerta de stock bajo: solo al CRUZAR el umbral (evita spam si ya estaba bajo).
        cruzo = minimo > 0 and stock_antes > minimo and stock_despues <= minimo
        if cruzo:
            schedule_post_commit(
                self,
                lambda: self._notificar_stock_bajo(
                    nombre, stock_despues, minimo, unidad, categoria, usuario_id,
                ),
            )
        mov.bajo_minimo = minimo > 0 and stock_despues <= minimo
        mov.CON_Stock_Minimo = minimo
        return mov

    @transactional
    async def registrar_ajuste(self, id: int, schema, usuario_id=None, ip=None, aprobacion: dict | None = None,
                               stock_esperado: int | None = None):
        """
        Ajuste por conteo físico: la existencia pasa a CON_Stock_Real. Una
        disminución solo se ejecuta aprobada (AprobacionService); al aprobarla se
        exige que la existencia siga siendo la del momento de la solicitud.
        """
        obj = await self.get_or_404(id, "CONSUMABLE_NOT_FOUND")
        antes = obj.CON_Stock_Actual
        if stock_esperado is not None and antes != stock_esperado:
            raise HTTPException(409, "STOCK_CHANGED_SINCE_REQUEST")
        if schema.CON_Stock_Real == antes:
            raise HTTPException(400, "STOCK_ALREADY_MATCHES")
        if schema.CON_Stock_Real < antes and aprobacion is None:
            raise HTTPException(409, "STOCK_DECREASE_REQUIRES_APPROVAL")
        obj.CON_Stock_Actual = schema.CON_Stock_Real
        mov = MovimientoConsumible(
            CON_Consumible=id, MOC_Tipo="AJUSTE", MOC_Cantidad=abs(schema.CON_Stock_Real - antes),
            MOC_Stock_Resultante=schema.CON_Stock_Real, MOC_Motivo=schema.MOC_Motivo, USU_Usuario=usuario_id,
        )
        await self.repo.add_movimiento(mov)
        await KardexService(self.db).registrar(
            "CONSUMIBLE_AJUSTE", consumible_id=id, referencia=obj.CON_Nombre,
            cantidad=schema.CON_Stock_Real - antes, saldo=schema.CON_Stock_Real,
            costo=obj.CON_Costo_Unitario, moneda=obj.CON_Moneda,
            saldo_valor=valoracion.valor_saldo(schema.CON_Stock_Real, obj.CON_Costo_Unitario),
            destino={"sede": obj.SED_Sede}, motivo=schema.MOC_Motivo, usuario_id=usuario_id,
            detalle={"stock_antes": antes, "stock_despues": schema.CON_Stock_Real,
                     **({"aprobacion": {k: str(v) for k, v in aprobacion.items()}} if aprobacion else {})},
        )
        await self.gov_repo.create_audit_log(
            "STOCK_ADJUST", "INV_CONSUMIBLE",
            {"id": id, "nombre": obj.CON_Nombre, "antes": antes, "despues": schema.CON_Stock_Real,
             "motivo": schema.MOC_Motivo},
            usuario_id=usuario_id, ip_origen=ip, sede_id=obj.SED_Sede,
        )
        await self.db.flush()
        await self.db.refresh(mov)
        return mov

    async def _equipo_para_consumo(self, activo_id):
        """El equipo que recibe el consumible: visible, no dado de baja."""
        from app.models.catalogs import EstadoOperativo
        from app.models.core import Activo
        equipo = (await self.db.execute(select(Activo).where(Activo.ACT_Activo == activo_id))).scalar_one_or_none()
        if equipo is None:
            raise HTTPException(404, "ASSET_NOT_FOUND")
        estado = (await self.db.execute(select(EstadoOperativo.EOP_Nombre).where(
            EstadoOperativo.EOP_Estado_Operativo == equipo.EOP_Estado_Operativo))).scalar()
        if (estado or "").lower() == "baja":
            raise HTTPException(400, "ASSET_DECOMMISSIONED")
        return equipo

    # ------------------------------------------------------------------
    # Compatibilidad con modelos de equipo y consumo por equipo
    # ------------------------------------------------------------------
    async def compatibles(self, id: int) -> list[dict]:
        from app.models.catalogs import Marca, Modelo
        from app.models.consumable import ConsumibleCompatible
        await self.get_or_404(id, "CONSUMABLE_NOT_FOUND")
        filas = (await self.db.execute(
            select(Modelo.MOD_Modelo, Modelo.MOD_Nombre, Marca.MAR_Nombre)
            .join(ConsumibleCompatible, ConsumibleCompatible.MOD_Modelo == Modelo.MOD_Modelo)
            .join(Marca, Marca.MAR_Marca == Modelo.MAR_Marca)
            .where(ConsumibleCompatible.CON_Consumible == id).order_by(Marca.MAR_Nombre, Modelo.MOD_Nombre)
        )).all()
        return [{"MOD_Modelo": m, "modelo": n, "marca": mar} for m, n, mar in filas]

    @transactional
    async def fijar_compatibles(self, id: int, modelos: list[int], usuario_id=None, ip=None) -> list[dict]:
        from sqlalchemy import delete
        from app.models.catalogs import Modelo
        from app.models.consumable import ConsumibleCompatible
        obj = await self.get_or_404(id, "CONSUMABLE_NOT_FOUND")
        modelos = sorted(set(modelos))
        existentes = set((await self.db.execute(select(Modelo.MOD_Modelo).where(
            Modelo.MOD_Modelo.in_(modelos)))).scalars()) if modelos else set()
        if len(existentes) != len(modelos):
            raise HTTPException(404, "MODEL_NOT_FOUND")
        antes = sorted((await self.db.execute(select(ConsumibleCompatible.MOD_Modelo).where(
            ConsumibleCompatible.CON_Consumible == id))).scalars())
        await self.db.execute(delete(ConsumibleCompatible).where(ConsumibleCompatible.CON_Consumible == id))
        for m in modelos:
            self.db.add(ConsumibleCompatible(CON_Consumible=id, MOD_Modelo=m))
        await self.gov_repo.create_audit_log(
            "UPDATE", "INV_CONSUMIBLE_COMPATIBLE", {"id": id, "nombre": obj.CON_Nombre, "antes": antes, "despues": modelos},
            usuario_id=usuario_id, ip_origen=ip, sede_id=obj.SED_Sede,
        )
        await self.db.flush()
        return await self.compatibles(id)

    async def consumo_de_equipo(self, activo_id) -> dict:
        """Consumibles instalados en un equipo (historial) y los compatibles con su modelo."""
        from app.models.consumable import Consumible, ConsumibleCompatible
        from app.models.core import Activo
        from app.models.organization import Usuario
        equipo = (await self.db.execute(select(Activo).where(Activo.ACT_Activo == activo_id))).scalar_one_or_none()
        if equipo is None:
            raise HTTPException(404, "ASSET_NOT_FOUND")
        historial = (await self.db.execute(
            select(MovimientoConsumible, Consumible.CON_Nombre, Consumible.CON_Unidad, Usuario.USU_Username)
            .join(Consumible, Consumible.CON_Consumible == MovimientoConsumible.CON_Consumible)
            .outerjoin(Usuario, Usuario.USU_Usuario == MovimientoConsumible.USU_Usuario)
            .where(MovimientoConsumible.ACT_Activo == activo_id)
            .order_by(MovimientoConsumible.MOC_Fecha.desc()).limit(200)
        )).all()
        compatibles = (await self.db.execute(
            select(Consumible).join(ConsumibleCompatible, ConsumibleCompatible.CON_Consumible == Consumible.CON_Consumible)
            .where(ConsumibleCompatible.MOD_Modelo == equipo.MOD_Modelo, Consumible.CON_Activo.is_(True))
            .order_by(Consumible.CON_Nombre)
        )).scalars().all()
        return {
            "historial": [{"MOC_Movimiento": m.MOC_Movimiento, "MOC_Fecha": m.MOC_Fecha, "CON_Consumible": m.CON_Consumible,
                           "consumible": n, "unidad": u, "MOC_Cantidad": m.MOC_Cantidad, "MOC_Motivo": m.MOC_Motivo,
                           "usuario": usu} for m, n, u, usu in historial],
            "compatibles": [{"CON_Consumible": c.CON_Consumible, "CON_Nombre": c.CON_Nombre,
                             "CON_Stock_Actual": c.CON_Stock_Actual, "CON_Unidad": c.CON_Unidad,
                             "SED_Sede": c.SED_Sede} for c in compatibles],
        }

    async def _notificar_stock_bajo(self, nombre, stock_actual, minimo, unidad, categoria, usuario_id):
        """Email post-commit fire-and-forget a admins. No bloquea ni revierte."""
        try:
            from sqlalchemy import select as _select
            from app.core.email import send_notification
            from app.models.organization import Persona, Usuario

            op_name, op_role, op_email = "Sistema", "", None
            if usuario_id:
                usu = (await self.db.execute(
                    _select(Usuario).where(Usuario.USU_Usuario == usuario_id)
                )).scalar_one_or_none()
                if usu:
                    op_role = usu.USU_Rol or ""
                    per = (await self.db.execute(
                        _select(Persona).where(Persona.PER_Persona == usu.PER_Persona)
                    )).scalar_one_or_none()
                    if per:
                        op_name = f"{per.PER_Primer_Nombre} {per.PER_Primer_Apellido}"
                        op_email = per.PER_Email_Corporativo
            await send_notification(
                "stock_bajo",
                {"codigo": nombre, "stock_actual": stock_actual, "stock_minimo": minimo,
                 "unidad": unidad, "categoria": categoria or ""},
                to=(),  # solo admins
                reply_to=op_email, operator_name=op_name, operator_role=op_role,
            )
        except Exception:  # noqa: BLE001
            pass

    async def list_movimientos(self, id: int):
        await self.get_or_404(id, "CONSUMABLE_NOT_FOUND")
        return await self.repo.get_movimientos(id)
