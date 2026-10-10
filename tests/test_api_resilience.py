"""Tests for the cloud-API resilience layer (DeepL + NVIDIA NIM).

The defect these defend against was observed live: a single network stall produced
``NVIDIA NIM smart notes generation failed: Request timed out.`` and that sentence
never got whiteboard notes, because nothing retried and nothing recorded it.

Measured latency for both providers is excellent (DeepL median 0.40 s, NVIDIA
median 0.66 s), so the goal is not faster calls but *not losing* a call to a
momentary hiccup — and not hammering a provider that is genuinely down.
"""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from src.server import app

client = TestClient(app)


def _method_body(source: str, name: str, indent: str = "    ") -> str:
    """Return a method body, tolerant of nested `async def` helpers.

    Naive `split("async def ")` truncates at the first nested helper, which made
    an earlier version of this test report a false failure.
    """
    marker = f"{indent}async def {name}("
    start = source.index(marker)
    rest = source[start + len(marker):]
    lines = rest.splitlines()
    for idx, line in enumerate(lines[1:], start=1):
        if line.startswith(f"{indent}async def ") or line.startswith(f"{indent}def "):
            return "\n".join(lines[:idx])
    return rest

from src.core.api_resilience import (
    AsyncRetrier,
    CircuitBreaker,
    Failure,
    ProviderLimiter,
    ProviderMetrics,
    classify_exception,
    classify_response,
)


class HTTPError(Exception):
    def __init__(self, status_code, retry_after=None):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        if retry_after is not None:
            self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503, 504, 522, 524])
def test_transient_statuses_are_retryable(status):
    assert classify_response(status).retryable is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
def test_permanent_statuses_are_not_retried(status):
    assert classify_response(status).retryable is False


def test_deepl_quota_exceeded_is_permanent():
    """DeepL returns 456 when the billing-period quota is gone."""
    failure = classify_response(456)
    assert failure.retryable is False
    assert failure.reason == "quota_exceeded"


@pytest.mark.parametrize("exc", [
    TimeoutError(), ConnectionError("reset"), ConnectionResetError(),
    asyncio.TimeoutError(),
])
def test_transient_exceptions_are_retryable(exc):
    assert classify_exception(exc).retryable is True


def test_unknown_exceptions_are_not_retried():
    assert classify_exception(ValueError("bad input")).retryable is False


def test_retry_after_header_is_parsed():
    class Headers:
        def get(self, key):
            return "3" if key == "retry-after" else None

    failure = classify_response(429, Headers())
    assert failure.retry_after == 3.0


def test_exception_retry_after_is_picked_up():
    failure = classify_exception(HTTPError(429, retry_after=2.5))
    assert failure.retryable is True
    assert failure.retry_after == 2.5


# ---------------------------------------------------------------------------
# Retrier
# ---------------------------------------------------------------------------

def test_retry_recovers_from_transient_failures():
    """The core fix: two 503s then success must yield the result, not None."""
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise HTTPError(503)
        return "translated"

    metrics = ProviderMetrics("t")
    retrier = AsyncRetrier(max_attempts=4, base_delay=0.01, max_delay=0.02)
    result, failure = asyncio.run(retrier.run(flaky, provider="T", metrics=metrics))

    assert result == "translated"
    assert failure is None
    assert calls["n"] == 3
    assert metrics.successes == 1
    assert metrics.retries == 2


def test_permanent_failure_is_not_retried():
    calls = {"n": 0}

    async def unauthorised():
        calls["n"] += 1
        raise HTTPError(401)

    retrier = AsyncRetrier(max_attempts=4, base_delay=0.01)
    result, failure = asyncio.run(retrier.run(unauthorised, provider="T"))

    assert result is None
    assert failure.reason == "http_401"
    assert calls["n"] == 1, "a permanent error must not be retried"


