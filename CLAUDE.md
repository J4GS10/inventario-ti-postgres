# Inventario-TI — versión PostgreSQL

Repositorio exclusivo de la versión PostgreSQL 16
(https://github.com/J4GS10/inventario-ti-postgres, rama `main`). No se agrega
código, dependencias ni SQL de otro motor de base de datos;
`inventarioTI-backend/tests/test_aislamiento_version.py` lo verifica. Pruebas: `bash herramientas-qa/pruebas_backend.sh` (todo en contenedores).

## Grafo de código (graphify)

Hay un grafo de dependencias del código en `graphify-out/` (no versionado).
Python no está instalado en el equipo: graphify corre en un contenedor.

- Construir o actualizar (solo AST, sin LLM ni costo): `bash herramientas-qa/grafo.sh`
- Antes de explorar código para una pregunta de arquitectura, consultar el grafo:
  - `bash herramientas-qa/grafo.sh query "<pregunta>"`
  - `bash herramientas-qa/grafo.sh explain "<símbolo>"` (quién lo llama y de qué depende)
  - `bash herramientas-qa/grafo.sh path "<A>" "<B>"` (si el nombre es ambiguo, use `ruta/archivo.py::Símbolo`)
- Vista general: `graphify-out/GRAPH_REPORT.md`; interactiva: `graphify-out/graph.html`.
- Tras cambios grandes de código, reconstruir el grafo.
