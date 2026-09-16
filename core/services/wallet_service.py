"""Primitives financières internes : seul ce service doit écrire wallet.balance.

Une écriture SUCCESS est immuable : toute correction doit être une nouvelle
transaction REFUND/ADJUSTMENT, jamais une modification du journal historique.
ADJUSTMENT est volontairement permis en crédit et en débit.

Les verrous de lignes nécessitent une base qui supporte select_for_update
(ex. PostgreSQL). SQLite ne permet pas de prouver cette garantie de concurrence.
"""

from decimal import Decimal, InvalidOperation, localcontext

from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from core.models import CommissionReservation, Course, Driver, DriverWallet, WalletTransaction


class WalletError(ValueError):
    """Erreur métier du portefeuille."""


class WalletNotActiveError(WalletError):
    pass


class InvalidWalletAmountError(WalletError):
    pass


class InsufficientWalletBalanceError(WalletError):
    pass


class WalletIdempotencyConflictError(WalletError):
    pass


class WalletTransactionConsistencyError(WalletError):
    pass


class CommissionReservationError(WalletError):
    pass


class CommissionReservationConflictError(CommissionReservationError):
    pass


class CommissionReservationStateError(CommissionReservationError):
    pass


_MONEY_QUANTUM = Decimal("0.01")
_MAX_MONEY = Decimal("999999999999.99")
_CREDIT_TYPES = frozenset((
    WalletTransaction.Type.TOPUP, WalletTransaction.Type.REFUND,
    WalletTransaction.Type.ADJUSTMENT,
))
_DEBIT_TYPES = frozenset((
    WalletTransaction.Type.COMMISSION, WalletTransaction.Type.ADJUSTMENT,
))


def _normalize_amount(value):
    """Accepte Decimal, int et str ; aucun float ni arrondi silencieux."""
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise InvalidWalletAmountError("Amount must be a Decimal, integer or decimal string.")
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount <= 0 or amount > _MAX_MONEY:
            raise InvalidWalletAmountError("Amount must be finite, positive and fit 14 digits.")
        with localcontext() as context:
            context.prec = 28
            normalized = amount.quantize(_MONEY_QUANTUM)
        if normalized != amount:
            raise InvalidWalletAmountError("Amount must be an exact multiple of 0.01.")
        return normalized
    except InvalidOperation as exc:
        raise InvalidWalletAmountError("Invalid decimal amount.") from exc


def get_or_create_driver_wallet(driver):
    """L'unicité OneToOne et get_or_create protègent les créations répétées."""
    with transaction.atomic():
        wallet, _ = DriverWallet.objects.get_or_create(
            driver=driver,
            defaults={
                "balance": Decimal("0.00"),
                "reserved_balance": Decimal("0.00"),
                "currency": "XAF",
            },
        )
        return wallet


def _existing_operation(existing, values):
    # Les metadata ne font pas partie de l'identité financière. Un retry
    # retourne le journal tel quel, sans modifier ses metadata historiques.
    financial_fields = (
        "wallet_id", "direction", "type", "amount", "course_id",
        "commission_id", "provider", "provider_reference", "status",
    )
    if any(getattr(existing, field) != values[field] for field in financial_fields):
        raise WalletIdempotencyConflictError(
            "Idempotency key already belongs to a different operation."
        )
    with localcontext() as context:
        context.prec = 28
        expected = (
            existing.balance_before + existing.amount
            if existing.direction == WalletTransaction.Direction.CREDIT
            else existing.balance_before - existing.amount
        )
    if existing.balance_after != expected:
        raise WalletTransactionConsistencyError("Existing ledger balances are inconsistent.")
    return existing


