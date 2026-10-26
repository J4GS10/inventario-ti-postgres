"""Servicio de Compras: Proveedores, Órdenes de Compra y vista de Garantías."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.transactional import transactional
from app.models.consumable import MovimientoConsumible
from app.models.procurement import OrdenCompra, OrdenCompraLinea, OrdenCompraLineaActivo
from app.repositories.catalogs import CatalogRepository
from app.repositories.consumable import ConsumibleRepository
from app.repositories.core import CoreRepository
from app.repositories.governance import GovernanceRepository
from app.repositories.procurement import ProcurementRepository
from app.schemas.core import ActivoCreate
from app.schemas.procurement import (
    OrdenCreate, ProveedorCreate, ProveedorUpdate, RecepcionOrden,
)
from app.schemas.software import LicenciaCreate
from app.services.base import BaseService
from app.services.software import SoftwareService


class ProcurementService(BaseService[ProcurementRepository]):
    repo_class = ProcurementRepository

    def __init__(self, db: AsyncSession):
        super().__init__(db)
        self.gov_repo = GovernanceRepository(db)

    # =====================================================================
    # PROVEEDOR
    # =====================================================================
    async def list_proveedores(self, solo_activos: bool = False):
        return await self.repo.get_proveedores(solo_activos=solo_activos)

    async def get_proveedor(self, id: int):
        obj = await self.repo.get_proveedor(id)
        if not obj:
            raise HTTPException(404, "SUPPLIER_NOT_FOUND")
        return obj

    @transactional
    async def create_proveedor(self, schema: ProveedorCreate, usuario_id=None, ip=None):
        if await self.repo.get_proveedor_by_name(schema.PRV_Nombre):
            raise HTTPException(409, "SUPPLIER_ALREADY_EXISTS")
        obj = await self.repo.create_proveedor(schema)
        await self.gov_repo.create_audit_log(
            "CREATE", "INV_PROVEEDOR", {"nombre": schema.PRV_Nombre},
            usuario_id=usuario_id, ip_origen=ip,
        )
        await self.db.refresh(obj)
        return obj

    @transactional
    async def update_proveedor(self, id: int, schema: ProveedorUpdate, usuario_id=None, ip=None):
        if not await self.repo.get_proveedor(id):
            raise HTTPException(404, "SUPPLIER_NOT_FOUND")
        obj = await self.repo.update_proveedor(id, schema)
        await self.gov_repo.create_audit_log(
            "UPDATE", "INV_PROVEEDOR",
            {"id": id, "cambios": schema.model_dump(exclude_unset=True, mode="json")},
            usuario_id=usuario_id, ip_origen=ip,
        )
        return obj

    @transactional
    async def delete_proveedor(self, id: int, usuario_id=None, ip=None):
        prov = await self.repo.get_proveedor(id)
        if not prov:
            raise HTTPException(404, "SUPPLIER_NOT_FOUND")
        if await self.repo.count_ordenes_de_proveedor(id) > 0:
            raise HTTPException(409, "CANNOT_DELETE_SUPPLIER_HAS_ORDERS")
        await self.repo.delete_proveedor(id)
        await self.gov_repo.create_audit_log(
            "DELETE", "INV_PROVEEDOR", {"id": id, "nombre": prov.PRV_Nombre},
            usuario_id=usuario_id, ip_origen=ip,
        )

    # =====================================================================
    # ORDEN DE COMPRA
    # =====================================================================
    async def list_ordenes(self, estado: str | None = None):
        return await self.repo.list_ordenes(estado=estado)

    async def get_orden(self, id: int):
        obj = await self.repo.get_orden(id, with_lineas=True)
        if not obj:
            raise HTTPException(404, "PURCHASE_ORDER_NOT_FOUND")
        return obj

    @transactional
    async def create_orden(self, schema: OrdenCreate, usuario_id=None, ip=None):
        from app.core.data_scope import elevated, require_sede
        sede = require_sede(schema.SED_Sede, required=False)
        if not await self.repo.get_proveedor(schema.PRV_Proveedor):
            raise HTTPException(404, "SUPPLIER_NOT_FOUND")
        # El número de orden es único en toda la empresa, no solo en el alcance.
        async with elevated(self.db):
            numero_existe = await self.repo.get_orden_by_numero(schema.OCO_Numero)
        if numero_existe:
            raise HTTPException(409, "PURCHASE_ORDER_NUMBER_EXISTS")
        # Las líneas solo pueden referenciar activos y consumibles visibles.
        core_repo = CoreRepository(self.db)
        con_repo = ConsumibleRepository(self.db)
        for ln in schema.lineas:
            if ln.ACT_Activo and not await core_repo.get_by_id_simple(ln.ACT_Activo):
                raise HTTPException(404, "ASSET_NOT_FOUND")
            if ln.CON_Consumible and not await con_repo.get_by_id(ln.CON_Consumible):
                raise HTTPException(404, "CONSUMABLE_NOT_FOUND")

        orden = OrdenCompra(
            OCO_Numero=schema.OCO_Numero,
            OCO_Fecha=schema.OCO_Fecha,
            OCO_Moneda=schema.OCO_Moneda,
            OCO_Notas=schema.OCO_Notas,
            PRV_Proveedor=schema.PRV_Proveedor,
            USU_Usuario=usuario_id,
            OCO_Estado="BORRADOR",
            SED_Sede=sede,
        )
        total = Decimal("0")
        for ln in schema.lineas:
            subtotal = (Decimal(ln.OCL_Precio_Unitario) * ln.OCL_Cantidad).quantize(Decimal("0.01"))
            total += subtotal
            orden.lineas.append(OrdenCompraLinea(
                OCL_Descripcion=ln.OCL_Descripcion,
                OCL_Cantidad=ln.OCL_Cantidad,
                OCL_Precio_Unitario=Decimal(ln.OCL_Precio_Unitario),
                OCL_Subtotal=subtotal,
                OCL_Tipo=ln.OCL_Tipo,
                ACT_Activo=ln.ACT_Activo,
                CON_Consumible=ln.CON_Consumible,
                LIC_Licencia=ln.LIC_Licencia,
            ))
        orden.OCO_Total = total

        await self.repo.add_orden(orden)
        await self.gov_repo.create_audit_log(
            "CREATE", "INV_ORDEN_COMPRA",
            {"numero": schema.OCO_Numero, "proveedor_id": schema.PRV_Proveedor,
             "moneda": schema.OCO_Moneda, "sede": sede,
             "total": str(total), "lineas": len(schema.lineas)},
            usuario_id=usuario_id, ip_origen=ip, sede_id=sede,
        )
        return await self.repo.get_orden(orden.OCO_Orden, with_lineas=True)

    @transactional
    async def cambiar_estado(self, id: int, estado: str, usuario_id=None, ip=None, motivo: str | None = None,
                             aprobacion: dict | None = None):
        """Cancela un borrador. Solo se ejecuta aprobada (AprobacionService.solicitar_cancelacion_orden)."""
        if estado == "CANCELADA" and aprobacion is None:
            raise HTTPException(409, "ORDER_CANCEL_REQUIRES_APPROVAL")
        orden = await self.repo.get_orden(id, with_lineas=True)
        if not orden:
            raise HTTPException(404, "PURCHASE_ORDER_NOT_FOUND")
        if orden.OCO_Estado == "CANCELADA":
            raise HTTPException(409, "ORDER_ALREADY_CANCELLED")
        # Máquina de estados de la orden: por aquí solo se cancela un borrador.
        # "Parcial"/"Recibida" se alcanzan únicamente recibiendo (POST /recibir)
        # y "Cerrada con faltante" con POST /cerrar. Recibida, Cerrada y
        # Cancelada son finales: reabrir una orden recibida permitía recibirla
        # dos veces y duplicar stock y activos.
        if orden.OCO_Estado == estado:
            return orden
        if orden.OCO_Estado == "PARCIAL" and estado == "CANCELADA":
            raise HTTPException(409, "ORDER_HAS_RECEIPTS_CLOSE_WITH_SHORTAGE")
        if not (orden.OCO_Estado == "BORRADOR" and estado == "CANCELADA"):
            raise HTTPException(409, "ORDER_STATE_TRANSITION_NOT_ALLOWED")
        await self.repo.set_estado_orden(id, estado)
        if motivo:
            orden.OCO_Motivo_Cierre = motivo
        await self.gov_repo.create_audit_log(
            "UPDATE", "INV_ORDEN_COMPRA",
            {"id": id, "diff": {"OCO_Estado": {"antes": orden.OCO_Estado, "despues": estado}}, "motivo": motivo,
             "aprobacion": {k: str(v) for k, v in (aprobacion or {}).items()} or None},
            usuario_id=usuario_id, ip_origen=ip, sede_id=orden.SED_Sede,
        )
        return await self.repo.get_orden(id, with_lineas=True)

    @transactional
    async def cerrar_con_faltante(self, id: int, motivo: str, usuario_id=None, ip=None, aprobacion: dict | None = None):
        """Cierra una orden PARCIAL aceptando que lo pendiente no llegará (solo aprobada)."""
        if aprobacion is None:
            raise HTTPException(409, "ORDER_CLOSE_REQUIRES_APPROVAL")
        orden = await self.repo.get_orden(id, with_lineas=True, lock=True)
        if not orden:
            raise HTTPException(404, "PURCHASE_ORDER_NOT_FOUND")
        if orden.OCO_Estado != "PARCIAL":
            raise HTTPException(409, "ONLY_PARTIAL_ORDERS_CAN_BE_CLOSED")
        faltantes = [
            {"linea": ln.OCL_Linea, "descripcion": ln.OCL_Descripcion,
             "pendiente": ln.OCL_Cantidad - (ln.OCL_Cantidad_Recibida or 0)}
            for ln in orden.lineas if (ln.OCL_Cantidad_Recibida or 0) < ln.OCL_Cantidad
        ]
        await self.repo.set_estado_orden(id, "CERRADA_CON_FALTANTE")
        orden.OCO_Motivo_Cierre = motivo
        await self.gov_repo.create_audit_log(
            "CLOSE_WITH_SHORTAGE", "INV_ORDEN_COMPRA",
            {"id": id, "numero": orden.OCO_Numero, "motivo": motivo, "faltantes": faltantes,
             "aprobacion": {k: str(v) for k, v in aprobacion.items()}},
            usuario_id=usuario_id, ip_origen=ip, sede_id=orden.SED_Sede,
        )
        self._avisar_diferencias(orden, "cierre_con_faltante", faltantes, [], motivo)
        return await self.repo.get_orden(id, with_lineas=True)

    async def list_recepciones(self, id: int):
        await self.get_orden(id)
        from sqlalchemy import select as _select
        from sqlalchemy.orm import selectinload
        from app.models.procurement import Recepcion
        return (await self.db.execute(
            _select(Recepcion).options(selectinload(Recepcion.lineas))
            .where(Recepcion.OCO_Orden == id).order_by(Recepcion.REC_Fecha, Recepcion.REC_Recepcion)
        )).scalars().all()

    def _avisar_diferencias(self, orden, motivo_aviso: str, faltantes: list, diferencias: list, texto=None) -> None:
        """Aviso post-commit a los administradores (faltantes, sobrantes, precios)."""
        if not faltantes and not diferencias:
            return
        from app.core.email import send_notification
        from app.core.transactional import schedule_post_commit
        contexto = {
            "numero": orden.OCO_Numero, "motivo_aviso": motivo_aviso,
            "faltantes": faltantes, "diferencias": diferencias, "motivo": texto or "",
        }
        schedule_post_commit(self, lambda: send_notification("recepcion_diferencias", contexto, to=()))

    # =====================================================================
    # RECEPCIÓN — documento de entrada con control de diferencias
    # =====================================================================
    @transactional
    async def recibir_orden(self, id: int, schema: RecepcionOrden, usuario_id=None, ip=None,
                            aprobacion: dict | None = None):
        """
        Registra una ENTREGA del proveedor (una orden puede recibirse en varias):

        - Cada línea acepta a lo sumo lo pendiente (pedido − recibido). Recibir
          de más solo con una diferencia justificada y aceptar_sobrante.
        - Un precio real fuera de la tolerancia (RECEPCION_TOLERANCIA_PRECIO_PCT)
          exige una justificación.
        - Una línea solo recibe lo que es (activo, consumible o licencia) y, si
          la línea indica el consumible, solo ese consumible.
        - La orden queda RECIBIDA si no falta nada, o PARCIAL con lo pendiente.
        Todo en una transacción: si algo falla, no entra nada.
        """
        from app.core.config import settings
        from app.core.data_scope import elevated
        from app.models.procurement import Recepcion, RecepcionLinea
        from app.services import valoracion
        from app.services.kardex import KardexService

        orden = await self.repo.get_orden(id, with_lineas=True, lock=True)
        if not orden:
            raise HTTPException(404, "PURCHASE_ORDER_NOT_FOUND")
        if orden.OCO_Estado not in ("BORRADOR", "PARCIAL"):
            raise HTTPException(409, "ORDER_NOT_RECEIVABLE")  # recibida, cerrada o cancelada
        if not (schema.consumibles or schema.activos or schema.licencias or schema.otros):
            raise HTTPException(400, "EMPTY_RECEIPT")

        lineas_validas = {ln.OCL_Linea: ln for ln in orden.lineas}
        justificacion = {d.OCL_Linea: d for d in schema.diferencias}
        kardex = KardexService(self.db)
        con_repo = ConsumibleRepository(self.db)
        core_repo = CoreRepository(self.db)
        cat_repo = CatalogRepository(self.db)
        sw_service = SoftwareService(self.db)
        documento = ("RECEPCION", f"OC {orden.OCO_Numero}" + (f" · {schema.documento}" if schema.documento else ""))

        # --- 0. Validaciones por línea: tipo, cantidad y precio ---
        por_linea: dict[int, dict] = {}

        def acumular(linea_id: int, tipo: str, cantidad: int, precio) -> None:
            linea = lineas_validas.get(linea_id)
            if linea is None:
                raise HTTPException(400, "LINE_NOT_IN_ORDER")
            if linea.OCL_Tipo and linea.OCL_Tipo != tipo:
                raise HTTPException(400, f"LINE_TYPE_MISMATCH:{linea_id}")
            item = por_linea.setdefault(linea_id, {"tipo": tipo, "cantidad": 0, "precios": []})
            if item["tipo"] != tipo:
                raise HTTPException(400, f"LINE_TYPE_MISMATCH:{linea_id}")
            item["cantidad"] += cantidad
            if precio is not None:
                item["precios"].append(Decimal(precio))

        for rc in schema.consumibles:
            acumular(rc.OCL_Linea, "CONSUMIBLE", rc.cantidad, rc.precio_unitario)
            linea = lineas_validas[rc.OCL_Linea]
            if linea.CON_Consumible and linea.CON_Consumible != rc.CON_Consumible:
                raise HTTPException(400, f"CONSUMABLE_DOES_NOT_MATCH_LINE:{rc.OCL_Linea}")
        for ra in schema.activos:
            acumular(ra.OCL_Linea, "ACTIVO", 1, ra.ACT_Costo)
        for rl in schema.licencias:
            acumular(rl.OCL_Linea, "LICENCIA", int(rl.cantidad or 0), rl.precio_unitario)
        # Fletes, instalación o servicios: solo se da conformidad (sin inventario).
        for ro in schema.otros:
            acumular(ro.OCL_Linea, "OTRO", ro.cantidad, ro.precio_unitario)

        tolerancia = Decimal(str(settings.RECEPCION_TOLERANCIA_PRECIO_PCT)) / Decimal(100)
        diferencias: list[dict] = []
        for linea_id, item in por_linea.items():
            linea = lineas_validas[linea_id]
            pendiente = linea.OCL_Cantidad - (linea.OCL_Cantidad_Recibida or 0)
            item["diferencia"] = None
            if item["cantidad"] > pendiente:
                just = justificacion.get(linea_id)
                if not (just and just.aceptar_sobrante):
                    raise HTTPException(400, f"RECEIPT_EXCEEDS_PENDING:{linea_id}:{max(pendiente, 0)}")
                item["diferencia"] = "SOBRANTE"
                diferencias.append({
                    "OCL_Linea": linea_id, "tipo": "SOBRANTE", "motivo": just.motivo,
                    "detalle": f"{linea.OCL_Descripcion}: pendiente {max(pendiente, 0)}, recibido {item['cantidad']}",
                })
            precio_orden = Decimal(linea.OCL_Precio_Unitario or 0)
            fuera = [p for p in item["precios"] if abs(p - precio_orden) > precio_orden * tolerancia]
            if fuera:
                just = justificacion.get(linea_id)
                if not just:
                    raise HTTPException(400, f"PRICE_DIFFERENCE_REQUIRES_JUSTIFICATION:{linea_id}")
                item["diferencia"] = item["diferencia"] or "PRECIO"
                diferencias.append({
                    "OCL_Linea": linea_id, "tipo": "PRECIO", "motivo": just.motivo,
                    "detalle": f"{linea.OCL_Descripcion}: orden {precio_orden} {orden.OCO_Moneda}, "
                               f"real {fuera[0]} {orden.OCO_Moneda}",
                })
            item["precio_real"] = item["precios"][0] if item["precios"] else None

        series_payload = [ra.ACT_Serie_Fabricante.strip().lower() for ra in schema.activos]
        if len(series_payload) != len(set(series_payload)):
            raise HTTPException(400, "DUPLICATE_SERIAL_NUMBER_IN_RECEIPT")

        # Separación de funciones: un sobrante no entra hasta que el Jefe de TI
        # (otra persona) lo aprueba. Se guarda la entrega tal cual y no se toca
        # el inventario; al aprobarse se ejecuta con estos mismos datos.
        sobrantes = [d for d in diferencias if d["tipo"] == "SOBRANTE"]
        if sobrantes and not aprobacion:
            from app.services.aprobaciones import AprobacionService
            solicitud = await AprobacionService(self.db).crear(
                "RECEPCION_SOBRANTE", payload=schema.model_dump(mode="json"), solicitante=usuario_id,
                resumen=(f"Sobrante en la OC {orden.OCO_Numero}: " + "; ".join(d["detalle"] for d in sobrantes))[:500],
                motivo="; ".join(d["motivo"] or "" for d in sobrantes)[:500],
                orden_id=id, sede=orden.SED_Sede, ip=ip,
            )
            return {
                "OCO_Orden": id, "OCO_Estado": orden.OCO_Estado, "REC_Recepcion": None,
                "consumibles_reabastecidos": 0, "activos_creados": 0,
                "licencias_creadas": 0, "licencias_claves_creadas": 0, "activos_codigos": [],
                "pendientes": [
                    {"OCL_Linea": ln.OCL_Linea, "descripcion": ln.OCL_Descripcion, "tipo": ln.OCL_Tipo,
                     "pedido": ln.OCL_Cantidad, "recibido": ln.OCL_Cantidad_Recibida or 0,
                     "pendiente": ln.OCL_Cantidad - (ln.OCL_Cantidad_Recibida or 0)}
                    for ln in orden.lineas if (ln.OCL_Cantidad_Recibida or 0) < ln.OCL_Cantidad
                ],
                "diferencias": diferencias,
                "aprobacion_pendiente": solicitud.APR_Aprobacion,
            }

        # --- 1. Consumibles: sumar stock (entrada auditada) ---
        reabastecidos = 0
        for rc in schema.consumibles:
            con = await con_repo.get_by_id(rc.CON_Consumible)
            if not con:
                raise HTTPException(404, "CONSUMABLE_NOT_FOUND")
            linea = lineas_validas[rc.OCL_Linea]
            precio = rc.precio_unitario if rc.precio_unitario is not None else linea.OCL_Precio_Unitario
            # Costo promedio ponderado en la moneda de valoración del consumible.
            promedio, moneda_val, costo = valoracion.entrada(
                con.CON_Stock_Actual, con.CON_Costo_Unitario, con.CON_Moneda, rc.cantidad, precio, orden.OCO_Moneda,
            )
            await con_repo.incrementar_stock(rc.CON_Consumible, rc.cantidad)
            refreshed = await con_repo.get_by_id(rc.CON_Consumible)
            refreshed.CON_Costo_Unitario = promedio
            refreshed.CON_Moneda = moneda_val
            await con_repo.add_movimiento(MovimientoConsumible(
                CON_Consumible=rc.CON_Consumible, MOC_Tipo="ENTRADA",
                MOC_Cantidad=rc.cantidad, MOC_Stock_Resultante=refreshed.CON_Stock_Actual,
                MOC_Motivo=f"Recepción OC {orden.OCO_Numero}", USU_Usuario=usuario_id,
            ))
            await kardex.registrar(
                "CONSUMIBLE_ENTRADA", consumible_id=con.CON_Consumible, referencia=con.CON_Nombre,
                cantidad=rc.cantidad, saldo=refreshed.CON_Stock_Actual, costo=costo, moneda=moneda_val,
                saldo_valor=valoracion.valor_saldo(refreshed.CON_Stock_Actual, promedio),
                destino={"sede": con.SED_Sede}, documento=documento, usuario_id=usuario_id,
                motivo=f"Recepción OC {orden.OCO_Numero}", detalle=aprobacion,
            )
            linea.CON_Consumible = rc.CON_Consumible
            await self.gov_repo.create_audit_log(
                "STOCK_IN", "INV_CONSUMIBLE",
                {"id": rc.CON_Consumible, "cantidad": rc.cantidad,
                 "stock_resultante": refreshed.CON_Stock_Actual,
                 "motivo": f"Recepción OC {orden.OCO_Numero}"},
                usuario_id=usuario_id, ip_origen=ip, sede_id=con.SED_Sede,
            )
            reabastecidos += 1

        # --- 2. Activos: alta + enlace a la línea (estado Disponible) ---
        from app.models.catalogs import EstadoOperativo
        from sqlalchemy import select as _select
        estado_disp = (await self.db.execute(
            _select(EstadoOperativo).where(EstadoOperativo.EOP_Nombre.ilike("Disponible"))
        )).scalar_one_or_none()
        if schema.activos and not estado_disp:
            # Fail-closed: sin el estado canónico, no corrompemos el alta.
            raise HTTPException(500, "SYSTEM_CONFIGURATION_ERROR_MISSING_STATUS_DISPONIBLE")

        codigos_creados: list[str] = []
        for ra in schema.activos:
            tipo = await cat_repo.get_tipo_activo_by_id(ra.TAC_Tipo_Activo)
            if not tipo:
                raise HTTPException(404, "ASSET_TYPE_NOT_FOUND")
            if not await cat_repo.get_modelo_by_id(ra.MOD_Modelo):
                raise HTTPException(404, "MODEL_NOT_FOUND")

            codigo = ra.ACT_Codigo_Interno
            if not codigo:
                if not tipo.TAC_Prefijo:
                    raise HTTPException(400, "ASSET_TYPE_HAS_NO_PREFIX_CONFIGURED_FOR_AUTO_GENERATION")
                codigo = await self.gov_repo.get_next_code(
                    contexto=f"ASSET_{tipo.TAC_Prefijo}", prefijo=tipo.TAC_Prefijo)
            # Código y serie son únicos en toda la empresa.
            async with elevated(self.db):
                codigo_existe = await core_repo.get_by_codigo_interno(codigo)
                serie_existe = await core_repo.get_by_serie(ra.ACT_Serie_Fabricante)
            if codigo_existe:
                raise HTTPException(400, "ASSET_CODE_ALREADY_EXISTS")
            if serie_existe:
                raise HTTPException(400, "SERIAL_NUMBER_ALREADY_EXISTS")
            if ra.ACT_Fin_Garantia and ra.ACT_Fin_Garantia < ra.ACT_Fecha_Compra:
                raise HTTPException(400, "WARRANTY_DATE_CANNOT_BE_BEFORE_PURCHASE_DATE")

            linea = lineas_validas[ra.OCL_Linea]
            costo = ra.ACT_Costo if ra.ACT_Costo is not None else linea.OCL_Precio_Unitario
            nuevo = await core_repo.create_activo(ActivoCreate(
                ACT_Codigo_Interno=codigo,
                ACT_Serie_Fabricante=ra.ACT_Serie_Fabricante,
                ACT_Hostname=ra.ACT_Hostname,
                ACT_Fecha_Compra=ra.ACT_Fecha_Compra,
                ACT_Fin_Garantia=ra.ACT_Fin_Garantia,
                ACT_Costo=costo,
                # El costo del activo está en la moneda en que se pagó la orden.
                ACT_Moneda=orden.OCO_Moneda,
                MOD_Modelo=ra.MOD_Modelo,
                TAC_Tipo_Activo=ra.TAC_Tipo_Activo,
                EOP_Estado_Operativo=estado_disp.EOP_Estado_Operativo,
                SED_Sede=orden.SED_Sede,
            ))
            # Enlazar la línea al activo recién creado (alimenta garantías↔proveedor).
            if linea.ACT_Activo is None:
                linea.ACT_Activo = nuevo.ACT_Activo
            self.db.add(OrdenCompraLineaActivo(OCL_Linea=ra.OCL_Linea, ACT_Activo=nuevo.ACT_Activo))
            await kardex.registrar(
                "RECEPCION", activo=nuevo, destino={"sede": orden.SED_Sede},
                costo=costo, moneda=orden.OCO_Moneda, documento=documento, usuario_id=usuario_id,
                motivo=f"Recepción OC {orden.OCO_Numero}", detalle=aprobacion,
            )
            await self.gov_repo.create_audit_log(
                "CREATE", "INV_ACTIVO",
                {"codigo": codigo, "serie": ra.ACT_Serie_Fabricante,
                 "origen": f"Recepción OC {orden.OCO_Numero}", "sede": orden.SED_Sede},
                usuario_id=usuario_id, ip_origen=ip, sede_id=orden.SED_Sede,
            )
            codigos_creados.append(codigo)

        # --- 3. Licencias: lote agregado + claves individuales cifradas ---
        licencias_creadas = 0
        claves_creadas = 0
        for rl in schema.licencias:
            linea = lineas_validas[rl.OCL_Linea]
            precio = rl.precio_unitario if rl.precio_unitario is not None else linea.OCL_Precio_Unitario
            licencia = await sw_service.create_licencia_in_transaction(
                LicenciaCreate(
                    LIC_Clave_Activacion=rl.LIC_Clave_Activacion,
                    LIC_Fecha_Vencimiento=rl.LIC_Fecha_Vencimiento,
                    LIC_Cantidad_Total=int(rl.cantidad or 0),
                    SOF_Software=rl.SOF_Software,
                    TLI_Tipo_Licencia=rl.TLI_Tipo_Licencia,
                    LIC_Claves=rl.claves,
                    LIC_Costo=(Decimal(precio) * int(rl.cantidad or 0)) if precio is not None else None,
                    LIC_Moneda=orden.OCO_Moneda,
                ),
                usuario_id=usuario_id,
                ip=ip,
                origen=f"Recepcion OC {orden.OCO_Numero}",
                documento=documento,
                sede=orden.SED_Sede,
            )
            linea.LIC_Licencia = licencia.LIC_Licencia
            licencias_creadas += 1
            claves_creadas += len(rl.claves)

        # --- 4. Documento de entrada, acumulados y estado de la orden ---
        for linea_id, item in por_linea.items():
            linea = lineas_validas[linea_id]
            linea.OCL_Cantidad_Recibida = (linea.OCL_Cantidad_Recibida or 0) + item["cantidad"]
            if not linea.OCL_Tipo:
                linea.OCL_Tipo = item["tipo"]
        pendientes = [
            {"OCL_Linea": ln.OCL_Linea, "descripcion": ln.OCL_Descripcion, "tipo": ln.OCL_Tipo,
             "pedido": ln.OCL_Cantidad, "recibido": ln.OCL_Cantidad_Recibida or 0,
             "pendiente": ln.OCL_Cantidad - (ln.OCL_Cantidad_Recibida or 0)}
            for ln in orden.lineas if (ln.OCL_Cantidad_Recibida or 0) < ln.OCL_Cantidad
        ]
        nuevo_estado = "PARCIAL" if pendientes else "RECIBIDA"
        recepcion = Recepcion(
            OCO_Orden=id, REC_Documento=schema.documento,
            REC_Observacion=(" · ".join(x for x in (
                schema.observacion,
                f"Sobrante aprobado (solicitud #{aprobacion['aprobacion']})" if aprobacion else None,
            ) if x) or None),
            REC_Resultado="PARCIAL" if pendientes else "COMPLETA", USU_Usuario=usuario_id,
        )
        for linea_id, item in por_linea.items():
            just = justificacion.get(linea_id)
            recepcion.lineas.append(RecepcionLinea(
                OCL_Linea=linea_id, RCL_Cantidad=item["cantidad"], RCL_Precio_Real=item["precio_real"],
                RCL_Diferencia=item["diferencia"], RCL_Motivo=just.motivo if (just and item["diferencia"]) else None,
            ))
        self.db.add(recepcion)
        await self.db.flush()

        estado_anterior = orden.OCO_Estado
        await self.repo.set_estado_orden(id, nuevo_estado)
        await self.gov_repo.create_audit_log(
            "RECEIVE", "INV_ORDEN_COMPRA",
            {"id": id, "numero": orden.OCO_Numero, "recepcion": recepcion.REC_Recepcion,
             "documento": schema.documento,
             "diff": {"OCO_Estado": {"antes": estado_anterior, "despues": nuevo_estado}},
             "consumibles_reabastecidos": reabastecidos,
             "activos_creados": len(codigos_creados),
             "licencias_creadas": licencias_creadas,
             "licencias_claves_creadas": claves_creadas,
             "pendientes": pendientes, "diferencias": diferencias,
             "aprobacion": {k: str(v) for k, v in (aprobacion or {}).items()} or None},
            usuario_id=usuario_id, ip_origen=ip, sede_id=orden.SED_Sede,
        )
        self._avisar_diferencias(orden, "recepcion", pendientes if diferencias else [], diferencias)
        return {
            "OCO_Orden": id, "OCO_Estado": nuevo_estado, "REC_Recepcion": recepcion.REC_Recepcion,
            "consumibles_reabastecidos": reabastecidos,
            "activos_creados": len(codigos_creados),
            "licencias_creadas": licencias_creadas,
            "licencias_claves_creadas": claves_creadas,
            "activos_codigos": codigos_creados,
            "pendientes": pendientes,
            "diferencias": diferencias,
        }

    # =====================================================================
    # GARANTÍAS
    # =====================================================================
    async def garantias(self, dias: int = 90, solo_alertas: bool = False):
        """
        Lista activos con su estado de garantía. `dias` define la ventana
        "por vencer". `solo_alertas` filtra a por_vencer + vencida.
        """
        rows = await self.repo.garantias()
        hoy = date.today()
        umbral = hoy + timedelta(days=dias)

        vistos: set = set()
        items = []
        for r in rows:
            act_id = r[0]
            if act_id in vistos:
                continue
            vistos.add(act_id)
            fin = r[4]
            if fin is None:
                estado, dias_rest = "sin_garantia", None
            elif fin < hoy:
                estado, dias_rest = "vencida", (fin - hoy).days
            elif fin <= umbral:
                estado, dias_rest = "por_vencer", (fin - hoy).days
            else:
                estado, dias_rest = "vigente", (fin - hoy).days

            if solo_alertas and estado not in ("por_vencer", "vencida"):
                continue

            items.append({
                "ACT_Activo": act_id,
                "ACT_Codigo_Interno": r[1],
                "ACT_Serie_Fabricante": r[2],
                "ACT_Fecha_Compra": r[3],
                "ACT_Fin_Garantia": fin,
                "dias_restantes": dias_rest,
                "estado_garantia": estado,
                "proveedor": r[5],
            })

        # Orden: alertas primero (vencida, por_vencer), luego vigente, luego sin.
        prioridad = {"vencida": 0, "por_vencer": 1, "vigente": 2, "sin_garantia": 3}
        items.sort(key=lambda x: (prioridad[x["estado_garantia"]],
                                  x["dias_restantes"] if x["dias_restantes"] is not None else 10**9))
        return items

    async def notificar_garantias(self, dias: int = 90):
        """
        Envía a los admins un digest de garantías vencidas/por vencer. Pensado
        para dispararse periódicamente (cron/routine) o a demanda. Devuelve el
        conteo; el email es fire-and-forget (no bloquea si SMTP falla).
        """
        alertas = await self.garantias(dias=dias, solo_alertas=True)
        if not alertas:
            return {"total": 0, "enviado": False}
        from app.core.email import send_notification
        items = [
            {"codigo": a["ACT_Codigo_Interno"],
             "fin": a["ACT_Fin_Garantia"].isoformat() if a["ACT_Fin_Garantia"] else "—",
             "estado": "Vencida" if a["estado_garantia"] == "vencida" else "Por vencer",
             "dias": a["dias_restantes"]}
            for a in alertas
        ]
        await send_notification(
            "garantia_por_vencer",
            {"total": len(alertas), "dias": dias, "items": items},
            to=(),  # solo admins
        )
        return {"total": len(alertas), "enviado": True}
