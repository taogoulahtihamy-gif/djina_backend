from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import Commission, CommissionSetting, CommissionSettlement, Course


MONEY_QUANTUM = Decimal("0.01")
DEFAULT_RATE = Decimal("15.00")


class CommissionSettlementError(ValueError):
    pass


def quantize_money(value):
    return Decimal(value or 0).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def get_current_commission_setting():
    setting, _ = CommissionSetting.objects.get_or_create(
        singleton_key=True,
        defaults={"rate": DEFAULT_RATE, "effective_at": timezone.now()},
    )
    return setting


def ensure_commission_for_course(course):
    if course.status != Course.Status.COMPLETED or course.driver_id is None:
        return None

    existing = Commission.objects.filter(course_id=course.pk).first()
    if existing:
        return existing

    gross_amount = quantize_money(course.final_price or course.initial_price or 0)
    rate = get_current_commission_setting().rate
    commission_amount = quantize_money(gross_amount * rate / Decimal("100"))
    driver_net_amount = quantize_money(gross_amount - commission_amount)

    try:
        with transaction.atomic():
            commission, _ = Commission.objects.get_or_create(
                course=course,
                defaults={
                    "driver_id": course.driver_id,
                    "gross_amount": gross_amount,
                    "commission_rate": rate,
                    "commission_amount": commission_amount,
                    "driver_net_amount": driver_net_amount,
                    "status": Commission.Status.PENDING,
                },
            )
            return commission
    except IntegrityError:
        return Commission.objects.get(course_id=course.pk)


@transaction.atomic
def confirm_commission_settlement(
    driver,
    commission_ids,
    payment_mode,
    reference,
    paid_at,
    confirmed_by,
):
    if not getattr(confirmed_by, "is_superuser", False):
        raise PermissionDenied("Only a super administrator can confirm a settlement.")
    if not commission_ids:
        raise CommissionSettlementError("At least one commission must be selected.")
    if payment_mode not in CommissionSettlement.PaymentMode.values:
        raise CommissionSettlementError("Invalid payment mode.")

    unique_ids = list(dict.fromkeys(commission_ids))
    commissions = list(
        Commission.objects.select_for_update()
        .filter(pk__in=unique_ids)
        .order_by("pk")
    )
    if len(commissions) != len(unique_ids):
        raise CommissionSettlementError("One or more commissions do not exist.")
    if any(commission.driver_id != driver.pk for commission in commissions):
        raise CommissionSettlementError("All commissions must belong to the selected driver.")
    if any(commission.status != Commission.Status.PENDING for commission in commissions):
        raise CommissionSettlementError("All commissions must be pending.")

    total_amount = quantize_money(sum((item.commission_amount for item in commissions), Decimal("0")))
    paid_at = paid_at or timezone.now()
    settlement = CommissionSettlement.objects.create(
        driver=driver,
        total_amount=total_amount,
        payment_mode=payment_mode,
        reference=reference or None,
        paid_at=paid_at,
        confirmed_by=confirmed_by,
        confirmed_at=timezone.now(),
    )
    for commission in commissions:
        commission.status = Commission.Status.PAID
        commission.settlement = settlement
        commission.paid_at = paid_at
        commission.updated_at = timezone.now()
    Commission.objects.bulk_update(commissions, ["status", "settlement", "paid_at", "updated_at"])
    return settlement


def commission_summary(queryset=None):
    queryset = queryset if queryset is not None else Commission.objects.all()
    values = queryset.aggregate(
        gross_course_volume=Sum("gross_amount"),
        commissions_generated=Sum("commission_amount"),
        driver_net_revenue=Sum("driver_net_amount"),
    )
    values["commissions_pending"] = queryset.filter(status=Commission.Status.PENDING).aggregate(
        total=Sum("commission_amount")
    )["total"] or Decimal("0")
    values["commissions_paid"] = queryset.filter(status=Commission.Status.PAID).aggregate(
        total=Sum("commission_amount")
    )["total"] or Decimal("0")
    for key in ("gross_course_volume", "commissions_generated", "driver_net_revenue"):
        values[key] = values[key] or Decimal("0")
    return values
