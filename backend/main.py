from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Ripple Campus Crisis API",
    description="Backend for the Ripple Campus in Crisis Datathon",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {
        "status": "success",
        "message": "Ripple Campus Crisis Backend is running!"
    }


@app.get("/api/health")
def health_check():
    return {
        "status": "healthy"
    }