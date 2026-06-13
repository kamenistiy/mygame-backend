from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request
from routers.players import router as players_router
from fastapi.responses import JSONResponse
import os
print(f"PORT env = {os.environ.get('PORT')}")

from routers.inventory import router as inventory_router
from routers.notifications import router as notifications_router
from routers.achievements import router as achievements_router
from routers.avatars import router as avatars_router
from routers.states import router as states_router


app = FastAPI()

app.include_router(players_router)
app.include_router(inventory_router)
app.include_router(notifications_router)
app.include_router(achievements_router)
app.include_router(avatars_router)
app.include_router(states_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://mygame-frontend.vercel.app", "http://localhost:3000",],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    print(f"➡️ {request.method} {request.url.path}")
    response = await call_next(request)
    return response

@app.exception_handler(Exception)
async def cors_aware_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
        headers={
            "Access-Control-Allow-Origin": "https://mygame-frontend.vercel.app",
            "Access-Control-Allow-Credentials": "true",
        }
    )
    
@app.get("/")
def root():
    return {"message": "Server working"}

@app.get("/ping")
def ping():
    return {"ping": "pong"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)