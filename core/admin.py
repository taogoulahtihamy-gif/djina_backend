from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from django.utils import timezone
from django import forms

from .models import (
    CustomUser,
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
)


# =====================================================
# FORMULAIRES PERSONNALISÉS
# =====================================================
class CustomUserCreationForm(UserCreationForm):
    """Formulaire pour créer un nouvel utilisateur"""
    
    class Meta:
        model = CustomUser
        fields = ('email', 'phone', 'first_name', 'last_name', 'user_type')
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            # Sauvegarder les relations many-to-many APRÈS la création de l'objet
            self.save_m2m()
        return user


class CustomUserChangeForm(UserChangeForm):
    """Formulaire pour modifier un utilisateur existant"""
    
    class Meta:
        model = CustomUser
        fields = '__all__'


# =====================================================
# MIXINS
# =====================================================
class SoftDeleteAdminMixin:
    readonly_fields = ("created_at", "updated_at", "deleted_at")
    actions = ["soft_delete_selected"]

    @admin.action(description="Soft delete selected items")
    def soft_delete_selected(self, request, queryset):
        queryset.update(deleted_at=timezone.now())


# =====================================================
# USER ADMIN - VERSION CORRIGÉE
# =====================================================
@admin.register(CustomUser)
class CustomUserAdmin(BaseUserAdmin):
    # Formulaires personnalisés
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    
    # Champs affichés dans la liste
    list_display = (
        "email",
        "phone",
        "first_name",
        "last_name",
        "user_type",
        "is_active",
        "is_staff",
        "created_at",
    )
    
    list_filter = ("user_type", "is_active", "is_staff", "created_at")
    search_fields = ("email", "phone", "first_name", "last_name")
    ordering = ("-created_at",)
    
    # IMPORTANT: fieldsets pour l'AJOUT (ne pas inclure groups et user_permissions)
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'phone', 'password1', 'password2'),
        }),
        ('Informations personnelles', {
            'fields': ('first_name', 'last_name', 'user_type', 'profile_image'),
        }),
        ('Permissions', {
            'fields': ('is_active', 'is_staff', 'is_superuser'),
            # NE PAS inclure groups et user_permissions ici
        }),
    )
    
    # fieldsets pour la MODIFICATION (inclure groups et user_permissions)
    fieldsets = (
        ('Identification', {
            'fields': ('email', 'phone', 'password')
        }),
        ('Informations personnelles', {
            'fields': ('first_name', 'last_name', 'profile_image', 'user_type')
        }),
        ('Permissions', {
            'fields': (
                'is_active',
                'is_staff',
                'is_superuser',
                'groups',  # OK en modification
                'user_permissions'  # OK en modification
            ),
        }),
        ('Dates importantes', {
            'fields': ('last_login', 'created_at', 'updated_at', 'deleted_at'),
        }),
    )
    
    readonly_fields = ('last_login', 'created_at', 'updated_at', 'deleted_at')
    
    # IMPORTANT: Pour les relations many-to-many
    # filter_horizontal = ('groups', 'user_permissions')


# =====================================================
# ADMIN PROFILE
# =====================================================
@admin.register(AdminProfile)
class AdminProfileAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("user", "type_of", "created_at")
    list_filter = ("type_of",)
    search_fields = ("user__email",)


# =====================================================
# REFERRAL
# =====================================================
@admin.register(Referral)
class ReferralAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("code", "owner_user", "sponsor_user", "created_at")
    search_fields = ("code", "owner_user__email", "sponsor_user__email")
    list_filter = ("created_at",)


# =====================================================
# CUSTOMER / DRIVER
# =====================================================
@admin.register(Customer)
class CustomerAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("user", "created_at")
    search_fields = ("user__email", "user__phone")


class DriverDocumentInline(admin.TabularInline):
    model = DriverDocument
    extra = 0
    readonly_fields = ("reviewed_at", "reviewed_by")
    fields = ("doc_type", "file", "status", "reviewed_by", "reviewed_at", "rejection_reason")


class VehicleInline(admin.TabularInline):
    model = Vehicle
    extra = 0
    fields = ("type", "model", "license_plate", "with_comfort", "is_active")


@admin.register(Driver)
class DriverAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = (
        "user",
        "is_enabled",
        "is_online",
        "rating_avg",
        "rating_count",
        "created_at",
    )
    list_filter = ("is_enabled", "is_online", "created_at")
    search_fields = ("user__email", "user__phone")
    inlines = [VehicleInline, DriverDocumentInline]


