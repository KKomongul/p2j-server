"""Local web preview: Flutter assets and API share one loopback origin."""

from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
WEB_ROOT = Path(__file__).resolve().parents[2] / "p2j-mobile" / "build" / "web"
HOP_HEADERS = {"host", "connection", "transfer-encoding", "content-length", "content-encoding"}


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def api(request: Request, path: str):
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    async with httpx.AsyncClient(timeout=60) as client:
        upstream = await client.request(
            request.method,
            f"http://127.0.0.1:8001/v1/{path}",
            params=request.query_params.multi_items(),
            headers=headers,
            content=await request.body(),
        )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers={k: v for k, v in upstream.headers.items() if k.lower() not in HOP_HEADERS},
    )


app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="flutter")
