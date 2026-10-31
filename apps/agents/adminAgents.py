from django.contrib import admin

from .models import Agent


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ("utilisateur", "fournisseur", "telephone", "zone", "compte_active")
    list_filter = ("fournisseur", "compte_active")
    search_fields = ("utilisateur__telephone", "telephone")
    readonly_fields = ("code_expiration", "date_activation", "date_creation")
