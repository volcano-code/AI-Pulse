import secrets
from fastapi import HTTPException, Request

LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "testserver", "api"}
LOCAL_ORIGINS = {f"http://{host}:{port}" for host in ("localhost", "127.0.0.1", "[::1]") for port in (3000, 8000)}

def authorize(request: Request):
    settings = request.app.state.settings
    authorization = request.headers.get("authorization", "")
    token = authorization[7:] if authorization.startswith("Bearer ") else ""
    if settings.admin_token:
        if not secrets.compare_digest(token, settings.admin_token):
            raise HTTPException(401, "需要有效的工作台访问令牌", headers={"WWW-Authenticate": "Bearer"})
    elif not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "无令牌模式仅允许本机访问")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin not in LOCAL_ORIGINS:
            # For public deployments, use a same-origin reverse proxy and configure an explicit origin.
            raise HTTPException(403, "不允许的跨域写请求")
        if not request.headers.get("content-type", "").lower().startswith("application/json"):
            raise HTTPException(415, "写请求必须使用 application/json")
