"""Pruebas de aislamiento de capacidades entre motores."""
import pytest

from app.db.dialects import dialect_for


@pytest.mark.parametrize("name", ["postgres", "postgresql"])
def test_postgres_capabilities(name):
    dialect = dialect_for(name)

    assert dialect.supports_for_update is True
    assert dialect.supports_replica_lag_probe is True
    assert dialect.read_only_statement == "SET TRANSACTION READ ONLY"
    assert dialect.connect_args(5) == {"connect_timeout": 5, "options": "-c timezone=UTC"}


@pytest.mark.asyncio
async def test_sesion_postgres_en_utc_aunque_el_servidor_no_lo_este(session):
    """now() alimenta las columnas DateTime sin zona y se compara con utcnow_naive."""
    from sqlalchemy import text
    assert (await session.execute(text("SHOW timezone"))).scalar_one() == "UTC"


def test_mysql_is_prepared_without_postgres_lag_sql():
    mysql = dialect_for("mysql")

    assert mysql.supports_for_update is True
    assert mysql.supports_replica_lag_probe is False
    assert mysql.read_only_statement


def test_motor_no_soportado_se_rechaza():
    """Solo postgres, mysql y sqlite: cualquier otro motor es un error de configuración."""
    with pytest.raises(ValueError):
        dialect_for("mssql")


def test_sqlite_disables_unsupported_features():
    dialect = dialect_for("sqlite")

    assert dialect.supports_for_update is False
    assert dialect.supports_replica_lag_probe is False
    assert dialect.read_only_statement is None


def test_unknown_engine_fails_explicitly():
    with pytest.raises(ValueError, match="no soportado"):
        dialect_for("unknown")
