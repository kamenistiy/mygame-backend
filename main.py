from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request
from routers.players import router as players_router
import os
print(f"PORT env = {os.environ.get('PORT')}")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://mygame-frontend.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    print(f"➡️ {request.method} {request.url.path}")
    response = await call_next(request)
    return response

app.include_router(players_router)

for r in app.routes:
    print(r.path)

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