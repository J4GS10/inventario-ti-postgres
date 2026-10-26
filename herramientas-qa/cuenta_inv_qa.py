"""
Cuenta de prueba en el stack DESECHABLE inv_qa (https://localhost:8443): un
SUPER_ADMIN `qa.catalogos` con contraseña y TOTP ya enrolado, creado
directamente en la base del contenedor. No toca `sa` ni el stack formal.

Las credenciales quedan en salidas/credenciales_inv_qa.json (no versionado).
Uso:  python herramientas-qa/cuenta_inv_qa.py
"""
import json
import pathlib
import secrets
import subprocess

import pyotp

SALIDA = pathlib.Path(__file__).parent / "salidas" / "credenciales_inv_qa.json"
USUARIO = "qa.catalogos"

clave = "Qa-" + secrets.token_urlsafe(12) + "9!"
totp = pyotp.random_base32()

CODIGO = f"""
import asyncio
from sqlalchemy import delete, select
from app.core.security import encrypt_field, get_password_hash
from app.core.data_scope import system_scope
from app.db.session import SessionLocal
from app.models.organization import Departamento, Cargo, Persona, Usuario

async def main():
    with system_scope():
        async with SessionLocal() as db:
            existente = (await db.execute(select(Usuario).where(Usuario.USU_Username == "{USUARIO}"))).scalar_one_or_none()
            if existente is None:
                dep = (await db.execute(select(Departamento))).scalars().first()
                car = (await db.execute(select(Cargo))).scalars().first()
                persona = (await db.execute(select(Persona).where(
                    Persona.PER_Email_Corporativo == "qa.catalogos@lombardi.qa.local"))).scalar_one_or_none()
                if persona is None:
                    persona = Persona(PER_Primer_Nombre="QA", PER_Primer_Apellido="Catálogos",
                                      PER_Email_Corporativo="qa.catalogos@lombardi.qa.local",
                                      DEP_Departamento=dep.DEP_Departamento, CAR_Cargo=car.CAR_Cargo)
                    db.add(persona)
                    await db.flush()
                existente = Usuario(USU_Username="{USUARIO}", USU_Rol="SUPER_ADMIN", PER_Persona=persona.PER_Persona,
                                    USU_Alcance_Global=True)
                db.add(existente)
            existente.USU_Password_Hash = get_password_hash("{clave}")
            existente.USU_2FA_Habilitado = True
            existente.USU_2FA_Metodo = "TOTP"
            existente.USU_2FA_Secret = encrypt_field("{totp}")
            existente.USU_2FA_Ultimo_Paso = None
            existente.USU_Intentos_Fallidos = 0
            existente.USU_Bloqueado_Hasta = None
            existente.USU_Debe_Cambiar_Password = False
            existente.USU_Estado = True
            await db.commit()
    print("ok")

asyncio.run(main())
"""

r = subprocess.run(["docker", "exec", "-i", "inv_qa-backend-1", "python", "-"], input=CODIGO, text=True,
                   capture_output=True, encoding="utf-8")
if r.returncode != 0 or "ok" not in r.stdout:
    raise SystemExit(f"No se pudo crear la cuenta: {r.stdout}\n{r.stderr}")
SALIDA.parent.mkdir(exist_ok=True)
SALIDA.write_text(json.dumps({"usuario": USUARIO, "clave": clave, "totp_secret": totp}, indent=1), encoding="utf-8")
print("Cuenta lista:", USUARIO, "→", SALIDA)
