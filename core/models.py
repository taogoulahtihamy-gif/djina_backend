from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models import Q
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils import timezone


# ---------------------------
# Base abstraite (timestamps + soft delete)
# ---------------------------
class TimeStampedSoftDeleteModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        abstract = True

    def soft_delete(self):
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])


# ---------------------------
# User + Manager
# ---------------------------
class CustomUserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, phone, password=None, **extra_fields):
        if not email:
            raise ValueError("The Email field must be set")
        if not phone:
            raise ValueError("The Phone field must be set")

        email = self.normalize_email(email)
        user = self.model(email=email, phone=phone, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        return self.create_user(email, phone, password, **extra_fields)


class CustomUser(AbstractUser, TimeStampedSoftDeleteModel):
    """
    On supprime username, on utilise email comme identifiant.
    On garde first_name / last_name (hérités de AbstractUser).
    """
    username = None
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, unique=True)

    # si tu veux vraiment stocker une URL, garde CharField.
    # si tu veux stocker un fichier, utilise ImageField (Pillow requis).
    profile_image = models.ImageField(upload_to="profiles/", blank=True, null=True)
    
    phone_verified = models.BooleanField(default=False, db_index=True, help_text="True si le numéro de téléphone a été vérifié par OTP.")

    class UserType(models.TextChoices):
        ADMIN = "admin", "Admin"
        CUSTOMER = "customer", "Customer"
        DRIVER = "driver", "Driver"

    user_type = models.CharField(
        max_length=10,
        choices=UserType.choices,
        default=UserType.CUSTOMER,
        db_index=True,
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["phone"]

    objects = CustomUserManager()

    def __str__(self):
        return self.email


# ---------------------------
# Admin profile (optionnel)
# ---------------------------
class AdminProfile(TimeStampedSoftDeleteModel):
    class AdminType(models.TextChoices):
        SUPER = "super", "Super"
        SIMPLE = "simple", "Simple"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="admin_profile")
    type_of = models.CharField(max_length=10, choices=AdminType.choices)

    def __str__(self):
        return f"AdminProfile({self.user.email}, {self.type_of})"


# ---------------------------
# Parrainage factorisé
# ---------------------------
class Referral(TimeStampedSoftDeleteModel):
    """
    Un code par profil (customer OU driver).
    On évite la duplication sponsor_code / sponsor dans 2 tables.
    """
    code = models.CharField(max_length=50, unique=True, db_index=True)
    owner_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="referral",
        help_text="Utilisateur propriétaire du code de parrainage",
    )
    sponsor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referrals_sponsored",
        help_text="Utilisateur parrain (celui qui a invité)",
    )

    def __str__(self):
        return f"{self.code}"


# ---------------------------
# Customer / Driver Profiles
# ---------------------------
class Customer(TimeStampedSoftDeleteModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="customer_profile")

    def __str__(self):
        return f"Customer({self.user.email})"


class Driver(TimeStampedSoftDeleteModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="driver_profile")
    is_enabled = models.BooleanField(default=True, db_index=True)

    # champs utiles
    is_online = models.BooleanField(default=False, db_index=True)
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=0)  # ex: 4.75
    rating_count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Driver({self.user.email})"


class DriverDocument(TimeStampedSoftDeleteModel):
    """
    Permet la validation des docs chauffeur par un admin.
    """
    class DocType(models.TextChoices):
        DRIVING_LICENSE = "driving_license", "Driving License"
        INSURANCE = "insurance", "Insurance"
        ID_CARD = "id_card", "ID Card"
        OTHER = "other", "Other"

    class DocStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=30, choices=DocType.choices, db_index=True)
    file = models.FileField(upload_to="driver_documents/")
    status = models.CharField(max_length=10, choices=DocStatus.choices, default=DocStatus.PENDING, db_index=True)

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_driver_documents",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["driver", "doc_type"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_driver_doctype_active",
            )
        ]

    def __str__(self):
        return f"{self.driver.user.email} - {self.doc_type} ({self.status})"


# ---------------------------
# Vehicle
# ---------------------------
class Vehicle(TimeStampedSoftDeleteModel):
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="vehicles")

    class VehicleType(models.TextChoices):
        CAR = "car", "Car"
        MOTORBIKE = "motorbike", "Motorbike"
        VAN = "van", "Van"
        OTHER = "other", "Other"

    type = models.CharField(max_length=20, choices=VehicleType.choices, db_index=True)
    model = models.CharField(max_length=80)
    license_plate = models.CharField(max_length=20, unique=True)
    with_comfort = models.BooleanField(default=False, db_index=True)
    image = models.ImageField(upload_to="vehicles/", blank=True, null=True)

    is_active = models.BooleanField(default=True, db_index=True)

    def __str__(self):
        return f"{self.license_plate} ({self.type})"


