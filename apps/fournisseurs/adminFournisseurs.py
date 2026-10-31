from django.contrib import admin

from .models import Fournisseur


@admin.register(Fournisseur)
class FournisseurAdmin(admin.ModelAdmin):
    list_display = (
        "nom_entreprise",
        "code_entreprise",
        "telephone",
        "est_suspendu",
        "date_suspension",
        "date_creation",
    )
    list_filter = ("est_suspendu", "date_creation")
    search_fields = (
        "nom_entreprise",
        "code_entreprise",
        "telephone",
        "utilisateur__telephone",
    )
    readonly_fields = ("code_entreprise", "date_creation")
