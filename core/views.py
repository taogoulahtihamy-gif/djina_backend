from django.contrib.auth import get_user_model
from django.db import transaction, models
from django.db.models import Sum
from django.utils import timezone
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny, IsAdminUser, BasePermission
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import (
    OutstandingToken,
    BlacklistedToken,
)
from rest_framework.decorators import api_view
from drf_spectacular.utils import extend_schema

from rest_framework import viewsets, permissions, filters
from .models import (
    Customer,
    Driver,
    Vehicle,
    DriverDocument,
    Course,
    Payment,
    Evaluation,
    Complaint,
    Referral,
    Setting,
    Tariff,
    Notification,
)
from .pricing import haversine_distance_km
from .serializers import (
    EmailTokenObtainPairSerializer,
    ChangePasswordSerializer,
    UpdateProfileSerializer,
    UserListSerializer,
    UserSerializer,
    RegisterCustomerSerializer,
    RegisterDriverSerializer,
    AdminCreateSerializer,
    CustomerSerializer,
    DriverSerializer,
    VehicleSerializer,
    DriverDocumentSerializer,
    CourseSerializer,
    CourseCreateByCustomerSerializer,
    PaymentSerializer,
    EvaluationSerializer,
    ComplaintSerializer,
    ReferralSerializer,
    SettingSerializer,
    NotificationSerializer,
    # Action request serializers
    CourseAcceptSerializer,
    CourseCompleteSerializer,
    CourseCancelSerializer,
    DriverDocumentRejectSerializer,
    PaymentMarkPaidSerializer,
    ComplaintResolveSerializer,
    PriceEstimateQuerySerializer,
    PriceEstimateSerializer,
)

User = get_user_model()

def home(request):
    return JsonResponse({
        "message": "Djina API",
        "documentation": "/api/docs/",
        "status": "running"
    })

class UserViewSet(viewsets.ModelViewSet):
    """
    Endpoint /api/users/
    - GET    /api/users/          → liste
    - GET    /api/users/{id}/     → détail
    - DELETE /api/users/{id}/     → soft delete (ou delete)
    """

    queryset = User.objects.filter(deleted_at__isnull=True)
    serializer_class = UserListSerializer
    permission_classes = [permissions.IsAdminUser]

    filter_backends = [
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    search_fields = ["email", "phone", "first_name", "last_name"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = super().get_queryset()
        user_type = self.request.query_params.get("user_type")
        if user_type:
            qs = qs.filter(user_type=user_type)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ("1", "true", "yes"))
        return qs

    def perform_destroy(self, instance):
        # soft delete
        instance.soft_delete()

# =====================================================
# Permissions
# =====================================================
class IsCustomerUser(IsAuthenticated):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.user_type == User.UserType.CUSTOMER


class IsDriverUser(IsAuthenticated):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.user_type == User.UserType.DRIVER


class IsSuperUser(BasePermission):
    message = "Seul un Super Admin peut créer un administrateur."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)


# =====================================================
# Auth / Registration
# =====================================================

class EmailTokenObtainPairView(TokenObtainPairView):
    serializer_class = EmailTokenObtainPairSerializer

    @extend_schema(
        description="Login (JWT) avec email + password. Retourne access + refresh.",
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)
    
