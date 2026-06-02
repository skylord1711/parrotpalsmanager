import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response

RAILWAY_URL = "https://parrotpalsmanager-production.up.railway.app"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def proxy(request: Request, path: str):
    url = f"{RAILWAY_URL}/{path}"
    headers = dict(request.headers)
    headers.pop("host", None)
    body = await request.body()
    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.request(
            method=request.method,
            url=url,
            headers=headers,
            params=request.query_params,
            content=body,
            follow_redirects=False,
        )
    content = resp.content
    headers_out = {}
    for k, v in resp.headers.items():
        if k.lower() not in ("content-encoding", "transfer-encoding", "content-length"):
            headers_out[k] = v
    return Response(content=content, status_code=resp.status_code, headers=headers_out)
