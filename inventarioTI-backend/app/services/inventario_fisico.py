"""
Toma de inventario físico (conciliación) por sede o área.

Flujo de diálogo con quien recorre las oficinas:
  1. Abre la toma (sede y, opcionalmente, área).
  2. Escanea cada QR/código: el sistema responde al instante qué es y si está
     donde debería (encontrado, en otra ubicación, de otra sede, de baja o no
     registrado) y cuánto falta por leer.
  3. Cierra la toma: lo esperado que no se leyó queda como FALTANTE; los
     hallazgos pasan al kardex y, si se pide, se corrige la ubicación de los
     equipos encontrados en otra área.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import utcnow_naive
from app.core.transactional import transactional
from app.models.catalogs import EstadoOperativo
from app.models.core import Activo
from app.models.inventario_fisico import LecturaToma, TomaInventario
from app.models.location import Area
from app.repositories.governance import GovernanceRepository
from app.services.kardex import KardexService


class InventarioFisicoService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.gov_repo = GovernanceRepository(db)

    # ------------------------------------------------------------------
    async def _baja_id(self) -> int | None:
        return (await self.db.execute(
            select(EstadoOperativo.EOP_Estado_Operativo).where(EstadoOperativo.EOP_Nombre.ilike("Baja"))
        )).scalar_one_or_none()

    async def _get(self, toma_id: int, *, lock: bool = False) -> TomaInventario:
        q = select(TomaInventario).where(TomaInventario.TOM_Toma == toma_id)
        if lock:
            q = q.with_for_update()
        toma = (await self.db.execute(q)).scalar_one_or_none()
        if not toma:
            raise HTTPException(404, "INVENTORY_COUNT_NOT_FOUND")
        return toma

    async def _esperados(self, toma: TomaInventario) -> list[Activo]:
        """Activos que el sistema dice que están en la sede (o el área) de la toma."""
        baja = await self._baja_id()
        cond = [Activo.SED_Sede == toma.SED_Sede]
        if baja is not None:
            cond.append(Activo.EOP_Estado_Operativo != baja)
        if toma.ARE_Area is not None:
            cond.append(Activo.ARE_Area_Ubicacion == toma.ARE_Area)
        return list((await self.db.execute(
            select(Activo).where(*cond).order_by(Activo.ACT_Codigo_Interno)
        )).scalars().all())

    async def _lecturas(self, toma_id: int) -> list[LecturaToma]:
        return list((await self.db.execute(
            select(LecturaToma).where(LecturaToma.TOM_Toma == toma_id).order_by(LecturaToma.TLE_Lectura)
        )).scalars().all())

    # ------------------------------------------------------------------
    async def listar(self) -> list[TomaInventario]:
        return list((await self.db.execute(
            select(TomaInventario).order_by(TomaInventario.TOM_Fecha_Inicio.desc()).limit(200)
        )).scalars().all())

    @transactional
    async def crear(self, schema, usuario_id=None, ip=None) -> TomaInventario:
        from app.core.data_scope import area_sede_id, require_sede
        sede = require_sede(schema.SED_Sede)
        if schema.ARE_Area is not None:
            if not await self.db.get(Area, schema.ARE_Area):
                raise HTTPException(404, "AREA_NOT_FOUND")
            if await area_sede_id(self.db, schema.ARE_Area) != sede:
                raise HTTPException(400, "AREA_NOT_IN_SEDE")
        abierta = (await self.db.execute(select(TomaInventario.TOM_Toma).where(
            TomaInventario.SED_Sede == sede, TomaInventario.TOM_Estado == "ABIERTA",
            (TomaInventario.ARE_Area == schema.ARE_Area) if schema.ARE_Area is not None
            else TomaInventario.ARE_Area.is_(None),
        ))).first()
        if abierta:
            raise HTTPException(409, f"INVENTORY_COUNT_ALREADY_OPEN:{abierta[0]}")
        toma = TomaInventario(SED_Sede=sede, ARE_Area=schema.ARE_Area,
                              TOM_Observacion=schema.TOM_Observacion, USU_Usuario=usuario_id)
        self.db.add(toma)
        await self.db.flush()
        await self.gov_repo.create_audit_log(
            "INVENTORY_COUNT_OPEN", "INV_TOMA_INVENTARIO",
            {"toma": toma.TOM_Toma, "sede": sede, "area": schema.ARE_Area},
            usuario_id=usuario_id, ip_origen=ip, sede_id=sede,
        )
        await self.db.refresh(toma)
        return toma

    @transactional
    async def leer(self, toma_id: int, schema, usuario_id=None) -> dict[str, Any]:
        from app.core.data_scope import elevated
        toma = await self._get(toma_id, lock=True)
        if toma.TOM_Estado != "ABIERTA":
            raise HTTPException(409, "INVENTORY_COUNT_NOT_OPEN")
        codigo = schema.codigo.strip()
        # Se busca en toda la empresa: un equipo de otra sede también es un hallazgo.
        async with elevated(self.db):
            activo = (await self.db.execute(
                select(Activo).where(
                    (func.lower(Activo.ACT_Codigo_Interno) == codigo.lower())
                    | (func.lower(Activo.ACT_Serie_Fabricante) == codigo.lower())
                )
            )).scalars().first()
            baja = await self._baja_id()
        lecturas = await self._lecturas(toma_id)
        area_lectura = schema.ARE_Area if schema.ARE_Area is not None else toma.ARE_Area

        if activo is None:
            resultado = "NO_REGISTRADO"
        elif any(le.ACT_Activo == activo.ACT_Activo and le.TLE_Resultado != "REPETIDO" for le in lecturas):
            resultado = "REPETIDO"
        elif baja is not None and activo.EOP_Estado_Operativo == baja:
            resultado = "DE_BAJA"
        elif activo.SED_Sede != toma.SED_Sede:
            resultado = "OTRA_SEDE"
        elif area_lectura is not None and activo.ARE_Area_Ubicacion is not None \
                and activo.ARE_Area_Ubicacion != area_lectura:
            resultado = "OTRA_UBICACION"
        elif toma.ARE_Area is not None and activo.ARE_Area_Ubicacion != toma.ARE_Area:
            resultado = "OTRA_UBICACION"
        else:
            resultado = "ENCONTRADO"

        lectura = LecturaToma(
            TOM_Toma=toma_id, TLE_Codigo_Leido=codigo[:100],
            ACT_Activo=activo.ACT_Activo if activo else None,
            TLE_Resultado=resultado, ARE_Area=area_lectura,
            TLE_Observacion=schema.observacion, USU_Usuario=usuario_id,
        )
        self.db.add(lectura)
        await self.db.flush()
        await self.db.refresh(lectura)
        if activo is not None and resultado in ("ENCONTRADO", "OTRA_UBICACION"):
            from app.services.faltantes import FaltanteService
            await FaltanteService(self.db).resolver_por_lectura(activo.ACT_Activo, toma_id, usuario_id=usuario_id)
        esperados = await self._esperados(toma)
        leidos = {le.ACT_Activo for le in lecturas if le.ACT_Activo} | ({activo.ACT_Activo} if activo else set())
        # De otra sede solo se informa el código (no sus datos: fuera del alcance).
        visible = activo is not None and resultado != "OTRA_SEDE"
        return {
            **{c: getattr(lectura, c) for c in ("TLE_Lectura", "TLE_Codigo_Leido", "TLE_Resultado",
                                                "ARE_Area", "TLE_Fecha", "TLE_Observacion")},
            "ACT_Activo": activo.ACT_Activo if visible else None,
            "activo_codigo": activo.ACT_Codigo_Interno if activo else None,
            "activo_descripcion": activo.ACT_Hostname if visible else None,
            "mensaje": resultado,
            "esperados": len(esperados),
            "leidos": len([a for a in esperados if a.ACT_Activo in leidos]),
        }

    async def resumen(self, toma: TomaInventario) -> dict[str, Any]:
        if toma.TOM_Estado != "ABIERTA" and toma.TOM_Resumen:
            return toma.TOM_Resumen
        esperados = await self._esperados(toma)
        lecturas = await self._lecturas(toma.TOM_Toma)
        leidos = {le.ACT_Activo for le in lecturas if le.ACT_Activo and le.TLE_Resultado != "REPETIDO"}

        def lista(resultado: str) -> list[dict]:
            return [{"codigo": le.TLE_Codigo_Leido, "activo": str(le.ACT_Activo) if le.ACT_Activo else None,
                     "area": le.ARE_Area} for le in lecturas if le.TLE_Resultado == resultado]

        faltantes = [{"codigo": a.ACT_Codigo_Interno, "activo": str(a.ACT_Activo),
                      "area": a.ARE_Area_Ubicacion} for a in esperados if a.ACT_Activo not in leidos]
        encontrados = lista("ENCONTRADO")
        return {
            "esperados": len(esperados),
            "leidos": len(lecturas),
            "encontrados": len(encontrados),
            "avance_pct": round(100 * (len(esperados) - len(faltantes)) / len(esperados), 1) if esperados else 100.0,
            "faltantes": faltantes,
            "otra_ubicacion": lista("OTRA_UBICACION"),
            "otra_sede": lista("OTRA_SEDE"),
            "de_baja": lista("DE_BAJA"),
            "no_registrados": lista("NO_REGISTRADO"),
            "repetidos": len(lista("REPETIDO")),
        }

    async def detalle(self, toma_id: int) -> dict[str, Any]:
        toma = await self._get(toma_id)
        return {
            **{c.name: getattr(toma, c.name) for c in TomaInventario.__table__.columns},
            "resumen": await self.resumen(toma),
            "lecturas": await self._lecturas(toma_id),
        }

    @transactional
    async def cerrar(self, toma_id: int, *, actualizar_ubicacion: bool = False, usuario_id=None, ip=None):
        from sqlalchemy import update as _upd
        toma = await self._get(toma_id, lock=True)
        if toma.TOM_Estado != "ABIERTA":
            raise HTTPException(409, "INVENTORY_COUNT_NOT_OPEN")
        resumen = await self.resumen(toma)
        kardex = KardexService(self.db)
        documento = ("TOMA_INVENTARIO", str(toma_id))
        for item in resumen["faltantes"]:
            await kardex.registrar(
                "INVENTARIO_FISICO", activo_id=uuid.UUID(item["activo"]), referencia=item["codigo"],
                origen={"sede": toma.SED_Sede, "area": item["area"]}, documento=documento,
                usuario_id=usuario_id, motivo="No se encontró en la toma de inventario",
                detalle={"resultado": "FALTANTE"},
            )
        for clave in ("otra_ubicacion", "de_baja"):
            for item in resumen[clave]:
                if not item["activo"]:
                    continue
                await kardex.registrar(
                    "INVENTARIO_FISICO", activo_id=uuid.UUID(item["activo"]), referencia=item["codigo"],
                    destino={"sede": toma.SED_Sede, "area": item["area"]}, documento=documento,
                    usuario_id=usuario_id, detalle={"resultado": clave.upper()},
                    motivo="Encontrado en otra área" if clave == "otra_ubicacion" else "Figura de baja y apareció",
                )
                if clave == "otra_ubicacion" and actualizar_ubicacion and item["area"] is not None:
                    await self.db.execute(
                        _upd(Activo).where(Activo.ACT_Activo == uuid.UUID(item["activo"]))
                        .values(ARE_Area_Ubicacion=item["area"])
                    )
        resumen["ubicaciones_actualizadas"] = bool(actualizar_ubicacion)
        # Lo que no apareció queda para investigar (Tomas → Faltantes).
        from app.services.faltantes import FaltanteService
        resumen["faltantes_nuevos"] = await FaltanteService(self.db).registrar_de_toma(toma, resumen["faltantes"])
        toma.TOM_Estado = "CERRADA"
        toma.TOM_Fecha_Cierre = utcnow_naive()
        toma.TOM_Resumen = resumen
        await self.gov_repo.create_audit_log(
            "INVENTORY_COUNT_CLOSE", "INV_TOMA_INVENTARIO",
            {"toma": toma_id, "esperados": resumen["esperados"], "faltantes": len(resumen["faltantes"]),
             "otra_ubicacion": len(resumen["otra_ubicacion"]), "no_registrados": len(resumen["no_registrados"])},
            usuario_id=usuario_id, ip_origen=ip, sede_id=toma.SED_Sede,
        )
        await self.db.flush()
        return await self.detalle(toma_id)

    @transactional
    async def anular(self, toma_id: int, usuario_id=None, ip=None):
        toma = await self._get(toma_id, lock=True)
        if toma.TOM_Estado != "ABIERTA":
            raise HTTPException(409, "INVENTORY_COUNT_NOT_OPEN")
        toma.TOM_Estado = "ANULADA"
        toma.TOM_Fecha_Cierre = utcnow_naive()
        await self.gov_repo.create_audit_log(
            "INVENTORY_COUNT_CANCEL", "INV_TOMA_INVENTARIO", {"toma": toma_id},
            usuario_id=usuario_id, ip_origen=ip, sede_id=toma.SED_Sede,
        )
        await self.db.flush()
        return toma
