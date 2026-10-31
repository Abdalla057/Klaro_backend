from django.contrib import admin

from .models import Encaissement


@admin.register(Encaissement)
class EncaissementAdmin(admin.ModelAdmin):
    list_display = ("client", "agent", "montant", "statut", "date_creation", "date_confirmation")
    list_filter = ("statut", "agent", "date_creation")
    readonly_fields = ("code_confirmation", "date_creation", "date_confirmation")