from pydantic import BaseModel

class ChatResponse(BaseModel):

    user_id : str
    message : str