"""Acceptation atomique : Course -> DriverWallet -> CommissionReservation."""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from core.models import CommissionReservation, Course, Driver, DriverWallet, Vehicle
from core.services.commission_service import get_current_commission_setting, quantize_money
from core.services.wallet_service import (
    CommissionReservationConflictError, CommissionReservationError,
    CommissionReservationStateError, WalletNotActiveError,
    WalletTransactionConsistencyError, get_or_create_driver_wallet,
    release_commission_reservation, reserve_commission,
)


class CourseFinancialError(ValueError):
    pass


class CourseAcceptanceStateError(CourseFinancialError):
    pass


class CourseAcceptanceVehicleError(CourseFinancialError):
    pass


class CourseAcceptanceConfigurationError(CourseFinancialError):
    pass


class CourseAcceptanceReservationError(CourseFinancialError):
    pass


class CourseCancellationStateError(CourseFinancialError):
    pass


class CourseCancellationPermissionError(CourseFinancialError):
    pass


class CourseCancellationReservationError(CourseFinancialError):
    pass


def _financial_decimal(value):
    try:
        amount = Decimal(value)
        if not amount.is_finite():
            raise CourseAcceptanceConfigurationError("Invalid financial configuration.")
        return quantize_money(amount)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CourseAcceptanceConfigurationError("Invalid financial configuration.") from exc


@transaction.atomic
def accept_course_with_commission_reservation(*, course_id, driver, vehicle_id=None):
    try:
        course = Course.objects.select_for_update().get(pk=course_id, deleted_at__isnull=True)
    except Course.DoesNotExist as exc:
        raise CourseAcceptanceStateError("Course is not in requested state.") from exc
    if course.status != Course.Status.REQUESTED:
        raise CourseAcceptanceStateError("Course is not in requested state.")

    if driver.pk is None or driver._state.adding:
        raise CourseAcceptanceStateError("Driver is not active.")
    try:
        driver = Driver.objects.get(
            pk=driver.pk, deleted_at__isnull=True, is_enabled=True,
            user__is_active=True, user__deleted_at__isnull=True, user__user_type="driver",
        )
    except Driver.DoesNotExist as exc:
        raise CourseAcceptanceStateError("Driver is not active.") from exc

    vehicle = None
    if vehicle_id is not None:
        try:
            vehicle = Vehicle.objects.get(
                pk=vehicle_id, driver=driver, deleted_at__isnull=True, is_active=True,
            )
        except Vehicle.DoesNotExist as exc:
            raise CourseAcceptanceVehicleError("Invalid vehicle.") from exc

    wallet = get_or_create_driver_wallet(driver)
    gross_amount = _financial_decimal(course.initial_price)
    rate = _financial_decimal(get_current_commission_setting().rate)
    if gross_amount <= 0 or not Decimal("0") <= rate <= Decimal("100"):
        raise CourseAcceptanceConfigurationError("Invalid financial configuration.")
    estimated_amount = quantize_money(gross_amount * rate / Decimal("100"))
    if estimated_amount <= 0:
        raise CourseAcceptanceConfigurationError("Invalid financial configuration.")

    # Vérifier aussi le wallet des réservations idempotentes : la primitive
    # wallet permet leur relecture historique sur un wallet devenu inactif.
    locked_wallet = DriverWallet.objects.select_for_update().get(pk=wallet.pk)
    if locked_wallet.status != DriverWallet.Status.ACTIVE:
        raise WalletNotActiveError("Wallet must be active.")
    reservation = reserve_commission(
        wallet=locked_wallet, driver=driver, course=course, estimated_amount=estimated_amount,
    )
    if reservation.status != CommissionReservation.Status.ACTIVE:
        raise CommissionReservationStateError("Reservation is no longer active.")
    if (
        reservation.wallet_id != wallet.pk or reservation.driver_id != driver.pk
        or reservation.course_id != course.pk or reservation.estimated_amount != estimated_amount
    ):
        raise CourseAcceptanceReservationError("Invalid commission reservation.")
    snapshot = (reservation.gross_amount, reservation.commission_rate)
    if snapshot == (None, None):
        reservation.gross_amount = gross_amount
        reservation.commission_rate = rate
        reservation.save(update_fields=["gross_amount", "commission_rate", "updated_at"])
    elif snapshot != (gross_amount, rate):
        raise CommissionReservationConflictError("Conflicting commission reservation.")

    course.driver = driver
    course.vehicle = vehicle
    course.status = Course.Status.ACCEPTED
    course.accepted_at = timezone.now()
    course.save(update_fields=["driver", "vehicle", "status", "accepted_at"])
    return course

