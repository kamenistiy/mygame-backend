from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers.players import router as players_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(players_router)

for r in app.routes:
    print(r.path)
    
@app.get("/")
def root():
    return {"message": "Server working"}

@app.get("/ping")
def ping():
    return {"ping": "pong"}