def test_retry_attempts_are_bounded():
    calls = {"n": 0}

    async def always_down():
        calls["n"] += 1
        raise HTTPError(503)

    retrier = AsyncRetrier(max_attempts=3, base_delay=0.01, max_delay=0.02)
    result, failure = asyncio.run(retrier.run(always_down, provider="T"))

    assert result is None
    assert calls["n"] == 3
    assert failure.retryable is True


def test_invalid_response_is_retried_then_reported():
    """A structurally unusable answer deserves another attempt."""
    calls = {"n": 0}

    async def garbage():
        calls["n"] += 1
        return "not what we asked for"

    retrier = AsyncRetrier(max_attempts=3, base_delay=0.01, max_delay=0.02)
    result, failure = asyncio.run(
        retrier.run(garbage, provider="T", validate=lambda v: "unusable" if v != "good" else None)
    )

    assert result is None
    assert calls["n"] == 3
    assert failure.reason.startswith("invalid_response")


def test_validation_pass_returns_the_value():
    async def good():
        return "good"

    retrier = AsyncRetrier(max_attempts=2, base_delay=0.01)
    result, failure = asyncio.run(
        retrier.run(good, provider="T", validate=lambda v: None if v == "good" else "bad")
    )
    assert result == "good" and failure is None


def test_retry_delay_grows_and_is_capped():
    retrier = AsyncRetrier(max_attempts=5, base_delay=0.5, max_delay=4.0, jitter=0.0)
    first = retrier.delay_for(1)
    third = retrier.delay_for(3)
    tenth = retrier.delay_for(10)
    assert first < third
    assert tenth <= 4.0


def test_retry_after_overrides_backoff_but_is_capped():
    retrier = AsyncRetrier(max_attempts=3, base_delay=0.1, max_delay=5.0)
    failure = Failure(True, "http_429", 429, retry_after=2.0)
    assert retrier.delay_for(1, failure) == 2.0
    huge = Failure(True, "http_429", 429, retry_after=600.0)
    assert retrier.delay_for(1, huge) == 5.0


def test_cancellation_propagates_without_retrying():
    calls = {"n": 0}

    async def cancelled():
        calls["n"] += 1
        raise asyncio.CancelledError()

    retrier = AsyncRetrier(max_attempts=3, base_delay=0.01)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(retrier.run(cancelled, provider="T"))
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

def test_circuit_opens_after_repeated_failures():
    breaker = CircuitBreaker("b", failure_threshold=3, cooldown=60)
    assert breaker.allow() is True
    for _ in range(3):
        breaker.record_failure()
    assert breaker.is_open is True
    assert breaker.allow() is False, "an open circuit must stop further calls"


def test_circuit_half_opens_after_cooldown_then_closes_on_success():
    breaker = CircuitBreaker("b", failure_threshold=2, cooldown=0.05)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.allow() is False

    time.sleep(0.08)
    assert breaker.allow() is True, "one probe must be allowed through"
    assert breaker.allow() is False, "only one probe at a time"

    breaker.record_success()
    assert breaker.is_open is False
    assert breaker.allow() is True


def test_circuit_blocks_calls_inside_the_retrier():
    calls = {"n": 0}

    async def down():
        calls["n"] += 1
        raise HTTPError(503)

    breaker = CircuitBreaker("b", failure_threshold=1, cooldown=60)
    retrier = AsyncRetrier(max_attempts=2, base_delay=0.01, max_delay=0.02)
    asyncio.run(retrier.run(down, provider="T", breaker=breaker))
    before = calls["n"]

    # The circuit is now open, so further operations must not even be attempted.
    result, failure = asyncio.run(retrier.run(down, provider="T", breaker=breaker))
    assert result is None
    assert calls["n"] == before, "an open circuit still called the provider"
    assert "circuit_open" in failure.reason


# ---------------------------------------------------------------------------
# Concurrency limiter
# ---------------------------------------------------------------------------