def _apply_operation(
    *, wallet, amount, transaction_type, idempotency_key, direction,
    course, commission, provider, provider_reference, metadata,
):
    amount = _normalize_amount(amount)
    if not isinstance(idempotency_key, str):
        raise WalletError("A non-empty idempotency key of at most 120 characters is required.")
    idempotency_key = idempotency_key.strip()
    if not idempotency_key or len(idempotency_key) > 120:
        raise WalletError("A non-empty idempotency key of at most 120 characters is required.")
    for reference in (course, commission):
        if reference is not None and reference.pk is None:
            raise WalletError("References must be saved objects.")
    values = {
        "wallet_id": wallet.pk, "amount": amount, "type": transaction_type,
        "direction": direction, "idempotency_key": idempotency_key,
        "status": WalletTransaction.Status.SUCCESS,
        "course_id": course.pk if course is not None else None,
        "commission_id": commission.pk if commission is not None else None,
        "provider": provider, "provider_reference": provider_reference,
        "metadata": {} if metadata is None else metadata,
    }
    try:
        with transaction.atomic():
            locked = DriverWallet.objects.select_for_update().get(pk=wallet.pk)
            existing = WalletTransaction.objects.filter(idempotency_key=idempotency_key).first()
            if existing is not None:
                # Relecture historique uniquement : aucun nouveau mouvement, même
                # si le wallet est depuis bloqué/fermé ou son disponible a changé.
                return _existing_operation(existing, values)
            allowed = _CREDIT_TYPES if direction == WalletTransaction.Direction.CREDIT else _DEBIT_TYPES
            if transaction_type not in allowed:
                raise WalletError("Transaction type is incompatible with direction.")
            if locked.status != DriverWallet.Status.ACTIVE:
                raise WalletNotActiveError("Wallet must be active.")
            with localcontext() as context:
                context.prec = 28
                before = locked.balance
                if direction == WalletTransaction.Direction.DEBIT:
                    if amount > before - locked.reserved_balance:
                        raise InsufficientWalletBalanceError("Insufficient available wallet balance.")
                    after = before - amount
                else:
                    after = before + amount
            if after > _MAX_MONEY:
                raise InvalidWalletAmountError("Resulting balance exceeds wallet capacity.")
            ledger_values = dict(values, balance_before=before, balance_after=after)
            # Validation des champs optionnels avant toute écriture ; l'unicité
            # reste arbitrée par la DB, y compris entre wallets différents.
            WalletTransaction(**ledger_values).full_clean(
                validate_unique=False, validate_constraints=False,
            )
            locked.balance = after
            locked.save(update_fields=["balance", "updated_at"])
            return WalletTransaction.objects.create(**ledger_values)
    except IntegrityError:
        # Hors du bloc atomic : le mouvement perdant est déjà entièrement annulé.
        # Une collision globale de clé peut survenir entre deux wallets verrouillés
        # séparément. Ne jamais absorber une autre erreur d'intégrité.
        existing = WalletTransaction.objects.filter(idempotency_key=idempotency_key).first()
        if existing is None:
            raise
        return _existing_operation(existing, values)


def credit_wallet(
    *, wallet, amount, transaction_type, idempotency_key, course=None,
    commission=None, provider=None, provider_reference=None, metadata=None,
):
    """Crédite TOPUP/REFUND/ADJUSTMENT une seule fois pour une clé globale."""
    return _apply_operation(
        wallet=wallet, amount=amount, transaction_type=transaction_type,
        idempotency_key=idempotency_key, direction=WalletTransaction.Direction.CREDIT,
        course=course, commission=commission, provider=provider,
        provider_reference=provider_reference, metadata=metadata,
    )


def debit_wallet(
    *, wallet, amount, transaction_type, idempotency_key, course=None,
    commission=None, provider=None, provider_reference=None, metadata=None,
):
    """Débite COMMISSION/ADJUSTMENT du disponible une seule fois par clé."""
    return _apply_operation(
        wallet=wallet, amount=amount, transaction_type=transaction_type,
        idempotency_key=idempotency_key, direction=WalletTransaction.Direction.DEBIT,
        course=course, commission=commission, provider=provider,
        provider_reference=provider_reference, metadata=metadata,
    )


def _require_saved_reservation_reference(instance):
    if instance.pk is None or instance._state.adding:
        raise CommissionReservationError("Reservation references must be saved objects.")


def _existing_reservation(existing, *, wallet_id, driver_id, estimated_amount):
    if (
        existing.wallet_id != wallet_id
        or existing.driver_id != driver_id
        or existing.estimated_amount != estimated_amount
    ):
        raise CommissionReservationConflictError(
            "Course already belongs to a different commission reservation."
        )
    # Un retry ne réactive jamais une réservation terminée et ne dépend pas
    # du statut actuel du wallet ou du disponible restant.
    return existing


def _is_course_reservation_collision(error):
    """Reconnaît uniquement la contrainte unique du OneToOne course."""
    cause = error.__cause__
    table = CommissionReservation._meta.db_table
    column = CommissionReservation._meta.get_field("course").column
    if connection.vendor == "sqlite":
        return (
            getattr(cause, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_UNIQUE"
            and str(cause) == f"UNIQUE constraint failed: {table}.{column}"
        )
    if connection.vendor == "postgresql":
        if getattr(cause, "sqlstate", getattr(cause, "pgcode", None)) != "23505":
            return False
        diagnostic = getattr(cause, "diag", None)
        if getattr(diagnostic, "table_name", None) != table:
            return False
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, table)
        constraint = constraints.get(getattr(diagnostic, "constraint_name", None), {})
        return constraint.get("unique", False) and constraint.get("columns") == [column]
    return False


