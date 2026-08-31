from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction

from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from .models import (
    AdminProfile,
    Customer,
    Driver,
    DriverDocument,
    Vehicle,
    Course,
    Payment,
    Evaluation,
    Complaint,
    Referral,
    Setting,
    Tariff,
    Notification,
    Commission,
    CommissionSetting,
    CommissionSettlement,
)

from .pricing import haversine_distance_km


User = get_user_model()


# =====================================================
# ACTION REQUEST SERIALIZERS
# =====================================================

class CourseAcceptSerializer(serializers.Serializer):
    vehicle_id = serializers.IntegerField(
        required=False
    )


class CourseCompleteSerializer(serializers.Serializer):
    final_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
    )


class CourseCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
    )


class DriverDocumentRejectSerializer(serializers.Serializer):
    rejection_reason = serializers.CharField(
        required=True
    )


class PaymentMarkPaidSerializer(serializers.Serializer):
    pass


class ComplaintResolveSerializer(serializers.Serializer):
    resolution_note = serializers.CharField(
        required=False,
        allow_blank=True,
    )


class CommissionSettlementConfirmSerializer(serializers.Serializer):
    driver_id = serializers.IntegerField()
    commission_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False,
    )
    payment_mode = serializers.ChoiceField(choices=CommissionSettlement.PaymentMode.choices)
    reference = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    paid_at = serializers.DateTimeField(required=False)


# =====================================================
# USER
# =====================================================

class UserSerializer(serializers.ModelSerializer):
    is_online = serializers.SerializerMethodField()

    class Meta:
        model = User

        fields = (
            "id",
            "email",
            "phone",
            "first_name",
            "last_name",
            "profile_image",
            "user_type",
            "is_active",
            "is_staff",
            "is_superuser",
            "is_online",
            "created_at",
            "updated_at",
        )

        read_only_fields = (
            "id",
            "is_active",
            "is_staff",
            "is_superuser",
            "created_at",
            "updated_at",
        )

    def get_is_online(self, obj):
        try:
            return obj.driver_profile.is_online
        except Driver.DoesNotExist:
            return None


class UserListSerializer(serializers.ModelSerializer):
    class Meta:
        model = User

        fields = (
            "id",
            "email",
            "phone",
            "first_name",
            "last_name",
            "user_type",
            "is_active",
            "created_at",
        )


# =====================================================
# AUTHENTICATION
# =====================================================

class EmailTokenObtainPairSerializer(
    TokenObtainPairSerializer
):
    username_field = "email"

    def validate(self, attrs):
        data = super().validate(attrs)

        refresh = RefreshToken(
            data["refresh"]
        )

        session_id = str(
            refresh["jti"]
        )

        refresh["sid"] = session_id

        access = refresh.access_token
        access["sid"] = session_id

        data["refresh"] = str(
            refresh
        )

        data["access"] = str(
            access
        )

        return data


