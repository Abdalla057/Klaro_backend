from django.contrib import admin

from .models import Client


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("utilisateur", "fournisseur", "telephone", "solde_du", "compte_active")
    list_filter = ("fournisseur", "compte_active")
    search_fields = ("utilisateur__telephone", "telephone")
    readonly_fields = ("code_expiration", "date_activation", "date_creation")
