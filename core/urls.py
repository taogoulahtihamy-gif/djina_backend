# core/urls.py
from django.urls import path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from core.views_otp import OTPViewSet

from .views import (
    EmailTokenObtainPairView,  # Importer votre vue personnalisée
    AuthViewSet,
    AdminCreateViewSet,
    CustomerViewSet,
    DriverViewSet,
    UserViewSet,
    VehicleViewSet,
    DriverDocumentViewSet,
    CourseViewSet,
    PaymentViewSet,
    EvaluationViewSet,
    ComplaintViewSet,
    ReferralViewSet,
    SettingViewSet,
    NotificationViewSet,
    dashboard_stats,  # Importer la fonction des stats
)

router = DefaultRouter()
router.register(r"auth", AuthViewSet, basename="auth")
router.register(r"admin/users", AdminCreateViewSet, basename="admin-users")
router.register(r"auth/otp", OTPViewSet, basename="otp") 
router.register(r"users", UserViewSet, basename="users")
router.register(r"customers", CustomerViewSet, basename="customers")
router.register(r"drivers", DriverViewSet, basename="drivers")
router.register(r"vehicles", VehicleViewSet, basename="vehicles")
router.register(r"driver-documents", DriverDocumentViewSet, basename="driver-documents")
router.register(r"courses", CourseViewSet, basename="courses")
router.register(r"payments", PaymentViewSet, basename="payments")
router.register(r"evaluations", EvaluationViewSet, basename="evaluations")
router.register(r"complaints", ComplaintViewSet, basename="complaints")
router.register(r"referrals", ReferralViewSet, basename="referrals")
router.register(r"settings", SettingViewSet, basename="settings")
router.register(r"notifications", NotificationViewSet, basename="notifications")

# IMPORTANT: Ne pas écraser urlpatterns, utiliser +=
urlpatterns = [
    # Auth JWT - UTILISER VOTRE VUE PERSONNALISÉE
    path('auth/token/', EmailTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    # Stats dashboard
    path('admin/dashboard/stats/', dashboard_stats, name='dashboard-stats'),
]

# Ajouter les routes du router
urlpatterns += router.urls
