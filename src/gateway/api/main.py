
from fastapi import FastAPI, HTTPException
from gateway.api.schemas import ChatResponse
from gateway.core.rate_limiter import is_allowed


app = FastAPI()


@app.get("/health")
async def health_check():
    return {'status': 'OK'}

@app.post("/chat")
async def chat(response : ChatResponse):
    if not is_allowed(response.user_id):
        raise HTTPException(status_code = 429, detail = "Rate limit exceeded")
    return {"echo": response.message, "from": response.user_id}