# ---------------------------
# Tariff (grille tarifaire configurable par catégorie de service)
# ---------------------------
class ServiceTier(models.TextChoices):
    ECONOMY = "economy", "Economy"
    CONFORT = "confort", "Confort"
    CONFORT_PLUS = "confort_plus", "Confort +"


class Tariff(TimeStampedSoftDeleteModel):
    service_tier = models.CharField(
        max_length=20, choices=ServiceTier.choices, unique=True, db_index=True
    )
    base_fare = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Prix de prise en charge, appliqué quelle que soit la distance.",
    )
    price_per_km = models.DecimalField(max_digits=10, decimal_places=2)
    min_price = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Prix minimum facturé, même pour une très courte distance.",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    def __str__(self):
        return f"{self.get_service_tier_display()} ({self.price_per_km}/km)"

    def price_for_distance(self, distance_km):
        price = self.base_fare + (self.price_per_km * Decimal(str(distance_km)))
        return max(price, self.min_price).quantize(Decimal("0.01"))


# ---------------------------
# Course (Ride)
# ---------------------------
class Course(TimeStampedSoftDeleteModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        ACCEPTED = "accepted", "Accepted"
        ARRIVING = "arriving", "Arriving"
        PICKED_UP = "picked_up", "Picked up"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class CancelledBy(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        DRIVER = "driver", "Driver"
        ADMIN = "admin", "Admin"
        SYSTEM = "system", "System"

    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, null=True, blank=True, related_name="courses")
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="courses")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True, related_name="courses")

    # catégorie de service demandée par le client (détermine le tarif appliqué)
    requested_service_tier = models.CharField(
        max_length=20, choices=ServiceTier.choices, default=ServiceTier.ECONOMY, db_index=True
    )
    distance_km = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True,
        help_text="Distance à vol d'oiseau entre départ et destination, calculée à la création.",
    )

    # coordonnées
    departure_latitude = models.DecimalField(max_digits=9, decimal_places=6)
    departure_longitude = models.DecimalField(max_digits=9, decimal_places=6)
    destination_latitude = models.DecimalField(max_digits=9, decimal_places=6)
    destination_longitude = models.DecimalField(max_digits=9, decimal_places=6)

    starting_landmark = models.CharField(max_length=255)
    arrival_landmark = models.CharField(max_length=255)

    # statut + timestamps métier
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.REQUESTED, db_index=True)

    requested_at = models.DateTimeField(default=timezone.now)
    accepted_at = models.DateTimeField(null=True, blank=True)
    arriving_at = models.DateTimeField(null=True, blank=True)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    cancelled_by = models.CharField(max_length=10, choices=CancelledBy.choices, null=True, blank=True)
    cancellation_reason = models.TextField(null=True, blank=True)

    # prix
    initial_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    final_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        indexes = [
            models.Index(fields=["status", "requested_at"]),
            models.Index(fields=["customer", "requested_at"]),
            models.Index(fields=["driver", "requested_at"]),
        ]

    def __str__(self):
        return f"Course({self.id}) {self.status}"


# ---------------------------
# Payment
# ---------------------------
class Payment(TimeStampedSoftDeleteModel):
    class PaymentMode(models.TextChoices):
        CASH = "cash", "Cash"
        MOBILE_MONEY = "mobile_money", "Mobile Money"
        VISA = "visa", "Visa"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"
        FAILED = "failed", "Failed"

    course = models.OneToOneField(Course, on_delete=models.CASCADE, related_name="payment")

    final_amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=5, default="XAF")

    payment_mode = models.CharField(max_length=20, choices=PaymentMode.choices, db_index=True)
    provider = models.CharField(max_length=30, null=True, blank=True)  # ex: AirtelMoney, TigoCash, Stripe...
    transaction_id = models.CharField(max_length=100, null=True, blank=True, unique=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"Payment(Course={self.course_id}, {self.status})"


# ---------------------------
# Commissions Djina
# ---------------------------
class CommissionSetting(models.Model):
    rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("15.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commission_settings_updated",
    )
    effective_at = models.DateTimeField(default=timezone.now)
    singleton_key = models.BooleanField(default=True, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(rate__gte=0, rate__lte=100),
                name="commission_setting_rate_between_0_100",
            ),
        ]

    def __str__(self):
        return f"CommissionSetting({self.rate}%)"


