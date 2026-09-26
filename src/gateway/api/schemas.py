from pydantic import BaseModel, ConfigDict, Field

from gateway import config


class ChatRequest(BaseModel):
    # Unknown fields are rejected, not silently ignored: a typo'd field should be an error.
    model_config = ConfigDict(extra="forbid")

    # Trade-off: length is checked after the body is parsed; cap raw body size at the
    # reverse proxy (e.g. nginx client_max_body_size) once this is deployed.
    message: str = Field(min_length=1, max_length=config.MAX_MESSAGE_CHARS)
