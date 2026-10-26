"""
QA6 — persistencia de adjuntos tras reiniciar inv_ui-minio-1 e inv_ui-backend-1.

Uso: python qa6_persistencia.py            (verifica todos los adjuntos de estado["persistencia"])
     python qa6_persistencia.py --reiniciar (reinicia UNA vez ambos contenedores, espera healthy y verifica)
Resultados en salidas/qa6/persistencia.json.
"""
import subprocess
import sys
import time

from qa6_comun import Registro, cargar_estado, cliente_sa, objeto_en_bucket, sha
from qa_login import BASE

R = Registro("persistencia")
e = cargar_estado()
lista = e.get("persistencia", [])


def estado_contenedor(n):
    return subprocess.run(["docker", "inspect", "-f", "{{.State.Health.Status}} {{.State.StartedAt}}", n],
                          capture_output=True, text=True).stdout.strip()


if "--reiniciar" in sys.argv:
    antes = {n: estado_contenedor(n) for n in ("inv_ui-minio-1", "inv_ui-backend-1")}
    t0 = time.time()
    out = subprocess.run(["docker", "restart", "inv_ui-minio-1", "inv_ui-backend-1"], capture_output=True, text=True)
    print("docker restart:", out.stdout.strip(), out.stderr.strip())
    listo = False
    while time.time() - t0 < 300:
        est = {n: estado_contenedor(n) for n in ("inv_ui-minio-1", "inv_ui-backend-1")}
        if all(v.startswith("healthy") for v in est.values()):
            listo = True
            break
        time.sleep(3)
    R.caso("P01", "Reinicio de MinIO y backend (docker restart) vuelve a healthy", listo,
           f"antes={antes}; después={est}; tardó {time.time() - t0:.0f} s", "Ambos healthy")
    time.sleep(5)

c = cliente_sa()
fallos = []
for a in lista:
    d = c.get(f"{BASE}/adjuntos/{a['id']}/download")
    existe, sha_b, _ = objeto_en_bucket(a["key"])
    if not (d.status_code == 200 and sha(d.content) == a["sha"] and existe and sha_b == a["sha"]):
        fallos.append((a["caso"], d.status_code, existe))
R.caso("P02" if "--reiniciar" in sys.argv else "P00",
       "Adjuntos (activo y orden) íntegros " + ("tras el reinicio" if "--reiniciar" in sys.argv else "antes del reinicio"),
       not fallos and lista, f"{len(lista) - len(fallos)}/{len(lista)} descargan con el mismo SHA-256 y siguen en el bucket; fallos={fallos}",
       "Todos presentes e íntegros")
if "--reiniciar" in sys.argv:
    r = c.post(f"{BASE}/adjuntos/activos/{e['activo']}", files={"file": ("QA6-post-reinicio.pdf", b"%PDF-1.4\nQA6\n%%EOF", "application/pdf")},
               data={"categoria": "otro"})
    ok = r.status_code == 201
    if ok:
        c.delete(f"{BASE}/adjuntos/{r.json()['ADJ_Adjunto']}")
    R.caso("P03", "Tras el reinicio se puede subir y borrar de nuevo", ok, f"HTTP {r.status_code}", "201")