class CommissionSettlement(models.Model):
    class PaymentMode(models.TextChoices):
        CASH = "cash", "Cash"
        AIRTEL_MONEY = "airtel_money", "Airtel Money"
        MOOV_MONEY = "moov_money", "Moov Money"
        BANK_TRANSFER = "bank_transfer", "Bank transfer"
        WALLET = "wallet", "Wallet"

    driver = models.ForeignKey(Driver, on_delete=models.PROTECT, related_name="commission_settlements")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_mode = models.CharField(max_length=20, choices=PaymentMode.choices)
    reference = models.CharField(max_length=100, null=True, blank=True)
    paid_at = models.DateTimeField()
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="confirmed_commission_settlements",
        null=True,
        blank=True,
    )
    wallet_transaction = models.OneToOneField(
        "WalletTransaction", on_delete=models.PROTECT, null=True, blank=True,
        related_name="commission_settlement",
    )
    confirmed_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(total_amount__gte=0),
                name="commission_settlement_total_non_negative",
            ),
            models.CheckConstraint(
                condition=(
                    (~Q(payment_mode="wallet") & Q(confirmed_by__isnull=False, wallet_transaction__isnull=True))
                    | Q(payment_mode="wallet", confirmed_by__isnull=True, wallet_transaction__isnull=False)
                ),
                name="commission_settlement_source_consistent",
            ),
        ]

    def __str__(self):
        return f"CommissionSettlement({self.pk}, driver={self.driver_id})"


class Commission(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"

    course = models.OneToOneField(Course, on_delete=models.PROTECT, related_name="commission")
    driver = models.ForeignKey(Driver, on_delete=models.PROTECT, related_name="commissions")
    gross_amount = models.DecimalField(max_digits=12, decimal_places=2)
    commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    commission_amount = models.DecimalField(max_digits=12, decimal_places=2)
    driver_net_amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    settlement = models.ForeignKey(
        CommissionSettlement,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="commissions",
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(gross_amount__gte=0), name="commission_gross_non_negative"),
            models.CheckConstraint(condition=Q(commission_amount__gte=0), name="commission_amount_non_negative"),
            models.CheckConstraint(condition=Q(driver_net_amount__gte=0), name="commission_net_non_negative"),
            models.CheckConstraint(
                condition=Q(commission_rate__gte=0, commission_rate__lte=100),
                name="commission_rate_between_0_100",
            ),
            models.CheckConstraint(
                condition=Q(status="pending", settlement__isnull=True) | Q(status="paid", settlement__isnull=False),
                name="paid_commission_requires_settlement",
            ),
        ]

    def __str__(self):
        return f"Commission(Course={self.course_id}, {self.status})"


# ---------------------------
# Evaluation (1 course = 1 évaluation)
# ---------------------------
class Evaluation(TimeStampedSoftDeleteModel):
    course = models.OneToOneField(Course, on_delete=models.CASCADE, related_name="evaluation")
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="evaluations")
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="evaluations")

    rate = models.PositiveSmallIntegerField()  # tu peux ajouter des validateurs 1..5
    comment = models.TextField(blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=["driver", "created_at"]),
            models.Index(fields=["customer", "created_at"]),
        ]

    def __str__(self):
        return f"Evaluation(Course={self.course_id}, Rate={self.rate})"


# ---------------------------
# Complaint (1 course = 1 réclamation par défaut)
# ---------------------------
class Complaint(TimeStampedSoftDeleteModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RESOLVED = "resolved", "Resolved"
        REJECTED = "rejected", "Rejected"

    course = models.OneToOneField(Course, on_delete=models.CASCADE, related_name="complaint")
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="complaints")

    description = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)

    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_complaints",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"Complaint(Course={self.course_id}, {self.status})"


# ---------------------------
# Settings simples (garde-le si tu veux)
# ---------------------------
class Setting(TimeStampedSoftDeleteModel):
    setting_name = models.CharField(max_length=100, unique=True)
    value = models.TextField()

    def __str__(self):
        return self.setting_name


# ---------------------------
# Notification
# ---------------------------
class Notification(TimeStampedSoftDeleteModel):
    class NotifType(models.TextChoices):
        COURSE = "course", "Course"
        SYSTEM = "system", "System"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    course = models.ForeignKey(
        Course, on_delete=models.SET_NULL, null=True, blank=True, related_name="notifications"
    )

    notif_type = models.CharField(max_length=10, choices=NotifType.choices, default=NotifType.SYSTEM)
    title = models.CharField(max_length=150)
    body = models.TextField(blank=True)
    is_read = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Notification({self.user_id}, {self.title})"


