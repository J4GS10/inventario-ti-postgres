"""
Diagnóstico (y corrección opcional) de incoherencias del inventario.

Antes de la Fase 1 el estado de un activo se podía editar a mano y algunos
tipos de movimiento dejaban custodias abiertas; este script encuentra lo que
quedó mal en los datos existentes y, solo con --corregir, aplica las
correcciones seguras. Cada corrección queda en la bitácora (acción DATA_FIX).

Uso:
    python -m app.diagnostico_inventario                 # solo informe
    python -m app.diagnostico_inventario --corregir      # informe + correcciones
    python -m app.diagnostico_inventario --json salida.json

Reglas de corrección (lo que no aparece aquí solo se informa):
    - "Asignado" sin custodia abierta            → "En Bodega"
    - "Disponible"/"En Bodega" con custodia      → "Asignado"
    - "En Reparación" sin ticket abierto          → "Asignado" si tiene custodia, si no "En Bodega"
    - ticket abierto con otro estado (no Baja)    → "En Reparación"
    - cupo usado de una licencia ≠ instalaciones → se recalcula
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, update

from app.db.session import SessionLocal as AsyncSessionLocal
from app.models.catalogs import EstadoOperativo
from app.models.consumable import Consumible, MovimientoConsumible
from app.models.core import Activo
from app.models.governance import AuditoriaSistema
from app.models.organization import Persona
from app.models.software import Instalacion, Licencia
from app.models.traceability import Mantenimiento, Movimiento


@dataclass
class Hallazgo:
    regla: str
    descripcion: str
    corregible: bool
    items: list[dict[str, Any]] = field(default_factory=list)


async def _estados(db) -> dict[str, int]:
    rows = (await db.execute(select(EstadoOperativo.EOP_Nombre, EstadoOperativo.EOP_Estado_Operativo))).all()
    return {n: i for n, i in rows}


async def diagnosticar(db) -> list[Hallazgo]:
    est = await _estados(db)
    asignado, bodega, disponible = est.get("Asignado"), est.get("En Bodega"), est.get("Disponible")
    reparacion, baja = est.get("En Reparación"), est.get("Baja")

    custodia = select(Movimiento.ACT_Activo).where(Movimiento.MOV_Fecha_Devolucion.is_(None))
    ticket = select(Mantenimiento.ACT_Activo).where(Mantenimiento.MAN_Fecha_Cierre.is_(None))

    async def activos(*cond) -> list[dict]:
        rows = (await db.execute(
            select(Activo.ACT_Activo, Activo.ACT_Codigo_Interno, Activo.EOP_Estado_Operativo).where(*cond)
            .order_by(Activo.ACT_Codigo_Interno)
        )).all()
        return [{"activo": str(a), "codigo": c, "estado": e} for a, c, e in rows]

    hallazgos = [
        Hallazgo("ASIGNADO_SIN_CUSTODIA", "Activos «Asignado» sin custodia abierta", True,
                 await activos(Activo.EOP_Estado_Operativo == asignado, Activo.ACT_Activo.not_in(custodia))),
        Hallazgo("ALMACEN_CON_CUSTODIA", "Activos «Disponible» o «En Bodega» con custodia abierta", True,
                 await activos(Activo.EOP_Estado_Operativo.in_([disponible, bodega]), Activo.ACT_Activo.in_(custodia))),
        Hallazgo("REPARACION_SIN_TICKET", "Activos «En Reparación» sin ticket abierto", True,
                 await activos(Activo.EOP_Estado_Operativo == reparacion, Activo.ACT_Activo.not_in(ticket))),
        Hallazgo("TICKET_SIN_REPARACION", "Activos con ticket abierto que no están «En Reparación»", True,
                 await activos(Activo.ACT_Activo.in_(ticket),
                               Activo.EOP_Estado_Operativo.not_in([reparacion, baja]))),
        Hallazgo("BAJA_CON_CUSTODIA", "Activos de baja con custodia abierta (revisar y devolver)", False,
                 await activos(Activo.EOP_Estado_Operativo == baja, Activo.ACT_Activo.in_(custodia))),
        Hallazgo("SIN_SEDE", "Activos sin sede (invisibles para cuentas con alcance por sede)", False,
                 await activos(Activo.SED_Sede.is_(None))),
    ]

    rows = (await db.execute(
        select(Persona.PER_Persona, Persona.PER_Primer_Nombre, Persona.PER_Primer_Apellido,
               func.count(Movimiento.MOV_Movimiento))
        .join(Movimiento, Movimiento.PER_Persona == Persona.PER_Persona)
        .where(Persona.PER_Estado.is_(False), Movimiento.MOV_Fecha_Devolucion.is_(None))
        .group_by(Persona.PER_Persona, Persona.PER_Primer_Nombre, Persona.PER_Primer_Apellido)
    )).all()
    hallazgos.append(Hallazgo(
        "PERSONA_INACTIVA_CON_EQUIPOS", "Personas inactivas que conservan equipos (hacer el descargo)", False,
        [{"persona": str(p), "nombre": f"{n} {a}", "custodias": c} for p, n, a, c in rows],
    ))

    usadas = (select(Instalacion.LIC_Licencia, func.count().label("n"))
              .where(Instalacion.INS_Estado.is_(True)).group_by(Instalacion.LIC_Licencia).subquery())
    rows = (await db.execute(
        select(Licencia.LIC_Licencia, Licencia.LIC_Cantidad_Usada, func.coalesce(usadas.c.n, 0))
        .join(usadas, usadas.c.LIC_Licencia == Licencia.LIC_Licencia, isouter=True)
        .where(Licencia.LIC_Cantidad_Usada != func.coalesce(usadas.c.n, 0))
    )).all()
    hallazgos.append(Hallazgo(
        "LICENCIA_CUPO_DESCUADRADO", "Licencias cuyo cupo usado no coincide con sus instalaciones", True,
        [{"licencia": lic, "usada": u, "instalaciones": n} for lic, u, n in rows],
    ))

    ultimo = (select(MovimientoConsumible.CON_Consumible, func.max(MovimientoConsumible.MOC_Movimiento).label("m"))
              .group_by(MovimientoConsumible.CON_Consumible).subquery())
    rows = (await db.execute(
        select(Consumible.CON_Consumible, Consumible.CON_Nombre, Consumible.CON_Stock_Actual,
               MovimientoConsumible.MOC_Stock_Resultante)
        .join(ultimo, ultimo.c.CON_Consumible == Consumible.CON_Consumible)
        .join(MovimientoConsumible, MovimientoConsumible.MOC_Movimiento == ultimo.c.m)
        .where(Consumible.CON_Stock_Actual != MovimientoConsumible.MOC_Stock_Resultante)
    )).all()
    hallazgos.append(Hallazgo(
        "STOCK_DISTINTO_AL_KARDEX", "Consumibles cuyo stock no coincide con su último movimiento (hacer un ajuste)",
        False, [{"consumible": c, "nombre": n, "stock": s, "kardex": k} for c, n, s, k in rows],
    ))
    # Duplicados que impiden crear los índices únicos sin mayúsculas/espacios.
    from sqlalchemy import text as _text
    from app.db.unicidad import INDICES_CI
    duplicados = []
    for nombre, tabla, texto_cols, exactas, codigo in INDICES_CI:
        claves = [f'"{c}"' for c in exactas] + [f'lower(trim("{c}"))' for c in texto_cols]
        grupo = ", ".join(claves)
        filas = (await db.execute(_text(
            f'SELECT {grupo}, count(*) FROM "{tabla}" GROUP BY {grupo} HAVING count(*) > 1 LIMIT 20'
        ))).all()
        duplicados += [{"indice": nombre, "valor": " / ".join(str(v) for v in f[:-1]), "veces": f[-1]} for f in filas]
    hallazgos.append(Hallazgo(
        "DUPLICADOS", "Valores repetidos (sin distinguir mayúsculas/espacios): unifíquelos y ejecute "
        "python -m app.db.unicidad_instalar", False, duplicados,
    ))
    return hallazgos


async def corregir(db, hallazgos: list[Hallazgo]) -> int:
    est = await _estados(db)
    custodias = set((await db.execute(
        select(Movimiento.ACT_Activo).where(Movimiento.MOV_Fecha_Devolucion.is_(None))
    )).scalars().all())
    cambios = 0

    async def fijar(activo: str, estado: int, regla: str, antes: int) -> None:
        nonlocal cambios
        await db.execute(update(Activo).where(Activo.ACT_Activo == activo).values(EOP_Estado_Operativo=estado))
        db.add(AuditoriaSistema(
            AUD_Accion="DATA_FIX", AUD_Entidad_Afectada="INV_ACTIVO",
            AUD_Snapshot_JSON={"regla": regla, "activo": activo, "estado_antes": antes, "estado_despues": estado},
        ))
        cambios += 1

    for h in hallazgos:
        if not h.corregible:
            continue
        for it in h.items:
            if h.regla == "ASIGNADO_SIN_CUSTODIA":
                await fijar(it["activo"], est["En Bodega"], h.regla, it["estado"])
            elif h.regla == "ALMACEN_CON_CUSTODIA":
                await fijar(it["activo"], est["Asignado"], h.regla, it["estado"])
            elif h.regla == "REPARACION_SIN_TICKET":
                import uuid as _uuid
                destino = "Asignado" if _uuid.UUID(it["activo"]) in custodias else "En Bodega"
                await fijar(it["activo"], est[destino], h.regla, it["estado"])
            elif h.regla == "TICKET_SIN_REPARACION":
                await fijar(it["activo"], est["En Reparación"], h.regla, it["estado"])
            elif h.regla == "LICENCIA_CUPO_DESCUADRADO":
                await db.execute(update(Licencia).where(Licencia.LIC_Licencia == it["licencia"])
                                 .values(LIC_Cantidad_Usada=it["instalaciones"]))
                db.add(AuditoriaSistema(AUD_Accion="DATA_FIX", AUD_Entidad_Afectada="INV_LICENCIA",
                                        AUD_Snapshot_JSON={"regla": h.regla, **it}))
                cambios += 1
    await db.commit()
    return cambios


def informe_texto(hallazgos: list[Hallazgo]) -> str:
    lineas = ["# Diagnóstico del inventario", ""]
    total = sum(len(h.items) for h in hallazgos)
    lineas.append(f"Hallazgos: {total}")
    for h in hallazgos:
        marca = "corregible" if h.corregible else "solo informe"
        lineas.append(f"\n## {h.descripcion} — {len(h.items)} ({marca})")
        for it in h.items[:50]:
            lineas.append("- " + ", ".join(f"{k}={v}" for k, v in it.items()))
        if len(h.items) > 50:
            lineas.append(f"- … y {len(h.items) - 50} más")
    return "\n".join(lineas)


async def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corregir", action="store_true", help="aplica las correcciones seguras")
    ap.add_argument("--json", help="guarda el diagnóstico en este archivo JSON")
    args = ap.parse_args(argv)
    async with AsyncSessionLocal() as db:
        hallazgos = await diagnosticar(db)
        print(informe_texto(hallazgos))
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump([h.__dict__ for h in hallazgos], f, ensure_ascii=False, indent=1, default=str)
        if args.corregir:
            n = await corregir(db, hallazgos)
            print(f"\nCorrecciones aplicadas: {n}")
            restantes = sum(len(h.items) for h in await diagnosticar(db) if h.corregible)
            print(f"Incoherencias corregibles restantes: {restantes}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
