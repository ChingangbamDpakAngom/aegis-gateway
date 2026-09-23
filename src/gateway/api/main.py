
from fastapi import FastAPI, HTTPException
from gateway.api.schemas import ChatRequest
from gateway.core.rate_limiter import is_allowed


app = FastAPI()


@app.get("/health")
async def health_check():
    return {'status': 'OK'}

@app.post("/chat")
async def chat(request: ChatRequest):
    if not is_allowed(request.user_id):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
        )

    return {
        "echo": request.message,
        "from": request.user_id,
    }