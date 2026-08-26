from fastapi import FastAPI

from vera.api.schemas import ComposeRequest, ComposeResponse
from vera.pipeline import run

app = FastAPI(title="Vera Engine")


@app.post("/v1/compose", response_model=ComposeResponse)
def compose(request: ComposeRequest) -> ComposeResponse:
    return run(request.merchant, request.trigger, request.customer)
