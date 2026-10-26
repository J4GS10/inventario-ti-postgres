"""QA4 — AUT-12 (repeticion): 4 verificaciones simultaneas del mismo TOTP en una ventana NUEVA."""
import threading, time
import pyotp
from qa4_comun import API, Registro, cargar_cuentas, cliente
import json, os
from qa4_comun import SAL
cu = cargar_cuentas()
totp = pyotp.TOTP(cu["qa4_mfa"]["totp"])
# esperar a que empiece una ventana nueva (nunca usada)
time.sleep(31 - int(time.time()) % 30)
chs = []
for _ in range(4):
    x = cliente()
    chs.append((x, x.post(f"{API}/login/access-token", data={"username": "qa4_mfa", "password": cu["qa4_mfa"]["clave"]}).json()["challenge_token"]))
codigo = totp.now()
res = [None] * 4
bar = threading.Barrier(4)
def go(i):
    x, t = chs[i]; bar.wait()
    res[i] = x.post(f"{API}/login/2fa/verify", json={"challenge_token": t, "code": codigo}).status_code
hs = [threading.Thread(target=go, args=(i,)) for i in range(4)]
[h.start() for h in hs]; [h.join() for h in hs]
casos = json.load(open(os.path.join(SAL, "autenticacion.json"), encoding="utf-8"))
casos = [c for c in casos if c["id"] != "AUT-12"]
casos.append({"id": "AUT-12", "caso": "Anti-replay en concurrencia: 4 verificaciones simultaneas del mismo TOTP (ventana nueva)",
              "ok": res.count(200) == 1, "esperado": "Exactamente un 200", "obtenido": f"codigos={res}", "extra": None})
json.dump(casos, open(os.path.join(SAL, "autenticacion.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(res)
