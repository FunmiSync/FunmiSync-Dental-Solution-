from __future__ import annotations
import asyncio
import json
import logging
import random
from typing import Any, Callable
from app.config import settings
import httpx
from app.infrastructure.circuit_breaker import CircuitBreaker
from app.infrastructure.providers.toroforge.config import ToroforgeConfig
from app.infrastructure.providers.toroforge.exceptions import ToroForgeAuthError, ToroForgeHTTPError, ToroForgeTimeoutError, ToroForgeUnavailableError, ToroForgeValidationError
logger = logging.getLogger(__name__)

class ToroForgeClient:
    toroforge_breaker = CircuitBreaker(
        max_failures=5,
        reset_timeout= 30,
        half_open_max_calls= 1,
        name= "toroforge"
    )

    def __init__(
        self,
        config: ToroforgeConfig,
        *,
        breaker : CircuitBreaker,
        max_read_retries: int = 3 ,
        base_delay_seconds: float = 1.0,
        max_delay_seconds: float = 8.0,
        jitter_seconds: float = 0.25
        ) -> None:

        self.config = config
        self.breaker = breaker or self.toroforge_breaker
        self.max_read_retries = max_read_retries
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.jitter_seconds = jitter_seconds
        
        self.client = httpx.AsyncClient(
            timeout= httpx.Timeout(config.timeout_seconds),
            headers={"content-Type": "application/json"}
)
                
    async def aclose(self) -> None:
        await self.client.aclose()

    async def call_read(
            self,
            *,
            path: str,
            op: str,
            params: list[dict[str, Any]],
            method: str= "GET",
            headers: dict[str, str] | None = None,
            base_url: str | None = None
    ) -> dict[str, Any]:
        
        last_error: Exception | None = None

        for attempt in range(1, self.max_read_retries + 1):
            try:
                return await self.request_once(
                    method=method,
                    path=path,
                    op=op,
                    params=params,
                    headers=headers,
                    base_url=base_url
                )
            
            except Exception as exc:
                last_error = exc 

                if not self.should_retry_read(exc):
                    raise

                if attempt == self.max_read_retries:
                    raise

                sleep_for = self.compute_backoff(attempt)

                logger.warning(
                    "Toroforge read retry %s %s for op=%s path=%s after error=%s. Sleeping %.2fs",
                    attempt,
                    self.max_read_retries,
                    op,
                    path,
                    exc,
                    sleep_for
                )
                await asyncio.sleep(sleep_for)
        
        raise ToroForgeUnavailableError("ToroForge read retries exhausted") from last_error


    async def call_write(
            self,
            *,
            method:str,
            path:str,
            op: str ,
            params: list[dict[str, Any]],
            headers: dict[str, str] | None = None,
            base_url: str | None = None 
    ) -> dict[str, Any]:
        
        return await self.request_once(
            method = method,
            path= path,
            op = op,
            params = params,
            headers = headers,
            base_url = base_url       
            )
    


    async def request_once(
            self,
            *,
            method: str,
            path: str,
            op: str,
            params: list[dict[str, Any]],
            headers: dict[str, str] | None,
            base_url: str | None
    ) -> dict[str, Any]:
        if self.breaker is not None and not self.breaker.allow_request():
             raise ToroForgeUnavailableError(
                "ToroForge circuit breaker is open. Upstream is temporarily unavailable."
            )

        url = self.build_url(path=path, base_url=base_url)
        payload = {"op": op, "params": params}
        request_headers = self.build_headers(headers)

        try:
            response = await self.client.request(
                method = method.upper(),
                url=url,
                json= payload,
                headers= request_headers
            )

            response.raise_for_status()

            try:
                data = response.json()
            except json.JSONDecodeError as exc:
                raise ToroForgeValidationError(
                    f"ToroForge returned non-JSON response for op={op} path={path}"
                ) from exc

            if self.breaker is not None:
                self.breaker.success()

            return data 
        
        except httpx.TimeoutException as exc:
            if self.breaker is not None:
                self.breaker.on_failure()
            raise ToroForgeTimeoutError(
                f"ToroForge timeout for op={op} path={path}"
            ) from exc
        
        except httpx.HTTPStatusError as exc:
            if self.breaker is not None:
                self.breaker.on_failure()
            raise self.map_http_status_error(exc, op=op, path=path) from exc
        
        except httpx.RequestError as exc:
            if self.breaker is not None:
                self.breaker.on_failure()
            raise ToroForgeUnavailableError(
                f"ToroForge request error for op={op} path={path}: {exc}"
            ) from exc


        
    def build_url(self, *, path:str, base_url: str | None)-> str:
        root = (base_url or settings.toroforge_base_url).rstrip("/")
        return f"{root}/{path.lstrip('/')}"
    

    def build_headers(self, headers: dict[str, str] | None)-> dict[str, str]:
        merged= {"content-Type": "application/json"}
        if headers:
            merged.update(headers)
        return merged
    
    def should_retry_read(self, exc: Exception)-> bool:
        if isinstance(exc, (ToroForgeTimeoutError, ToroForgeUnavailableError)):
            return True
        
        if isinstance(exc, ToroForgeHTTPError):
            message = str(exc)
            transient_markers = ("408", "429", "500", "502", "503", "504")
            return any(marker in message for marker in transient_markers)
        
        return False 

    
    def compute_backoff(self, attempt:int) -> float :
        raw = min(
            self.base_delay_seconds * (2 ** (attempt - 1)),
            self.max_delay_seconds
        )
        return raw + random.uniform(0, self.jitter_seconds)
    
    def map_http_status_error(
            self,
            exc: httpx.HTTPStatusError,
            *,
            op: str,
            path: str,
    ) -> Exception:
        status = exc.response.status_code
        # Provider error bodies can echo credentials or personal data.
        body = "[provider body omitted]"

        if status in (401, 403):
                return ToroForgeAuthError(
                    f"ToroForge auth failed for op={op} path={path}: {status} {body}"
                )

        if status == 408 or status == 429 or 500 <= status < 600:
                return ToroForgeHTTPError(
                    f"ToroForge transient HTTP failure for op={op} path={path}: {status} {body}"
                )

        if 400 <= status < 500:
                return ToroForgeValidationError(
                    f"ToroForge client error for op={op} path={path}: {status} {body}"
                )

        return ToroForgeHTTPError(
                f"ToroForge HTTP failure for op={op} path={path}: {status} {body}"
            )
    
    


    #Plain json
    async def request_json(
            self,
            *,
            method: str,
            path: str,
            json_body: dict[str, Any],
            headers: dict[str, str] | None = None,
            base_url: str | None = None
    ) -> dict[str, Any]:
        if self.breaker is not None and not self.breaker.allow_request():
            raise ToroForgeUnavailableError(
            "ToroForge circuit breaker is open. Upstream is temporarily unavailable."
        )

        url = self.build_url(path=path, base_url=base_url)
        request_headers = self.build_headers(headers)

        try:
            response = await self.client.request(
                method = method.upper(),
                url=url,
                json=json_body,
                headers=request_headers
            )

            response.raise_for_status()

            try:
                data = response.json()
            except  json.JSONDecodeError as exc:
                raise ToroForgeValidationError(
                f"ToroForge returned non-JSON response for path={path}"
                ) from exc
            
            if self.breaker is not None:
                self.breaker.success()

            if not isinstance(data, dict):
                raise ToroForgeValidationError(
                f"ToroForge returned unexpected JSON response for path={path}: {data}"
            )

            return data 
        except httpx.TimeoutException as exc:
            if self.breaker is not None:
                self.breaker.on_failure()
            raise ToroForgeTimeoutError(
            f"ToroForge timeout for path={path}"
            ) from exc
        
        except httpx.HTTPStatusError as exc:
            if self.breaker is not None:
                self.breaker.on_failure()
            raise self.map_http_status_error(exc, op="json_request", path=path) from exc
        
        except httpx.RequestError as exc:
            if self.breaker is not None:
                self.breaker.on_failure()
            raise ToroForgeUnavailableError(
            f"ToroForge request error for path={path}: {exc}"
        ) from exc
            
            
            