class AuthViewSet(viewsets.GenericViewSet):
    """
    Authentification et gestion du compte.

    Endpoints:
      POST   /auth/register-customer/
      POST   /auth/register-driver/
      GET    /auth/me/
      POST   /auth/change-password/
      POST   /auth/logout/
      GET    /auth/sessions/
      DELETE /auth/sessions/{session_id}/
      POST   /auth/sessions/close-others/
    """

    queryset = User.objects.all()

    @extend_schema(
        request=RegisterCustomerSerializer,
        responses=CustomerSerializer,
        description="Inscription client (optionnel: referral_code).",
    )
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[AllowAny],
        url_path="register-customer",
    )
    def register_customer(self, request):
        serializer = RegisterCustomerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        customer = serializer.save()
        return Response(
            CustomerSerializer(customer).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=RegisterDriverSerializer,
        responses=DriverSerializer,
        description="Inscription chauffeur (optionnel: referral_code).",
    )
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[AllowAny],
        url_path="register-driver",
    )
    def register_driver(self, request):
        serializer = RegisterDriverSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        driver = serializer.save()
        return Response(
            DriverSerializer(driver).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=UpdateProfileSerializer,
        responses=UserSerializer,
        description="Retourne ou modifie le profil de l'utilisateur connecté.",
    )
    @action(
        detail=False,
        methods=["get", "patch"],
        permission_classes=[IsAuthenticated],
        url_path="me",
    )
    def me(self, request):
        if request.method == "GET":
            return Response(
                UserSerializer(
                    request.user,
                    context={"request": request},
                ).data
            )

        serializer = UpdateProfileSerializer(
            request.user,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return Response(
            UserSerializer(
                user,
                context={"request": request},
            ).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=ChangePasswordSerializer,
        responses={200: dict},
        description=(
            "Modifie le mot de passe de l'utilisateur connecté. "
            "Tous ses refresh tokens sont révoqués."
        ),
    )
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[IsAuthenticated],
        url_path="change-password",
    )
    def change_password(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        for token in OutstandingToken.objects.filter(user=request.user):
            BlacklistedToken.objects.get_or_create(token=token)

        return Response(
            {
                "detail": (
                    "Mot de passe modifié. "
                    "Veuillez vous reconnecter."
                )
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=dict,
        responses={204: None},
        description="Révoque le refresh token de la session courante.",
    )
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[IsAuthenticated],
        url_path="logout",
    )
    def logout(self, request):
        refresh_value = request.data.get("refresh")

        if not refresh_value:
            return Response(
                {"refresh": ["Ce champ est obligatoire."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            refresh = RefreshToken(refresh_value)

            if str(refresh["user_id"]) != str(request.user.pk):
                raise PermissionDenied(
                    "Ce token n'appartient pas à l'utilisateur connecté."
                )

            refresh.blacklist()

        except TokenError:
            return Response(
                {"detail": "Refresh token invalide."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        responses={200: list},
        description="Liste les sessions JWT actives de l'utilisateur connecté.",
    )
    @action(
        detail=False,
        methods=["get"],
        permission_classes=[IsAuthenticated],
        url_path="sessions",
    )
    def sessions(self, request):
        current_sid = request.auth.get("sid") if request.auth else None

        tokens = (
            OutstandingToken.objects
            .filter(
                user=request.user,
                expires_at__gt=timezone.now(),
                blacklistedtoken__isnull=True,
            )
            .order_by("-created_at")
        )

        session_list = [
            {
                "id": token.id,
                "session_id": token.jti,
                "created_at": token.created_at,
                "expires_at": token.expires_at,
                "current": str(token.jti) == str(current_sid),
            }
            for token in tokens
        ]

        return Response(session_list)

    @extend_schema(
        responses={204: None},
        description="Ferme une session JWT précise appartenant à l'utilisateur connecté.",
    )
    @action(
        detail=False,
        methods=["delete"],
        permission_classes=[IsAuthenticated],
        url_path=r"sessions/(?P<session_id>[^/.]+)",
    )
    def close_session(self, request, session_id=None):
        token = get_object_or_404(
            OutstandingToken,
            user=request.user,
            jti=session_id,
        )

        BlacklistedToken.objects.get_or_create(token=token)

        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        responses={200: dict},
        description="Ferme toutes les autres sessions JWT de l'utilisateur connecté.",
    )
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[IsAuthenticated],
        url_path="sessions/close-others",
    )
    def close_other_sessions(self, request):
        current_sid = request.auth.get("sid") if request.auth else None

        if not current_sid:
            return Response(
                {
                    "detail": (
                        "La session actuelle ne possède pas encore "
                        "d'identifiant SID. Déconnectez-vous puis reconnectez-vous."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        tokens = (
            OutstandingToken.objects
            .filter(
                user=request.user,
                expires_at__gt=timezone.now(),
                blacklistedtoken__isnull=True,
            )
            .exclude(jti=current_sid)
        )

        closed = 0

        for token in tokens:
            _, created = BlacklistedToken.objects.get_or_create(token=token)
            if created:
                closed += 1

        return Response(
            {"closed_sessions": closed},
            status=status.HTTP_200_OK,
        )



class AdminCreateViewSet(viewsets.GenericViewSet):
    serializer_class = AdminCreateSerializer
    permission_classes = [IsAuthenticated, IsSuperUser]

    @extend_schema(
        request=AdminCreateSerializer,
        responses={201: UserSerializer},
        description="Crée un administrateur. Endpoint réservé aux Super Admins authentifiés.",
    )
    @action(detail=False, methods=["post"], url_path="create-admin")
    def create_admin(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


# =====================================================
# Profiles
# =====================================================
class CustomerViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Customer.objects.filter(deleted_at__isnull=True).select_related("user")
    serializer_class = CustomerSerializer
    permission_classes = [IsAdminUser]


class DriverViewSet(viewsets.ModelViewSet):
    queryset = Driver.objects.filter(deleted_at__isnull=True).select_related("user")
    serializer_class = DriverSerializer
    permission_classes = [IsAdminUser]

    @extend_schema(responses=DriverSerializer, description="Profil chauffeur connecté.")
    @action(detail=False, methods=["get"], permission_classes=[IsDriverUser], url_path="me")
    def me(self, request):
        driver = Driver.objects.get(user=request.user, deleted_at__isnull=True)
        return Response(self.get_serializer(driver).data)

    @extend_schema(
        request=None,
        responses=dict,
        description="Met le chauffeur online/offline. Body: {'is_online': true/false}",
    )
    @action(detail=False, methods=["patch"], permission_classes=[IsDriverUser], url_path="set-online")
    def set_online(self, request):
        driver = Driver.objects.get(user=request.user, deleted_at__isnull=True)
        is_online = bool(request.data.get("is_online", True))
        driver.is_online = is_online
        driver.save(update_fields=["is_online"])
        return Response({"is_online": driver.is_online})


# =====================================================
# Vehicles
# =====================================================
class VehicleViewSet(viewsets.ModelViewSet):
    queryset = Vehicle.objects.filter(deleted_at__isnull=True).select_related("driver", "driver__user")
    serializer_class = VehicleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        # Admin: tout
        if self.request.user.is_staff:
            return qs
        # Driver: seulement ses véhicules
        if self.request.user.user_type == User.UserType.DRIVER:
            return qs.filter(driver__user=self.request.user)
        # Customer: rien (ou lecture seule si tu veux)
        return qs.none()

    def perform_create(self, serializer):
        if self.request.user.is_staff:
            serializer.save()
            return
        # Un driver crée un véhicule pour lui-même uniquement
        if self.request.user.user_type == User.UserType.DRIVER:
            driver = Driver.objects.get(user=self.request.user, deleted_at__isnull=True)
            serializer.save(driver=driver)
            return
        raise PermissionDenied("Only drivers or admins can create vehicles.")


# =====================================================
# Driver Documents
# =====================================================
class DriverDocumentViewSet(viewsets.ModelViewSet):
    queryset = DriverDocument.objects.filter(deleted_at__isnull=True).select_related("driver", "driver__user")
    serializer_class = DriverDocumentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_staff:
            return qs
        if self.request.user.user_type == User.UserType.DRIVER:
            return qs.filter(driver__user=self.request.user)
        return qs.none()

    def perform_create(self, serializer):
        if self.request.user.user_type != User.UserType.DRIVER:
            raise PermissionDenied("Only drivers can upload driver documents.")
        driver = Driver.objects.get(user=self.request.user, deleted_at__isnull=True)
        serializer.save(driver=driver)

    @extend_schema(
        request=None,
        responses=DriverDocumentSerializer,
        description="Admin: approuve un document chauffeur.",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser], url_path="approve")
    def approve(self, request, pk=None):
        doc = self.get_object()
        doc.status = DriverDocument.DocStatus.APPROVED
        doc.reviewed_by = request.user
        doc.reviewed_at = timezone.now()
        doc.rejection_reason = None
        doc.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason"])
        return Response(self.get_serializer(doc).data)

    @extend_schema(
        request=DriverDocumentRejectSerializer,
        responses=DriverDocumentSerializer,
        description="Admin: rejette un document chauffeur avec motif.",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser], url_path="reject")
    def reject(self, request, pk=None):
        doc = self.get_object()
        serializer = DriverDocumentRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        doc.status = DriverDocument.DocStatus.REJECTED
        doc.reviewed_by = request.user
        doc.reviewed_at = timezone.now()
        doc.rejection_reason = serializer.validated_data["rejection_reason"]
        doc.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason"])
        return Response(self.get_serializer(doc).data)


# =====================================================
# Courses (Rides)
# =====================================================
class CourseViewSet(viewsets.ModelViewSet):
    queryset = Course.objects.filter(deleted_at__isnull=True).select_related(
        "customer", "customer__user", "driver", "driver__user", "vehicle"
    )
    serializer_class = CourseSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()

        if self.request.user.is_staff:
            return qs

        if self.request.user.user_type == User.UserType.CUSTOMER:
            return qs.filter(customer__user=self.request.user)

        if self.request.user.user_type == User.UserType.DRIVER:
            # Ses propres courses + les courses non assignées disponibles à l'acceptation.
            return qs.filter(
                models.Q(driver__user=self.request.user)
                | models.Q(status=Course.Status.REQUESTED, driver__isnull=True)
            )

        return qs.none()

    def get_serializer_class(self):
        if self.action == "create" and self.request.user.user_type == User.UserType.CUSTOMER:
            return CourseCreateByCustomerSerializer
        return super().get_serializer_class()

    @extend_schema(
        parameters=[PriceEstimateQuerySerializer],
        responses=PriceEstimateSerializer(many=True),
        description=(
            "Estime le prix pour chaque catégorie de service active, à partir "
            "de la distance à vol d'oiseau entre départ et destination."
        ),
    )
    @action(detail=False, methods=["get"], url_path="estimate")
    def estimate(self, request):
        query = PriceEstimateQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data

        distance_km = haversine_distance_km(
            data["departure_latitude"],
            data["departure_longitude"],
            data["destination_latitude"],
            data["destination_longitude"],
        )

        tariffs = Tariff.objects.filter(is_active=True, deleted_at__isnull=True)
        estimates = [
            {
                "service_tier": tariff.service_tier,
                "distance_km": distance_km,
                "price": tariff.price_for_distance(distance_km),
            }
            for tariff in tariffs
        ]
        return Response(PriceEstimateSerializer(estimates, many=True).data)

    @extend_schema(
        request=CourseCreateByCustomerSerializer,
        responses=CourseSerializer,
        description="Customer: crée une course (status=requested).",
    )
    def create(self, request, *args, **kwargs):
        if not request.user.is_staff and request.user.user_type != User.UserType.CUSTOMER:
            return Response({"detail": "Only customers can create courses."}, status=403)

        if request.user.is_staff:
            return super().create(request, *args, **kwargs)

        customer = Customer.objects.get(user=request.user, deleted_at__isnull=True)
        serializer = self.get_serializer(data=request.data, context={"customer": customer})
        serializer.is_valid(raise_exception=True)
        course = serializer.save()
        return Response(CourseSerializer(course).data, status=status.HTTP_201_CREATED)

    # --------- Actions métier ---------

    @extend_schema(
        request=CourseAcceptSerializer,
        responses=CourseSerializer,
        description="Driver: accepte une course (optionnel: vehicle_id).",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsDriverUser], url_path="accept")
    def accept(self, request, pk=None):
        course = self.get_object()
        if course.status != Course.Status.REQUESTED:
            return Response({"detail": "Course is not in requested state."}, status=400)

        payload = CourseAcceptSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        driver = Driver.objects.get(user=request.user, deleted_at__isnull=True, is_enabled=True)

        vehicle = None
        vehicle_id = payload.validated_data.get("vehicle_id")
        if vehicle_id:
            vehicle = Vehicle.objects.get(
                id=vehicle_id, driver=driver, deleted_at__isnull=True, is_active=True
            )

        course.driver = driver
        course.vehicle = vehicle
        course.status = Course.Status.ACCEPTED
        course.accepted_at = timezone.now()
        course.save(update_fields=["driver", "vehicle", "status", "accepted_at"])
        return Response(CourseSerializer(course).data)

    @extend_schema(
        request=None,
        responses=CourseSerializer,
        description="Driver: passe la course à 'arriving'.",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsDriverUser], url_path="arriving")
    def arriving(self, request, pk=None):
        course = self.get_object()
        if course.status != Course.Status.ACCEPTED:
            return Response({"detail": "Course is not in accepted state."}, status=400)
        if course.driver is None or course.driver.user != request.user:
            return Response({"detail": "Not your course."}, status=403)

        course.status = Course.Status.ARRIVING
        course.arriving_at = timezone.now()
        course.save(update_fields=["status", "arriving_at"])
        return Response(CourseSerializer(course).data)

    @extend_schema(
        request=None,
        responses=CourseSerializer,
        description="Driver: passe la course à 'picked_up'.",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsDriverUser], url_path="picked-up")
    def picked_up(self, request, pk=None):
        course = self.get_object()
        if course.status != Course.Status.ARRIVING:
            return Response({"detail": "Course is not in arriving state."}, status=400)
        if course.driver is None or course.driver.user != request.user:
            return Response({"detail": "Not your course."}, status=403)

        course.status = Course.Status.PICKED_UP
        course.picked_up_at = timezone.now()
        course.save(update_fields=["status", "picked_up_at"])
        return Response(CourseSerializer(course).data)

    @extend_schema(
        request=CourseCompleteSerializer,
        responses=CourseSerializer,
        description="Driver: termine la course (final_price requis).",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsDriverUser], url_path="complete")
    def complete(self, request, pk=None):
        course = self.get_object()
        if course.status != Course.Status.PICKED_UP:
            return Response({"detail": "Course is not in picked_up state."}, status=400)
        if course.driver is None or course.driver.user != request.user:
            return Response({"detail": "Not your course."}, status=403)

        payload = CourseCompleteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        course.final_price = payload.validated_data["final_price"]
        course.status = Course.Status.COMPLETED
        course.completed_at = timezone.now()
        course.save(update_fields=["final_price", "status", "completed_at"])
        return Response(CourseSerializer(course).data)

    @extend_schema(
        request=CourseCancelSerializer,
        responses=CourseSerializer,
        description="Cancel une course (customer/driver/admin selon droits).",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated], url_path="cancel")
    def cancel(self, request, pk=None):
        course = self.get_object()
        if course.status in (Course.Status.COMPLETED, Course.Status.CANCELLED):
            return Response({"detail": "Course cannot be cancelled."}, status=400)

        payload = CourseCancelSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        reason = payload.validated_data.get("reason", "")

        if request.user.is_staff:
            cancelled_by = Course.CancelledBy.ADMIN
        elif request.user.user_type == User.UserType.CUSTOMER and course.customer.user == request.user:
            cancelled_by = Course.CancelledBy.CUSTOMER
        elif request.user.user_type == User.UserType.DRIVER and course.driver and course.driver.user == request.user:
            cancelled_by = Course.CancelledBy.DRIVER
        else:
            return Response({"detail": "Not allowed to cancel this course."}, status=403)

        course.status = Course.Status.CANCELLED
        course.cancelled_at = timezone.now()
        course.cancelled_by = cancelled_by
        course.cancellation_reason = reason
        course.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancellation_reason"])

        if cancelled_by == Course.CancelledBy.CUSTOMER and course.driver is not None:
            Notification.objects.create(
                user=course.driver.user,
                course=course,
                notif_type=Notification.NotifType.COURSE,
                title="Course annulée",
                body=f"Le client a annulé la course vers {course.arrival_landmark or 'destination'}.",
            )

        return Response(CourseSerializer(course).data)