@transaction.atomic
def cancel_course_with_reservation_release(*, course_id, user, reason=""):
    """Annule une course et libère atomiquement sa réservation éventuelle.

    Ordre global :
    Course -> DriverWallet -> CommissionReservation.

    Les anciennes courses sans réservation restent annulables.
    """
    try:
        course = (
            Course.objects.select_for_update()
            .select_related("customer__user", "driver__user")
            .get(pk=course_id, deleted_at__isnull=True)
        )
    except Course.DoesNotExist as exc:
        raise CourseCancellationStateError(
            "Course cannot be cancelled."
        ) from exc

    if course.status in (
        Course.Status.COMPLETED,
        Course.Status.CANCELLED,
    ):
        raise CourseCancellationStateError(
            "Course cannot be cancelled."
        )

    if getattr(user, "is_staff", False):
        cancelled_by = Course.CancelledBy.ADMIN

    elif (
        getattr(user, "user_type", None) == "customer"
        and course.customer.user_id == user.pk
    ):
        cancelled_by = Course.CancelledBy.CUSTOMER

    elif (
        getattr(user, "user_type", None) == "driver"
        and course.driver_id is not None
        and course.driver.user_id == user.pk
    ):
        cancelled_by = Course.CancelledBy.DRIVER

    else:
        raise CourseCancellationPermissionError(
            "Not allowed to cancel this course."
        )

    reservation = CommissionReservation.objects.filter(
        course_id=course.pk
    ).first()

    if reservation is not None:
        try:
            release_commission_reservation(
                reservation=reservation
            )
        except (
            CommissionReservationError,
            WalletTransactionConsistencyError,
        ) as exc:
            raise CourseCancellationReservationError(
                "Commission reservation cannot be released."
            ) from exc

    course.status = Course.Status.CANCELLED
    course.cancelled_at = timezone.now()
    course.cancelled_by = cancelled_by
    course.cancellation_reason = reason or ""

    course.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancelled_by",
            "cancellation_reason",
        ]
    )

    return course, cancelled_by


class CourseCompletionStateError(CourseFinancialError):
    pass


class CourseCompletionPermissionError(CourseFinancialError):
    pass


class CourseCompletionFinancialError(CourseFinancialError):
    pass


