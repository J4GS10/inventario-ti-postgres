# Prototipos de dominio

Esta carpeta contiene piezas reutilizables para nuevas funcionalidades. Su
dependencia permitida es únicamente el dominio estándar de Python; no importa
FastAPI, SQLAlchemy ni un driver de base de datos.

- `domain.py`: enums, rangos de fechas y guardas de transiciones.
- La API debe validar entrada con esquemas Pydantic, invocar un caso de uso,
  y dejar la persistencia a un puerto/repositorio.
- Los prototipos se promueven a producción sólo después de agregar pruebas de
  contrato para PostgreSQL y MySQL.