class UpdateProfileSerializer(serializers.ModelSerializer):
    first_name = serializers.CharField(
        max_length=150,
        required=True,
        allow_blank=False,
    )

    last_name = serializers.CharField(
        max_length=150,
        required=True,
        allow_blank=False,
    )

    phone = serializers.CharField(
        max_length=20,
        required=True,
        allow_blank=False,
    )

    class Meta:
        model = User

        fields = (
            "first_name",
            "last_name",
            "phone",
        )

    def validate_first_name(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError(
                "Le prénom est obligatoire."
            )

        return value

    def validate_last_name(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError(
                "Le nom est obligatoire."
            )

        return value

    def validate_phone(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError(
                "Le numéro de téléphone est obligatoire."
            )

        user = self.instance

        queryset = User.objects.filter(
            phone=value
        )

        if user:
            queryset = queryset.exclude(
                pk=user.pk
            )

        if queryset.exists():
            raise serializers.ValidationError(
                "Ce numéro de téléphone est déjà utilisé."
            )

        return value


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )

    new_password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )

    def validate_current_password(self, value):
        user = self.context[
            "request"
        ].user

        if not user.check_password(value):
            raise serializers.ValidationError(
                "Le mot de passe actuel est incorrect."
            )

        return value

    def validate_new_password(self, value):
        user = self.context[
            "request"
        ].user

        validate_password(
            value,
            user=user,
        )

        return value

    def validate(self, attrs):
        if (
            attrs["current_password"]
            == attrs["new_password"]
        ):
            raise serializers.ValidationError(
                {
                    "new_password": (
                        "Le nouveau mot de passe doit être "
                        "différent du mot de passe actuel."
                    )
                }
            )

        return attrs

    def save(self, **kwargs):
        user = self.context[
            "request"
        ].user

        user.set_password(
            self.validated_data[
                "new_password"
            ]
        )

        user.save(
            update_fields=[
                "password"
            ]
        )

        return user


# =====================================================
# REGISTRATION
# =====================================================

class RegisterCustomerSerializer(serializers.Serializer):
    email = serializers.EmailField()

    phone = serializers.CharField(
        max_length=20
    )

    password = serializers.CharField(
        write_only=True,
        min_length=6,
    )

    first_name = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    last_name = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    referral_code = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    def validate_referral_code(self, value):
        if value:
            exists = Referral.objects.filter(
                code=value,
                deleted_at__isnull=True,
            ).exists()

            if not exists:
                raise serializers.ValidationError(
                    "Invalid referral code."
                )

        return value

    @transaction.atomic
    def create(self, validated_data):
        referral_code = validated_data.pop(
            "referral_code",
            "",
        )

        password = validated_data.pop(
            "password"
        )

        user = User.objects.create_user(
            email=validated_data[
                "email"
            ],
            phone=validated_data[
                "phone"
            ],
            password=password,
            first_name=validated_data.get(
                "first_name",
                "",
            ),
            last_name=validated_data.get(
                "last_name",
                "",
            ),
            user_type=User.UserType.CUSTOMER,
            is_active=True,
        )

        customer = Customer.objects.create(
            user=user
        )

        code = f"CUST-{user.id}"

        sponsor_user = None

        if referral_code:
            sponsor_user = (
                Referral.objects.get(
                    code=referral_code,
                    deleted_at__isnull=True,
                ).owner_user
            )

        Referral.objects.create(
            code=code,
            owner_user=user,
            sponsor_user=sponsor_user,
        )

        return customer


class RegisterDriverSerializer(serializers.Serializer):
    email = serializers.EmailField()

    phone = serializers.CharField(
        max_length=20
    )

    password = serializers.CharField(
        write_only=True,
        min_length=6,
    )

    first_name = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    last_name = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    referral_code = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    def validate_referral_code(self, value):
        if value:
            exists = Referral.objects.filter(
                code=value,
                deleted_at__isnull=True,
            ).exists()

            if not exists:
                raise serializers.ValidationError(
                    "Invalid referral code."
                )

        return value

    @transaction.atomic
    def create(self, validated_data):
        referral_code = validated_data.pop(
            "referral_code",
            "",
        )

        password = validated_data.pop(
            "password"
        )

        user = User.objects.create_user(
            email=validated_data[
                "email"
            ],
            phone=validated_data[
                "phone"
            ],
            password=password,
            first_name=validated_data.get(
                "first_name",
                "",
            ),
            last_name=validated_data.get(
                "last_name",
                "",
            ),
            user_type=User.UserType.DRIVER,
            is_active=True,
        )

        driver = Driver.objects.create(
            user=user
        )

        code = f"DRV-{user.id}"

        sponsor_user = None

        if referral_code:
            sponsor_user = (
                Referral.objects.get(
                    code=referral_code,
                    deleted_at__isnull=True,
                ).owner_user
            )

        Referral.objects.create(
            code=code,
            owner_user=user,
            sponsor_user=sponsor_user,
        )

        return driver


# =====================================================
# ADMIN CREATION
# =====================================================

class AdminCreateSerializer(serializers.Serializer):
    first_name = serializers.CharField(
        max_length=150
    )

    last_name = serializers.CharField(
        max_length=150
    )

    email = serializers.EmailField()

    phone = serializers.CharField(
        max_length=20
    )

    password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )

    admin_type = serializers.ChoiceField(
        choices=AdminProfile.AdminType.choices
    )

    def validate_email(self, value):
        value = (
            User.objects
            .normalize_email(value)
            .strip()
        )

        if User.objects.filter(
            email__iexact=value
        ).exists():
            raise serializers.ValidationError(
                "Un utilisateur avec cette adresse "
                "e-mail existe déjà."
            )

        return value

    def validate_phone(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError(
                "Ce champ est obligatoire."
            )

        if User.objects.filter(
            phone=value
        ).exists():
            raise serializers.ValidationError(
                "Un utilisateur avec ce numéro "
                "de téléphone existe déjà."
            )

        return value

    def validate(self, attrs):
        candidate = User(
            email=attrs[
                "email"
            ],
            phone=attrs[
                "phone"
            ],
            first_name=attrs[
                "first_name"
            ],
            last_name=attrs[
                "last_name"
            ],
        )

        validate_password(
            attrs["password"],
            user=candidate,
        )

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        admin_type = validated_data.pop(
            "admin_type"
        )

        password = validated_data.pop(
            "password"
        )

        user = User.objects.create_user(
            **validated_data,
            password=password,
            user_type=User.UserType.ADMIN,
            is_staff=True,
            is_active=True,
            is_superuser=(
                admin_type
                == AdminProfile.AdminType.SUPER
            ),
        )

        AdminProfile.objects.create(
            user=user,
            type_of=admin_type,
        )

        return user


# =====================================================
# PROFILES
# =====================================================

class CustomerSerializer(serializers.ModelSerializer):
    user = UserSerializer(
        read_only=True
    )

    class Meta:
        model = Customer

        fields = (
            "id",
            "user",
            "created_at",
            "updated_at",
        )

        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
        )


