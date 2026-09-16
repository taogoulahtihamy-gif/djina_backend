from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase

from core.services.wallet_provider_auth import (
    ExpiringBearerTokenAuthStrategy,
    WalletProviderAccessToken,
    WalletProviderAuthError,
)


class MutableClock:
    def __init__(self):
        self.now = datetime(
            2026,
            9,
            16,
            12,
            0,
            tzinfo=timezone.utc,
        )

    def __call__(self):
        return self.now


class ExpiringBearerTokenAuthTests(
    SimpleTestCase
):

    def setUp(self):
        self.clock = MutableClock()
        self.calls = 0

    def loader(self):
        self.calls += 1

        return WalletProviderAccessToken(
            access_token=f"token-{self.calls}",
            expires_at=(
                self.clock()
                + timedelta(seconds=120)
            ),
        )

    def strategy(self, **kwargs):
        return ExpiringBearerTokenAuthStrategy(
            token_loader=self.loader,
            refresh_skew_seconds=30,
            clock=self.clock,
            **kwargs,
        )

    def test_first_request_loads_token(self):
        strategy = self.strategy()

        headers = strategy.get_headers()

        self.assertEqual(
            headers["Authorization"],
            "Bearer token-1",
        )

        self.assertEqual(
            self.calls,
            1,
        )

    def test_token_is_reused_before_refresh_window(self):
        strategy = self.strategy()

        first = strategy.get_headers()
        second = strategy.get_headers()

        self.assertEqual(first, second)

        self.assertEqual(
            self.calls,
            1,
        )

    def test_token_is_refreshed_near_expiration(self):
        strategy = self.strategy()

        strategy.get_headers()

        self.clock.now += timedelta(
            seconds=95
        )

        headers = strategy.get_headers()

        self.assertEqual(
            headers["Authorization"],
            "Bearer token-2",
        )

        self.assertEqual(
            self.calls,
            2,
        )

    def test_clear_cached_token_forces_reload(self):
        strategy = self.strategy()

        strategy.get_headers()
        strategy.clear_cached_token()

        headers = strategy.get_headers()

        self.assertEqual(
            headers["Authorization"],
            "Bearer token-2",
        )

        self.assertEqual(
            self.calls,
            2,
        )

    def test_loader_exception_is_wrapped(self):
        def explode():
            raise RuntimeError("boom")

        strategy = (
            ExpiringBearerTokenAuthStrategy(
                token_loader=explode,
                clock=self.clock,
            )
        )

        with self.assertRaises(
            WalletProviderAuthError
        ):
            strategy.get_headers()

    def test_empty_token_rejected(self):
        def loader():
            return WalletProviderAccessToken(
                access_token="   ",
                expires_at=(
                    self.clock()
                    + timedelta(minutes=5)
                ),
            )

        strategy = (
            ExpiringBearerTokenAuthStrategy(
                token_loader=loader,
                clock=self.clock,
            )
        )

        with self.assertRaises(
            WalletProviderAuthError
        ):
            strategy.get_headers()

    def test_token_with_newline_rejected(self):
        def loader():
            return WalletProviderAccessToken(
                access_token="bad\ntoken",
                expires_at=(
                    self.clock()
                    + timedelta(minutes=5)
                ),
            )

        strategy = (
            ExpiringBearerTokenAuthStrategy(
                token_loader=loader,
                clock=self.clock,
            )
        )

        with self.assertRaises(
            WalletProviderAuthError
        ):
            strategy.get_headers()

    def test_naive_expiration_rejected(self):
        def loader():
            return WalletProviderAccessToken(
                access_token="token",
                expires_at=datetime(
                    2026,
                    9,
                    16,
                    13,
                    0,
                ),
            )

        strategy = (
            ExpiringBearerTokenAuthStrategy(
                token_loader=loader,
                clock=self.clock,
            )
        )

        with self.assertRaises(
            WalletProviderAuthError
        ):
            strategy.get_headers()

    def test_already_expired_token_rejected(self):
        def loader():
            return WalletProviderAccessToken(
                access_token="expired",
                expires_at=(
                    self.clock()
                    - timedelta(seconds=1)
                ),
            )

        strategy = (
            ExpiringBearerTokenAuthStrategy(
                token_loader=loader,
                clock=self.clock,
            )
        )

        with self.assertRaises(
            WalletProviderAuthError
        ):
            strategy.get_headers()

    def test_token_inside_refresh_skew_is_rejected(self):
        def loader():
            return WalletProviderAccessToken(
                access_token="short-lived",
                expires_at=(
                    self.clock()
                    + timedelta(seconds=10)
                ),
            )

        strategy = (
            ExpiringBearerTokenAuthStrategy(
                token_loader=loader,
                refresh_skew_seconds=30,
                clock=self.clock,
            )
        )

        with self.assertRaises(
            WalletProviderAuthError
        ):
            strategy.get_headers()

    def test_invalid_loader_rejected(self):
        with self.assertRaises(
            WalletProviderAuthError
        ):
            ExpiringBearerTokenAuthStrategy(
                token_loader=None
            )

    def test_invalid_refresh_skew_rejected(self):
        for value in (
            -1,
            301,
            True,
            "30",
        ):
            with self.subTest(value=value):
                with self.assertRaises(
                    WalletProviderAuthError
                ):
                    ExpiringBearerTokenAuthStrategy(
                        token_loader=self.loader,
                        refresh_skew_seconds=value,
                    )
