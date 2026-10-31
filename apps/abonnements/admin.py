from django.contrib import admin

from .models import Abonnement, EvenementAbonnement, PlanAbonnement


@admin.register(PlanAbonnement)
class PlanAbonnementAdmin(admin.ModelAdmin):
    list_display = (
        "nom",
        "code",
        "prix",
        "devise",
        "duree_jours",
        "max_clients",
        "max_agents",
        "est_essai",
        "actif",
    )
    list_filter = ("actif", "est_essai", "devise")
    search_fields = ("nom", "code")
    ordering = ("prix", "duree_jours")


@admin.register(Abonnement)
class AbonnementAdmin(admin.ModelAdmin):
    list_display = (
        "fournisseur",
        "plan",
        "statut",
        "date_debut",
        "date_fin",
        "est_essai",
        "montant",
        "devise",
    )
    list_filter = ("statut", "est_essai", "plan", "devise")
    search_fields = (
        "fournisseur__nom_entreprise",
        "fournisseur__code_entreprise",
        "fournisseur__utilisateur__telephone",
        "reference_paiement",
    )
    readonly_fields = ("date_creation", "date_modification")
    date_hierarchy = "date_debut"
    autocomplete_fields = ("fournisseur", "plan", "cree_par")


@admin.register(EvenementAbonnement)
class EvenementAbonnementAdmin(admin.ModelAdmin):
    list_display = (
        "abonnement",
        "type_evenement",
        "ancien_statut",
        "nouveau_statut",
        "montant",
        "effectue_par",
        "date_creation",
    )
    list_filter = ("type_evenement",)
    search_fields = (
        "abonnement__fournisseur__nom_entreprise",
        "motif",
        "reference_paiement",
    )
    readonly_fields = (
        "abonnement",
        "type_evenement",
        "ancien_statut",
        "nouveau_statut",
        "montant",
        "reference_paiement",
        "motif",
        "effectue_par",
        "date_creation",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