class DriverSerializer(serializers.ModelSerializer):
    user = UserSerializer(
        read_only=True
    )

    class Meta:
        model = Driver

        fields = (
            "id",
            "user",
            "is_enabled",
            "is_online",
            "rating_avg",
            "rating_count",
            "created_at",
            "updated_at",
        )

        read_only_fields = (
            "id",
            "rating_avg",
            "rating_count",
            "created_at",
            "updated_at",
        )


# =====================================================
# VEHICLE
# =====================================================

class VehicleSerializer(serializers.ModelSerializer):
    driver_id = serializers.PrimaryKeyRelatedField(
        source="driver",
        queryset=Driver.objects.filter(
            deleted_at__isnull=True
        ),
        required=False,
        write_only=True,
    )

    driver = DriverSerializer(
        read_only=True
    )

    class Meta:
        model = Vehicle

        fields = (
            "id",
            "driver",
            "driver_id",
            "type",
            "model",
            "license_plate",
            "with_comfort",
            "image",
            "is_active",
            "created_at",
            "updated_at",
        )

        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
        )


# =====================================================
# DRIVER DOCUMENT
# =====================================================

class DriverDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = DriverDocument

        fields = (
            "id",
            "driver",
            "doc_type",
            "file",
            "status",
            "reviewed_by",
            "reviewed_at",
            "rejection_reason",
            "created_at",
            "updated_at",
        )

        read_only_fields = (
            "id",
            "driver",
            "status",
            "reviewed_by",
            "reviewed_at",
            "rejection_reason",
            "created_at",
            "updated_at",
        )


# =====================================================
# COURSE
# =====================================================

class CourseSerializer(serializers.ModelSerializer):
    customer = CustomerSerializer(
        read_only=True
    )

    driver = DriverSerializer(
        read_only=True
    )

    vehicle = VehicleSerializer(
        read_only=True
    )

    class Meta:
        model = Course

        fields = (
            "id",
            "customer",
            "driver",
            "vehicle",
            "departure_latitude",
            "departure_longitude",
            "destination_latitude",
            "destination_longitude",
            "starting_landmark",
            "arrival_landmark",
            "requested_service_tier",
            "distance_km",
            "status",
            "requested_at",
            "accepted_at",
            "arriving_at",
            "picked_up_at",
            "completed_at",
            "cancelled_at",
            "cancelled_by",
            "cancellation_reason",
            "initial_price",
            "final_price",
            "created_at",
            "updated_at",
        )

        read_only_fields = fields


class CourseCreateByCustomerSerializer(
    serializers.ModelSerializer
):
    class Meta:
        model = Course

        fields = (
            "departure_latitude",
            "departure_longitude",
            "destination_latitude",
            "destination_longitude",
            "starting_landmark",
            "arrival_landmark",
            "requested_service_tier",
        )

    def validate_requested_service_tier(
        self,
        value,
    ):
        exists = Tariff.objects.filter(
            service_tier=value,
            is_active=True,
            deleted_at__isnull=True,
        ).exists()

        if not exists:
            raise serializers.ValidationError(
                "No active tariff configured "
                "for this service tier."
            )

        return value

    def create(self, validated_data):
        customer = self.context[
            "customer"
        ]

        tariff = Tariff.objects.get(
            service_tier=validated_data[
                "requested_service_tier"
            ],
            is_active=True,
            deleted_at__isnull=True,
        )

        distance_km = haversine_distance_km(
            validated_data[
                "departure_latitude"
            ],
            validated_data[
                "departure_longitude"
            ],
            validated_data[
                "destination_latitude"
            ],
            validated_data[
                "destination_longitude"
            ],
        )

        return Course.objects.create(
            customer=customer,
            distance_km=distance_km,
            initial_price=(
                tariff.price_for_distance(
                    distance_km
                )
            ),
            **validated_data,
        )


# =====================================================
# PAYMENT
# =====================================================

class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment

        fields = (
            "id",
            "course",
            "final_amount",
            "currency",
            "payment_mode",
            "provider",
            "transaction_id",
            "status",
            "paid_at",
            "failure_reason",
            "created_at",
            "updated_at",
        )

        read_only_fields = (
            "id",
            "status",
            "paid_at",
            "failure_reason",
            "created_at",
            "updated_at",
        )


