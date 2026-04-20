import logging
from typing import Callable
import uuid
from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from core.queue import async_redis
from auth.oauth2 import decode_token


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
            current_count: int = int(await async_redis.incr(key))
            if current_count == 1 :
                await async_redis.expire(key, RATE_LIMIT_WINDOW_SECONDS)

            if current_count > RATE_LIMIT_MAX_REQUESTS:
                ttl = await async_redis.ttl(key)
                retry_after = ttl if ttl and ttl >  0 else RATE_LIMIT_WINDOW_SECONDS   #type: ignore[arg-type]
                log.warning(f"Rate Limiting hit for this {identifier}, retry_after = {retry_after}",
                            request.method, request.url.path)
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
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        client = request.client
        if client and client.host:
            return client.host 
        return "unknown"
    def _get_user_id_from_token(self, request: Request):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.lower().startswith("bearer"):
            return None
        token = auth_header.split(" ", 1)[1]
        try:
            payload = decode_token(token)
        except Exception :
            return None
        
        return payload.get("id")
        
      
