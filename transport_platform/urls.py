# transport_platform/urls.py (ou votre urls.py principal)
from core.views import home
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

urlpatterns = [
    path("", home, name="home"), 
    
    path("admin/", admin.site.urls),

    # Schema OpenAPI
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),

    # Swagger UI
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),

    # ReDoc (optionnel)
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),

    # Toutes les routes API (y compris auth/token/)
    path("api/", include("core.urls")),
]

# Servir les fichiers media en développement
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)