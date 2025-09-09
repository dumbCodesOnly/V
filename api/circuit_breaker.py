"""
Circuit Breaker Pattern Implementation for External API Calls

This module implements a circuit breaker pattern to prevent cascading failures
when external APIs are unresponsive or failing. It automatically stops making
requests to failing services and allows them time to recover.

States:
- CLOSED: Normal operation, requests pass through
- OPEN: Service is failing, requests are blocked
- HALF_OPEN: Testing if service has recovered
"""

import time
import threading
from enum import Enum
from typing import Callable, Any, Optional, Dict
import logging
from datetime import datetime, timedelta

# Import configuration constants for circuit breaker defaults
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CircuitBreakerConfig

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open and blocking requests"""
    pass

class CircuitBreaker:
    """
    Circuit breaker for external API calls with configurable thresholds
    """
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = CircuitBreakerConfig.DEFAULT_FAILURE_THRESHOLD,
        recovery_timeout: int = CircuitBreakerConfig.DEFAULT_RECOVERY_TIMEOUT,
        expected_exception: tuple = (Exception,),
        success_threshold: int = CircuitBreakerConfig.DEFAULT_SUCCESS_THRESHOLD
    ):
        """
        Initialize circuit breaker
        
        Args:
            name: Identifier for this circuit breaker
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before trying again
            expected_exception: Exception types that count as failures
            success_threshold: Successful calls needed to close circuit from half-open
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.success_threshold = success_threshold
        
        # State tracking
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        self.last_success_time = None
        
        # Thread safety
        self._lock = threading.RLock()
        
        # Metrics
        self.total_requests = 0
        self.total_failures = 0
        self.total_successes = 0
        self.state_changes = []
        
        logging.info(f"Circuit breaker '{name}' initialized - threshold: {failure_threshold}, timeout: {recovery_timeout}s")
    
    def __call__(self, func: Callable) -> Callable:
        """Decorator to wrap functions with circuit breaker"""
        def wrapper(*args, **kwargs):
            return self.call(func, *args, **kwargs)
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function with circuit breaker protection
        
        Args:
            func: Function to execute
            *args, **kwargs: Arguments to pass to function
            
        Returns:
            Function result if successful
            
        Raises:
            CircuitBreakerError: When circuit is open
            Original exception: When function fails and circuit remains closed
        """
        with self._lock:
            self.total_requests += 1
            
            # Check if circuit is open
            if self.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._change_state(CircuitState.HALF_OPEN)
                    logging.info(f"Circuit breaker '{self.name}' attempting recovery (HALF_OPEN)")
                else:
                    self.total_failures += 1
                    raise CircuitBreakerError(f"Circuit breaker '{self.name}' is OPEN - service unavailable")
        
        # Execute the function
        try:
            start_time = time.time()
            result = func(*args, **kwargs)
            execution_time = (time.time() - start_time) * 1000  # Convert to ms
            
            with self._lock:
                self._on_success(execution_time)
                
            return result
            
        except self.expected_exception as e:
            with self._lock:
                self._on_failure(e)
            raise
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt recovery"""
        if self.last_failure_time is None:
            return True
        return time.time() - self.last_failure_time >= self.recovery_timeout
    
    def _on_success(self, execution_time: float):
        """Handle successful function execution"""
        self.total_successes += 1
        self.last_success_time = time.time()
        
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self._change_state(CircuitState.CLOSED)
                self.failure_count = 0
                logging.info(f"Circuit breaker '{self.name}' recovered - back to CLOSED state")
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0  # Reset failure count on success
    
    def _on_failure(self, exception: Exception):
        """Handle function execution failure"""
        self.total_failures += 1
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitState.HALF_OPEN:
            # Failed during recovery attempt - go back to open
            self._change_state(CircuitState.OPEN)
            self.success_count = 0
            logging.warning(f"Circuit breaker '{self.name}' recovery failed - back to OPEN state")
        elif self.state == CircuitState.CLOSED:
            if self.failure_count >= self.failure_threshold:
                self._change_state(CircuitState.OPEN)
                logging.error(f"Circuit breaker '{self.name}' OPENED - {self.failure_count} failures exceeded threshold")
    
    def _change_state(self, new_state: CircuitState):
        """Change circuit breaker state and log the change"""
        old_state = self.state
        self.state = new_state
        timestamp = datetime.now().isoformat()
        
        self.state_changes.append({
            'timestamp': timestamp,
            'from_state': old_state.value,
            'to_state': new_state.value,
            'failure_count': self.failure_count,
            'total_requests': self.total_requests
        })
        
        # Keep only last state changes
        if len(self.state_changes) > CircuitBreakerConfig.MAX_STATE_CHANGES:
            self.state_changes = self.state_changes[-CircuitBreakerConfig.MAX_STATE_CHANGES:]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics"""
        with self._lock:
            uptime_seconds = time.time() - (self.state_changes[0]['timestamp'] if self.state_changes else time.time())
            
            return {
                'name': self.name,
                'state': self.state.value,
                'failure_count': self.failure_count,
                'success_count': self.success_count,
                'total_requests': self.total_requests,
                'total_failures': self.total_failures,
                'total_successes': self.total_successes,
                'failure_rate': (self.total_failures / max(1, self.total_requests)) * 100,
                'last_failure_time': self.last_failure_time,
                'last_success_time': self.last_success_time,
                'time_since_last_failure': time.time() - self.last_failure_time if self.last_failure_time else None,
                'state_changes': self.state_changes[-CircuitBreakerConfig.LAST_STATE_CHANGES_DISPLAY:],  # Last state changes
                'configuration': {
                    'failure_threshold': self.failure_threshold,
                    'recovery_timeout': self.recovery_timeout,
                    'success_threshold': self.success_threshold
                }
            }
    
    def reset(self):
        """Manually reset circuit breaker to closed state"""
        with self._lock:
            old_state = self.state
            self._change_state(CircuitState.CLOSED)
            self.failure_count = 0
            self.success_count = 0
            logging.info(f"Circuit breaker '{self.name}' manually reset from {old_state.value} to CLOSED")
    
    def force_open(self):
        """Manually force circuit breaker to open state"""
        with self._lock:
            old_state = self.state
            self._change_state(CircuitState.OPEN)
            self.last_failure_time = time.time()
            logging.warning(f"Circuit breaker '{self.name}' manually forced from {old_state.value} to OPEN")


class CircuitBreakerManager:
    """
    Manages multiple circuit breakers for different services
    """
    
    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = threading.RLock()
    
    def get_breaker(
        self,
        name: str,
        failure_threshold: int = CircuitBreakerConfig.DEFAULT_FAILURE_THRESHOLD,
        recovery_timeout: int = CircuitBreakerConfig.DEFAULT_RECOVERY_TIMEOUT,
        expected_exception: tuple = (Exception,),
        success_threshold: int = CircuitBreakerConfig.DEFAULT_SUCCESS_THRESHOLD
    ) -> CircuitBreaker:
        """Get or create a circuit breaker for a service"""
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(
                    name=name,
                    failure_threshold=failure_threshold,
                    recovery_timeout=recovery_timeout,
                    expected_exception=expected_exception,
                    success_threshold=success_threshold
                )
            return self._breakers[name]
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all circuit breakers"""
        with self._lock:
            return {name: breaker.get_stats() for name, breaker in self._breakers.items()}
    
    def reset_all(self):
        """Reset all circuit breakers"""
        with self._lock:
            for breaker in self._breakers.values():
                breaker.reset()
            logging.info("All circuit breakers reset")
    
    def get_healthy_services(self) -> list:
        """Get list of services with closed circuit breakers"""
        with self._lock:
            return [name for name, breaker in self._breakers.items() if breaker.state == CircuitState.CLOSED]
    
    def get_unhealthy_services(self) -> list:
        """Get list of services with open circuit breakers"""
        with self._lock:
            return [name for name, breaker in self._breakers.items() if breaker.state == CircuitState.OPEN]


# Global circuit breaker manager instance
circuit_manager = CircuitBreakerManager()


def with_circuit_breaker(
    name: str,
    failure_threshold: int = CircuitBreakerConfig.DEFAULT_FAILURE_THRESHOLD,
    recovery_timeout: int = CircuitBreakerConfig.DEFAULT_RECOVERY_TIMEOUT,
    expected_exception: tuple = (Exception,),
    success_threshold: int = CircuitBreakerConfig.DEFAULT_SUCCESS_THRESHOLD
):
    """
    Decorator to add circuit breaker protection to functions
    
    Usage:
        @with_circuit_breaker('toobit_api', failure_threshold=3, recovery_timeout=30)
        def get_toobit_price(symbol):
            # API call logic here
            pass
    """
    def decorator(func: Callable) -> Callable:
        breaker = circuit_manager.get_breaker(
            name=name,
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            expected_exception=expected_exception,
            success_threshold=success_threshold
        )
        return breaker(func)
    
    return decorator
