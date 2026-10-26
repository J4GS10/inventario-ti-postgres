"""
Credenciales y cupos de las licencias (plan de licencias y catálogos, bloque A).

Cada licencia tiene un método de activación que decide qué pide cada
credencial (usuario/correo, secreto, serial, key, servidor, archivo,
dispositivo). Aquí se valida exactamente eso, se cifran el secreto y la key,
y se controla quién los ve:

- Los listados nunca devuelven el secreto ni la key completa (solo sus
  últimos caracteres para reconocerla).
- «Revelar» devuelve el secreto a JEFE_TI / ADMIN_TI / SUPER_ADMIN y deja
  LICENSE_SECRET_VIEWED en la bitácora y LICENCIA_SECRETO_VISTO en el kardex.
- «Rotar» cambia el secreto o la key, guarda la fecha y quita el pendiente
  de cambio que deja la salida de una persona.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.security import decrypt_field, encrypt_field, fingerprint_field
from app.core.transactional import transactional
from app.models.attachment import Adjunto
from app.models.catalogo_estandar import CAMPOS_CREDENCIAL, MetodoActivacion
from app.models.software import Instalacion, Licencia, LicenciaClave, Software
from app.repositories.governance import GovernanceRepository
from app.services.kardex import KardexService

# Campo de la credencial → columna. «secreto» y «clave» se guardan cifrados.
COLUMNA = {
    "usuario": "LCL_Usuario", "secreto": "LCL_Secreto_Enc", "serial": "LCL_Serial", "clave": "LCL_Clave_Activacion",
    "servidor": "LCL_Servidor", "archivo": "LCL_Archivo", "dispositivo": "LCL_Dispositivo",
}
CIFRADOS = ("secreto", "clave")


def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def campos_pedidos(metodo: MetodoActivacion | None) -> set[str]:
    """Campos que pide el método. Sin método (licencias anteriores): la product key."""
    if metodo is None:
        return {"clave"}
    return {c for c in CAMPOS_CREDENCIAL if getattr(metodo, f"MAC_Pide_{c.capitalize()}")}


def _texto(valor) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, uuid.UUID):
        return str(valor)
    return str(valor).strip() or None


def huella(licencia_id: int, datos: dict) -> str | None:
    """
    Huella única sin revelar el valor. La key y el serial son únicos en toda
    la empresa; una cuenta o un servidor, dentro de su licencia (la misma
    cuenta puede activar dos productos distintos).
    """
    if datos.get("clave"):
        return fingerprint_field(datos["clave"])  # compatible con las claves anteriores
    if datos.get("serial"):
        return fingerprint_field(f"serial:{datos['serial'].strip().upper()}")
    if datos.get("dispositivo"):
        return fingerprint_field(f"dongle:{datos['dispositivo'].strip().upper()}")
    if datos.get("usuario"):
        return fingerprint_field(f"cuenta:{licencia_id}:{datos['usuario'].strip().lower()}")
    if datos.get("servidor"):
        return fingerprint_field(f"servidor:{licencia_id}:{datos['servidor'].strip().lower()}")
    if datos.get("secreto"):
        return fingerprint_field(f"token:{licencia_id}:{datos['secreto']}")
    return None


def _mascara(clave_cifrada: str | None) -> str | None:
    if not clave_cifrada:
        return None
    clave = decrypt_field(clave_cifrada) or ""
    return f"•••• {clave[-5:]}" if clave else None


class CredencialService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.gov_repo = GovernanceRepository(db)

    # ── Utilidades ───────────────────────────────────────────────────────────
    async def _licencia(self, licencia_id: int) -> Licencia:
        lic = (await self.db.execute(
            select(Licencia).options(selectinload(Licencia.metodo_activacion), selectinload(Licencia.forma_conteo))
            .where(Licencia.LIC_Licencia == licencia_id)
        )).scalar_one_or_none()
        if lic is None:
            raise HTTPException(404, "LICENSE_NOT_FOUND")
        return lic

    async def _credencial(self, credencial_id: int) -> LicenciaClave:
        cred = await self.db.get(LicenciaClave, credencial_id)
        if cred is None:
            raise HTTPException(404, "LICENSE_KEY_NOT_FOUND")
        return cred

    async def _referencia(self, licencia_id: int) -> str:
        sw = (await self.db.execute(
            select(Software).join(Licencia, Licencia.SOF_Software == Software.SOF_Software)
            .where(Licencia.LIC_Licencia == licencia_id)
        )).scalar_one_or_none()
        nombre = " ".join(p for p in (sw.SOF_Nombre, sw.SOF_Version) if p) if sw else "Licencia"
        return f"{nombre} · licencia #{licencia_id}"[:200]

    def validar(self, metodo: MetodoActivacion | None, datos: dict, *, parcial: bool = False) -> dict:
        """
        Exige exactamente los campos que pide el método y rechaza los demás.
        `parcial` (edición): solo valida los campos presentes.
        """
        pedidos = campos_pedidos(metodo)
        limpios = {c: _texto(datos.get(c)) for c in CAMPOS_CREDENCIAL if c in datos}
        if not pedidos and not parcial:
            raise HTTPException(422, "LICENSE_METHOD_HAS_NO_CREDENTIALS")
        for campo, valor in limpios.items():
            if valor and campo not in pedidos:
                raise HTTPException(422, f"LICENSE_FIELD_NOT_ALLOWED:{campo}")
        for campo in pedidos:
            if parcial and campo not in limpios:
                continue
            if not limpios.get(campo):
                raise HTTPException(422, f"LICENSE_FIELD_REQUIRED:{campo}")
        if "archivo" in limpios and limpios["archivo"]:
            limpios["archivo"] = uuid.UUID(limpios["archivo"])
        return limpios

    async def _validar_archivo(self, licencia_id: int, archivo: uuid.UUID | None) -> None:
        if archivo is None:
            return
        adj = await self.db.get(Adjunto, archivo)
        if adj is None or adj.LIC_Licencia != licencia_id:
            raise HTTPException(422, "LICENSE_FILE_NOT_FOUND")

    @staticmethod
    def _columnas(limpios: dict) -> dict:
        return {COLUMNA[c]: (encrypt_field(v) if c in CIFRADOS and v else v) for c, v in limpios.items()}

    # ── Alta (una o varias, p. ej. pegadas desde una lista) ─────────────────
    @transactional
    async def crear(self, licencia_id: int, credenciales: list[dict], *, usuario_id=None, ip=None,
                    registrar_kardex: bool = True) -> list[LicenciaClave]:
        lic = await self._licencia(licencia_id)
        metodo = lic.metodo_activacion
        lote = [self.validar(metodo, c) for c in credenciales]
        huellas = [huella(licencia_id, c) for c in lote]
        reales = [h for h in huellas if h]
        if len(reales) != len(set(reales)):
            raise HTTPException(400, "DUPLICATE_LICENSE_KEY_IN_REQUEST")
        if reales and (await self.db.execute(
                select(func.count()).select_from(LicenciaClave).where(LicenciaClave.LCL_Clave_Hash.in_(reales))
        )).scalar_one():
            raise HTTPException(409, "LICENSE_KEY_ALREADY_EXISTS")
        # Las credenciales son cupos: no más que los comprados (salvo por sitio).
        forma = lic.forma_conteo.FCO_Codigo if lic.forma_conteo else None
        existentes = (await self.db.execute(select(func.count()).select_from(LicenciaClave).where(
            LicenciaClave.LIC_Licencia == licencia_id, LicenciaClave.LCL_Estado != "BAJA"))).scalar_one()
        if forma != "SITIO" and existentes + len(lote) > lic.LIC_Cantidad_Total:
            raise HTTPException(409, f"LICENSE_CREDENTIALS_EXCEED_SEATS:{lic.LIC_Cantidad_Total}")

        creadas = []
        ahora = _ahora()
        for datos, original, h in zip(lote, credenciales, huellas):
            await self._validar_archivo(licencia_id, datos.get("archivo"))
            cred = LicenciaClave(
                LIC_Licencia=licencia_id, LCL_Clave_Hash=h, LCL_Estado="DISPONIBLE",
                LCL_Referencia=_texto(original.get("referencia")), LCL_Vence=original.get("vence"),
                LCL_Ultimo_Cambio_Secreto=ahora if datos.get("secreto") else None,
                **self._columnas(datos),
            )
            self.db.add(cred)
            creadas.append(cred)
        await self.db.flush()
        if registrar_kardex:
            await KardexService(self.db).registrar(
                "LICENCIA_CREDENCIAL_ALTA", licencia_id=licencia_id, referencia=await self._referencia(licencia_id),
                cantidad=len(creadas), usuario_id=usuario_id,
                detalle={"credenciales": [c.LCL_Licencia_Clave for c in creadas]},
            )
        # Solo datos visibles en la bitácora: nunca secretos ni keys.
        await self.gov_repo.create_audit_log(
            "CREATE", "INV_LICENCIA_CLAVE",
            {"licencia": licencia_id, "cantidad": len(creadas),
             "usuarios": [c.LCL_Usuario for c in creadas if c.LCL_Usuario][:50]},
            usuario_id=usuario_id, ip_origen=ip,
        )
        return creadas

    # ── Listado enmascarado ──────────────────────────────────────────────────
    async def listar(self, licencia_id: int) -> list[dict]:
        lic = await self._licencia(licencia_id)
        filas = (await self.db.execute(
            select(LicenciaClave)
            .options(selectinload(LicenciaClave.instalaciones).selectinload(Instalacion.activo),
                     selectinload(LicenciaClave.instalaciones).selectinload(Instalacion.persona))
            .where(LicenciaClave.LIC_Licencia == licencia_id)
            .order_by(LicenciaClave.LCL_Licencia_Clave)
        )).scalars().all()
        archivos = {a.ADJ_Adjunto: a.ADJ_Nombre_Original for a in (await self.db.execute(
            select(Adjunto).where(Adjunto.LIC_Licencia == licencia_id))).scalars()}
        limite = settings.ROTACION_CUENTAS_DIAS
        hoy = _ahora()
        salida = []
        for c in filas:
            activa = next((i for i in c.instalaciones if i.INS_Estado), None)
            destino = None
            if activa is not None and activa.activo is not None:
                destino = {"tipo": "ACTIVO", "id": str(activa.ACT_Activo), "nombre": activa.activo.ACT_Codigo_Interno}
            elif activa is not None and activa.persona is not None:
                p = activa.persona
                destino = {"tipo": "PERSONA", "id": str(activa.PER_Persona),
                           "nombre": " ".join(x for x in (p.PER_Primer_Nombre, p.PER_Primer_Apellido) if x),
                           "detalle": p.PER_Email_Corporativo}
            cambio = c.LCL_Ultimo_Cambio_Secreto or c.created_at
            salida.append({
                "LCL_Licencia_Clave": c.LCL_Licencia_Clave, "LIC_Licencia": c.LIC_Licencia,
                "referencia": c.LCL_Referencia, "usuario": c.LCL_Usuario, "serial": c.LCL_Serial,
                "servidor": c.LCL_Servidor, "dispositivo": c.LCL_Dispositivo,
                "archivo": {"id": str(c.LCL_Archivo), "nombre": archivos.get(c.LCL_Archivo)} if c.LCL_Archivo else None,
                "clave_mascara": _mascara(c.LCL_Clave_Activacion),
                "tiene_secreto": bool(c.LCL_Secreto_Enc), "tiene_clave": bool(c.LCL_Clave_Activacion),
                "vence": c.LCL_Vence, "estado": c.LCL_Estado, "creado": c.created_at,
                "ultimo_cambio_secreto": c.LCL_Ultimo_Cambio_Secreto,
                "requiere_cambio": c.LCL_Requiere_Cambio,
                "sin_rotar": bool(c.LCL_Secreto_Enc and cambio and (hoy - cambio).days > limite),
                "asignada_en": activa.INS_Fecha_Instalacion if activa else None,
                "destino": destino,
            })
        # Licencia anterior con una sola key en la cabecera (sin credenciales).
        if not salida and lic.LIC_Clave_Activacion:
            salida.append({
                "LCL_Licencia_Clave": -lic.LIC_Licencia, "LIC_Licencia": lic.LIC_Licencia, "referencia": None,
                "usuario": None, "serial": None, "servidor": None, "dispositivo": None, "archivo": None,
                "clave_mascara": _mascara(lic.LIC_Clave_Activacion), "tiene_secreto": False, "tiene_clave": True,
                "vence": None, "estado": "ASIGNADA" if lic.LIC_Cantidad_Usada else "DISPONIBLE", "creado": None,
                "ultimo_cambio_secreto": None, "requiere_cambio": False, "sin_rotar": False,
                "asignada_en": None, "destino": None, "legada": True,
            })
        return salida

    # ── Edición de datos visibles ────────────────────────────────────────────
    @transactional
    async def editar(self, credencial_id: int, datos: dict, *, usuario_id=None, ip=None) -> None:
        cred = await self._credencial(credencial_id)
        lic = await self._licencia(cred.LIC_Licencia)
        visibles = {k: v for k, v in datos.items() if k in ("usuario", "serial", "servidor", "dispositivo")}
        limpios = self.validar(lic.metodo_activacion, visibles, parcial=True)
        cambios = self._columnas(limpios)
        if "referencia" in datos:
            cambios["LCL_Referencia"] = _texto(datos["referencia"])
        if "vence" in datos:
            cambios["LCL_Vence"] = datos["vence"]
        if datos.get("estado") and datos["estado"] != cred.LCL_Estado:
            if cred.LCL_Estado == "ASIGNADA":
                raise HTTPException(409, "LICENSE_CREDENTIAL_IN_USE")
            cambios["LCL_Estado"] = datos["estado"]
        if limpios:
            actual = {c: getattr(cred, COLUMNA[c]) for c in ("usuario", "serial", "servidor", "dispositivo")}
            actual.update(limpios)
            actual["clave"] = decrypt_field(cred.LCL_Clave_Activacion) if cred.LCL_Clave_Activacion else None
            nueva = huella(cred.LIC_Licencia, actual)
            if nueva != cred.LCL_Clave_Hash and nueva and (await self.db.execute(
                    select(LicenciaClave.LCL_Licencia_Clave).where(LicenciaClave.LCL_Clave_Hash == nueva))).first():
                raise HTTPException(409, "LICENSE_KEY_ALREADY_EXISTS")
            cambios["LCL_Clave_Hash"] = nueva
        for columna, valor in cambios.items():
            setattr(cred, columna, valor)
        await self.gov_repo.create_audit_log(
            "UPDATE", "INV_LICENCIA_CLAVE",
            {"id": credencial_id, "cambios": sorted(k for k in cambios if k not in ("LCL_Clave_Hash",))},
            usuario_id=usuario_id, ip_origen=ip,
        )

    # ── Revelar y rotar ──────────────────────────────────────────────────────
    @transactional
    async def revelar(self, credencial_id: int, *, usuario_id=None, ip=None) -> dict:
        cred = await self._credencial(credencial_id)
        if not cred.LCL_Secreto_Enc and not cred.LCL_Clave_Activacion:
            raise HTTPException(404, "LICENSE_CREDENTIAL_HAS_NO_SECRET")
        await self.gov_repo.create_audit_log(
            "LICENSE_SECRET_VIEWED", "INV_LICENCIA_CLAVE",
            {"credencial": credencial_id, "licencia": cred.LIC_Licencia, "usuario_cuenta": cred.LCL_Usuario},
            usuario_id=usuario_id, ip_origen=ip,
        )
        await KardexService(self.db).registrar(
            "LICENCIA_SECRETO_VISTO", licencia_id=cred.LIC_Licencia, clave_id=credencial_id,
            referencia=await self._referencia(cred.LIC_Licencia), usuario_id=usuario_id,
        )
        return {
            "secreto": decrypt_field(cred.LCL_Secreto_Enc) if cred.LCL_Secreto_Enc else None,
            "clave": decrypt_field(cred.LCL_Clave_Activacion) if cred.LCL_Clave_Activacion else None,
            "ocultar_en_segundos": 30,
        }

    @transactional
    async def rotar(self, credencial_id: int, datos: dict, *, usuario_id=None, ip=None) -> None:
        cred = await self._credencial(credencial_id)
        lic = await self._licencia(cred.LIC_Licencia)
        pedidos = campos_pedidos(lic.metodo_activacion)
        nuevos = {c: _texto(datos.get(c)) for c in CIFRADOS if _texto(datos.get(c))}
        if not nuevos:
            raise HTTPException(422, "LICENSE_ROTATION_EMPTY")
        for campo in nuevos:
            if campo not in pedidos:
                raise HTTPException(422, f"LICENSE_FIELD_NOT_ALLOWED:{campo}")
        if "clave" in nuevos:
            nueva = fingerprint_field(nuevos["clave"])
            if (await self.db.execute(select(LicenciaClave.LCL_Licencia_Clave).where(
                    LicenciaClave.LCL_Clave_Hash == nueva,
                    LicenciaClave.LCL_Licencia_Clave != credencial_id))).first():
                raise HTTPException(409, "LICENSE_KEY_ALREADY_EXISTS")
            cred.LCL_Clave_Hash = nueva
        for columna, valor in self._columnas(nuevos).items():
            setattr(cred, columna, valor)
        cred.LCL_Ultimo_Cambio_Secreto = _ahora()
        cred.LCL_Requiere_Cambio = False
        await KardexService(self.db).registrar(
            "LICENCIA_SECRETO_CAMBIADO", licencia_id=cred.LIC_Licencia, clave_id=credencial_id,
            referencia=await self._referencia(cred.LIC_Licencia), usuario_id=usuario_id,
            detalle={"campos": sorted(nuevos)},
        )
        await self.gov_repo.create_audit_log(
            "LICENSE_SECRET_ROTATED", "INV_LICENCIA_CLAVE",
            {"credencial": credencial_id, "licencia": cred.LIC_Licencia, "campos": sorted(nuevos)},
            usuario_id=usuario_id, ip_origen=ip,
        )

    async def marcar_cambio_tras_salida(self, credenciales: list[int]) -> int:
        """Cuentas que usaba una persona que salió: su clave debe cambiarse."""
        if not credenciales:
            return 0
        res = await self.db.execute(
            update(LicenciaClave)
            .where(LicenciaClave.LCL_Licencia_Clave.in_(credenciales), LicenciaClave.LCL_Secreto_Enc.isnot(None))
            .values(LCL_Requiere_Cambio=True)
        )
        return res.rowcount or 0