# =====================================================
# EVALUATION
# =====================================================

class EvaluationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Evaluation

        fields = (
            "id",
            "course",
            "customer",
            "driver",
            "rate",
            "comment",
            "created_at",
            "updated_at",
        )

        read_only_fields = (
            "id",
            "customer",
            "driver",
            "created_at",
            "updated_at",
        )


# =====================================================
# COMPLAINT
# =====================================================

class ComplaintResolverSerializer(
    serializers.ModelSerializer
):
    """
    Informations publiques minimales sur
    l'administrateur ayant traité la réclamation.
    """

    class Meta:
        model = User

        fields = (
            "id",
            "first_name",
            "last_name",
            "user_type",
            "is_staff",
            "is_superuser",
        )

        read_only_fields = fields


class ComplaintSerializer(serializers.ModelSerializer):
    resolved_by_user = ComplaintResolverSerializer(
        source="resolved_by",
        read_only=True,
    )

    class Meta:
        model = Complaint

        fields = (
            "id",
            "course",
            "customer",
            "description",
            "status",
            "resolved_by",
            "resolved_by_user",
            "resolved_at",
            "resolution_note",
            "created_at",
            "updated_at",
        )

        read_only_fields = (
            "id",
            "status",
            "resolved_by",
            "resolved_by_user",
            "resolved_at",
            "resolution_note",
            "created_at",
            "updated_at",
        )


# =====================================================
# NOTIFICATION
# =====================================================

class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification

        fields = (
            "id",
            "notif_type",
            "title",
            "body",
            "is_read",
            "course",
            "created_at",
        )

        read_only_fields = fields


# =====================================================
# REFERRAL
# =====================================================

class ReferralSerializer(serializers.ModelSerializer):
    owner_user = UserSerializer(
        read_only=True
    )

    sponsor_user = UserSerializer(
        read_only=True
    )

    class Meta:
        model = Referral

        fields = (
            "id",
            "code",
            "owner_user",
            "sponsor_user",
            "created_at",
            "updated_at",
        )

        read_only_fields = fields


# =====================================================
# SETTING
# =====================================================

class SettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Setting

        fields = (
            "id",
            "setting_name",
            "value",
            "created_at",
            "updated_at",
        )


class CommissionSettingSerializer(serializers.ModelSerializer):
    updated_by = UserListSerializer(read_only=True)

    class Meta:
        model = CommissionSetting
        fields = ("id", "rate", "updated_by", "effective_at", "created_at", "updated_at")
        read_only_fields = ("id", "updated_by", "effective_at", "created_at", "updated_at")

    def validate_rate(self, value):
        if value < 0 or value > 100:
            raise serializers.ValidationError("Rate must be between 0 and 100.")
        return value


class CommissionSettlementSerializer(serializers.ModelSerializer):
    confirmed_by = UserListSerializer(read_only=True)

    class Meta:
        model = CommissionSettlement
        fields = (
            "id", "driver", "total_amount", "payment_mode", "reference", "paid_at",
            "confirmed_by", "confirmed_at", "created_at", "updated_at",
        )
        read_only_fields = fields


class CommissionSerializer(serializers.ModelSerializer):
    starting_landmark = serializers.CharField(source="course.starting_landmark", read_only=True)
    arrival_landmark = serializers.CharField(source="course.arrival_landmark", read_only=True)
    completed_at = serializers.DateTimeField(source="course.completed_at", read_only=True)
    settlement_reference = serializers.CharField(source="settlement.reference", read_only=True)
    settlement_mode = serializers.CharField(source="settlement.payment_mode", read_only=True)

    class Meta:
        model = Commission
        fields = (
            "id", "course", "driver", "gross_amount", "commission_rate",
            "commission_amount", "driver_net_amount", "status", "settlement", "paid_at",
            "settlement_reference", "settlement_mode", "starting_landmark", "arrival_landmark",
            "completed_at", "created_at", "updated_at",
        )
        read_only_fields = fields

        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
        )


# =====================================================
# TARIFF
# =====================================================

class TariffSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tariff

        fields = (
            "id",
            "service_tier",
            "base_fare",
            "price_per_km",
            "min_price",
            "is_active",
            "created_at",
            "updated_at",
        )

        read_only_fields = fields


# =====================================================
# PRICE ESTIMATE
# =====================================================

class PriceEstimateQuerySerializer(serializers.Serializer):
    departure_latitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
    )

    departure_longitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
    )

    destination_latitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
    )

    destination_longitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
    )


class PriceEstimateSerializer(serializers.Serializer):
    service_tier = serializers.CharField()

    distance_km = serializers.DecimalField(
        max_digits=8,
        decimal_places=3,
    )

    price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
    )
