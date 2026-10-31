from decimal import Decimal

from django.db.models import Sum
from django.db.models.deletion import ProtectedError
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.abonnements.serializers import FournisseurAbonnementSerializer
from apps.abonnements.services import etat_abonnement_fournisseur
from apps.agents.models import Agent
from apps.clients.models import Client
from apps.encaissements.models import Encaissement
from apps.finances.models import DemandeDecaissement, TransactionFinanciere

from .models import Fournisseur
from .serializers import FournisseurInscriptionSerializer, FournisseurSerializer


class FournisseurViewSet(viewsets.ModelViewSet):
    """Profil du fournisseur connecté et inscription publique.

    - create : inscription publique (compte Utilisateur + profil + essai gratuit)
    - retrieve/update/partial_update : uniquement son propre profil
    - destroy : supprime le profil et le compte, sauf si des données
      protégées (abonnements, encaissements) empêchent la suppression
    - abonnement : état de l'abonnement, des quotas et de leur utilisation
    """

    def get_serializer_class(self):
        if self.action == "create":
            return FournisseurInscriptionSerializer
        return FournisseurSerializer

    def get_permissions(self):
        if self.action == "create":
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def get_throttles(self):
        if self.action == "create":
            self.throttle_scope = "inscription"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Fournisseur.objects.none()
        if hasattr(user, "fournisseur_profile"):
            return Fournisseur.objects.filter(
                pk=user.fournisseur_profile.pk
            ).order_by("nom_entreprise")
        return Fournisseur.objects.none()

    def perform_destroy(self, instance):
        try:
            # La suppression du compte entraîne le profil via la cascade.
            instance.utilisateur.delete()
        except ProtectedError:
            raise DRFValidationError(
                "Ce fournisseur possède des données liées (abonnements, "
                "encaissements) et ne peut pas être supprimé. "
                "Suspendez-le plutôt."
            )

    @action(detail=False, methods=["get"], url_path="abonnement")
    def abonnement(self, request):
        fournisseur = getattr(request.user, "fournisseur_profile", None)
        if fournisseur is None:
            raise PermissionDenied(
                "Seul un fournisseur possède un abonnement."
            )
        etat = etat_abonnement_fournisseur(fournisseur)
        return Response(FournisseurAbonnementSerializer(etat).data)

    @action(detail=False, methods=["get"], url_path="statistiques")
    def statistiques(self, request):
        fournisseur = getattr(request.user, "fournisseur_profile", None)
        if fournisseur is None:
            raise PermissionDenied(
                "Seul un fournisseur possède des statistiques."
            )

        clients = Client.objects.filter(fournisseur=fournisseur)
        agents = Agent.objects.filter(fournisseur=fournisseur)
        encaissements = Encaissement.objects.filter(
            client__fournisseur=fournisseur,
            statut=Encaissement.Statut.CONFIRME,
        )
        transactions = TransactionFinanciere.objects.filter(
            fournisseur=fournisseur,
            statut=TransactionFinanciere.Statut.VALIDEE,
        )
        credits = (
            transactions.filter(sens=TransactionFinanciere.Sens.CREDIT).aggregate(
                total=Sum("montant")
            )["total"]
            or Decimal("0")
        )
        debits = (
            transactions.filter(sens=TransactionFinanciere.Sens.DEBIT).aggregate(
                total=Sum("montant")
            )["total"]
            or Decimal("0")
        )
        demandes_en_cours = DemandeDecaissement.objects.filter(
            fournisseur=fournisseur,
            statut__in=DemandeDecaissement.STATUTS_EN_COURS,
        )

        return Response(
            {
                "nombre_clients": clients.count(),
                "nombre_clients_actifs": clients.filter(compte_active=True).count(),
                "nombre_agents": agents.count(),
                "nombre_agents_actifs": agents.filter(compte_active=True).count(),
                "nombre_encaissements": encaissements.count(),
                "montant_encaisse": (
                    encaissements.aggregate(total=Sum("montant"))["total"]
                    or Decimal("0")
                ),
                "solde_total_clients": (
                    clients.aggregate(total=Sum("solde_du"))["total"]
                    or Decimal("0")
                ),
                "total_credits": credits,
                "total_debits": debits,
                "solde_net": credits - debits,
                "decaissements_en_cours": demandes_en_cours.count(),
                "montant_decaissements_en_cours": (
                    demandes_en_cours.aggregate(total=Sum("montant"))["total"]
                    or Decimal("0")
                ),
            }
        )
