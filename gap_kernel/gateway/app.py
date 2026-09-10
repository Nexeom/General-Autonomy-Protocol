"""Authenticated bounded HTTP surface; no caller-selected dispatch endpoints."""
from contextlib import asynccontextmanager
import hmac

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from gap_kernel.gateway.models import ExecuteRequest, ProposalRequest
from gap_kernel.gateway.service import GatewayError, GatewayService

MAX_BODY_BYTES = 64 * 1024


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        messages = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > MAX_BODY_BYTES:
                response = JSONResponse({"error": "request_too_large"}, status_code=413)
                return await response(scope, receive, send)
            messages.append(message)
            if not message.get("more_body", False):
                break

        async def bounded_receive():
            if messages:
                return messages.pop(0)
            return await receive()

        await self.app(scope, bounded_receive, send)


def create_gateway_app(service: GatewayService):
    @asynccontextmanager
    async def lifespan(app):
        yield
        service.close()

    app = FastAPI(title="GAP Tool Gateway", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.add_middleware(BodyLimit)

    def authenticated(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + service.agent_token):
            raise HTTPException(status_code=401, detail="authentication_required")

    @app.exception_handler(GatewayError)
    async def gateway_error(request, exc):
        return JSONResponse({"error": exc.code}, status_code=exc.status_code)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/v1/proposals", dependencies=[Depends(authenticated)])
    def propose(request: ProposalRequest):
        return service.propose(request)

    @app.get("/v1/requests/{request_id}", dependencies=[Depends(authenticated)])
    def get_request(request_id: str):
        return service.get(request_id)

    @app.post("/v1/requests/{request_id}/execute", dependencies=[Depends(authenticated)])
    def execute(request_id: str, request: ExecuteRequest):
        return service.execute(request_id, request)

    @app.post("/v1/requests/{request_id}/reauthorize", dependencies=[Depends(authenticated)])
    def reauthorize(request_id: str):
        return service.reauthorize(request_id)

    @app.get("/v1/audit", dependencies=[Depends(authenticated)])
    def audit():
        return service.audit_status()

    return app


def main():
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    service = GatewayService(args.config, args.state_dir)
    uvicorn.run(create_gateway_app(service), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
