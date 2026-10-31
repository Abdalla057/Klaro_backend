from decimal import Decimal

from django.db.models import Sum
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.abonnements.permissions import AbonnementActifPourEcriture
from apps.clients.models import Client
from apps.comptes.permissions import EstFournisseur
from apps.comptes.throttles import RenvoiCodeThrottle

from .models import Agent
from .serializers import (
    AgentActivationSerializer,
    AgentCreationSerializer,
    AgentSerializer,
)
from .services import agents_accessibles_par, renvoyer_code_agent


class AgentViewSet(viewsets.ModelViewSet):
    """Gestion des agents d'un fournisseur."""

    permission_classes = [
        permissions.IsAuthenticated,
        AbonnementActifPourEcriture,
    ]
    throttle_scope = None

    def get_serializer_class(self):
        if self.action == "create":
            return AgentCreationSerializer
        return AgentSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [
                permissions.IsAuthenticated(),
                EstFournisseur(),
                AbonnementActifPourEcriture(),
            ]
        if self.action == "renvoyer_code":
            return [
                permissions.IsAuthenticated(),
                EstFournisseur(),
                AbonnementActifPourEcriture(),
            ]
        if self.action in (
            "activer",
            "verifier_code",
            "definir_mot_de_passe",
        ):
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        return agents_accessibles_par(self.request.user)

    def perform_create(self, serializer):
        user = self.request.user
        if not hasattr(user, "fournisseur_profile"):
            raise PermissionDenied("Seul un fournisseur peut ajouter un agent.")
        serializer.save(fournisseur=user.fournisseur_profile)

    @action(
        detail=False,
        methods=["post"],
        url_path="activer",
        permission_classes=[permissions.AllowAny],
        throttle_classes=[ScopedRateThrottle],
        throttle_scope="activation",
    )
    def activer(self, request):
        """Active le compte agent avec le code reçu et un mot de passe choisi."""
        serializer = AgentActivationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agent = serializer.save()
        return Response(AgentSerializer(agent).data)

    @action(
        detail=False,
        methods=["post"],
        url_path="verifier-code",
        permission_classes=[permissions.AllowAny],
        throttle_classes=[ScopedRateThrottle],
        throttle_scope="activation",
    )
    def verifier_code(self, request):
        from apps.comptes.serializers import VerificationCodeSerializer
        from apps.comptes.services import verifier_code_activation

        serializer = VerificationCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profil, token = verifier_code_activation(**serializer.validated_data)
        return Response(
            {
                "telephone": profil.telephone,
                "role": profil.utilisateur.role,
                "token": token,
                "expire_dans_minutes": 10,
            }
        )

    @action(
        detail=False,
        methods=["post"],
        url_path="definir-mot-de-passe",
        permission_classes=[permissions.AllowAny],
        throttle_classes=[ScopedRateThrottle],
        throttle_scope="activation",
    )
    def definir_mot_de_passe(self, request):
        from apps.comptes.serializers import DefinitionMotDePasseSerializer
        from apps.comptes.services import definir_mot_de_passe_activation

        serializer = DefinitionMotDePasseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profil = definir_mot_de_passe_activation(**serializer.validated_data)
        return Response(
            {
                "detail": "Compte activé avec succès.",
                "telephone": profil.telephone,
                "role": profil.utilisateur.role,
            }
        )

    @action(
        detail=False,
        methods=["post"],
        url_path="renvoyer-code",
        permission_classes=[permissions.IsAuthenticated, EstFournisseur],
        throttle_classes=[RenvoiCodeThrottle],
        throttle_scope="renvoi_code",
    )
    def renvoyer_code(self, request):
        """Renvoie un nouveau code d'activation pour un agent du fournisseur."""
        from apps.comptes.serializers import RenvoyerCodeSerializer
        from apps.comptes.services import trouver_profil_activation
        from apps.comptes.validators import normaliser_telephone

        serializer = RenvoyerCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        telephone = normaliser_telephone(serializer.validated_data["telephone"])
        profil = trouver_profil_activation(telephone)
        if not isinstance(profil, Agent):
            return Response(
                {"detail": "Aucun compte agent en attente avec ce numéro."},
                status=status.HTTP_404_NOT_FOUND,
            )
        agent = renvoyer_code_agent(
            request.user.fournisseur_profile,
            telephone,
        )
        return Response(
            {
                "detail": "Nouveau code d'activation généré.",
                "code_activation": getattr(agent, "_code_activation_clair", None),
            }
        )

    @action(
        detail=True,
        methods=["get"],
        url_path="statistiques",
    )
    def statistiques(self, request, pk=None):
        from apps.encaissements.models import Encaissement

        agent = self.get_object()
        encaissements = Encaissement.objects.filter(
            agent=agent,
            statut=Encaissement.Statut.CONFIRME,
        )
        montant_collecte = (
            encaissements.aggregate(total=Sum("montant"))["total"]
            or Decimal("0")
        )
        return Response(
            {
                "agent": agent.pk,
                "nom_complet": agent.nom_complet,
                "nombre_clients": Client.objects.filter(
                    fournisseur=agent.fournisseur
                ).count(),
                "nombre_recouvrements": encaissements.count(),
                "montant_collecte": montant_collecte,
            }
        )
