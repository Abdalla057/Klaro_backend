from django.contrib import admin

from .models import DemandeDecaissement, TransactionFinanciere


@admin.register(TransactionFinanciere)
class TransactionFinanciereAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "fournisseur",
        "client",
        "type_transaction",
        "sens",
        "montant",
        "statut",
        "date_creation",
    )
    list_filter = ("type_transaction", "sens", "statut")
    search_fields = ("reference", "client__telephone", "client__last_name")
    readonly_fields = ("reference", "date_creation")


@admin.register(DemandeDecaissement)
class DemandeDecaissementAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "fournisseur",
        "client",
        "montant",
        "statut",
        "date_creation",
    )
    list_filter = ("statut",)
    search_fields = ("reference", "client__telephone", "client__last_name")
    readonly_fields = ("reference", "date_creation")
