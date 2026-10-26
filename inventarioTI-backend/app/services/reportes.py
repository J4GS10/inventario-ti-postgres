"""
Reportes de trazabilidad sobre el kardex unificado:

  - Kardex legible (pantalla y CSV): la vida de un activo, consumible o
    licencia, o la bitácora de entradas y salidas / reasignaciones.
  - Custodia a una fecha: quién tenía qué equipo, dónde, en un día dado.
  - Resumen de entradas y salidas por periodo (con valores por moneda).
  - Hoja de vida de un activo en PDF.
  - CSV de licencias (uso, vencimiento, costo).

Todo respeta el alcance por sede (criterios ORM y RLS) y sale en ES/EN/IT.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalogs import Modelo, TipoActivo
from app.models.core import Activo
from app.models.kardex import EVENTOS_ENTRADA, EVENTOS_SALIDA, Kardex
from app.models.location import Area, Sede
from app.models.organization import Persona, Usuario
from app.models.software import Licencia, Software, TipoLicencia
from app.models.traceability import Movimiento, TipoMovimiento
from app.services.kardex import KardexService
from app.services.report_i18n import a_hora_local, format_amount, format_iso_date, format_iso_datetime, normalize_lang

# Nombre legible de cada tipo de evento.
TIPOS_EVENTO_I18N: dict[str, dict[str, str]] = {
    "ALTA": {"es": "Alta", "en": "Registration", "it": "Registrazione"},
    "RECEPCION": {"es": "Recepción de compra", "en": "Purchase receipt", "it": "Ricevimento acquisto"},
    "ASIGNACION": {"es": "Asignación", "en": "Assignment", "it": "Assegnazione"},
    "PRESTAMO": {"es": "Préstamo", "en": "Loan", "it": "Prestito"},
    "DEVOLUCION": {"es": "Devolución", "en": "Return", "it": "Restituzione"},
    "TRANSFERENCIA": {"es": "Transferencia", "en": "Transfer", "it": "Trasferimento"},
    "TRASLADO_SEDE": {"es": "Traslado de sede", "en": "Site transfer", "it": "Trasferimento di sede"},
    "MANTENIMIENTO_ENTRADA": {"es": "Entrada a mantenimiento", "en": "Sent to maintenance",
                              "it": "Ingresso in manutenzione"},
    "MANTENIMIENTO_SALIDA": {"es": "Salida de mantenimiento", "en": "Back from maintenance",
                             "it": "Uscita dalla manutenzione"},
    "COMPONENTE_INSTALADO": {"es": "Componente instalado", "en": "Component installed",
                             "it": "Componente installato"},
    "COMPONENTE_RETIRADO": {"es": "Componente retirado", "en": "Component removed", "it": "Componente rimosso"},
    "ESPECIFICACION": {"es": "Cambio de especificación", "en": "Specification change",
                       "it": "Modifica della specifica"},
    "CAMBIO_ESTADO": {"es": "Cambio de estado", "en": "Status change", "it": "Cambio di stato"},
    "BAJA": {"es": "Baja", "en": "Decommissioning", "it": "Dismissione"},
    "INVENTARIO_FISICO": {"es": "Inventario físico", "en": "Physical count", "it": "Inventario fisico"},
    "CONSUMIBLE_ENTRADA": {"es": "Entrada de stock", "en": "Stock in", "it": "Entrata di magazzino"},
    "CONSUMIBLE_SALIDA": {"es": "Salida de stock", "en": "Stock out", "it": "Uscita di magazzino"},
    "CONSUMIBLE_AJUSTE": {"es": "Ajuste de stock", "en": "Stock adjustment", "it": "Rettifica di magazzino"},
    "LICENCIA_ALTA": {"es": "Alta de licencia", "en": "License registered", "it": "Licenza registrata"},
    "LICENCIA_ASIGNADA": {"es": "Licencia asignada", "en": "License assigned", "it": "Licenza assegnata"},
    "LICENCIA_RETIRADA": {"es": "Licencia retirada", "en": "License removed", "it": "Licenza rimossa"},
    "LICENCIA_AJUSTE": {"es": "Ajuste de licencia", "en": "License adjustment", "it": "Rettifica della licenza"},
    "LICENCIA_BAJA": {"es": "Baja de licencia", "en": "License removed", "it": "Licenza dismessa"},
    "CONSUMIBLE_TRASLADO": {"es": "Traslado de bodega", "en": "Storage transfer", "it": "Trasferimento di magazzino"},
}

OBJETO_I18N = {
    "ACTIVO": {"es": "Activo", "en": "Asset", "it": "Asset"},
    "CONSUMIBLE": {"es": "Consumible", "en": "Consumable", "it": "Consumabile"},
    "LICENCIA": {"es": "Licencia", "en": "License", "it": "Licenza"},
}

_CABECERAS = {
    "kardex": {
        "es": ["Fecha y hora", "Evento", "Objeto", "Referencia", "Desde (persona)", "Desde (área)",
               "Desde (sede)", "Hacia (persona)", "Hacia (área)", "Hacia (sede)", "Equipo",
               "Cantidad", "Saldo", "Costo", "Moneda", "Documento", "Motivo", "Registrado por", "Migrado"],
        "en": ["Date and time", "Event", "Item", "Reference", "From (person)", "From (area)",
               "From (site)", "To (person)", "To (area)", "To (site)", "Device",
               "Quantity", "Balance", "Cost", "Currency", "Document", "Reason", "Recorded by", "Migrated"],
        "it": ["Data e ora", "Evento", "Oggetto", "Riferimento", "Da (persona)", "Da (area)",
               "Da (sede)", "A (persona)", "A (area)", "A (sede)", "Dispositivo",
               "Quantità", "Saldo", "Costo", "Valuta", "Documento", "Motivo", "Registrato da", "Migrato"],
    },
    "custodia": {
        "es": ["Código", "Número de serie", "Tipo de activo", "Custodio", "Área", "Sede", "Desde",
               "Tipo de movimiento", "Devolución prevista"],
        "en": ["Code", "Serial number", "Asset type", "Custodian", "Area", "Site", "Since",
               "Movement type", "Expected return"],
        "it": ["Codice", "Numero di serie", "Tipo di asset", "Assegnatario", "Area", "Sede", "Dal",
               "Tipo di movimento", "Restituzione prevista"],
    },
    "licencias": {
        "es": ["Software", "Versión", "Fabricante", "Tipo de licencia", "Total", "En uso", "Disponibles",
               "Uso (%)", "Vencimiento", "Días para vencer", "Costo", "Moneda"],
        "en": ["Software", "Version", "Vendor", "License type", "Total", "In use", "Available",
               "Usage (%)", "Expiration", "Days to expire", "Cost", "Currency"],
        "it": ["Software", "Versione", "Produttore", "Tipo di licenza", "Totale", "In uso", "Disponibili",
               "Utilizzo (%)", "Scadenza", "Giorni alla scadenza", "Costo", "Valuta"],
    },
}

_SI_NO = {"es": ("Sí", "No"), "en": ("Yes", "No"), "it": ("Sì", "No")}


def tipo_evento(tipo: str, lang: str) -> str:
    return TIPOS_EVENTO_I18N.get(tipo, {}).get(normalize_lang(lang), tipo)


def _csv_seguro(valor) -> str:
    texto = "" if valor is None else str(valor)
    return "'" + texto if texto[:1] in ("=", "+", "-", "@", "\t", "\r") else texto


def csv_bytes(cabeceras: list[str], filas: list[list]) -> bytes:
    buffer = io.StringIO()
    escritor = csv.writer(buffer)
    escritor.writerow(cabeceras)
    for fila in filas:
        escritor.writerow([_csv_seguro(v) for v in fila])
    return ("﻿" + buffer.getvalue()).encode("utf-8")


class ReportesService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.kardex = KardexService(db)

    # ------------------------------------------------------------------
    # Nombres legibles (en lote)
    # ------------------------------------------------------------------
    async def _nombres(self, eventos: list[Kardex]) -> dict[str, dict]:
        personas = {e.KDX_Origen_Persona for e in eventos} | {e.KDX_Destino_Persona for e in eventos}
        areas = {e.KDX_Origen_Area for e in eventos} | {e.KDX_Destino_Area for e in eventos}
        sedes = {e.KDX_Origen_Sede for e in eventos} | {e.KDX_Destino_Sede for e in eventos}
        equipos = {e.KDX_Origen_Activo for e in eventos} | {e.KDX_Destino_Activo for e in eventos}
        usuarios = {e.USU_Usuario for e in eventos}
        for conjunto in (personas, areas, sedes, equipos, usuarios):
            conjunto.discard(None)
        res: dict[str, dict] = {"persona": {}, "area": {}, "sede": {}, "activo": {}, "usuario": {}}
        if personas:
            for pid, n, a in (await self.db.execute(select(
                    Persona.PER_Persona, Persona.PER_Primer_Nombre, Persona.PER_Primer_Apellido)
                    .where(Persona.PER_Persona.in_(personas)))).all():
                res["persona"][pid] = f"{n} {a}"
        if areas:
            res["area"] = dict((await self.db.execute(
                select(Area.ARE_Area, Area.ARE_Nombre).where(Area.ARE_Area.in_(areas)))).all())
        if sedes:
            res["sede"] = dict((await self.db.execute(
                select(Sede.SED_Sede, Sede.SED_Nombre).where(Sede.SED_Sede.in_(sedes)))).all())
        if equipos:
            res["activo"] = dict((await self.db.execute(
                select(Activo.ACT_Activo, Activo.ACT_Codigo_Interno).where(Activo.ACT_Activo.in_(equipos)))).all())
        if usuarios:
            res["usuario"] = dict((await self.db.execute(
                select(Usuario.USU_Usuario, Usuario.USU_Username).where(Usuario.USU_Usuario.in_(usuarios)))).all())
        return res

    def _legible(self, e: Kardex, n: dict[str, dict]) -> dict[str, Any]:
        from app.schemas.kardex import KardexEvento
        base = KardexEvento.model_validate(e).model_dump()
        return {
            **base,
            "origen_persona": n["persona"].get(e.KDX_Origen_Persona),
            "origen_area": n["area"].get(e.KDX_Origen_Area),
            "origen_sede": n["sede"].get(e.KDX_Origen_Sede),
            "origen_activo": n["activo"].get(e.KDX_Origen_Activo),
            "destino_persona": n["persona"].get(e.KDX_Destino_Persona),
            "destino_area": n["area"].get(e.KDX_Destino_Area),
            "destino_sede": n["sede"].get(e.KDX_Destino_Sede),
            "destino_activo": n["activo"].get(e.KDX_Destino_Activo),
            "usuario": n["usuario"].get(e.USU_Usuario),
        }

    async def kardex_pagina(self, filtros: dict, *, limite: int, desplazamiento: int) -> dict:
        cond = KardexService.filtros(**filtros)
        eventos, total = await self.kardex.listar(cond, limite=limite, desplazamiento=desplazamiento)
        nombres = await self._nombres(eventos)
        return {"total": total, "items": [self._legible(e, nombres) for e in eventos]}

    async def kardex_csv(self, filtros: dict, lang: str) -> bytes:
        lang = normalize_lang(lang)
        eventos = await self.kardex.todos(KardexService.filtros(**filtros))
        n = await self._nombres(eventos)
        si, no = _SI_NO[lang]
        filas = []
        for e in eventos:
            equipo = n["activo"].get(e.KDX_Destino_Activo) or n["activo"].get(e.KDX_Origen_Activo) or ""
            filas.append([
                format_iso_datetime(e.KDX_Fecha), tipo_evento(e.KDX_Tipo, lang),
                OBJETO_I18N[e.KDX_Objeto][lang], e.KDX_Referencia or "",
                n["persona"].get(e.KDX_Origen_Persona, ""), n["area"].get(e.KDX_Origen_Area, ""),
                n["sede"].get(e.KDX_Origen_Sede, ""),
                n["persona"].get(e.KDX_Destino_Persona, ""), n["area"].get(e.KDX_Destino_Area, ""),
                n["sede"].get(e.KDX_Destino_Sede, ""), equipo,
                "" if e.KDX_Cantidad is None else e.KDX_Cantidad, "" if e.KDX_Saldo is None else e.KDX_Saldo,
                format_amount(e.KDX_Costo), e.KDX_Moneda or "",
                " ".join(x for x in (e.KDX_Documento_Tipo, e.KDX_Documento_Ref) if x),
                e.KDX_Motivo or "", n["usuario"].get(e.USU_Usuario, ""), si if e.KDX_Migrado else no,
            ])
        return csv_bytes(_CABECERAS["kardex"][lang], filas)

    # ------------------------------------------------------------------
    # Custodia a una fecha
    # ------------------------------------------------------------------
    async def custodia(self, *, fecha: date | None = None, persona_id=None, area_id=None,
                       sede_id=None) -> list[dict]:
        corte = datetime.combine(fecha, time.max) if fecha else datetime.utcnow()
        cond = [Movimiento.MOV_Fecha_Asignacion <= corte,
                or_(Movimiento.MOV_Fecha_Devolucion.is_(None), Movimiento.MOV_Fecha_Devolucion > corte)]
        if persona_id:
            cond.append(Movimiento.PER_Persona == persona_id)
        if area_id:
            cond.append(Movimiento.ARE_Area == area_id)
        if sede_id:
            cond.append(Activo.SED_Sede == sede_id)
        filas = (await self.db.execute(
            select(Movimiento, Activo, Persona, Area.ARE_Nombre, TipoActivo.TAC_Nombre, TipoMovimiento.TMO_Nombre)
            .join(Activo, Activo.ACT_Activo == Movimiento.ACT_Activo)
            .join(Persona, Persona.PER_Persona == Movimiento.PER_Persona)
            .join(Area, Area.ARE_Area == Movimiento.ARE_Area, isouter=True)
            .join(TipoActivo, TipoActivo.TAC_Tipo_Activo == Activo.TAC_Tipo_Activo, isouter=True)
            .join(TipoMovimiento, TipoMovimiento.TMO_Tipo_Movimiento == Movimiento.TMO_Tipo_Movimiento, isouter=True)
            .where(*cond)
            .order_by(Persona.PER_Primer_Apellido, Persona.PER_Primer_Nombre, Activo.ACT_Codigo_Interno)
        )).all()
        return [{
            "ACT_Activo": a.ACT_Activo, "activo_codigo": a.ACT_Codigo_Interno, "activo_serie": a.ACT_Serie_Fabricante,
            "activo_tipo": tac, "PER_Persona": p.PER_Persona,
            "persona": f"{p.PER_Primer_Nombre} {p.PER_Primer_Apellido}",
            "ARE_Area": m.ARE_Area, "area": area, "SED_Sede": a.SED_Sede,
            "desde": m.MOV_Fecha_Asignacion, "tipo": tmo, "devolucion_prevista": m.MOV_Fecha_Prevista_Devolucion,
        } for m, a, p, area, tac, tmo in filas]

    async def custodia_csv(self, lang: str, **filtros) -> bytes:
        from app.services.report_i18n import translate_catalog
        lang = normalize_lang(lang)
        items = await self.custodia(**filtros)
        sedes = dict((await self.db.execute(select(Sede.SED_Sede, Sede.SED_Nombre))).all())
        filas = [[
            i["activo_codigo"], i["activo_serie"], translate_catalog("tipo_activo", i["activo_tipo"], lang) if i["activo_tipo"] else "",
            i["persona"], i["area"] or "", sedes.get(i["SED_Sede"], ""), format_iso_datetime(i["desde"]),
            translate_catalog("tipo_movimiento", i["tipo"], lang) if i["tipo"] else "",
            format_iso_date(i["devolucion_prevista"]),
        ] for i in items]
        return csv_bytes(_CABECERAS["custodia"][lang], filas)

    # ------------------------------------------------------------------
    # Entradas y salidas por periodo
    # ------------------------------------------------------------------
    async def resumen_motivos(self, *, desde: date | None, hasta: date | None, sede_id: int | None) -> dict:
        """
        Agrupado por catálogo en el periodo (plan de catálogos §4.4): movimientos
        por motivo y tipo, mantenimientos por tipo de falla (con su costo por
        moneda), acciones de mantenimiento realizadas y bajas por motivo y por
        destino. Respeta el alcance por sede (RLS). Sin motivo de catálogo
        (registros anteriores o de texto libre) se agrupan como `codigo: null`.
        """
        from app.models.catalogo_estandar import AccionMantenimiento, MotivoMovimiento, TipoFalla
        from app.models.traceability import DetalleMantenimiento, Mantenimiento

        def rango(col):
            cond = []
            if desde:
                cond.append(col >= datetime.combine(desde, time.min))
            if hasta:
                cond.append(col <= datetime.combine(hasta, time.max))
            return cond

        sede = [Activo.SED_Sede == sede_id] if sede_id else []

        movimientos = [
            {"codigo": c, "nombre": n, "tipo": tmo, "total": int(t)}
            for c, n, tmo, t in (await self.db.execute(
                select(MotivoMovimiento.MMO_Codigo, MotivoMovimiento.MMO_Nombre, TipoMovimiento.TMO_Nombre, func.count())
                .select_from(Movimiento)
                .join(Activo, Activo.ACT_Activo == Movimiento.ACT_Activo)
                .join(TipoMovimiento, TipoMovimiento.TMO_Tipo_Movimiento == Movimiento.TMO_Tipo_Movimiento, isouter=True)
                .join(MotivoMovimiento, MotivoMovimiento.MMO_Motivo == Movimiento.MMO_Motivo, isouter=True)
                .where(*rango(Movimiento.MOV_Fecha_Asignacion), *sede)
                .group_by(MotivoMovimiento.MMO_Codigo, MotivoMovimiento.MMO_Nombre, TipoMovimiento.TMO_Nombre)
                .order_by(func.count().desc())
            )).all()
        ]
        fallas: dict[tuple, dict] = {}
        for c, n, moneda, t, costo in (await self.db.execute(
            select(TipoFalla.TFA_Codigo, TipoFalla.TFA_Nombre, Mantenimiento.MAN_Moneda, func.count(),
                   func.coalesce(func.sum(Mantenimiento.MAN_Costo_Total), 0))
            .select_from(Mantenimiento)
            .join(Activo, Activo.ACT_Activo == Mantenimiento.ACT_Activo)
            .join(TipoFalla, TipoFalla.TFA_Tipo_Falla == Mantenimiento.TFA_Tipo_Falla, isouter=True)
            .where(*rango(Mantenimiento.MAN_Fecha_Ingreso), *sede)
            .group_by(TipoFalla.TFA_Codigo, TipoFalla.TFA_Nombre, Mantenimiento.MAN_Moneda)
        )).all():
            f = fallas.setdefault((c, n), {"codigo": c, "nombre": n, "total": 0, "costo_por_moneda": {}})
            f["total"] += int(t)
            if costo:
                f["costo_por_moneda"][moneda or "GTQ"] = str(costo)
        acciones = [
            {"codigo": c, "nombre": n, "total": int(t)}
            for c, n, t in (await self.db.execute(
                select(AccionMantenimiento.AMA_Codigo, AccionMantenimiento.AMA_Nombre, func.count())
                .select_from(DetalleMantenimiento)
                .join(Mantenimiento, Mantenimiento.MAN_Mantenimiento == DetalleMantenimiento.MAN_Mantenimiento)
                .join(Activo, Activo.ACT_Activo == Mantenimiento.ACT_Activo)
                .join(AccionMantenimiento, AccionMantenimiento.AMA_Accion == DetalleMantenimiento.AMA_Accion, isouter=True)
                .where(*rango(Mantenimiento.MAN_Fecha_Ingreso), *sede)
                .group_by(AccionMantenimiento.AMA_Codigo, AccionMantenimiento.AMA_Nombre)
                .order_by(func.count().desc())
            )).all()
        ]

        def bajas_por(col) -> list[dict]:
            cond = [Activo.ACT_Baja_Fecha.is_not(None), *sede]
            if desde:
                cond.append(Activo.ACT_Baja_Fecha >= desde)
            if hasta:
                cond.append(Activo.ACT_Baja_Fecha <= hasta)
            return select(col, func.count()).where(*cond).group_by(col).order_by(func.count().desc())

        bajas_motivo = [{"codigo": c, "total": int(t)} for c, t in (await self.db.execute(bajas_por(Activo.ACT_Baja_Motivo))).all()]
        bajas_destino = [{"codigo": c, "total": int(t)} for c, t in (await self.db.execute(bajas_por(Activo.ACT_Baja_Destino))).all()]
        return {
            "movimientos": movimientos,
            "fallas": sorted(fallas.values(), key=lambda f: -f["total"]),
            "acciones": acciones,
            "bajas_motivo": bajas_motivo,
            "bajas_destino": bajas_destino,
        }

    async def entradas_salidas(self, *, desde: date | None, hasta: date | None, sede_id: int | None) -> dict:
        cond = KardexService.filtros(categoria="ENTRADAS_SALIDAS", desde=desde, hasta=hasta, sede_id=sede_id)
        filas = (await self.db.execute(
            select(Kardex.KDX_Tipo, Kardex.KDX_Moneda, func.count(), func.coalesce(func.sum(Kardex.KDX_Cantidad), 0),
                   func.coalesce(func.sum(Kardex.KDX_Costo * func.coalesce(Kardex.KDX_Cantidad, 1)), 0))
            .where(*cond).group_by(Kardex.KDX_Tipo, Kardex.KDX_Moneda)
        )).all()
        resumen: dict[str, dict] = {}
        for tipo, moneda, eventos, cantidad, valor in filas:
            r = resumen.setdefault(tipo, {"tipo": tipo, "eventos": 0, "cantidad": 0, "valor_por_moneda": {},
                                          "clase": "ENTRADA" if tipo in EVENTOS_ENTRADA else "SALIDA"})
            r["eventos"] += int(eventos)
            r["cantidad"] += int(cantidad or 0)
            if moneda and valor:
                r["valor_por_moneda"][moneda] = float(Decimal(str(valor)))
        return {"desde": desde, "hasta": hasta, "sede": sede_id, "por_tipo": list(resumen.values())}

    # ------------------------------------------------------------------
    # Licencias
    # ------------------------------------------------------------------
    async def licencias_csv(self, lang: str) -> bytes:
        lang = normalize_lang(lang)
        hoy = a_hora_local(datetime.utcnow()).date()
        filas_db = (await self.db.execute(
            select(Licencia, Software, TipoLicencia.TLI_Nombre)
            .join(Software, Software.SOF_Software == Licencia.SOF_Software)
            .join(TipoLicencia, TipoLicencia.TLI_Tipo_Licencia == Licencia.TLI_Tipo_Licencia, isouter=True)
            .order_by(Software.SOF_Nombre, Licencia.LIC_Licencia)
        )).all()
        filas = []
        for lic, sw, tipo in filas_db:
            total, usada = lic.LIC_Cantidad_Total or 0, lic.LIC_Cantidad_Usada or 0
            vence = lic.LIC_Fecha_Vencimiento
            filas.append([
                sw.SOF_Nombre, sw.SOF_Version or "", sw.SOF_Fabricante or "", tipo or "", total, usada,
                total - usada, f"{(100 * usada / total):.0f}" if total else "0",
                format_iso_date(vence), (vence - hoy).days if vence else "",
                format_amount(lic.LIC_Costo), lic.LIC_Moneda or "",
            ])
        return csv_bytes(_CABECERAS["licencias"][lang], filas)

    # ------------------------------------------------------------------
    # Hoja de vida del activo (PDF)
    # ------------------------------------------------------------------
    async def hoja_de_vida_pdf(self, activo_id: uuid.UUID, lang: str, *, con_costo: bool = True) -> tuple[bytes, str]:
        from fastapi import HTTPException
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        from xml.sax.saxutils import escape

        lang = normalize_lang(lang)
        activo = (await self.db.execute(select(Activo).where(Activo.ACT_Activo == activo_id))).scalar_one_or_none()
        if not activo:
            raise HTTPException(404, "ASSET_NOT_FOUND")
        modelo = await self.db.get(Modelo, activo.MOD_Modelo)
        eventos = await self.kardex.todos(KardexService.filtros(activo_id=activo_id))
        n = await self._nombres(eventos)
        t = {
            "es": ("Hoja de vida del activo", "Código", "Serie", "Modelo", "Costo", "Compra", "Fecha", "Evento",
                   "Desde", "Hacia", "Motivo / documento", "Registró", "Sin eventos registrados."),
            "en": ("Asset history", "Code", "Serial", "Model", "Cost", "Purchase", "Date", "Event",
                   "From", "To", "Reason / document", "Recorded by", "No events recorded."),
            "it": ("Storico dell'asset", "Codice", "Seriale", "Modello", "Costo", "Acquisto", "Data", "Evento",
                   "Da", "A", "Motivo / documento", "Registrato da", "Nessun evento registrato."),
        }[lang]
        estilos = getSampleStyleSheet()
        chico = estilos["BodyText"].clone("chico", fontSize=7.5, leading=9)
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                                topMargin=1.2 * cm, bottomMargin=1.2 * cm, title=t[0])
        cabecera = [
            Paragraph(f"<b>{escape(t[0])}</b>", estilos["Title"]),
            Paragraph(escape(
                f"{t[1]}: {activo.ACT_Codigo_Interno}   ·   {t[2]}: {activo.ACT_Serie_Fabricante}   ·   "
                f"{t[3]}: {modelo.MOD_Nombre if modelo else '—'}   ·   {t[5]}: {format_iso_date(activo.ACT_Fecha_Compra)}"
                + (f"   ·   {t[4]}: {format_amount(activo.ACT_Costo)} {activo.ACT_Moneda or ''}" if con_costo else "")),
                estilos["BodyText"]),
            Spacer(1, 0.4 * cm),
        ]

        def lugar(persona, area, sede, equipo) -> str:
            return " · ".join(x for x in (persona, area, sede, equipo) if x) or "—"

        filas = [[Paragraph(f"<b>{escape(x)}</b>", chico) for x in (t[6], t[7], t[8], t[9], t[10], t[11])]]
        for e in eventos:
            doc_ref = " ".join(x for x in (e.KDX_Documento_Tipo, e.KDX_Documento_Ref) if x)
            filas.append([Paragraph(escape(v), chico) for v in (
                format_iso_datetime(e.KDX_Fecha), tipo_evento(e.KDX_Tipo, lang),
                lugar(n["persona"].get(e.KDX_Origen_Persona), n["area"].get(e.KDX_Origen_Area),
                      n["sede"].get(e.KDX_Origen_Sede), n["activo"].get(e.KDX_Origen_Activo)),
                lugar(n["persona"].get(e.KDX_Destino_Persona), n["area"].get(e.KDX_Destino_Area),
                      n["sede"].get(e.KDX_Destino_Sede), n["activo"].get(e.KDX_Destino_Activo)),
                " — ".join(x for x in (e.KDX_Motivo, doc_ref) if x) or "",
                n["usuario"].get(e.USU_Usuario, "") or "",
            )])
        cuerpo = cabecera
        if len(filas) == 1:
            cuerpo.append(Paragraph(escape(t[12]), estilos["BodyText"]))
        else:
            tabla = Table(filas, colWidths=[3.2 * cm, 3.6 * cm, 5.2 * cm, 5.2 * cm, 6.3 * cm, 3.0 * cm], repeatRows=1)
            tabla.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef3")),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#b8c4cc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            cuerpo.append(tabla)
        doc.build(cuerpo)
        nombre = {"es": "Hoja_de_vida", "en": "Asset_history", "it": "Storico_asset"}[lang]
        return buffer.getvalue(), f"{nombre}_{activo.ACT_Codigo_Interno}.pdf"

    # ------------------------------------------------------------------
    # Alertas para el tablero ("Requiere su atención")
    # ------------------------------------------------------------------
    # Pendientes que atiende o consulta cada rol; sin entrada = todos (los
    # administradores de TI). Se filtra en el servidor: un rol no recibe ni el
    # total de lo que no le corresponde.
    ATENCION_POR_ROL: dict[str, tuple[str, ...]] = {
        "TECNICO": ("TICKET_ANTIGUO", "PRESTAMO_VENCIDO", "ASIGNADO_SIN_CUSTODIO", "ALMACEN_CON_CUSTODIO",
                    "STOCK_BAJO", "PERSONA_INACTIVA_CON_EQUIPOS", "FALTANTE_PENDIENTE", "RESPONSABLE_INACTIVO"),
        "AUDITOR": ("ASIGNADO_SIN_CUSTODIO", "ALMACEN_CON_CUSTODIO", "BAJA_CON_CUSTODIA",
                    "PERSONA_INACTIVA_CON_EQUIPOS", "DATOS_INCOMPLETOS", "CUENTA_SIN_ROTAR", "FALTANTE_PENDIENTE",
                    "INSUMOS_POR_RECLASIFICAR"),
        "COMPRAS": ("ORDEN_PARCIAL", "GARANTIA_POR_VENCER", "STOCK_BAJO"),
        "CONTABILIDAD": ("DATOS_INCOMPLETOS",),
        "CONSULTA": (),
    }

    async def atencion(self, rol: str | None = None) -> dict[str, Any]:
        permitidos = self.ATENCION_POR_ROL.get(rol or "")
        if permitidos is not None and not permitidos:
            return {"generado": datetime.utcnow().isoformat(), "items": []}
        from app.core.config import settings
        from app.models.catalogs import EstadoOperativo
        from app.models.consumable import Consumible
        from app.models.traceability import Mantenimiento
        hoy = a_hora_local(datetime.utcnow()).date()
        estados = dict((await self.db.execute(
            select(EstadoOperativo.EOP_Nombre, EstadoOperativo.EOP_Estado_Operativo))).all())
        custodias = select(Movimiento.ACT_Activo).where(Movimiento.MOV_Fecha_Devolucion.is_(None))

        async def contar_activos(*cond) -> list[dict]:
            filas = (await self.db.execute(
                select(Activo.ACT_Activo, Activo.ACT_Codigo_Interno).where(*cond)
                .order_by(Activo.ACT_Codigo_Interno).limit(100))).all()
            return [{"id": str(a), "codigo": c} for a, c in filas]

        items: list[dict] = []

        def agregar(clave: str, severidad: str, lista: list, **extra) -> None:
            if lista:
                items.append({"clave": clave, "severidad": severidad, "total": len(lista), "items": lista[:20], **extra})

        baja = estados.get("Baja")
        agregar("ASIGNADO_SIN_CUSTODIO", "alta", await contar_activos(
            Activo.EOP_Estado_Operativo == estados.get("Asignado"), Activo.ACT_Activo.not_in(custodias)))
        agregar("ALMACEN_CON_CUSTODIO", "alta", await contar_activos(
            Activo.EOP_Estado_Operativo.in_([estados.get("Disponible"), estados.get("En Bodega")]),
            Activo.ACT_Activo.in_(custodias)))

        if baja:
            agregar("BAJA_CON_CUSTODIA", "alta", await contar_activos(
                Activo.EOP_Estado_Operativo == baja, Activo.ACT_Activo.in_(custodias)))

        from app.models.aprobacion import Aprobacion
        filas = (await self.db.execute(
            select(Aprobacion.APR_Aprobacion, Aprobacion.APR_Tipo, Aprobacion.APR_Resumen)
            .where(Aprobacion.APR_Estado == "PENDIENTE").order_by(Aprobacion.APR_Fecha_Solicitud)
        )).all()
        agregar("APROBACION_PENDIENTE", "alta",
                [{"id": i, "tipo": t, "resumen": r} for i, t, r in filas])

        filas = (await self.db.execute(
            select(Persona.PER_Persona, Persona.PER_Primer_Nombre, Persona.PER_Primer_Apellido,
                   func.count(Movimiento.MOV_Movimiento))
            .join(Movimiento, Movimiento.PER_Persona == Persona.PER_Persona)
            .where(Persona.PER_Estado.is_(False), Movimiento.MOV_Fecha_Devolucion.is_(None))
            .group_by(Persona.PER_Persona, Persona.PER_Primer_Nombre, Persona.PER_Primer_Apellido)
        )).all()
        agregar("PERSONA_INACTIVA_CON_EQUIPOS", "alta",
                [{"id": str(p), "nombre": f"{n} {a}", "cantidad": c} for p, n, a, c in filas])

        # Faltantes de tomas sin resolver (investigar, localizar o pedir la baja).
        from app.models.inventario_fisico import Faltante
        filas = (await self.db.execute(
            select(Faltante.FAL_Faltante, Activo.ACT_Codigo_Interno, Faltante.FAL_Estado)
            .join(Activo, Activo.ACT_Activo == Faltante.ACT_Activo)
            .where(Faltante.FAL_Estado.in_(("PENDIENTE", "EN_INVESTIGACION")))
            .order_by(Faltante.FAL_Fecha)
        )).all()
        agregar("FALTANTE_PENDIENTE", "alta", [{"id": f, "codigo": c, "estado": e} for f, c, e in filas])

        # Equipos compartidos cuyo responsable ya no está activo.
        filas = (await self.db.execute(
            select(Activo.ACT_Activo, Activo.ACT_Codigo_Interno, Persona.PER_Primer_Nombre, Persona.PER_Primer_Apellido)
            .join(Persona, Persona.PER_Persona == Activo.ACT_Responsable)
            .where(Activo.ACT_Instalado.is_(True), Persona.PER_Estado.is_(False))
        )).all()
        agregar("RESPONSABLE_INACTIVO", "media",
                [{"id": str(a), "codigo": c, "nombre": f"{n} {ap}"} for a, c, n, ap in filas])

        # Tipos «insumo» que aún tienen activos (deben pasar a consumibles).
        from app.models.catalogs import TipoActivo
        filas = (await self.db.execute(
            select(TipoActivo.TAC_Tipo_Activo, TipoActivo.TAC_Nombre, func.count(Activo.ACT_Activo))
            .join(Activo, Activo.TAC_Tipo_Activo == TipoActivo.TAC_Tipo_Activo)
            .where(TipoActivo.TAC_Clase == "INSUMO", *([Activo.EOP_Estado_Operativo != baja] if baja else []))
            .group_by(TipoActivo.TAC_Tipo_Activo, TipoActivo.TAC_Nombre)
        )).all()
        agregar("INSUMOS_POR_RECLASIFICAR", "baja", [{"id": t, "nombre": n, "cantidad": c} for t, n, c in filas])

        filas = (await self.db.execute(
            select(Movimiento.MOV_Movimiento, Activo.ACT_Codigo_Interno, Movimiento.MOV_Fecha_Prevista_Devolucion)
            .join(Activo, Activo.ACT_Activo == Movimiento.ACT_Activo)
            .where(Movimiento.MOV_Fecha_Devolucion.is_(None), Movimiento.MOV_Fecha_Prevista_Devolucion.is_not(None),
                   Movimiento.MOV_Fecha_Prevista_Devolucion < hoy)
        )).all()
        agregar("PRESTAMO_VENCIDO", "media",
                [{"id": str(m), "codigo": c, "fecha": f.isoformat()} for m, c, f in filas])

        filas = (await self.db.execute(
            select(Consumible.CON_Consumible, Consumible.CON_Nombre, Consumible.CON_Stock_Actual,
                   Consumible.CON_Stock_Minimo)
            .where(Consumible.CON_Activo.is_(True), Consumible.CON_Stock_Minimo > 0,
                   Consumible.CON_Stock_Actual <= Consumible.CON_Stock_Minimo)
        )).all()
        agregar("STOCK_BAJO", "media",
                [{"id": c, "nombre": n, "stock": s, "minimo": m} for c, n, s, m in filas])

        from app.models.procurement import OrdenCompra
        filas = (await self.db.execute(
            select(OrdenCompra.OCO_Orden, OrdenCompra.OCO_Numero, OrdenCompra.OCO_Fecha)
            .where(OrdenCompra.OCO_Estado == "PARCIAL").order_by(OrdenCompra.OCO_Fecha)
        )).all()
        agregar("ORDEN_PARCIAL", "media",
                [{"id": o, "numero": num, "fecha": f.isoformat()} for o, num, f in filas])

        limite_ticket = datetime.utcnow() - timedelta(days=15)
        filas = (await self.db.execute(
            select(Mantenimiento.MAN_Mantenimiento, Activo.ACT_Codigo_Interno, Mantenimiento.MAN_Fecha_Ingreso)
            .join(Activo, Activo.ACT_Activo == Mantenimiento.ACT_Activo)
            .where(Mantenimiento.MAN_Fecha_Cierre.is_(None), Mantenimiento.MAN_Fecha_Ingreso < limite_ticket)
        )).all()
        agregar("TICKET_ANTIGUO", "media",
                [{"id": str(m), "codigo": c, "desde": format_iso_date(f)} for m, c, f in filas])

        cond_vigentes = [Activo.EOP_Estado_Operativo != baja] if baja else []
        agregar("GARANTIA_POR_VENCER", "baja", await contar_activos(
            *cond_vigentes, Activo.ACT_Fin_Garantia.is_not(None), Activo.ACT_Fin_Garantia >= hoy,
            Activo.ACT_Fin_Garantia <= hoy + timedelta(days=90)))

        filas = (await self.db.execute(
            select(Licencia.LIC_Licencia, Software.SOF_Nombre, Licencia.LIC_Fecha_Vencimiento)
            .join(Software, Software.SOF_Software == Licencia.SOF_Software)
            .where(Licencia.LIC_Fecha_Vencimiento.is_not(None),
                   Licencia.LIC_Fecha_Vencimiento <= hoy + timedelta(days=settings.ALERTA_LICENCIAS_DIAS))
            .order_by(Licencia.LIC_Fecha_Vencimiento)
        )).all()
        agregar("LICENCIA_POR_VENCER", "media",
                [{"id": lic, "nombre": n, "fecha": f.isoformat(), "vencida": f < hoy} for lic, n, f in filas])

        # Cuentas de licencia: clave por cambiar tras la salida de una persona,
        # claves sin rotar hace demasiado y licencias sin cupos libres.
        from app.models.catalogo_estandar import FormaConteo
        from app.models.software import LicenciaClave
        filas = (await self.db.execute(
            select(LicenciaClave.LCL_Licencia_Clave, LicenciaClave.LIC_Licencia, LicenciaClave.LCL_Usuario,
                   Software.SOF_Nombre)
            .join(Licencia, Licencia.LIC_Licencia == LicenciaClave.LIC_Licencia)
            .join(Software, Software.SOF_Software == Licencia.SOF_Software)
            .where(LicenciaClave.LCL_Requiere_Cambio.is_(True), LicenciaClave.LCL_Estado != "BAJA")
        )).all()
        agregar("CUENTA_POR_CAMBIAR", "alta",
                [{"id": c, "licencia": lic, "usuario": u, "nombre": n} for c, lic, u, n in filas])

        limite_rotacion = datetime.utcnow() - timedelta(days=settings.ROTACION_CUENTAS_DIAS)
        filas = (await self.db.execute(
            select(LicenciaClave.LCL_Licencia_Clave, LicenciaClave.LIC_Licencia, LicenciaClave.LCL_Usuario,
                   Software.SOF_Nombre)
            .join(Licencia, Licencia.LIC_Licencia == LicenciaClave.LIC_Licencia)
            .join(Software, Software.SOF_Software == Licencia.SOF_Software)
            .where(LicenciaClave.LCL_Secreto_Enc.is_not(None), LicenciaClave.LCL_Estado != "BAJA",
                   LicenciaClave.LCL_Requiere_Cambio.is_(False),
                   func.coalesce(LicenciaClave.LCL_Ultimo_Cambio_Secreto, LicenciaClave.created_at) < limite_rotacion)
        )).all()
        agregar("CUENTA_SIN_ROTAR", "baja",
                [{"id": c, "licencia": lic, "usuario": u, "nombre": n} for c, lic, u, n in filas],
                dias=settings.ROTACION_CUENTAS_DIAS)

        filas = (await self.db.execute(
            select(Licencia.LIC_Licencia, Software.SOF_Nombre, Licencia.LIC_Cantidad_Total)
            .join(Software, Software.SOF_Software == Licencia.SOF_Software)
            .join(FormaConteo, FormaConteo.FCO_Forma == Licencia.FCO_Forma_Conteo, isouter=True)
            .where(Licencia.LIC_Cantidad_Usada >= Licencia.LIC_Cantidad_Total,
                   func.coalesce(FormaConteo.FCO_Codigo, "") != "SITIO")
        )).all()
        agregar("CUPOS_AGOTADOS", "media", [{"id": lic, "nombre": n, "total": t} for lic, n, t in filas])

        sin_garantia = (await self.db.execute(select(func.count()).select_from(Activo).where(
            *cond_vigentes, Activo.ACT_Fin_Garantia.is_(None)))).scalar_one()
        sin_costo = (await self.db.execute(select(func.count()).select_from(Activo).where(
            *cond_vigentes, Activo.ACT_Costo.is_(None)))).scalar_one()
        if sin_garantia or sin_costo:
            items.append({"clave": "DATOS_INCOMPLETOS", "severidad": "baja", "total": sin_garantia + sin_costo,
                          "sin_garantia": sin_garantia, "sin_costo": sin_costo, "items": []})
        if permitidos is not None:
            items = [i for i in items if i["clave"] in permitidos]
        orden = {"alta": 0, "media": 1, "baja": 2}
        items.sort(key=lambda i: orden[i["severidad"]])
        return {"generado": datetime.utcnow().isoformat(), "items": items}
