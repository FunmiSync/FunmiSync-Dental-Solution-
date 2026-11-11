import time 
import logging 
from dataclasses import dataclass , field
from enum import Enum

logger = logging.getLogger(__name__)

class CircuitBreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    pass


@dataclass
class CircuitBreaker:
    max_failures: int = 5
    reset_timeout: int = 30
    half_open_max_calls: int = 1 
    name: str = "default"

    state: CircuitBreakerState = field(default= CircuitBreakerState.CLOSED, init= False)
    failure_count: int = field(default=0, init=False)
    last_failure_time: float = field(default=0.0, init=False)
    half_open_in_flight: int = field(default=0, init=False)

    def allow_request(self)-> bool:
        now = time.time()

        if self.state == CircuitBreakerState.CLOSED:
            return True
        
        if self.state == CircuitBreakerState.OPEN:
            if now - self.last_failure_time >= self.reset_timeout:
                self.state = CircuitBreakerState.HALF_OPEN
                self.half_open_in_flight = 0 
                logger.info(
                    "Circuit breaker transitioned to half-open",
                    extra={"breaker_name": self.name},
                )

            else:
                logger.warning(
                    "Circuit breaker blocked request while open",
                    extra={
                        "breaker_name": self.name,
                        "failure_count": self.failure_count,
                    },
                )
                return False
            
        if self.state == CircuitBreakerState.HALF_OPEN:
           if self.half_open_in_flight >= self.half_open_max_calls:
               logger.warning(
                    "Circuit breaker blocked request during half-open probe limit",
                    extra={"breaker_name": self.name},
                )
               return False
           
           self.half_open_in_flight += 1
           logger.info(
                "Circuit breaker allowed half-open probe request",
                extra={
                    "breaker_name": self.name,
                    "half_open_in_flight": self.half_open_in_flight,
                },
            )
           return True
        
        return True
    
    def success(self)  -> None:
        previous_state = self.state
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0 
        self.half_open_in_flight = 0 

        logger.info(
            "Circuit breaker recorded success and closed",
            extra={
                "breaker_name": self.name,
                "previous_state": previous_state.value,
            },
        )

    def on_failure(self) -> None:
        self.last_failure_time = time.time()

        if self.state == CircuitBreakerState.HALF_OPEN:
            self.state = CircuitBreakerState.OPEN
            self.half_open_in_flight = 0
            self.failure_count += 1

            logger.warning(
                "Circuit breaker half-open probe failed and reopened",
                extra={
                    "breaker_name": self.name,
                    "failure_count": self.failure_count,
                },
            )
            return

        self.failure_count += 1

        if self.failure_count >= self.max_failures:
            self.state = CircuitBreakerState.OPEN
            logger.warning(
                "Circuit breaker opened after reaching failure threshold",
                extra={
                    "breaker_name": self.name,
                    "failure_count": self.failure_count,
                    "max_failures": self.max_failures,
                },
            )
            return

        logger.warning(
            "Circuit breaker recorded failure but remains closed",
            extra={
                "breaker_name": self.name,
                "failure_count": self.failure_count,
                "max_failures": self.max_failures,
            },
        )

    def assert_request_allowed(self) -> None:
        if not self.allow_request():
            raise CircuitBreakerOpenError(
                f"Circuit breaker '{self.name}' is open; request blocked"
            )

    @property
    def is_open(self) -> bool:
        return self.state == CircuitBreakerState.OPEN

    @property
    def is_closed(self) -> bool:
        return self.state == CircuitBreakerState.CLOSED

    @property
    def is_half_open(self) -> bool:
        return self.state == CircuitBreakerState.HALF_OPEN


# Backward-compatible aliases for existing imports in the repo.
circuit_breaker = CircuitBreaker
circuit_breaker_open_error = CircuitBreakerOpenError



                   
               



