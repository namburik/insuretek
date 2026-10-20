from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse
from starlette.routing import Mount, Route

from mltek.common.infra.context import set_app_name
from mltek.common.router.resolver import register_domain_provider
from mltek.common.starlette_app import CorrelationMiddleware, NoCacheStaticFiles, create_starlette_app, run_starlette_app
from mltek.common.log import setup_structured_logging
from mltek.config import config
from insuretek.domain_provider import InsureTekDomainProvider

set_app_name("insuretek")
register_domain_provider(InsureTekDomainProvider())
from web_gateway.routes.intake_routes import INTAKE_ROUTES
from web_gateway.routes.workflow_routes import WORKFLOW_ROUTES
from web_gateway.routes.auth_routes import AUTH_ROUTES
from web_gateway.routes.demo_routes import DEMO_ROUTES

_REACT_DIST = Path(__file__).parent / "react" / "dist"
_REACT_INDEX = _REACT_DIST / "index.html"


async def serve_react(request: Request) -> FileResponse:
    return FileResponse(_REACT_INDEX)


app = CorrelationMiddleware(create_starlette_app(routes=[
    Mount("/assets", app=NoCacheStaticFiles(directory=_REACT_DIST / "assets"), name="insuretek-react-assets"),
    *AUTH_ROUTES,
    *INTAKE_ROUTES,
    *WORKFLOW_ROUTES,
    *DEMO_ROUTES,
    Route("/", serve_react, methods=["GET"]),
]))

if __name__ == "__main__":
    setup_structured_logging(config.LOG_LEVEL)
    run_starlette_app(app, host=config.SERVER_HOST, port=6001)
