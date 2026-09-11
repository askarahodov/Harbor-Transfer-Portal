from pydantic import BaseModel


class ErrorPayload(BaseModel):
    code: str
    message: str


class ErrorEnvelope(BaseModel):
    error: ErrorPayload