# =====================================================
# Payment
# =====================================================
class PaymentViewSet(viewsets.ModelViewSet):
    queryset = Payment.objects.filter(deleted_at__isnull=True).select_related("course", "course__customer", "course__driver")
    serializer_class = PaymentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()

        if self.request.user.is_staff:
            return qs

        if self.request.user.user_type == User.UserType.CUSTOMER:
            return qs.filter(course__customer__user=self.request.user)

        if self.request.user.user_type == User.UserType.DRIVER:
            return qs.filter(course__driver__user=self.request.user)

        return qs.none()

    @extend_schema(
        request=PaymentMarkPaidSerializer,
        responses=PaymentSerializer,
        description="Admin: marque un paiement comme payé.",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        payment = self.get_object()
        if payment.status == Payment.Status.PAID:
            return Response({"detail": "Already paid."}, status=400)

        payment.status = Payment.Status.PAID
        payment.paid_at = timezone.now()
        payment.save(update_fields=["status", "paid_at"])
        return Response(self.get_serializer(payment).data)


# =====================================================
# Evaluation (rating)
# =====================================================
class EvaluationViewSet(viewsets.ModelViewSet):
    queryset = Evaluation.objects.filter(deleted_at__isnull=True).select_related("course", "customer", "driver")
    serializer_class = EvaluationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_staff:
            return qs
        if self.request.user.user_type == User.UserType.CUSTOMER:
            return qs.filter(customer__user=self.request.user)
        if self.request.user.user_type == User.UserType.DRIVER:
            return qs.filter(driver__user=self.request.user)
        return qs.none()

    @transaction.atomic
    def perform_create(self, serializer):
        course = serializer.validated_data["course"]
        if course.driver is None:
            raise PermissionDenied("Course has no driver assigned yet.")

        if self.request.user.user_type == User.UserType.CUSTOMER:
            customer = Customer.objects.get(user=self.request.user, deleted_at__isnull=True)
        else:
            customer = course.customer

        evaluation = serializer.save(customer=customer, driver=course.driver)

        # recalcul simple
        driver = evaluation.driver
        agg = driver.evaluations.filter(deleted_at__isnull=True).aggregate(
            avg=models.Avg("rate"),
            cnt=models.Count("id"),
        )
        driver.rating_avg = agg["avg"] or 0
        driver.rating_count = agg["cnt"] or 0
        driver.save(update_fields=["rating_avg", "rating_count"])


# =====================================================
# Complaint
# =====================================================
class ComplaintViewSet(viewsets.ModelViewSet):
    queryset = Complaint.objects.filter(deleted_at__isnull=True).select_related("course", "customer", "customer__user")
    serializer_class = ComplaintSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_staff:
            return qs
        if self.request.user.user_type == User.UserType.CUSTOMER:
            return qs.filter(customer__user=self.request.user)
        return qs.none()

    def perform_create(self, serializer):
        if self.request.user.user_type != User.UserType.CUSTOMER and not self.request.user.is_staff:
            raise PermissionDenied("Only customers can create complaints.")

        if self.request.user.is_staff:
            serializer.save()
            return

        customer = Customer.objects.get(user=self.request.user, deleted_at__isnull=True)
        serializer.save(customer=customer)

    @extend_schema(
        request=ComplaintResolveSerializer,
        responses=ComplaintSerializer,
        description="Admin: résout une plainte (optionnel: resolution_note).",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser], url_path="resolve")
    def resolve(self, request, pk=None):
        obj = self.get_object()

        payload = ComplaintResolveSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        note = payload.validated_data.get("resolution_note", "")

        obj.status = Complaint.Status.RESOLVED
        obj.resolved_by = request.user
        obj.resolved_at = timezone.now()
        obj.resolution_note = note
        obj.save()
        return Response(self.get_serializer(obj).data)


