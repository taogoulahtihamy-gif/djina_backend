"""Tests déterministes d'idempotence et de rollback.

Ces tests SQLite ne prouvent pas la concurrence réelle ni le verrouillage
PostgreSQL. Un test concurrent sur PostgreSQL reste à prévoir.
"""

from decimal import Decimal, localcontext
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from core.models import (
    Commission, CommissionReservation, Course, Customer, CustomUser, Driver,
    DriverWallet, WalletTransaction,
)
from core.services.wallet_service import (
    CommissionReservationConflictError, CommissionReservationError,
    CommissionReservationStateError,
    InsufficientWalletBalanceError, InvalidWalletAmountError, WalletError,
    WalletIdempotencyConflictError, WalletNotActiveError,
    WalletTransactionConsistencyError, credit_wallet, debit_wallet,
    get_or_create_driver_wallet, reserve_commission,
    release_commission_reservation, consume_commission_reservation,
)


class WalletServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.driver = cls.make_driver("1")
        cls.other_driver = cls.make_driver("2")
        customer_user = CustomUser.objects.create_user(
            email="wallet-service-customer@example.com", phone="+23569990003",
        )
        customer = Customer.objects.create(user=customer_user)
        cls.course = Course.objects.create(
            driver=cls.driver, customer=customer,
            departure_latitude=Decimal("12.1"), departure_longitude=Decimal("15.1"),
            destination_latitude=Decimal("12.2"), destination_longitude=Decimal("15.2"),
            starting_landmark="Chagoua", arrival_landmark="Farcha",
            initial_price=Decimal("1000"), final_price=Decimal("1000"),
            status=Course.Status.COMPLETED,
        )
        cls.commission = Commission.objects.get(course=cls.course)

    @staticmethod
    def make_driver(suffix):
        user = CustomUser.objects.create_user(
            email=f"wallet-service-{suffix}@example.com", phone=f"+2356999000{suffix}",
            user_type="driver",
        )
        return Driver.objects.create(user=user)

    def setUp(self):
        self.wallet = get_or_create_driver_wallet(self.driver)

    def credit(self, **overrides):
        values = dict(wallet=self.wallet, amount=Decimal("10000"),
                      transaction_type=WalletTransaction.Type.TOPUP, idempotency_key="credit")
        values.update(overrides)
        return credit_wallet(**values)

    def debit(self, **overrides):
        values = dict(wallet=self.wallet, amount=Decimal("1000"),
                      transaction_type=WalletTransaction.Type.COMMISSION, idempotency_key="debit")
        values.update(overrides)
        return debit_wallet(**values)

    def assert_balance(self, expected):
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal(expected))

    def assert_rejected(self, operation, exception, **values):
        before = DriverWallet.objects.get(pk=self.wallet.pk).balance
        count = WalletTransaction.objects.count()
        with self.assertRaises(exception):
            operation(**values)
        self.assert_balance(before)
        self.assertEqual(WalletTransaction.objects.count(), count)

    def test_get_or_create_creates_wallet(self):
        other = get_or_create_driver_wallet(self.other_driver)
        self.assertTrue(DriverWallet.objects.filter(pk=other.pk, driver=self.other_driver).exists())

    def test_get_or_create_returns_same_wallet(self):
        self.assertEqual(get_or_create_driver_wallet(self.driver).pk, self.wallet.pk)
        self.assertEqual(DriverWallet.objects.filter(driver=self.driver).count(), 1)

    def test_wallet_initial_values(self):
        self.assert_balance("0.00")
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.currency, "XAF")
        self.assertEqual(self.wallet.status, DriverWallet.Status.ACTIVE)

    def test_get_or_create_preserves_existing_balance(self):
        self.credit()
        self.assertEqual(get_or_create_driver_wallet(self.driver).balance, Decimal("10000"))

    def test_credit_balance(self):
        self.credit()
        self.assert_balance("10000")

    def test_credit_ledger(self):
        result = self.credit()
        result.refresh_from_db()
        self.assertEqual(result.wallet_id, self.wallet.pk)
        self.assertEqual(result.direction, WalletTransaction.Direction.CREDIT)
        self.assertEqual(result.status, WalletTransaction.Status.SUCCESS)
        self.assertEqual(result.type, WalletTransaction.Type.TOPUP)
        self.assertEqual(result.amount, Decimal("10000.00"))
        self.assertEqual(result.balance_before, Decimal("0.00"))
        self.assertEqual(result.balance_after, Decimal("10000.00"))
        self.assertEqual(result.idempotency_key, "credit")

    def test_credit_retry_does_not_credit_twice(self):
        self.credit()
        self.credit()
        self.assert_balance("10000")
        self.assertEqual(self.wallet.transactions.count(), 1)

    def test_credit_retry_returns_existing_transaction(self):
        first = self.credit()
        self.assertEqual(self.credit().pk, first.pk)

    def test_credit_retry_different_amount_conflicts(self):
        self.credit()
        self.assert_rejected(self.credit, WalletIdempotencyConflictError, amount=Decimal("1"))

    def test_credit_retry_different_type_conflicts(self):
        self.credit()
        self.assert_rejected(self.credit, WalletIdempotencyConflictError,
                             transaction_type=WalletTransaction.Type.REFUND)

    def test_zero_rejected(self):
        self.assert_rejected(self.credit, InvalidWalletAmountError, amount=Decimal("0"))

    def test_negative_rejected(self):
        self.assert_rejected(self.credit, InvalidWalletAmountError, amount=Decimal("-1"))

    def test_nan_rejected(self):
        for value in ("NaN", "sNaN"):
            self.assert_rejected(self.credit, InvalidWalletAmountError, amount=Decimal(value))

    def test_infinity_rejected(self):
        for value in ("Infinity", "-Infinity"):
            self.assert_rejected(self.credit, InvalidWalletAmountError, amount=Decimal(value))

    def test_float_and_bool_rejected(self):
        for value in (1.1, True, False):
            self.assert_rejected(self.credit, InvalidWalletAmountError, amount=value)

    def test_invalid_decimal_input_rejected(self):
        for value in (None, "abc", "", object()):
            self.assert_rejected(self.credit, InvalidWalletAmountError, amount=value)

    def test_fractional_cent_rejected_without_rounding(self):
        for value in ("0.001", "1.999"):
            self.assert_rejected(self.credit, InvalidWalletAmountError, amount=value)

    def test_exact_convertible_amounts(self):
        for index, value in enumerate((1, "0.10", Decimal("0.2000"))):
            self.credit(amount=value, idempotency_key=f"exact-{index}")
        self.assert_balance("1.30")

    def test_amount_capacity_rejected(self):
        self.assert_rejected(self.credit, InvalidWalletAmountError, amount="1000000000000")

    def test_balance_capacity_rejected(self):
        self.credit(amount="999999999999.99")
        self.assert_rejected(self.credit, InvalidWalletAmountError, amount="0.01", idempotency_key="overflow")

    def test_decimal_context_does_not_round_balances(self):
        with localcontext() as context:
            context.prec = 3
            self.credit(amount="10000.01")
            self.debit(amount="0.02")
        self.assert_balance("9999.99")

    def test_blocked_wallet_rejected(self):
        DriverWallet.objects.filter(pk=self.wallet.pk).update(status=DriverWallet.Status.BLOCKED)
        self.assert_rejected(self.credit, WalletNotActiveError)

    def test_closed_wallet_rejected(self):
        DriverWallet.objects.filter(pk=self.wallet.pk).update(status=DriverWallet.Status.CLOSED)
        self.assert_rejected(self.credit, WalletNotActiveError)

    def test_credit_rejects_incompatible_types(self):
        for value in (WalletTransaction.Type.COMMISSION, "unknown"):
            self.assert_rejected(self.credit, WalletError, transaction_type=value)

    def test_refund_and_adjustment_credit_allowed(self):
        for value in (WalletTransaction.Type.REFUND, WalletTransaction.Type.ADJUSTMENT):
            self.credit(transaction_type=value, idempotency_key=value)
        self.assert_balance("20000")

    def test_debit_balance_and_ledger(self):
        self.credit()
        result = self.debit()
        result.refresh_from_db()
        self.assert_balance("9000")
        self.assertEqual(result.direction, WalletTransaction.Direction.DEBIT)
        self.assertEqual(result.status, WalletTransaction.Status.SUCCESS)
        self.assertEqual(result.type, WalletTransaction.Type.COMMISSION)
        self.assertEqual(result.amount, Decimal("1000"))
        self.assertEqual(result.balance_before, Decimal("10000"))
        self.assertEqual(result.balance_after, Decimal("9000"))

    def test_debit_above_balance_has_no_effect(self):
        self.assert_rejected(self.debit, InsufficientWalletBalanceError)
        self.assertFalse(self.wallet.transactions.exists())

    def test_reserved_balance_reduces_available(self):
        self.credit()
        DriverWallet.objects.filter(pk=self.wallet.pk).update(reserved_balance=Decimal("9500"))
        self.assert_rejected(self.debit, InsufficientWalletBalanceError)

    def test_debit_exact_available_preserves_reserve(self):
        self.credit()
        DriverWallet.objects.filter(pk=self.wallet.pk).update(reserved_balance=Decimal("9000"))
        self.debit()
        self.assert_balance("9000")
        self.assertEqual(self.wallet.reserved_balance, Decimal("9000"))
        self.assertEqual(self.wallet.available_balance, Decimal("0"))

    def test_debit_retry_returns_existing_without_second_debit(self):
        self.credit()
        result = self.debit(amount=Decimal("10000"))
        self.assertEqual(self.debit(amount=Decimal("10000")).pk, result.pk)
        self.assert_balance("0")
        self.assertEqual(self.wallet.transactions.count(), 2)

    def test_debit_idempotency_conflict(self):
        self.credit()
        self.debit()
        self.assert_rejected(self.debit, WalletIdempotencyConflictError, amount=Decimal("2"))
        self.assert_rejected(self.debit, WalletIdempotencyConflictError,
                             transaction_type=WalletTransaction.Type.ADJUSTMENT)

    def test_debit_invalid_amounts(self):
        self.credit()
        for value in ("0", "-1", "NaN", "Infinity", "-Infinity", "0.001", 1.1):
            self.assert_rejected(self.debit, InvalidWalletAmountError, amount=value)

    def test_debit_inactive_statuses(self):
        self.credit()
        for status in (DriverWallet.Status.BLOCKED, DriverWallet.Status.CLOSED):
            DriverWallet.objects.filter(pk=self.wallet.pk).update(status=status)
            self.assert_rejected(self.debit, WalletNotActiveError)

    def test_debit_incompatible_types(self):
        self.credit()
        for value in (WalletTransaction.Type.TOPUP, WalletTransaction.Type.REFUND, "unknown"):
            self.assert_rejected(self.debit, WalletError, transaction_type=value)

    def test_adjustment_debit_allowed(self):
        self.credit()
        self.debit(transaction_type=WalletTransaction.Type.ADJUSTMENT)
        self.assert_balance("9000")

    def test_credit_ledger_failure_rolls_back_balance(self):
        with patch("core.services.wallet_service.WalletTransaction.objects.create", side_effect=IntegrityError("forced")):
            self.assert_rejected(self.credit, IntegrityError)
        self.assertFalse(self.wallet.transactions.exists())
        self.credit()  # La même clé reste disponible après rollback.
        self.assert_balance("10000")

    def test_debit_ledger_failure_rolls_back_balance(self):
        self.credit()
        with patch("core.services.wallet_service.WalletTransaction.objects.create", side_effect=IntegrityError("forced")):
            self.assert_rejected(self.debit, IntegrityError)
        self.assertFalse(self.wallet.transactions.filter(idempotency_key="debit").exists())
        self.debit()
        self.assert_balance("9000")

    def test_failure_after_ledger_insert_rolls_back_both_writes(self):
        create = WalletTransaction.objects.create

        def insert_then_fail(**values):
            create(**values)
            raise RuntimeError("failure after insert")

        with patch("core.services.wallet_service.WalletTransaction.objects.create", side_effect=insert_then_fail):
            self.assert_rejected(self.credit, RuntimeError)
        self.assertFalse(self.wallet.transactions.exists())

    def test_wallet_isolation(self):
        other = get_or_create_driver_wallet(self.other_driver)
        self.credit(wallet=other, amount="20", idempotency_key="other")
        self.credit()
        self.debit()
        other.refresh_from_db()
        self.assertEqual(other.balance, Decimal("20"))
        self.assertEqual(other.transactions.count(), 1)
        self.assert_balance("9000")

    def test_global_key_conflict_between_wallets(self):
        self.credit()
        other = get_or_create_driver_wallet(self.other_driver)
        self.assert_rejected(self.credit, WalletIdempotencyConflictError, wallet=other)
        other.refresh_from_db()
        self.assertEqual(other.balance, Decimal("0"))
        self.assertFalse(other.transactions.exists())

    def test_metadata_saved(self):
        metadata = {"reason": "manual", "details": {"attempt": 1}, "tags": ["test"]}
        result = self.credit(metadata=metadata)
        result.refresh_from_db()
        self.assertEqual(result.metadata, metadata)
        self.assertEqual(self.credit(metadata=metadata).pk, result.pk)

    def test_metadata_defaults_to_dict(self):
        result = self.credit()
        result.refresh_from_db()
        self.assertEqual(result.metadata, {})
        self.assertEqual(self.credit(metadata={}).pk, result.pk)

    def test_retry_with_different_metadata_returns_existing_transaction(self):
        first = self.credit(metadata={"reason": "first"})
        retry = self.credit(metadata={"reason": "second"})
        self.assertEqual(retry.pk, first.pk)
        self.assert_balance("10000")
        self.assertEqual(self.wallet.transactions.count(), 1)

    def test_retry_preserves_historical_metadata(self):
        first = self.credit(metadata={"reason": "first"})
        retry = self.credit(metadata={"reason": "second"})
        self.assertEqual(retry.metadata, {"reason": "first"})
        first.refresh_from_db()
        self.assertEqual(first.metadata, {"reason": "first"})

    def test_metadata_change_does_not_hide_financial_conflict(self):
        self.credit(metadata={"reason": "first"})
        self.assert_rejected(
            self.credit, WalletIdempotencyConflictError,
            amount="1", metadata={"reason": "second"},
        )

    def test_credit_key_whitespace_retry_is_idempotent(self):
        first = self.credit(idempotency_key="credit-key")
        retry = self.credit(idempotency_key=" credit-key ")
        self.assertEqual(retry.pk, first.pk)
        self.assert_balance("10000")
        self.assertEqual(self.wallet.transactions.count(), 1)

    def test_key_normalized_before_creation(self):
        first = self.credit(idempotency_key=" credit-key ")
        first.refresh_from_db()
        self.assertEqual(first.idempotency_key, "credit-key")
        self.assertEqual(self.credit(idempotency_key="credit-key").pk, first.pk)
        self.assert_balance("10000")
        self.assertEqual(self.wallet.transactions.count(), 1)

    def test_whitespace_only_key_rejected(self):
        self.assert_rejected(self.credit, WalletError, idempotency_key=" \t\n ")

    def test_debit_retry_with_normalized_key_and_changed_metadata(self):
        self.credit()
        first = self.debit(idempotency_key="debit-key", metadata={"attempt": 1})
        retry = self.debit(idempotency_key=" debit-key ", metadata={"attempt": 2})
        self.assertEqual(retry.pk, first.pk)
        first.refresh_from_db()
        self.assertEqual(first.metadata, {"attempt": 1})
        self.assert_balance("9000")
        self.assertEqual(self.wallet.transactions.count(), 2)

    def test_optional_references_saved_and_compared(self):
        values = dict(course=self.course, commission=self.commission,
                      provider="other", provider_reference="provider-123")
        result = self.credit(**values)
        result.refresh_from_db()
        self.assertEqual(result.course_id, self.course.pk)
        self.assertEqual(result.commission_id, self.commission.pk)
        self.assertEqual(result.provider, "other")
        self.assertEqual(result.provider_reference, "provider-123")
        self.assertEqual(self.credit(**values).pk, result.pk)
        for field in values:
            changed = dict(values, **{field: None})
            self.assert_rejected(self.credit, WalletIdempotencyConflictError, **changed)

    def test_direction_conflict(self):
        self.credit(transaction_type=WalletTransaction.Type.ADJUSTMENT)
        self.assert_rejected(self.debit, WalletIdempotencyConflictError,
                             amount="10000", idempotency_key="credit",
                             transaction_type=WalletTransaction.Type.ADJUSTMENT)

    def test_invalid_keys_rejected(self):
        for key in (None, "", "   ", "x" * 121, 123):
            self.assert_rejected(self.credit, WalletError, idempotency_key=key)

    def test_stale_wallet_balance_ignored(self):
        self.wallet.balance = Decimal("999999")
        self.credit()
        self.debit()
        self.assert_balance("9000")

    def test_successful_retry_after_status_change_is_read_only(self):
        result = self.credit()
        DriverWallet.objects.filter(pk=self.wallet.pk).update(status=DriverWallet.Status.CLOSED)
        self.assertEqual(self.credit().pk, result.pk)
        self.assert_balance("10000")
        self.assertEqual(self.wallet.transactions.count(), 1)

    def test_non_success_existing_transaction_conflicts(self):
        # Fixture d'un état historique incompatible, pas une correction métier.
        result = self.credit()
        for status in (WalletTransaction.Status.PENDING, WalletTransaction.Status.FAILED,
                       WalletTransaction.Status.REVERSED):
            WalletTransaction.objects.filter(pk=result.pk).update(status=status)
            self.assert_rejected(self.credit, WalletIdempotencyConflictError)

    def test_inconsistent_existing_ledger_rejected(self):
        result = self.credit()
        WalletTransaction.objects.filter(pk=result.pk).update(balance_after=Decimal("1"))
        self.assert_rejected(self.credit, WalletTransactionConsistencyError)

    def test_invalid_optional_field_rolls_back(self):
        self.assert_rejected(self.credit, ValidationError, provider="x" * 31)

    def test_global_collision_recovery_rolls_back_losing_wallet(self):
        # Simulation déterministe de la fenêtre lecture/insertion, PAS un test
        # concurrent : le premier SELECT manque une clé déjà prise en DB.
        self.credit()
        other = get_or_create_driver_wallet(self.other_driver)
        real_filter = WalletTransaction.objects.filter
        calls = 0

        def miss_first_lookup(**kwargs):
            nonlocal calls
            calls += 1
            self.assertEqual(kwargs["idempotency_key"], "credit")
            return WalletTransaction.objects.none() if calls == 1 else real_filter(**kwargs)

        with patch("core.services.wallet_service.WalletTransaction.objects.filter", side_effect=miss_first_lookup):
            self.assert_rejected(
                self.credit, WalletIdempotencyConflictError,
                wallet=other, idempotency_key=" credit ",
            )
        other.refresh_from_db()
        self.assertEqual(other.balance, Decimal("0"))
        self.assertFalse(other.transactions.exists())


class CommissionReservationServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        drivers = []
        for index in (1, 2):
            user = CustomUser.objects.create_user(
                email=f"reservation-{index}@example.com", phone=f"+2356888000{index}",
                user_type="driver",
            )
            drivers.append(Driver.objects.create(user=user))
        cls.driver, cls.other_driver = drivers
        user = CustomUser.objects.create_user(
            email="reservation-customer@example.com", phone="+23568880003",
        )
        cls.customer = Customer.objects.create(user=user)
        # Soldes de fixture uniquement ; aucune transaction nécessaire pour
        # vérifier que ces primitives ne créent jamais de WalletTransaction.
        cls.wallet = DriverWallet.objects.create(driver=cls.driver, balance=Decimal("1000"))
        cls.other_wallet = DriverWallet.objects.create(driver=cls.other_driver, balance=Decimal("2000"))
        cls.course = cls.make_course(cls.driver)
        cls.other_course = cls.make_course(cls.other_driver)

    @classmethod
    def make_course(cls, driver):
        return Course.objects.create(
            driver=driver, customer=cls.customer,
            departure_latitude=Decimal("12.1"), departure_longitude=Decimal("15.1"),
            destination_latitude=Decimal("12.2"), destination_longitude=Decimal("15.2"),
            starting_landmark="Chagoua", arrival_landmark="Farcha",
            initial_price=Decimal("1000"),
        )

    def reserve(self, **overrides):
        values = dict(wallet=self.wallet, driver=self.driver, course=self.course,
                      estimated_amount=Decimal("150"))
        values.update(overrides)
        return reserve_commission(**values)

    def snapshot(self):
        return (
            list(DriverWallet.objects.order_by("pk").values()),
            list(CommissionReservation.objects.order_by("pk").values()),
            list(WalletTransaction.objects.order_by("pk").values()),
        )

    def assert_rejected(self, operation, exception, **kwargs):
        before = self.snapshot()
        with self.assertRaises(exception):
            operation(**kwargs)
        self.assertEqual(self.snapshot(), before)

    def assert_wallet(self, reserved, balance="1000"):
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal(reserved))
        self.assertEqual(self.wallet.balance, Decimal(balance))
        self.assertEqual(self.wallet.available_balance, Decimal(balance) - Decimal(reserved))
        self.assertFalse(WalletTransaction.objects.exists())

    def test_reserve_creates_active_reservation(self):
        result = self.reserve()
        result.refresh_from_db()
        self.assertEqual(result.wallet_id, self.wallet.pk)
        self.assertEqual(result.driver_id, self.driver.pk)
        self.assertEqual(result.course_id, self.course.pk)
        self.assertEqual(result.estimated_amount, Decimal("150.00"))
        self.assertEqual(result.status, CommissionReservation.Status.ACTIVE)
        self.assertIsNone(result.released_at)
        self.assertIsNone(result.consumed_at)
        self.assert_wallet("150")

    def test_reserve_keeps_balance_and_reduces_available(self):
        self.reserve()
        self.assert_wallet("150")
        self.assertEqual(self.wallet.available_balance, Decimal("850"))

    def test_reserve_creates_no_wallet_transaction(self):
        self.reserve()
        self.assertFalse(WalletTransaction.objects.exists())

    def test_reserve_retry_returns_same_without_mutation(self):
        result = self.reserve()
        before = self.snapshot()
        self.assertEqual(self.reserve().pk, result.pk)
        self.assertEqual(self.snapshot(), before)
        self.assert_wallet("150")

    def test_reserve_retry_after_release_does_not_reactivate(self):
        result = release_commission_reservation(reservation=self.reserve())
        before = self.snapshot()
        retry = self.reserve()
        self.assertEqual(retry.pk, result.pk)
        self.assertEqual(retry.status, CommissionReservation.Status.RELEASED)
        self.assertEqual(self.snapshot(), before)
        self.assert_wallet("0")

    def test_reserve_retry_after_consume_does_not_reactivate(self):
        result = consume_commission_reservation(reservation=self.reserve())
        before = self.snapshot()
        retry = self.reserve()
        self.assertEqual(retry.pk, result.pk)
        self.assertEqual(retry.status, CommissionReservation.Status.CONSUMED)
        self.assertEqual(self.snapshot(), before)
        self.assert_wallet("0")

    def test_reserve_retry_on_inactive_wallet(self):
        result = self.reserve()
        for status in (DriverWallet.Status.BLOCKED, DriverWallet.Status.CLOSED):
            DriverWallet.objects.filter(pk=self.wallet.pk).update(status=status)
            before = self.snapshot()
            self.assertEqual(self.reserve().pk, result.pk)
            self.assertEqual(self.snapshot(), before)

    def test_reserve_different_amount_conflicts(self):
        self.reserve()
        self.assert_rejected(self.reserve, CommissionReservationConflictError, estimated_amount="151")

    def test_reserve_different_wallet_conflicts(self):
        self.reserve()
        self.assert_rejected(self.reserve, CommissionReservationConflictError, wallet=self.other_wallet)

    def test_reserve_different_driver_conflicts(self):
        self.reserve()
        self.assert_rejected(self.reserve, CommissionReservationConflictError, driver=self.other_driver)

    def test_terminal_reservations_still_reject_financial_conflicts(self):
        for finish in (release_commission_reservation, consume_commission_reservation):
            course = self.make_course(self.driver)
            finish(reservation=self.reserve(course=course))
            self.assert_rejected(self.reserve, CommissionReservationConflictError,
                                 course=course, estimated_amount="151")

    def test_reserve_zero_rejected(self):
        self.assert_rejected(self.reserve, InvalidWalletAmountError, estimated_amount="0")

    def test_reserve_negative_rejected(self):
        self.assert_rejected(self.reserve, InvalidWalletAmountError, estimated_amount="-1")

    def test_reserve_float_rejected(self):
        self.assert_rejected(self.reserve, InvalidWalletAmountError, estimated_amount=1.1)

    def test_reserve_fractional_cent_rejected(self):
        self.assert_rejected(self.reserve, InvalidWalletAmountError, estimated_amount="1.001")

    def test_reserve_non_finite_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            self.assert_rejected(self.reserve, InvalidWalletAmountError, estimated_amount=value)

    def test_reserve_amount_above_capacity_rejected(self):
        self.assert_rejected(self.reserve, InvalidWalletAmountError, estimated_amount="1000000000000")

    def test_reserve_above_available_rolls_back(self):
        self.reserve()
        self.assert_rejected(self.reserve, InsufficientWalletBalanceError,
                             course=self.make_course(self.driver), estimated_amount="851")

    def test_reserve_exact_available(self):
        self.reserve(estimated_amount="1000")
        self.assert_wallet("1000")

    def test_reserve_blocked_wallet_rejected(self):
        DriverWallet.objects.filter(pk=self.wallet.pk).update(status=DriverWallet.Status.BLOCKED)
        self.assert_rejected(self.reserve, WalletNotActiveError)

    def test_reserve_closed_wallet_rejected(self):
        DriverWallet.objects.filter(pk=self.wallet.pk).update(status=DriverWallet.Status.CLOSED)
        self.assert_rejected(self.reserve, WalletNotActiveError)

    def test_reserve_wrong_wallet_driver_rejected(self):
        self.assert_rejected(self.reserve, CommissionReservationError, driver=self.other_driver)

    def test_reserve_wrong_course_driver_rejected(self):
        self.assert_rejected(self.reserve, CommissionReservationError, course=self.other_course)

    def test_reserve_null_course_driver_does_not_modify_course(self):
        course = self.make_course(None)
        before = Course.objects.filter(pk=course.pk).values().get()
        self.reserve(course=course)
        self.assertEqual(Course.objects.filter(pk=course.pk).values().get(), before)
        self.assert_wallet("150")

    def test_reserve_unsaved_driver_rejected(self):
        self.assert_rejected(self.reserve, CommissionReservationError, driver=Driver())
        self.assert_rejected(self.reserve, CommissionReservationError, driver=Driver(pk=self.driver.pk))

    def test_reserve_unsaved_course_rejected(self):
        self.assert_rejected(self.reserve, CommissionReservationError, course=Course())
        self.assert_rejected(self.reserve, CommissionReservationError, course=Course(pk=self.course.pk))

    def test_reserve_deleted_course_rejected(self):
        course = self.make_course(None)
        Course.objects.filter(pk=course.pk).delete()
        self.assert_rejected(self.reserve, CommissionReservationError, course=course)

    def test_reserve_stale_wallet_ignored(self):
        self.wallet.balance = Decimal("999999")
        self.wallet.reserved_balance = Decimal("900")
        self.wallet.driver_id = self.other_driver.pk
        self.wallet.status = DriverWallet.Status.CLOSED
        self.reserve()
        self.assert_wallet("150")

    def test_reserve_stale_course_driver_ignored(self):
        Course.objects.filter(pk=self.course.pk).update(driver=self.other_driver)
        self.assert_rejected(self.reserve, CommissionReservationError)

    def test_release_active_updates_state_and_timestamp(self):
        result = release_commission_reservation(reservation=self.reserve())
        result.refresh_from_db()
        self.assertEqual(result.status, CommissionReservation.Status.RELEASED)
        self.assertIsNotNone(result.released_at)
        self.assertIsNone(result.consumed_at)
        self.assert_wallet("0")

    def test_release_retry_is_read_only(self):
        original = self.reserve()
        result = release_commission_reservation(reservation=original)
        before = self.snapshot()
        retry = release_commission_reservation(reservation=original)
        self.assertEqual(retry.pk, result.pk)
        self.assertEqual(retry.released_at, result.released_at)
        self.assertEqual(self.snapshot(), before)

    def test_release_consumed_rejected(self):
        result = consume_commission_reservation(reservation=self.reserve())
        self.assert_rejected(release_commission_reservation, CommissionReservationStateError,
                             reservation=result)

    def test_release_blocked_wallet(self):
        result = self.reserve()
        DriverWallet.objects.filter(pk=self.wallet.pk).update(status=DriverWallet.Status.BLOCKED)
        release_commission_reservation(reservation=result)
        self.assert_wallet("0")
        self.assertEqual(self.wallet.status, DriverWallet.Status.BLOCKED)

    def test_release_closed_wallet(self):
        result = self.reserve()
        DriverWallet.objects.filter(pk=self.wallet.pk).update(status=DriverWallet.Status.CLOSED)
        release_commission_reservation(reservation=result)
        self.assert_wallet("0")
        self.assertEqual(self.wallet.status, DriverWallet.Status.CLOSED)

    def test_release_insufficient_reserve_rolls_back(self):
        result = self.reserve()
        DriverWallet.objects.filter(pk=self.wallet.pk).update(reserved_balance=Decimal("149"))
        self.assert_rejected(release_commission_reservation, WalletTransactionConsistencyError,
                             reservation=result)

    def test_consume_active_updates_state_and_timestamp(self):
        result = consume_commission_reservation(reservation=self.reserve())
        result.refresh_from_db()
        self.assertEqual(result.status, CommissionReservation.Status.CONSUMED)
        self.assertIsNotNone(result.consumed_at)
        self.assertIsNone(result.released_at)
        self.assert_wallet("0")

    def test_consume_retry_is_read_only(self):
        original = self.reserve()
        result = consume_commission_reservation(reservation=original)
        before = self.snapshot()
        retry = consume_commission_reservation(reservation=original)
        self.assertEqual(retry.pk, result.pk)
        self.assertEqual(retry.consumed_at, result.consumed_at)
        self.assertEqual(self.snapshot(), before)

    def test_consume_released_rejected(self):
        result = release_commission_reservation(reservation=self.reserve())
        self.assert_rejected(consume_commission_reservation, CommissionReservationStateError,
                             reservation=result)

    def test_consume_blocked_wallet(self):
        result = self.reserve()
        DriverWallet.objects.filter(pk=self.wallet.pk).update(status=DriverWallet.Status.BLOCKED)
        consume_commission_reservation(reservation=result)
        self.assert_wallet("0")
        self.assertEqual(self.wallet.status, DriverWallet.Status.BLOCKED)

    def test_consume_closed_wallet(self):
        result = self.reserve()
        DriverWallet.objects.filter(pk=self.wallet.pk).update(status=DriverWallet.Status.CLOSED)
        consume_commission_reservation(reservation=result)
        self.assert_wallet("0")
        self.assertEqual(self.wallet.status, DriverWallet.Status.CLOSED)

    def test_consume_insufficient_reserve_rolls_back(self):
        result = self.reserve()
        DriverWallet.objects.filter(pk=self.wallet.pk).update(reserved_balance=Decimal("149"))
        self.assert_rejected(consume_commission_reservation, WalletTransactionConsistencyError,
                             reservation=result)

    def test_reserve_isolated_from_other_wallet(self):
        before = DriverWallet.objects.filter(pk=self.other_wallet.pk).values().get()
        self.reserve()
        self.assertEqual(DriverWallet.objects.filter(pk=self.other_wallet.pk).values().get(), before)

    def test_release_isolated_from_other_wallet(self):
        self.reserve(wallet=self.other_wallet, driver=self.other_driver, course=self.other_course)
        result = self.reserve()
        before = DriverWallet.objects.filter(pk=self.other_wallet.pk).values().get()
        release_commission_reservation(reservation=result)
        self.assertEqual(DriverWallet.objects.filter(pk=self.other_wallet.pk).values().get(), before)
        self.assert_wallet("0")

    def test_consume_isolated_from_other_wallet(self):
        self.reserve(wallet=self.other_wallet, driver=self.other_driver, course=self.other_course)
        result = self.reserve()
        before = DriverWallet.objects.filter(pk=self.other_wallet.pk).values().get()
        consume_commission_reservation(reservation=result)
        self.assertEqual(DriverWallet.objects.filter(pk=self.other_wallet.pk).values().get(), before)
        self.assert_wallet("0")

    def test_reserve_creation_failure_rolls_back(self):
        def fail_after_increase(**kwargs):
            self.assertEqual(DriverWallet.objects.get(pk=self.wallet.pk).reserved_balance, Decimal("150"))
            raise IntegrityError("forced creation failure")

        with patch("core.services.wallet_service.CommissionReservation.objects.create", side_effect=fail_after_increase):
            self.assert_rejected(self.reserve, IntegrityError)
        self.reserve()
        self.assert_wallet("150")

    def test_reserve_failure_after_insert_rolls_back(self):
        create = CommissionReservation.objects.create

        def insert_then_fail(**kwargs):
            create(**kwargs)
            raise RuntimeError("failure after insert")

        with patch("core.services.wallet_service.CommissionReservation.objects.create", side_effect=insert_then_fail):
            self.assert_rejected(self.reserve, RuntimeError)

    def assert_finish_save_failure_rolls_back(self, operation):
        result = self.reserve()
        save = CommissionReservation.save

        def save_then_fail(instance, **kwargs):
            self.assertEqual(DriverWallet.objects.get(pk=self.wallet.pk).reserved_balance, Decimal("0"))
            save(instance, **kwargs)
            raise IntegrityError("failure after reservation save")

        with patch.object(CommissionReservation, "save", autospec=True, side_effect=save_then_fail):
            self.assert_rejected(operation, IntegrityError, reservation=result)
        operation(reservation=result)
        self.assert_wallet("0")

    def test_release_save_failure_rolls_back(self):
        self.assert_finish_save_failure_rolls_back(release_commission_reservation)

    def test_consume_save_failure_rolls_back(self):
        self.assert_finish_save_failure_rolls_back(consume_commission_reservation)

    def test_multiple_reservations_contribute_only_while_active(self):
        first = self.reserve()
        second = self.reserve(course=self.make_course(self.driver), estimated_amount="250")
        third = self.reserve(course=self.make_course(self.driver), estimated_amount="300")
        self.assert_wallet("700")
        release_commission_reservation(reservation=first)
        self.assert_wallet("550")
        consume_commission_reservation(reservation=second)
        self.assert_wallet("300")
        self.assertEqual(CommissionReservation.objects.get(pk=third.pk).status, CommissionReservation.Status.ACTIVE)

    def test_finish_ignores_stale_reservation_fields(self):
        for operation in (release_commission_reservation, consume_commission_reservation):
            result = self.reserve(course=self.make_course(self.driver))
            result.wallet_id = self.other_wallet.pk
            result.estimated_amount = Decimal("9999")
            result.status = CommissionReservation.Status.RELEASED
            operation(reservation=result)
            self.assert_wallet("0")
            self.other_wallet.refresh_from_db()
            self.assertEqual(self.other_wallet.reserved_balance, Decimal("0"))

    def test_finish_unsaved_reservation_rejected(self):
        for operation in (release_commission_reservation, consume_commission_reservation):
            self.assert_rejected(operation, CommissionReservationError, reservation=CommissionReservation())

    def test_reservation_arithmetic_uses_exact_decimal(self):
        with localcontext() as context:
            context.prec = 3
            first = self.reserve(estimated_amount="150.01")
            second = self.reserve(course=self.make_course(self.driver), estimated_amount="250.02")
            release_commission_reservation(reservation=first)
            consume_commission_reservation(reservation=second)
        self.assert_wallet("0")

    def test_lock_order_for_reserve_release_and_consume(self):
        # Vérifie l'ordre d'appel, pas un verrouillage concurrent PostgreSQL.
        wallet_lock = DriverWallet.objects.select_for_update
        reservation_lock = CommissionReservation.objects.select_for_update
        order = []

        def lock_wallet(*args, **kwargs):
            order.append("wallet")
            return wallet_lock(*args, **kwargs)

        def lock_reservation(*args, **kwargs):
            order.append("reservation")
            return reservation_lock(*args, **kwargs)

        with patch.object(DriverWallet.objects, "select_for_update", side_effect=lock_wallet), \
                patch.object(CommissionReservation.objects, "select_for_update", side_effect=lock_reservation):
            for finish in (release_commission_reservation, consume_commission_reservation):
                result = self.reserve(course=self.make_course(self.driver))
                self.assertEqual(order, ["wallet", "reservation"])
                order.clear()
                finish(reservation=result)
                self.assertEqual(order, ["wallet", "reservation"])
                order.clear()

    def test_concurrent_collision_conflict_rolls_back_losing_wallet(self):
        # Simulation déterministe : première lecture aveugle à une ligne déjà
        # présente, puis collision OneToOne. Ne prouve pas la concurrence réelle.
        course = self.make_course(None)
        self.reserve(course=course)
        real_lock = CommissionReservation.objects.select_for_update
        calls = 0

        def miss_first_lookup(*args, **kwargs):
            nonlocal calls
            calls += 1
            return CommissionReservation.objects.none() if calls == 1 else real_lock(*args, **kwargs)

        with patch.object(CommissionReservation.objects, "select_for_update", side_effect=miss_first_lookup):
            self.assert_rejected(
                self.reserve, CommissionReservationConflictError,
                wallet=self.other_wallet, driver=self.other_driver, course=course,
            )
        self.assertEqual(calls, 2)

    def test_concurrent_identical_collision_returns_existing_without_mutation(self):
        # Même simulation de collision ; aucune assertion de concurrence SQLite.
        first = self.reserve()
        before = self.snapshot()
        real_lock = CommissionReservation.objects.select_for_update
        calls = 0

        def miss_first_lookup(*args, **kwargs):
            nonlocal calls
            calls += 1
            return CommissionReservation.objects.none() if calls == 1 else real_lock(*args, **kwargs)

        with patch.object(CommissionReservation.objects, "select_for_update", side_effect=miss_first_lookup):
            retry = self.reserve()
        self.assertEqual(retry.pk, first.pk)
        self.assertEqual(calls, 2)
        self.assertEqual(self.snapshot(), before)
