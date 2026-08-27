import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.db import init_db
from app.errors import DomainError, status_para
from app.routes import devices, energy, locais, solar

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Inicializa banco de dados
init_db()

app = FastAPI(title="Painel Tuya")

# Configurar templates e static files
base_dir = Path(__file__).parent
templates_dir = base_dir / "templates"
static_dir = base_dir / "static"

app.templates = Jinja2Templates(directory=str(templates_dir))
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Registrar routers
app.include_router(locais.router)
app.include_router(devices.router)
app.include_router(energy.router)
app.include_router(solar.router)


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError):
    """
    Erro de dominio levantado la no repository vira HTTP aqui, sem precisar de
    try/except em cada rota. O front le o campo `detail`.
    """
    logger.info("erro de dominio em %s: %s", request.url.path, exc.message)
    corpo = {"detail": exc.message}
    corpo.update(exc.details)
    return JSONResponse(status_code=status_para(exc), content=corpo)


@app.get("/")
async def root():
    """Página inicial redireciona para devices."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/devices")


@app.get("/health")
async def health_check():
    """Health check simples."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