def test_limiter_caps_in_flight_requests():
    limiter = ProviderLimiter("l", limit=2)
    peak = {"v": 0}

    async def task():
        async with limiter():
            peak["v"] = max(peak["v"], limiter._in_flight)
            await asyncio.sleep(0.02)

    async def run():
        await asyncio.gather(*[task() for _ in range(12)])

    asyncio.run(run())
    assert peak["v"] <= 2, f"concurrency reached {peak['v']} with a limit of 2"
    assert limiter.peak_in_flight <= 2


def test_limiter_releases_on_exception():
    limiter = ProviderLimiter("l", limit=1)

    async def boom():
        async with limiter():
            raise RuntimeError("fail")

    async def run():
        with pytest.raises(RuntimeError):
            await boom()
        # The slot must be free again.
        async with limiter():
            return True

    assert asyncio.run(run()) is True


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def test_metrics_track_success_rate_and_latency():
    metrics = ProviderMetrics("m")
    assert metrics.success_rate is None
    metrics.record_success(0.4)
    metrics.record_success(0.6)
    metrics.record_failure("http_503")
    snapshot = metrics.snapshot()
    assert snapshot["attempts"] == 3
    assert snapshot["successes"] == 2
    assert snapshot["failures"] == 1
    assert snapshot["success_rate"] == pytest.approx(0.6667, abs=1e-4)
    assert snapshot["p50_latency_seconds"] is not None
    assert snapshot["last_error"] == "http_503"
    assert snapshot["consecutive_failures"] == 1


def test_metrics_reset_consecutive_failures_on_success():
    metrics = ProviderMetrics("m")
    metrics.record_failure("a")
    metrics.record_failure("b")
    assert metrics.consecutive_failures == 2
    metrics.record_success(0.1)
    assert metrics.consecutive_failures == 0


def test_registry_registration_is_idempotent():
    from src.core.api_resilience import ProviderRegistry

    reg = ProviderRegistry()
    a = reg.register("p")
    b = reg.register("p")
    assert a == b, "re-registering a provider must reuse its state"
    assert "p" in reg.snapshot()


# ---------------------------------------------------------------------------
# Wiring: the real service must actually use the layer
# ---------------------------------------------------------------------------

def test_translation_service_registers_both_providers():
    from src.core.translation import TranslationService

    service = TranslationService()
    status = service.get_status()
    assert "providers" in status
    assert "retry_policy" in status
    assert status["retry_policy"]["max_attempts"] >= 2


def test_translation_service_provider_status_shape():
    from src.core.translation import TranslationService

    service = TranslationService()
    providers = service.get_status()["providers"]
    # Both providers are registered up front, even before the first call, so the
    # status endpoint can report on them immediately.
    for name in ("deepl", "nvidia"):
        assert name in providers, f"{name} is not registered"
        entry = providers[name]
        for key in ("attempts", "successes", "failures", "retries", "success_rate", "circuit"):
            assert key in entry, f"{name} status is missing {key}"
        assert entry["circuit"] is not None
        assert entry["concurrency"] is not None


def test_nvidia_client_disables_sdk_retries():
    """Two retry layers would multiply attempts and defeat the backoff."""
    from src.config import PROJECT_ROOT

    for path in ("src/core/translation.py", "src/core/notes_generator.py"):
        source = (PROJECT_ROOT / path).read_text(encoding="utf-8")
        assert "max_retries=0" in source, f"{path} lets the OpenAI SDK retry as well"


def test_deepl_calls_go_through_the_retrier():
    from src.config import PROJECT_ROOT

    source = (PROJECT_ROOT / "src/core/translation.py").read_text(encoding="utf-8")
    deepl_body = _method_body(source, "_translate_with_deepl")
    assert "self._retrier.run" in deepl_body, "DeepL is not retried"
    assert "classify_response" in deepl_body, "DeepL status codes are not classified"
    # A 429/5xx must reach the retrier as a retryable failure, not be swallowed.
    assert "DeepLHTTPError" in deepl_body


