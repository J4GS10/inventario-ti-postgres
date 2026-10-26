"""
Escritura y consulta del kardex unificado (ver app/models/kardex.py).

Uso desde un servicio, dentro de su transacción:

    await KardexService(self.db).registrar(
        "ASIGNACION", activo=activo, usuario_id=usuario_id,
        destino={"persona": persona_id, "area": area_id, "sede": sede_id},
        documento=("MOVIMIENTO", str(mov_id)), motivo=observacion,
    )
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kardex import (
    EVENTOS_CONSUMIBLE, EVENTOS_ENTRADA, EVENTOS_LICENCIA, EVENTOS_REASIGNACION, EVENTOS_SALIDA, Kardex,
)


def referencia_activo(activo) -> str | None:
    if activo is None:
        return None
    partes = [getattr(activo, "ACT_Codigo_Interno", None), getattr(activo, "ACT_Serie_Fabricante", None)]
    return " · ".join(p for p in partes if p)[:200] or None


def _objeto(tipo: str) -> str:
    if tipo in EVENTOS_CONSUMIBLE:
        return "CONSUMIBLE"
    if tipo in EVENTOS_LICENCIA:
        return "LICENCIA"
    return "ACTIVO"


def _json(valor: Any) -> Any:
    """Detalle serializable (UUID, fechas y decimales a texto)."""
    if isinstance(valor, dict):
        return {k: _json(v) for k, v in valor.items() if v is not None}
    if isinstance(valor, (list, tuple)):
        return [_json(v) for v in valor]
    if isinstance(valor, (uuid.UUID, date, datetime, Decimal)):
        return str(valor)
    return valor


class KardexService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def registrar(
        self,
        tipo: str,
        *,
        activo=None,
        activo_id: uuid.UUID | None = None,
        consumible_id: int | None = None,
        licencia_id: int | None = None,
        clave_id: int | None = None,
        referencia: str | None = None,
        origen: dict | None = None,
        destino: dict | None = None,
        cantidad: int | None = None,
        saldo: int | None = None,
        costo=None,
        moneda: str | None = None,
        saldo_valor=None,
        documento: tuple[str, str | None] | None = None,
        motivo: str | None = None,
        detalle: dict | None = None,
        usuario_id: uuid.UUID | None = None,
        fecha: datetime | None = None,
        sede: int | None = None,
        migrado: bool = False,
    ) -> Kardex:
        origen, destino = origen or {}, destino or {}
        if activo is not None and activo_id is None:
            activo_id = activo.ACT_Activo
        sede_evento = (
            sede or destino.get("sede") or origen.get("sede")
            or (getattr(activo, "SED_Sede", None) if activo is not None else None)
        )
        evento = Kardex(
            KDX_Tipo=tipo,
            KDX_Objeto=_objeto(tipo),
            ACT_Activo=activo_id,
            CON_Consumible=consumible_id,
            LIC_Licencia=licencia_id,
            LCL_Licencia_Clave=clave_id,
            KDX_Referencia=(referencia or referencia_activo(activo) or None),
            KDX_Origen_Persona=origen.get("persona"),
            KDX_Origen_Area=origen.get("area"),
            KDX_Origen_Sede=origen.get("sede"),
            KDX_Origen_Activo=origen.get("activo"),
            KDX_Destino_Persona=destino.get("persona"),
            KDX_Destino_Area=destino.get("area"),
            KDX_Destino_Sede=destino.get("sede"),
            KDX_Destino_Activo=destino.get("activo"),
            KDX_Cantidad=cantidad,
            KDX_Saldo=saldo,
            KDX_Costo=costo,
            KDX_Moneda=moneda,
            KDX_Saldo_Valor=saldo_valor,
            KDX_Documento_Tipo=documento[0] if documento else None,
            KDX_Documento_Ref=(str(documento[1])[:120] if documento and documento[1] else None),
            KDX_Motivo=(motivo or None) and str(motivo)[:500],
            KDX_Detalle=_json(detalle) if detalle else None,
            USU_Usuario=usuario_id,
            SED_Sede=sede_evento,
            KDX_Migrado=migrado,
        )
        if fecha is not None:
            evento.KDX_Fecha = fecha
        self.db.add(evento)
        return evento

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------
    @staticmethod
    def filtros(
        *,
        objeto: str | None = None,
        tipos: list[str] | None = None,
        categoria: str | None = None,
        activo_id: uuid.UUID | None = None,
        consumible_id: int | None = None,
        licencia_id: int | None = None,
        persona_id: uuid.UUID | None = None,
        sede_id: int | None = None,
        desde: date | None = None,
        hasta: date | None = None,
        texto: str | None = None,
    ) -> list:
        cond = []
        if objeto:
            cond.append(Kardex.KDX_Objeto == objeto)
        if tipos:
            cond.append(Kardex.KDX_Tipo.in_(tipos))
        if categoria == "ENTRADAS":
            cond.append(Kardex.KDX_Tipo.in_(EVENTOS_ENTRADA))
        elif categoria == "SALIDAS":
            cond.append(Kardex.KDX_Tipo.in_(EVENTOS_SALIDA))
        elif categoria == "ENTRADAS_SALIDAS":
            cond.append(Kardex.KDX_Tipo.in_(EVENTOS_ENTRADA + EVENTOS_SALIDA))
        elif categoria == "REASIGNACIONES":
            cond.append(Kardex.KDX_Tipo.in_(EVENTOS_REASIGNACION))
        if activo_id:
            # Un equipo también muestra los eventos de sus componentes.
            cond.append(or_(Kardex.ACT_Activo == activo_id,
                            Kardex.KDX_Destino_Activo == activo_id,
                            Kardex.KDX_Origen_Activo == activo_id))
        if consumible_id:
            cond.append(Kardex.CON_Consumible == consumible_id)
        if licencia_id:
            cond.append(Kardex.LIC_Licencia == licencia_id)
        if persona_id:
            cond.append(or_(Kardex.KDX_Origen_Persona == persona_id, Kardex.KDX_Destino_Persona == persona_id))
        if sede_id:
            cond.append(or_(Kardex.SED_Sede == sede_id, Kardex.KDX_Origen_Sede == sede_id,
                            Kardex.KDX_Destino_Sede == sede_id))
        if desde:
            cond.append(Kardex.KDX_Fecha >= datetime.combine(desde, time.min))
        if hasta:
            cond.append(Kardex.KDX_Fecha <= datetime.combine(hasta, time.max))
        if texto:
            patron = f"%{texto.strip()}%"
            cond.append(or_(Kardex.KDX_Referencia.ilike(patron), Kardex.KDX_Motivo.ilike(patron),
                            Kardex.KDX_Documento_Ref.ilike(patron)))
        return cond

    async def listar(self, cond: list, *, limite: int = 50, desplazamiento: int = 0) -> tuple[list[Kardex], int]:
        where = and_(*cond) if cond else True
        total = (await self.db.execute(select(func.count()).select_from(Kardex).where(where))).scalar_one()
        filas = (await self.db.execute(
            select(Kardex).where(where)
            .order_by(Kardex.KDX_Fecha.desc(), Kardex.KDX_Evento.desc())
            .offset(desplazamiento).limit(limite)
        )).scalars().all()
        return list(filas), int(total)

    async def todos(self, cond: list, *, maximo: int = 20000) -> list[Kardex]:
        where = and_(*cond) if cond else True
        return list((await self.db.execute(
            select(Kardex).where(where).order_by(Kardex.KDX_Fecha.asc(), Kardex.KDX_Evento.asc()).limit(maximo)
        )).scalars().all())
