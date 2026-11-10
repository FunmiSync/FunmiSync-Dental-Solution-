import logging
from typing import Callable
import uuid
from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from app.infrastructure.queue import async_redis
from app.security.oauth2 import decode_token
from app.infrastructure.rate_counter import increment_window


log = logging.getLogger(__name__)

RATE_LIMIT_MAX_REQUESTS = 100
RATE_LIMIT_WINDOW_SECONDS = 60

class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request:Request, call_next: Callable,)-> Response: 
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        
    
        identifier = self._get_identifier(request)
        key = f"rl:{identifier}"
        try: 
            current_count = await increment_window(async_redis, key, RATE_LIMIT_WINDOW_SECONDS)

            if current_count > RATE_LIMIT_MAX_REQUESTS:
                ttl = await async_redis.ttl(key)
                retry_after = ttl if ttl and ttl >  0 else RATE_LIMIT_WINDOW_SECONDS   #type: ignore[arg-type]
                log.warning("Rate limit hit for %s: retry_after=%s method=%s path=%s",
                            identifier, retry_after, request.method, request.url.path)
                response =  JSONResponse(
                    status_code= status.HTTP_429_TOO_MANY_REQUESTS , 
                    content = {
                        "detail" : "Too Many Request",
                        "retry_after" : retry_after
                    }
                )
                response.headers["Retry-After"] = str(retry_after)
                response.headers["X-Request-ID"] = request_id
                return response
            
        except Exception as exec : 
            log.error("Rate limiting skipped due to redis failure", 
                      exc_info= exec, 
                      extra = {
                "identifier": identifier,
            })

        
        response =  await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response 
    

    def _get_identifier(self, request : Request) -> str :
        user_id = self._get_user_id_from_token(request)
        if user_id is not None :
            return f"user:{user_id}"
        return f"ip:{self._get_client_ip(request)}"
    
    def _get_client_ip(self, request: Request) -> str:
        # Uvicorn applies forwarding only from its configured trusted proxies.
        # Never let arbitrary caller headers select a rate-limit identity here.
        client = request.client
        if client and client.host:
            return client.host 
        return "unknown"
    def _get_user_id_from_token(self, request: Request):
        auth_header = request.headers.get("Authorization")
        parts = (auth_header or "").split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        token = parts[1]
        try:
            payload = decode_token(token)
        except Exception :
            return None
        
        return payload.get("id") if payload.get("type") == "access" else None
        
      