def test_smart_notes_go_through_the_retrier():
    from src.config import PROJECT_ROOT

    source = (PROJECT_ROOT / "src/core/notes_generator.py").read_text(encoding="utf-8")
    gen_body = source.split("async def generate_notes", 1)[1]
    assert "self._retrier.run" in gen_body, "smart notes are not retried"
    assert "missing_required_sections" in gen_body, (
        "an incomplete whiteboard is not detected for retry"
    )


# ---------------------------------------------------------------------------
# Slow-call UX: a 10-35s cloud call must not look frozen
# ---------------------------------------------------------------------------

def test_whiteboard_shows_elapsed_progress():
    """NVIDIA NIM measured 10-35s (median ~21s) for a full whiteboard.

    A static "生成中…" over that window reads as a hung UI, so the loading state
    must show a live elapsed counter and a progress track.
    """
    from src.config import PROJECT_ROOT

    app_js = (PROJECT_ROOT / "src" / "static" / "app.js").read_text(encoding="utf-8")
    assert "notesElapsedTimer" in app_js
    assert "notes-elapsed" in app_js
    assert "notes-progress-fill" in app_js
    # The timer must always be cleared, including on the failure path.
    assert "function stopNotesElapsedTimer(" in app_js
    finally_block = app_js.split("stopNotesElapsedTimer();", 1)
    assert len(finally_block) >= 2, "the elapsed timer is never stopped"


def test_progress_indicator_has_styles():
    from src.config import PROJECT_ROOT

    css = (PROJECT_ROOT / "src" / "static" / "style.css").read_text(encoding="utf-8")
    assert ".notes-progress-track" in css
    assert ".notes-progress-fill" in css
    # Exactly one .loading-notes rule: a duplicate would fight the original.
    assert css.count(".loading-notes {") == 1


# ---------------------------------------------------------------------------
# Dedicated NVIDIA translation model (riva-translate)
# ---------------------------------------------------------------------------

def test_riva_model_is_configured():
    from src.config import NVIDIA_TRANSLATE_MODEL, NVIDIA_USE_TRANSLATE_MODEL

    assert "riva-translate" in NVIDIA_TRANSLATE_MODEL
    assert NVIDIA_USE_TRANSLATE_MODEL in (True, False)


def test_riva_prompt_puts_the_target_language_in_the_user_message():
    """Measured behaviour: this model IGNORES the system message.

    With the target language only in the system role it echoed the English back
    unchanged, or auto-detected Norwegian / Czech. The instruction must be in the
    user turn, otherwise the fast lane silently returns wrong-language output.
    """
    from src.config import PROJECT_ROOT

    source = (PROJECT_ROOT / "src/core/translation.py").read_text(encoding="utf-8")
    body = _method_body(source, "_translate_sentences_with_riva")
    assert "Translate to Simplified Chinese: " in body
    assert "NVIDIA_TRANSLATE_MODEL" in body
    # The prefix must be concatenated into the user content, not a system role.
    assert '{"role": "user", "content": f"{prefix}{payload}"}' in body
    assert 'role": "system"' not in body, (
        "a system message is ignored by this model; the instruction must be in the user turn"
    )


def test_riva_rejects_wrong_language_output():
    """Validation must refuse output with no Chinese at all."""
    from src.config import PROJECT_ROOT

    source = (PROJECT_ROOT / "src/core/translation.py").read_text(encoding="utf-8")
    body = _method_body(source, "_translate_sentences_with_riva")
    assert "no_chinese_in_output" in body


def test_riva_rejects_traditional_chinese():
    """It sometimes emits Traditional Chinese; the learner should get Simplified."""
    from src.core.translation import TRADITIONAL_ONLY_CHARS
    from src.config import PROJECT_ROOT

    assert "圖" in TRADITIONAL_ONLY_CHARS and "书" not in TRADITIONAL_ONLY_CHARS
    source = (PROJECT_ROOT / "src/core/translation.py").read_text(encoding="utf-8")
    body = _method_body(source, "_translate_sentences_with_riva")
    assert "traditional_chinese" in body


