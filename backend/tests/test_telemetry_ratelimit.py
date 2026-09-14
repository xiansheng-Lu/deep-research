"""埋点进程内限流器与稳定采样单测（M2-8a，无 DB/网络）。"""

from __future__ import annotations

from app.telemetry_ingest import SlidingWindowRateLimiter, stable_sample


class TestSlidingWindow:
    def test_allows_up_to_limit_then_rejects(self) -> None:
        limiter = SlidingWindowRateLimiter(max_per_minute=3, window_seconds=60.0)
        assert all(limiter.allow("u1", now=1000.0 + i) for i in range(3))
        assert limiter.allow("u1", now=1003.0) is False

    def test_window_slides_open(self) -> None:
        limiter = SlidingWindowRateLimiter(max_per_minute=2, window_seconds=60.0)
        assert limiter.allow("u1", now=0.0) is True
        assert limiter.allow("u1", now=10.0) is True
        assert limiter.allow("u1", now=20.0) is False
        # t=61：0 已滑出（cutoff=1），10 仍在，空出 1 个名额
        assert limiter.allow("u1", now=61.0) is True
        # t=62：窗口内仍为 10/61，满
        assert limiter.allow("u1", now=62.0) is False
        # t=71：cutoff=11，10 滑出，仅 61 在
        assert limiter.allow("u1", now=71.0) is True
        assert limiter.allow("u1", now=72.0) is False

    def test_keys_isolated(self) -> None:
        limiter = SlidingWindowRateLimiter(max_per_minute=1, window_seconds=60.0)
        assert limiter.allow("u1", now=0.0) is True
        assert limiter.allow("u2", now=0.0) is True
        assert limiter.allow("u1", now=1.0) is False
        assert limiter.allow("u2", now=1.0) is False


class TestStableSample:
    def test_zero_and_one_boundaries(self) -> None:
        assert stable_sample("any-user", 0.0) is False
        assert stable_sample("any-user", -1.0) is False
        assert stable_sample("any-user", 1.0) is True
        assert stable_sample("any-user", 2.0) is True

    def test_same_user_deterministic(self) -> None:
        outcomes = [stable_sample("user-fixed", 0.5) for _ in range(20)]
        assert all(outcomes[0] == o for o in outcomes)

    def test_distributes_between_users(self) -> None:
        # 0.5 采样下，足够多用户应同时出现采/不采（非全采或全不采）
        sampled = {stable_sample(f"user-{i}", 0.5) for i in range(200)}
        assert sampled == {True, False}