@transaction.atomic
def complete_course_with_wallet_commission(*, course_id, user):
    """Termine atomiquement une course et encaisse la commission réservée.

    Ordre financier global :
        Course -> DriverWallet -> CommissionReservation -> Commission

    Le prix et le taux proviennent exclusivement du snapshot créé lors
    de l'acceptation. Aucun montant fourni par l'application chauffeur
    n'est utilisé.
    """
    from core.models import (
        Commission,
        CommissionSettlement,
        WalletTransaction,
    )
    from core.services.wallet_service import (
        CommissionReservationError,
        WalletError,
        settle_commission_reservation,
    )

    try:
        course = (
            Course.objects.select_for_update()
            .select_related("driver__user")
            .get(pk=course_id, deleted_at__isnull=True)
        )
    except Course.DoesNotExist as exc:
        raise CourseCompletionStateError(
            "Course is not in picked_up state."
        ) from exc

    if course.status != Course.Status.PICKED_UP:
        raise CourseCompletionStateError(
            "Course is not in picked_up state."
        )

    if (
        course.driver_id is None
        or course.driver.user_id != getattr(user, "pk", None)
    ):
        raise CourseCompletionPermissionError(
            "Not your course."
        )

    try:
        reservation = CommissionReservation.objects.get(
            course_id=course.pk
        )
    except CommissionReservation.DoesNotExist as exc:
        raise CourseCompletionFinancialError(
            "Commission reservation is missing."
        ) from exc

    if reservation.status != CommissionReservation.Status.ACTIVE:
        raise CourseCompletionFinancialError(
            "Commission reservation is not active."
        )

    if (
        reservation.driver_id != course.driver_id
        or reservation.gross_amount is None
        or reservation.commission_rate is None
    ):
        raise CourseCompletionFinancialError(
            "Invalid commission reservation."
        )

    gross_amount = _financial_decimal(
        reservation.gross_amount
    )
    rate = _financial_decimal(
        reservation.commission_rate
    )
    commission_amount = _financial_decimal(
        reservation.estimated_amount
    )

    if (
        gross_amount <= 0
        or commission_amount <= 0
        or rate <= 0
        or rate > Decimal("100")
    ):
        raise CourseCompletionFinancialError(
            "Invalid commission reservation."
        )

    expected_commission = quantize_money(
        gross_amount * rate / Decimal("100")
    )

    if commission_amount != expected_commission:
        raise CourseCompletionFinancialError(
            "Commission reservation amount is inconsistent."
        )

    driver_net_amount = quantize_money(
        gross_amount - commission_amount
    )

    if driver_net_amount < 0:
        raise CourseCompletionFinancialError(
            "Invalid commission reservation."
        )

    commission = Commission.objects.filter(
        course_id=course.pk
    ).first()

    if commission is None:
        commission = Commission.objects.create(
            course=course,
            driver=course.driver,
            gross_amount=gross_amount,
            commission_rate=rate,
            commission_amount=commission_amount,
            driver_net_amount=driver_net_amount,
            status=Commission.Status.PENDING,
        )
    else:
        expected_identity = (
            course.driver_id,
            gross_amount,
            rate,
            commission_amount,
            driver_net_amount,
        )
        actual_identity = (
            commission.driver_id,
            commission.gross_amount,
            commission.commission_rate,
            commission.commission_amount,
            commission.driver_net_amount,
        )

        if (
            actual_identity != expected_identity
            or commission.status != Commission.Status.PENDING
            or commission.settlement_id is not None
        ):
            raise CourseCompletionFinancialError(
                "Existing commission is inconsistent."
            )

    idempotency_key = f"course:{course.pk}:commission"

    try:
        wallet_transaction = settle_commission_reservation(
            reservation=reservation,
            commission=commission,
            idempotency_key=idempotency_key,
        )
    except (WalletError, CommissionReservationError) as exc:
        raise CourseCompletionFinancialError(
            "Commission could not be settled."
        ) from exc

    if (
        wallet_transaction.type != WalletTransaction.Type.COMMISSION
        or wallet_transaction.direction != WalletTransaction.Direction.DEBIT
        or wallet_transaction.status != WalletTransaction.Status.SUCCESS
        or wallet_transaction.amount != commission_amount
        or wallet_transaction.course_id != course.pk
        or wallet_transaction.commission_id != commission.pk
    ):
        raise CourseCompletionFinancialError(
            "Invalid wallet commission transaction."
        )

    paid_at = timezone.now()

    settlement = CommissionSettlement.objects.create(
        driver=course.driver,
        total_amount=commission_amount,
        payment_mode=CommissionSettlement.PaymentMode.WALLET,
        reference=idempotency_key,
        paid_at=paid_at,
        confirmed_by=None,
        wallet_transaction=wallet_transaction,
        confirmed_at=paid_at,
    )

    commission.status = Commission.Status.PAID
    commission.settlement = settlement
    commission.paid_at = paid_at
    commission.save(
        update_fields=[
            "status",
            "settlement",
            "paid_at",
            "updated_at",
        ]
    )

    # Prix autoritaire : snapshot gelé lors de l'acceptation.
    course.final_price = gross_amount
    course.status = Course.Status.COMPLETED
    course.completed_at = paid_at
    course.save(
        update_fields=[
            "final_price",
            "status",
            "completed_at",
        ]
    )

    return course