# ---------------------------
# Portefeuille prépayé chauffeur
# ---------------------------
class DriverWallet(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        BLOCKED = "blocked", "Blocked"
        CLOSED = "closed", "Closed"

    driver = models.OneToOneField(Driver, on_delete=models.PROTECT, related_name="wallet")
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    reserved_balance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    currency = models.CharField(max_length=3, default="XAF")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def available_balance(self):
        return self.balance - self.reserved_balance

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(balance__gte=0), name="wallet_balance_non_negative"),
            models.CheckConstraint(condition=Q(reserved_balance__gte=0), name="wallet_reserved_non_negative"),
            models.CheckConstraint(
                condition=Q(reserved_balance__lte=models.F("balance")),
                name="wallet_reserved_lte_balance",
            ),
        ]


class WalletTransaction(models.Model):
    """Journal financier destiné à être immuable dans la future couche service."""

    class Type(models.TextChoices):
        TOPUP = "topup", "Top up"
        COMMISSION = "commission", "Commission"
        REFUND = "refund", "Refund"
        ADJUSTMENT = "adjustment", "Adjustment"

    class Direction(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        REVERSED = "reversed", "Reversed"

    wallet = models.ForeignKey(DriverWallet, on_delete=models.PROTECT, related_name="transactions")
    type = models.CharField(max_length=10, choices=Type.choices)
    direction = models.CharField(max_length=6, choices=Direction.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    balance_before = models.DecimalField(max_digits=14, decimal_places=2)
    balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    course = models.ForeignKey(
        Course, on_delete=models.PROTECT, null=True, blank=True, related_name="wallet_transactions",
    )
    commission = models.ForeignKey(
        Commission, on_delete=models.PROTECT, null=True, blank=True, related_name="wallet_transactions",
    )
    provider = models.CharField(max_length=30, null=True, blank=True)
    provider_reference = models.CharField(max_length=120, null=True, blank=True)
    idempotency_key = models.CharField(max_length=120, unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="wallet_tx_amount_positive"),
            models.CheckConstraint(condition=Q(balance_before__gte=0), name="wallet_tx_before_non_negative"),
            models.CheckConstraint(condition=Q(balance_after__gte=0), name="wallet_tx_after_non_negative"),
        ]
        indexes = [
            models.Index(fields=["wallet", "created_at"], name="wallet_tx_wallet_created_idx"),
            models.Index(fields=["type", "created_at"], name="wallet_tx_type_created_idx"),
            models.Index(fields=["status", "created_at"], name="wallet_tx_status_created_idx"),
        ]


class WalletTopUp(models.Model):
    class Provider(models.TextChoices):
        AIRTEL_MONEY = "airtel_money", "Airtel Money"
        MOOV_MONEY = "moov_money", "Moov Money"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    wallet = models.ForeignKey(DriverWallet, on_delete=models.PROTECT, related_name="topups")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="XAF")
    provider = models.CharField(max_length=20, choices=Provider.choices)
    phone = models.CharField(max_length=20)
    provider_reference = models.CharField(max_length=120, null=True, blank=True)
    provider_status = models.CharField(
        max_length=50,
        blank=True,
        default="",
    )
    initiated_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    idempotency_key = models.CharField(max_length=120, unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    requested_at = models.DateTimeField(default=timezone.now)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="wallet_topup_amount_positive"),
            models.UniqueConstraint(
                fields=["provider", "provider_reference"],
                condition=Q(provider_reference__isnull=False) & ~Q(provider_reference=""),
                name="wallet_topup_provider_ref_unique",
            ),
        ]


class CommissionReservation(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CONSUMED = "consumed", "Consumed"
        RELEASED = "released", "Released"

    wallet = models.ForeignKey(DriverWallet, on_delete=models.PROTECT, related_name="commission_reservations")
    course = models.OneToOneField(Course, on_delete=models.PROTECT, related_name="commission_reservation")
    driver = models.ForeignKey(Driver, on_delete=models.PROTECT, related_name="commission_reservations")
    estimated_amount = models.DecimalField(max_digits=14, decimal_places=2)
    # Nullable pendant la transition ; aucun snapshot financier inventé.
    gross_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(estimated_amount__gt=0), name="commission_res_amount_positive"),
            models.CheckConstraint(
                condition=(
                    Q(gross_amount__isnull=True, commission_rate__isnull=True)
                    | Q(gross_amount__isnull=False, commission_rate__isnull=False)
                ),
                name="commission_res_snapshot_complete",
            ),
            models.CheckConstraint(
                condition=Q(gross_amount__isnull=True) | Q(gross_amount__gte=0),
                name="commission_res_gross_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(commission_rate__isnull=True) | Q(commission_rate__gte=0, commission_rate__lte=100),
                name="commission_res_rate_between_0_100",
            ),
        ]