# =====================================================
# Notification (read-only + mark-read)
# =====================================================
class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Notification.objects.filter(deleted_at__isnull=True)
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)

    @extend_schema(request=None, responses=NotificationSerializer, description="Marque une notification comme lue.")
    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        notif = self.get_object()
        if not notif.is_read:
            notif.is_read = True
            notif.save(update_fields=["is_read"])
        return Response(self.get_serializer(notif).data)

    @extend_schema(request=None, responses=None, description="Marque toutes les notifications de l'utilisateur comme lues.")
    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        self.get_queryset().filter(is_read=False).update(is_read=True)
        return Response({"detail": "ok"})


# =====================================================
# Referral (read-only)
# =====================================================
class ReferralViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Referral.objects.filter(deleted_at__isnull=True).select_related("owner_user", "sponsor_user")
    serializer_class = ReferralSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_staff:
            return qs
        return qs.filter(owner_user=self.request.user)


# =====================================================
# Setting (admin only)
# =====================================================
class SettingViewSet(viewsets.ModelViewSet):
    queryset = Setting.objects.filter(deleted_at__isnull=True)
    serializer_class = SettingSerializer
    permission_classes = [IsAdminUser]



@api_view(['GET'])
@extend_schema(description="Statistiques du dashboard admin")
def dashboard_stats(request):
    """Retourne les statistiques pour le dashboard admin"""
    
    if not request.user.is_staff:
        return Response({"detail": "Admin only"}, status=403)
    
    stats = {
        "total_users": User.objects.filter(deleted_at__isnull=True).count(),
        "total_drivers": Driver.objects.filter(deleted_at__isnull=True).count(),
        "total_customers": Customer.objects.filter(deleted_at__isnull=True).count(),
        "active_drivers": Driver.objects.filter(
            deleted_at__isnull=True,
            is_enabled=True,
            is_online=True
        ).count(),
        "total_courses": Course.objects.filter(deleted_at__isnull=True).count(),
        "completed_courses": Course.objects.filter(
            deleted_at__isnull=True,
            status=Course.Status.COMPLETED
        ).count(),
        "cancelled_courses": Course.objects.filter(
            deleted_at__isnull=True,
            status=Course.Status.CANCELLED
        ).count(),
        "pending_courses": Course.objects.filter(
            deleted_at__isnull=True,
            status=Course.Status.REQUESTED
        ).count(),
        "total_revenue": Payment.objects.filter(
            deleted_at__isnull=True,
            status=Payment.Status.PAID
        ).aggregate(total=Sum('final_amount'))['total'] or 0,
        "pending_complaints": Complaint.objects.filter(
            deleted_at__isnull=True,
            status=Complaint.Status.PENDING
        ).count(),
        "pending_documents": DriverDocument.objects.filter(
            deleted_at__isnull=True,
            status=DriverDocument.DocStatus.PENDING
        ).count(),
    }
    
    return Response(stats)


