"""Estadísticas: dashboard. Todos los roles de inventario, con los datos de su alcance."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, require_stock_read
from app.core.roles import SOFTWARE_READ_ROLES
from app.db.session import get_read_db
from app.services.stats import StatsService

router = APIRouter()


@router.get("/dashboard", dependencies=[Depends(require_stock_read)])
async def get_dashboard_metrics(current_user: CurrentUser, db: AsyncSession = Depends(get_read_db)):
    """KPIs principales, calculados solo con las sedes del alcance del usuario.
    Sin licencias para quien no ve software; los costos los quita ocultar_costos."""
    datos = await StatsService(db).get_dashboard_stats()
    if current_user.USU_Rol not in SOFTWARE_READ_ROLES:
        datos = {k: v for k, v in datos.items() if not k.startswith("licencias")}
    return datos