# =====================================================
# DRIVER DOCUMENT
# =====================================================
@admin.register(DriverDocument)
class DriverDocumentAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("driver", "doc_type", "status", "reviewed_by", "reviewed_at")
    list_filter = ("doc_type", "status")
    search_fields = ("driver__user__email",)
    actions = ["approve_documents", "reject_documents"]

    @admin.action(description="Approve selected documents")
    def approve_documents(self, request, queryset):
        queryset.update(
            status=DriverDocument.DocStatus.APPROVED,
            reviewed_at=timezone.now(),
            reviewed_by=request.user,
        )

    @admin.action(description="Reject selected documents")
    def reject_documents(self, request, queryset):
        queryset.update(
            status=DriverDocument.DocStatus.REJECTED,
            reviewed_at=timezone.now(),
            reviewed_by=request.user,
        )


# =====================================================
# VEHICLE
# =====================================================
@admin.register(Vehicle)
class VehicleAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = (
        "license_plate",
        "driver",
        "type",
        "model",
        "with_comfort",
        "is_active",
    )
    list_filter = ("type", "with_comfort", "is_active")
    search_fields = ("license_plate", "driver__user__email")


# =====================================================
# TARIFF
# =====================================================
@admin.register(Tariff)
class TariffAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = (
        "service_tier",
        "base_fare",
        "price_per_km",
        "min_price",
        "is_active",
    )
    list_filter = ("is_active",)
    search_fields = ("service_tier",)


# =====================================================
# COURSE (RIDE)
# =====================================================
@admin.register(Course)
class CourseAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "customer",
        "driver",
        "status",
        "requested_service_tier",
        "distance_km",
        "initial_price",
        "final_price",
        "requested_at",
    )
    list_filter = ("status", "requested_service_tier", "requested_at")
    search_fields = (
        "customer__user__email",
        "driver__user__email",
        "starting_landmark",
        "arrival_landmark",
    )
    ordering = ("-requested_at",)
    readonly_fields = (
        "requested_at",
        "accepted_at",
        "arriving_at",
        "picked_up_at",
        "completed_at",
        "cancelled_at",
        "distance_km",
    )
    fieldsets = (
        ("Participants", {
            "fields": ("customer", "driver", "vehicle")
        }),
        ("Itinéraire", {
            "fields": (
                "starting_landmark",
                "arrival_landmark",
                "departure_latitude",
                "departure_longitude",
                "destination_latitude",
                "destination_longitude",
                "distance_km",
            )
        }),
        ("Statut & prix", {
            "fields": (
                "status",
                "requested_service_tier",
                "initial_price",
                "final_price",
                "cancelled_by",
                "cancellation_reason",
            )
        }),
        ("Timeline", {
            "fields": (
                "requested_at",
                "accepted_at",
                "arriving_at",
                "picked_up_at",
                "completed_at",
                "cancelled_at",
            )
        }),
    )


# =====================================================
# PAYMENT
# =====================================================
@admin.register(Payment)
class PaymentAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = (
        "course",
        "final_amount",
        "currency",
        "payment_mode",
        "status",
        "paid_at",
    )
    list_filter = ("payment_mode", "status")
    search_fields = ("course__id", "transaction_id")
    readonly_fields = ("paid_at",)


# =====================================================
# EVALUATION
# =====================================================
@admin.register(Evaluation)
class EvaluationAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("course", "driver", "customer", "rate", "created_at")
    list_filter = ("rate", "created_at")
    search_fields = ("driver__user__email", "customer__user__email")


# =====================================================
# COMPLAINT
# =====================================================
@admin.register(Complaint)
class ComplaintAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("course", "customer", "status", "created_at", "resolved_at")
    list_filter = ("status",)
    search_fields = ("customer__user__email", "course__id")
    readonly_fields = ("resolved_at",)


# =====================================================
# NOTIFICATION
# =====================================================
@admin.register(Notification)
class NotificationAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("user", "title", "notif_type", "is_read", "created_at")
    list_filter = ("notif_type", "is_read")
    search_fields = ("user__email", "title")


# =====================================================
# SETTINGS
# =====================================================
@admin.register(Setting)
class SettingAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("setting_name", "created_at")
    search_fields = ("setting_name",)