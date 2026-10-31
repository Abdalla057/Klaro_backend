from decimal import Decimal

from django.db.models import Sum
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.abonnements.permissions import AbonnementActifPourEcriture
from apps.comptes.permissions import EstFournisseur
from apps.comptes.throttles import RenvoiCodeThrottle
from apps.finances.models import DemandeDecaissement, TransactionFinanciere
from apps.finances.serializers import (
    TransactionFinanciereSerializer,
)
from apps.finances.services import (
    attribuer_solde_initial,
    solde_disponible_client,
)

from .models import Client
from .serializers import (
    ClientActivationSerializer,
    ClientCreationSerializer,
    ClientSerializer,
)
from .services import clients_accessibles_par, renvoyer_code_client


class ClientViewSet(viewsets.ModelViewSet):
    """Gestion des clients d'un fournisseur et de leur solde."""

    permission_classes = [
        permissions.IsAuthenticated,
        AbonnementActifPourEcriture,
    ]
    throttle_scope = None

    def get_serializer_class(self):
        if self.action == "create":
            return ClientCreationSerializer
        return ClientSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [
                permissions.IsAuthenticated(),
                EstFournisseur(),
                AbonnementActifPourEcriture(),
            ]
        if self.action in ("renvoyer_code", "attribuer_solde"):
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
        return clients_accessibles_par(self.request.user)

    def perform_create(self, serializer):
        user = self.request.user
        if not hasattr(user, "fournisseur_profile"):
            raise PermissionDenied("Seul un fournisseur peut ajouter un client.")
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
        """Active le compte client avec le code reçu et un mot de passe choisi."""
        serializer = ClientActivationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        client = serializer.save()
        return Response(ClientSerializer(client).data)

    @action(
        detail=False,
        methods=["post"],
        url_path="verifier-code",
        permission_classes=[permissions.AllowAny],
        throttle_classes=[ScopedRateThrottle],
        throttle_scope="activation",
    )
    def verifier_code(self, request):
        """Vérifie le code d'activation et renvoie un jeton temporaire."""
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
        """Définit le mot de passe à partir du jeton d'activation temporaire."""
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
        """Renvoie un nouveau code d'activation pour un client du fournisseur."""
        from apps.comptes.serializers import RenvoyerCodeSerializer
        from apps.comptes.services import trouver_profil_activation
        from apps.comptes.validators import normaliser_telephone

        serializer = RenvoyerCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        telephone = normaliser_telephone(serializer.validated_data["telephone"])
        profil = trouver_profil_activation(telephone)
        if not isinstance(profil, Client):
            return Response(
                {"detail": "Aucun compte client en attente avec ce numéro."},
                status=status.HTTP_404_NOT_FOUND,
            )
        client = renvoyer_code_client(
            request.user.fournisseur_profile,
            telephone,
        )
        return Response(
            {
                "detail": "Nouveau code d'activation généré.",
                "code_activation": getattr(client, "_code_activation_clair", None),
            }
        )

    @action(
        detail=True,
        methods=["post"],
        url_path="attribuer-solde",
        permission_classes=[permissions.IsAuthenticated, EstFournisseur],
    )
    def attribuer_solde(self, request, pk=None):
        """Attribue le solde initial du client. Une seule fois par client."""
        client = self.get_object()
        try:
            montant = request.data["montant"]
        except KeyError as exc:
            raise ValidationError({"montant": "Ce champ est requis."}) from exc
        mouvement = attribuer_solde_initial(
            fournisseur=request.user.fournisseur_profile,
            client=client,
            montant=montant,
            auteur=request.user,
            commentaire=request.data.get("commentaire", ""),
            cle_idempotence=request.data.get("cle_idempotence"),
        )
        return Response(
            TransactionFinanciereSerializer(mouvement).data,
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=True,
        methods=["get"],
        url_path="solde",
    )
    def solde(self, request, pk=None):
        client = self.get_object()
        en_cours = (
            DemandeDecaissement.objects.filter(
                client=client,
                statut__in=DemandeDecaissement.STATUTS_EN_COURS,
            ).aggregate(total=Sum("montant"))["total"]
            or Decimal("0")
        )
        return Response(
            {
                "client": client.pk,
                "nom_complet": client.nom_complet,
                "telephone": client.telephone,
                "solde_du": client.solde_du,
                "montant_en_cours": en_cours,
                "solde_disponible": solde_disponible_client(client),
            }
        )

    @action(
        detail=True,
        methods=["get"],
        url_path="transactions",
    )
    def transactions(self, request, pk=None):
        client = self.get_object()
        queryset = TransactionFinanciere.objects.filter(client=client).select_related(
            "client",
            "agent",
            "fournisseur",
        )
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = TransactionFinanciereSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = TransactionFinanciereSerializer(queryset, many=True)
        return Response(serializer.data)
