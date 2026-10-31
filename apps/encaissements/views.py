from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.abonnements.permissions import AbonnementActifPourEcriture
from apps.comptes.permissions import EstAgent, EstClient

from .serializers import EncaissementAgentSerializer, EncaissementSerializer
from .services import (
    annuler_encaissement,
    confirmer_encaissement,
    confirmer_encaissement_par_code,
    creer_encaissement_par_agent,
    encaissements_accessibles_par,
)


class EncaissementViewSet(viewsets.ModelViewSet):
    serializer_class = EncaissementSerializer
    permission_classes = [
        permissions.IsAuthenticated,
        AbonnementActifPourEcriture,
    ]

    def get_permissions(self):
        permissions_ecriture = [
            permissions.IsAuthenticated(),
            AbonnementActifPourEcriture(),
        ]
        if self.action == "collecter":
            return permissions_ecriture + [EstAgent()]
        if self.action == "create":
            return permissions_ecriture + [EstClient()]
        if self.action in ("confirmer", "confirmer_par_code"):
            return permissions_ecriture + [EstAgent()]
        if self.action == "annuler":
            return permissions_ecriture
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        return encaissements_accessibles_par(self.request.user)

    def perform_create(self, serializer):
        client = getattr(self.request.user, "client_profile", None)
        serializer.save(client=client)

    @action(detail=False, methods=["post"], url_path="collecter")
    def collecter(self, request):
        """Encaissement saisi et confirmé directement par un agent."""
        serializer = EncaissementAgentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data
        encaissement = creer_encaissement_par_agent(
            agent=request.user.agent_profile,
            client=donnees["client"],
            montant=donnees["montant"],
            commentaire=donnees.get("commentaire", ""),
        )
        return Response(
            self.get_serializer(encaissement).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="confirmer")
    def confirmer(self, request, pk=None):
        """Confirmation par clic sur le bouton "Confirmer" dans la liste de l'agent."""
        encaissement = self.get_object()
        agent = getattr(request.user, "agent_profile", None)
        encaissement = confirmer_encaissement(encaissement, agent)
        return Response(self.get_serializer(encaissement).data)

    @action(detail=False, methods=["post"], url_path="confirmer-par-code")
    def confirmer_par_code(self, request):
        """Confirmation par scan du QR code affiché par le client (code_confirmation)."""
        agent = getattr(request.user, "agent_profile", None)
        encaissement = confirmer_encaissement_par_code(request.data.get("code"), agent)
        return Response(self.get_serializer(encaissement).data)

    @action(detail=True, methods=["post"], url_path="annuler")
    def annuler(self, request, pk=None):
        """Le client (propriétaire) ou le fournisseur peut annuler un encaissement en attente."""
        encaissement = self.get_object()
        encaissement = annuler_encaissement(encaissement, request.user)
        return Response(self.get_serializer(encaissement).data)