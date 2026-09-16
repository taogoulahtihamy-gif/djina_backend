"""Acceptation atomique : Course -> DriverWallet -> CommissionReservation."""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from core.models import CommissionReservation, Course, Driver, DriverWallet, Vehicle
from core.services.commission_service import get_current_commission_setting, quantize_money
from core.services.wallet_service import (
    CommissionReservationConflictError, CommissionReservationStateError,
    WalletNotActiveError, get_or_create_driver_wallet, reserve_commission,
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
