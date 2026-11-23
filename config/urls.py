from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

from config.health import health

urlpatterns = [
    path('health/', health, name='health'),
    path('admin/', admin.site.urls),

    # Documentation Swagger
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

  #  API URLs
    path("api/comptes/", include("apps.comptes.urls")),
    path("api/fournisseurs/", include("apps.fournisseurs.urls")),
    path("api/clients/", include("apps.clients.urls")),
    path("api/agents/", include("apps.agents.urls")),
    path("api/encaissements/", include("apps.encaissements.urls")),
    path("api/finances/", include("apps.finances.urls")),
    path("api/abonnements/", include("apps.abonnements.urls")),
    path("api/paiements/", include("apps.abonnements.urls_paiements")),
    path("api/super-admin/", include("apps.superAdmin.urls")),
    path("api/commerce/", include("apps.commerce.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)