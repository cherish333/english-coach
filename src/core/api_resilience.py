"""Resilience helpers for the cloud APIs (DeepL, NVIDIA NIM).

Why this exists
---------------
Measured latency for both providers is excellent (DeepL median 0.40 s, NVIDIA
median 0.66 s over real calls), so timeouts are *not* the problem. The problem is
that a single transient hiccup was permanent:

* **No retry at all.** One network stall produced
  ``NVIDIA NIM smart notes generation failed: Request timed out.`` in the live
  log, and that sentence got no whiteboard notes — forever, because the failure
  was never retried and never cached.
* **No failure classification.** A 429 (rate limit) or 5xx was treated exactly
  like a permanent 401, and the caller silently fell through to the next engine.
* **No back-pressure.** ``asyncio.gather`` over the missing sentences could open
  an unbounded number of concurrent cloud requests.
* **No negative cache.** A provider that was down stayed "first choice" and was
  re-probed on every single sentence, so every request paid the failure latency.

This module fixes all four with no new third-party dependency:

``AsyncRetrier``      bounded exponential backoff with jitter, retrying only
                      classifiable-as-transient failures and honouring
                      ``Retry-After``.
``CircuitBreaker``    stops hammering a provider that is clearly down, then
                      probes it again after a cool-down.
``ProviderLimiter``   caps concurrent in-flight requests per provider.
``ProviderMetrics``   attempts / successes / failures / latency, exposed through
                      ``/api/translation/status`` so the user can *see* whether
                      the two APIs are healthy instead of guessing.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("english_coach.api_resilience")

# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------

# HTTP statuses worth trying again: rate limits, request timeout, and the
# server-side transient family.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 522, 524})
# Statuses that mean "this will never work with the current configuration".
PERMANENT_STATUS = frozenset({400, 401, 403, 404, 413, 422})
# DeepL returns 456 for "quota exceeded" — permanently out of quota for the
# billing period, so retrying only wastes time.
DEEPL_QUOTA_EXCEEDED_STATUS = 456

RETRYABLE_EXCEPTION_NAMES = frozenset({
    "APITimeoutError", "APIConnectionError", "InternalServerError",
    "RateLimitError", "ConnectError", "ConnectTimeout", "ReadTimeout",
    "ReadError", "WriteError", "PoolTimeout", "RemoteProtocolError",
    "TimeoutException", "TimeoutError", "ConnectionError", "ConnectionResetError",
    "IncompleteReadError",
})


@dataclass
class Failure:
    """A classified API failure."""

    retryable: bool
    reason: str
    status: Optional[int] = None
    retry_after: Optional[float] = None


def classify_exception(exc: BaseException) -> Failure:
    """Decide whether an exception is worth retrying."""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    # A caller may attach the server's own guidance (e.g. DeepL's Retry-After).
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        try:
            retry_after = float(retry_after)
        except (TypeError, ValueError):
            retry_after = None
    if isinstance(status, int):
        if status == DEEPL_QUOTA_EXCEEDED_STATUS:
            return Failure(False, "quota_exceeded", status, retry_after)
        if status in RETRYABLE_STATUS:
            return Failure(True, f"http_{status}", status, retry_after)
        if status in PERMANENT_STATUS:
            return Failure(False, f"http_{status}", status, retry_after)
    if name in RETRYABLE_EXCEPTION_NAMES:
        return Failure(True, name)
    # openai's APIConnectionError subclasses carry `__cause__`.
    cause = getattr(exc, "__cause__", None)
    if cause is not None and type(cause).__name__ in RETRYABLE_EXCEPTION_NAMES:
        return Failure(True, type(cause).__name__)
    return Failure(False, name)


def classify_response(status: int, headers: Optional[Any] = None) -> Failure:
    """Classify an HTTP status code returned without raising."""
    retry_after = None
    if headers is not None:
        raw = None
        try:
            raw = headers.get("retry-after")
        except Exception:
            raw = None
        if raw:
            try:
                retry_after = float(str(raw).strip())
            except (TypeError, ValueError):
                retry_after = None
    if status == DEEPL_QUOTA_EXCEEDED_STATUS:
        return Failure(False, "quota_exceeded", status, retry_after)
    if status in RETRYABLE_STATUS:
        return Failure(True, f"http_{status}", status, retry_after)
    return Failure(False, f"http_{status}", status, retry_after)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@dataclass
class ProviderMetrics:
    """Counters and latency for one provider, for the status endpoint."""

    name: str
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    retries: int = 0
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    last_error_at: Optional[float] = None
    last_success_at: Optional[float] = None
    total_latency: float = 0.0
    _latencies: List[float] = field(default_factory=list, repr=False)

    def record_success(self, latency: float) -> None:
        self.attempts += 1
        self.successes += 1
        self.consecutive_failures = 0
        self.last_success_at = time.time()
        self.total_latency += latency
        self._latencies.append(latency)
        if len(self._latencies) > 200:
            del self._latencies[:100]

    def record_failure(self, reason: str) -> None:
        self.attempts += 1
        self.failures += 1
        self.consecutive_failures += 1
        self.last_error = reason
        self.last_error_at = time.time()

    def record_retry(self) -> None:
        self.retries += 1

    @property
    def success_rate(self) -> Optional[float]:
        if not self.attempts:
            return None
        return round(self.successes / self.attempts, 4)

    @property
    def p50_latency(self) -> Optional[float]:
        if not self._latencies:
            return None
        ordered = sorted(self._latencies)
        return round(ordered[len(ordered) // 2], 3)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "attempts": self.attempts,
            "successes": self.successes,
            "failures": self.failures,
            "retries": self.retries,
            "success_rate": self.success_rate,
            "consecutive_failures": self.consecutive_failures,
            "p50_latency_seconds": self.p50_latency,
            "last_error": self.last_error,
            "last_error_seconds_ago": (
                round(time.time() - self.last_error_at, 1) if self.last_error_at else None
            ),
            "last_success_seconds_ago": (
                round(time.time() - self.last_success_at, 1) if self.last_success_at else None
            ),
        }


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """Stop calling a provider that keeps failing; probe again after a cooldown.

    Deliberately simple: after ``failure_threshold`` consecutive failures the
    circuit opens for ``cooldown`` seconds. The next call after the cooldown is
    allowed through as a probe (half-open); a success closes it, a failure
    re-opens it.
    """

    def __init__(self, name: str, failure_threshold: int = 3, cooldown: float = 60.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self._failures = 0
        self._opened_at: Optional[float] = None
        self._probe_in_flight = False

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None

    def allow(self) -> bool:
        """True when a request may be attempted right now."""
        if self._opened_at is None:
            return True
        if time.time() - self._opened_at >= self.cooldown:
            # Half-open: let exactly one probe through.
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> None:
        self._probe_in_flight = False
        self._failures += 1
        if self._failures >= self.failure_threshold:
            if self._opened_at is None:
                logger.warning(
                    "%s circuit opened after %s consecutive failures; pausing %.0fs",
                    self.name, self._failures, self.cooldown,
                )
            self._opened_at = time.time()

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        return {
            "open": self.is_open,
            "consecutive_failures": self._failures,
            "retry_in_seconds": (
                round(max(0.0, self.cooldown - (now - self._opened_at)), 1)
                if self._opened_at else None
            ),
        }


# ---------------------------------------------------------------------------
# Retrier
# ---------------------------------------------------------------------------

class AsyncRetrier:
    """Bounded exponential backoff with jitter, honouring ``Retry-After``."""

    def __init__(
        self,
        *,
        max_attempts: int = 3,
        base_delay: float = 0.4,
        max_delay: float = 6.0,
        jitter: float = 0.25,
    ):
        self.max_attempts = max(1, int(max_attempts))
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter

    def delay_for(self, attempt: int, failure: Optional[Failure] = None) -> float:
        if failure is not None and failure.retry_after:
            # Respect the server's own guidance, capped so a bad header cannot
            # stall the UI for minutes.
            return min(max(failure.retry_after, 0.0), self.max_delay)
        raw = min(self.base_delay * (2 ** max(0, attempt - 1)), self.max_delay)
        return raw + random.uniform(0, raw * self.jitter)

    async def run(
        self,
        operation: Callable[[], Awaitable[Any]],
        *,
        provider: str = "api",
        metrics: Optional[ProviderMetrics] = None,
        breaker: Optional[CircuitBreaker] = None,
        validate: Optional[Callable[[Any], Optional[str]]] = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> Tuple[Optional[Any], Optional[Failure]]:
        """Run ``operation`` with retries.

        ``validate`` may return a rejection reason; a rejection is retried (the
        model produced something unusable) but never treated as a transport error.
        Returns ``(result, failure)``; exactly one is ``None``.
        """
        last: Optional[Failure] = None
        for attempt in range(1, self.max_attempts + 1):
            if breaker is not None and not breaker.allow():
                return None, Failure(False, f"{provider}_circuit_open")
            started = time.perf_counter()
            try:
                result = await operation()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure = classify_exception(exc)
                last = failure
                if metrics is not None:
                    metrics.record_failure(failure.reason)
                if breaker is not None:
                    breaker.record_failure()
                if not failure.retryable or attempt >= self.max_attempts:
                    logger.warning("%s attempt %s failed (%s)", provider, attempt, failure.reason)
                    return None, failure
                if metrics is not None:
                    metrics.record_retry()
                wait = self.delay_for(attempt, failure)
                logger.info(
                    "%s attempt %s failed (%s); retrying in %.2fs",
                    provider, attempt, failure.reason, wait,
                )
                await sleeper(wait)
                continue

            if validate is not None:
                reason = validate(result)
                if reason:
                    # A usable-answer problem: retry, but do not count it as a
                    # transport failure for the circuit breaker.
                    last = Failure(False, f"invalid_response:{reason}")
                    if metrics is not None:
                        metrics.record_failure(last.reason)
                    if attempt >= self.max_attempts:
                        return None, last
                    if metrics is not None:
                        metrics.record_retry()
                    await sleeper(self.delay_for(attempt))
                    continue

            if metrics is not None:
                metrics.record_success(time.perf_counter() - started)
            if breaker is not None:
                breaker.record_success()
            return result, None

        return None, last or Failure(False, "unknown")


# ---------------------------------------------------------------------------
# Concurrency limit
# ---------------------------------------------------------------------------

class ProviderLimiter:
    """Cap in-flight requests per provider.

    ``translate_batch`` fans out with ``asyncio.gather``; without a cap that is
    an unbounded number of simultaneous cloud requests, which is both a rate-limit
    risk and a way to make the app look abusive to the provider.
    """

    def __init__(self, name: str, limit: int = 4):
        self.name = name
        self.limit = max(1, int(limit))
        self._semaphore = asyncio.Semaphore(self.limit)
        self.peak_in_flight = 0
        self._in_flight = 0

    def __call__(self) -> "_LimiterContext":
        return _LimiterContext(self)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "limit": self.limit,
            "peak_in_flight": self.peak_in_flight,
        }


class _LimiterContext:
    def __init__(self, limiter: ProviderLimiter):
        self._limiter = limiter

    async def __aenter__(self):
        await self._limiter._semaphore.acquire()
        self._limiter._in_flight += 1
        self._limiter.peak_in_flight = max(
            self._limiter.peak_in_flight, self._limiter._in_flight
        )
        return self

    async def __aexit__(self, *exc_info):
        self._limiter._in_flight -= 1
        self._limiter._semaphore.release()
        return False


# ---------------------------------------------------------------------------
# Shared registry (read by /api/translation/status)
# ---------------------------------------------------------------------------

class ProviderRegistry:
    """Holds the per-provider resilience state for observability."""

    def __init__(self):
        self.metrics: Dict[str, ProviderMetrics] = {}
        self.breakers: Dict[str, CircuitBreaker] = {}
        self.limiters: Dict[str, ProviderLimiter] = {}

    def register(
        self,
        name: str,
        *,
        failure_threshold: int = 3,
        cooldown: float = 60.0,
        concurrency: int = 4,
    ) -> Tuple[ProviderMetrics, CircuitBreaker, ProviderLimiter]:
        metrics = self.metrics.setdefault(name, ProviderMetrics(name=name))
        breaker = self.breakers.setdefault(
            name, CircuitBreaker(name, failure_threshold=failure_threshold, cooldown=cooldown)
        )
        limiter = self.limiters.setdefault(name, ProviderLimiter(name, limit=concurrency))
        return metrics, breaker, limiter

    def snapshot(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for name, metrics in self.metrics.items():
            breaker = self.breakers.get(name)
            limiter = self.limiters.get(name)
            entry = metrics.snapshot()
            entry["circuit"] = breaker.snapshot() if breaker else None
            entry["concurrency"] = limiter.snapshot() if limiter else None
            out[name] = entry
        return out


registry = ProviderRegistry()