def test_glosses_do_not_use_the_translation_model():
    """A translation model cannot honour the structured JSON gloss contract.

    Asking riva-translate for a word/gloss dictionary made it auto-detect the
    language and answer in Czech ("Klíčová slova: učení...") and Arabic.
    """
    from src.config import PROJECT_ROOT

    source = (PROJECT_ROOT / "src/core/translation.py").read_text(encoding="utf-8")
    gloss_body = _method_body(source, "_get_glosses_with_nvidia")
    assert "NVIDIA_TRANSLATE_MODEL" not in gloss_body, (
        "the translation-specialised model must not serve glosses"
    )
    # And the entry point must not route glosses to the fast lane either.
    entry = source.split("async def get_sentence_word_glosses", 1)[1].split("async def ", 1)[0]
    assert "_translate_sentences_with_riva" not in entry


def test_riva_is_the_preferred_nvidia_sentence_path():
    from src.config import PROJECT_ROOT

    source = (PROJECT_ROOT / "src/core/translation.py").read_text(encoding="utf-8")
    body = _method_body(source, "_translate_with_nvidia")
    # The fast model is tried first, then the general model takes over.
    assert body.index("_translate_sentences_with_riva") < body.index("NVIDIA_MODEL")


def test_gloss_stopwords_are_filtered():
    """Function words waste gloss slots and carry no learning value."""
    from src.core.translation import _GLOSS_STOPWORDS, _normalize_gloss_dict

    assert {"the", "a", "of", "is", "and"} <= _GLOSS_STOPWORDS
    cleaned = _normalize_gloss_dict({"the": "这", "library": "图书馆", "is": "是"})
    assert cleaned == {"library": "图书馆"}


# ---------------------------------------------------------------------------
# Background whiteboard pre-generation
# ---------------------------------------------------------------------------

def test_prefetch_is_available_and_configured():
    from src.core.notes_generator import smart_notes_generator

    status = smart_notes_generator.prefetch_status()
    assert set(status) >= {"enabled", "ahead", "concurrency", "in_flight", "queued"}
    assert status["concurrency"] >= 1


def test_prefetch_endpoint_queues_without_blocking():
    """The endpoint must return immediately; generation happens in background."""
    import time

    resp = client.post(
        "/api/sentence/notes-prefetch",
        json={"sentences": ["Prefetch probe sentence about consistency."], "ahead": 1},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "queued" in body
    assert "status" in body


def test_prefetch_status_endpoint():
    resp = client.get("/api/sentence/notes-prefetch/status")
    assert resp.status_code == 200
    assert set(resp.json()) >= {"enabled", "ahead", "concurrency"}


def test_prefetch_ignores_junk_input():
    """Bad input must be ignored, never raise, and never queue work."""
    from src.core.notes_generator import smart_notes_generator

    assert smart_notes_generator.schedule_prefetch(None) == 0
    assert smart_notes_generator.schedule_prefetch([]) == 0
    assert smart_notes_generator.schedule_prefetch([None, 123, "", "   "]) == 0
    # Over-long "sentences" are not real sentences.
    assert smart_notes_generator.schedule_prefetch(["x" * 900]) == 0
    assert smart_notes_generator.schedule_prefetch(["valid"], ahead=0) == 0


def test_prefetch_is_cancelled_on_shutdown():
    from src.config import PROJECT_ROOT

    server_py = (PROJECT_ROOT / "src" / "server.py").read_text(encoding="utf-8")
    assert "cancel_prefetch" in server_py, "background notes leak past shutdown"


def test_frontend_triggers_prefetch_with_a_throttle():
    from src.config import PROJECT_ROOT

    app_js = (PROJECT_ROOT / "src" / "static" / "app.js").read_text(encoding="utf-8")
    assert "function prefetchUpcomingNotes(" in app_js
    assert "/api/sentence/notes-prefetch" in app_js
    assert "notesPrefetchAt" in app_js, "no throttle: arrow-key navigation would spam requests"
    assert "ai_coach_notes_prefetch" in app_js, "no user opt-out"
