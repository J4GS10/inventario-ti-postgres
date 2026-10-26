# Grafo de código y dependencias del proyecto con graphify (Apache-2.0, gratis).
# Solo extracción AST con tree-sitter: no usa LLM ni claves de API.
FROM python:3.12-slim
RUN pip install --no-cache-dir --root-user-action=ignore graphifyy==0.9.96
WORKDIR /repo
ENTRYPOINT ["graphify"]
