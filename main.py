from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os

print(f"PORT env = {os.environ.get('PORT')}")

from routers.players import router as players_router
from routers.inventory import router as inventory_router
from routers.notifications import router as notifications_router
from routers.achievements import router as achievements_router
from routers.avatars import router as avatars_router
from routers.states import router as states_router
from routers.map import router as map_router
from routers import research # noqa: E402

app = FastAPI()

# ===== CORS — СТАВИМ СРАЗУ ПОСЛЕ СОЗДАНИЯ APP =====
origins = [
    "https://fwfront-kamenistiy.mia0.amvera.tech",
    "http://localhost:3000", 
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, # <-- Здесь передаем переменную
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== РОУТЕРЫ — ПОСЛЕ MIDDLEWARE =====
app.include_router(players_router)
app.include_router(inventory_router)
app.include_router(notifications_router)
app.include_router(achievements_router)
app.include_router(avatars_router)
app.include_router(states_router)
app.include_router(map_router)
app.include_router(research.router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    print(f"➡️ {request.method} {request.url.path}")
    response = await call_next(request)
    return response


@app.get("/")
def root():
    return {"message": "Server working"}


@app.get("/ping")
def ping():
    return {"ping": "pong", "version": "v-notes-test"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
# ===== стереть2 =====