def reserve_commission(*, wallet, driver, course, estimated_amount):
    """Réserve une fois par course, sans débit ni écriture WalletTransaction.

    Ordre des verrous : wallet, puis réservation. Le OneToOne arbitre aussi
    les tentatives concurrentes pour une même course sur des wallets distincts.
    """
    estimated_amount = _normalize_amount(estimated_amount)
    for reference in (driver, course):
        _require_saved_reservation_reference(reference)
    identity = dict(
        wallet_id=wallet.pk, driver_id=driver.pk, estimated_amount=estimated_amount,
    )
    try:
        with transaction.atomic():
            locked = DriverWallet.objects.select_for_update().get(pk=wallet.pk)
            existing = CommissionReservation.objects.select_for_update().filter(course_id=course.pk).first()
            if existing is not None:
                return _existing_reservation(existing, **identity)
            if locked.status != DriverWallet.Status.ACTIVE:
                raise WalletNotActiveError("Wallet must be active.")
            if locked.driver_id != driver.pk:
                raise CommissionReservationError("Wallet does not belong to this driver.")
            if not Driver.objects.filter(pk=driver.pk).exists():
                raise CommissionReservationError("Driver no longer exists.")
            try:
                current_course = Course.objects.get(pk=course.pk)
            except Course.DoesNotExist as exc:
                raise CommissionReservationError("Course no longer exists.") from exc
            if current_course.driver_id not in (None, driver.pk):
                raise CommissionReservationError("Course belongs to a different driver.")
            with localcontext() as context:
                context.prec = 28
                if estimated_amount > locked.balance - locked.reserved_balance:
                    raise InsufficientWalletBalanceError("Insufficient available wallet balance.")
                locked.reserved_balance += estimated_amount
            locked.save(update_fields=["reserved_balance", "updated_at"])
            return CommissionReservation.objects.create(
                **identity, course_id=course.pk, status=CommissionReservation.Status.ACTIVE,
            )
    except IntegrityError as exc:
        if not _is_course_reservation_collision(exc):
            raise
        # L'augmentation perdante est déjà rollbackée. Reprendre les verrous
        # dans le même ordre avant de relire le gagnant, sans absorber une
        # erreur d'intégrité sans réservation correspondante.
        with transaction.atomic():
            DriverWallet.objects.select_for_update().get(pk=wallet.pk)
            existing = CommissionReservation.objects.select_for_update().filter(course_id=course.pk).first()
            if existing is None:
                raise
            return _existing_reservation(existing, **identity)


def _finish_commission_reservation(*, reservation, target_status, timestamp_field):
    _require_saved_reservation_reference(reservation)
    with transaction.atomic():
        # Lecture sans verrou pour identifier le wallet depuis la DB, jamais
        # depuis l'objet reçu. Les verrous restent wallet -> réservation.
        try:
            wallet_id = CommissionReservation.objects.values_list("wallet_id", flat=True).get(pk=reservation.pk)
        except CommissionReservation.DoesNotExist as exc:
            raise CommissionReservationError("Reservation no longer exists.") from exc
        locked_wallet = DriverWallet.objects.select_for_update().get(pk=wallet_id)
        locked_reservation = CommissionReservation.objects.select_for_update().get(pk=reservation.pk)
        if locked_reservation.wallet_id != locked_wallet.pk:
            raise WalletTransactionConsistencyError("Reservation wallet changed during locking.")
        if locked_reservation.status == target_status:
            return locked_reservation
        if locked_reservation.status != CommissionReservation.Status.ACTIVE:
            raise CommissionReservationStateError("Reservation is already in a different terminal state.")
        if locked_wallet.reserved_balance < locked_reservation.estimated_amount:
            raise WalletTransactionConsistencyError("Reserved balance is smaller than the reservation.")
        with localcontext() as context:
            context.prec = 28
            locked_wallet.reserved_balance -= locked_reservation.estimated_amount
        locked_wallet.save(update_fields=["reserved_balance", "updated_at"])
        locked_reservation.status = target_status
        setattr(locked_reservation, timestamp_field, timezone.now())
        locked_reservation.save(update_fields=["status", timestamp_field, "updated_at"])
        return locked_reservation


def release_commission_reservation(*, reservation):
    """Libère la réserve, même sur un wallet inactif, sans modifier balance."""
    return _finish_commission_reservation(
        reservation=reservation, target_status=CommissionReservation.Status.RELEASED,
        timestamp_field="released_at",
    )


def consume_commission_reservation(*, reservation):
    """Libère uniquement la réserve, sans débit ni WalletTransaction.

    Le débit réel devra appartenir à une future orchestration atomique englobant
    cette primitive. Un wallet inactif peut également terminer sa réservation.
    """
    return _finish_commission_reservation(
        reservation=reservation, target_status=CommissionReservation.Status.CONSUMED,
        timestamp_field="consumed_at",
    )
