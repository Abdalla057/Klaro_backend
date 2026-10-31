from decimal import Decimal

from django.db.models import Sum
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.abonnements.permissions import AbonnementActifPourEcriture
from apps.clients.models import Client
from apps.comptes.permissions import EstAgent, EstClient, EstFournisseur

from .models import DemandeDecaissement, TransactionFinanciere
from .serializers import (
    CreationDemandeDecaissementSerializer,
    DemandeDecaissementSerializer,
    RefusDemandeSerializer,
    RegularisationSerializer,
    TransactionFinanciereSerializer,
)
from .services import (
    annuler_demande_decaissement,
    approuver_demande_decaissement,
    confirmer_demande_decaissement,
    creer_demande_decaissement,
    demandes_accessibles_par,
    enregistrer_regularisation,
    payer_demande_decaissement,
    refuser_demande_decaissement,
    solde_disponible_client,
    transactions_accessibles_par,
)


class TransactionFinanciereViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TransactionFinanciereSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = transactions_accessibles_par(self.request.user).select_related(
            "client",
            "agent",
            "fournisseur",
        )
        parametres = self.request.query_params
        if parametres.get("type_transaction"):
            queryset = queryset.filter(
                type_transaction=parametres["type_transaction"]
            )
        if parametres.get("sens"):
            queryset = queryset.filter(sens=parametres["sens"])
        if parametres.get("statut"):
            queryset = queryset.filter(statut=parametres["statut"])
        if parametres.get("client"):
            queryset = queryset.filter(client_id=parametres["client"])
        if parametres.get("agent"):
            queryset = queryset.filter(agent_id=parametres["agent"])
        if parametres.get("date_debut"):
            queryset = queryset.filter(
                date_creation__date__gte=parametres["date_debut"]
            )
        if parametres.get("date_fin"):
            queryset = queryset.filter(
                date_creation__date__lte=parametres["date_fin"]
            )
        return queryset

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[
            IsAuthenticated,
            EstFournisseur,
            AbonnementActifPourEcriture,
        ],
    )
    def regulariser(self, request):
        serializer = RegularisationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data
        fournisseur = request.user.fournisseur_profile
        if donnees["client"].fournisseur_id != fournisseur.pk:
            raise PermissionDenied(
                "Vous ne pouvez pas régulariser un client d'un autre fournisseur."
            )
        mouvement = enregistrer_regularisation(
            fournisseur=fournisseur,
            client=donnees["client"],
            montant=donnees["montant"],
            sens=donnees["sens"],
            motif=donnees["motif"],
            auteur=request.user,
            cle_idempotence=donnees.get("cle_idempotence"),
        )
        return Response(
            TransactionFinanciereSerializer(mouvement).data,
            status=status.HTTP_201_CREATED,
        )


class DemandeDecaissementViewSet(viewsets.GenericViewSet):
    serializer_class = DemandeDecaissementSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        if self.action == "create":
            return [
                IsAuthenticated(),
                EstClient(),
                AbonnementActifPourEcriture(),
            ]
        if self.action == "confirmer":
            return [
                IsAuthenticated(),
                EstAgent(),
                AbonnementActifPourEcriture(),
            ]
        if self.action in ("approuver", "payer", "refuser"):
            return [
                IsAuthenticated(),
                EstFournisseur(),
                AbonnementActifPourEcriture(),
            ]
        if self.action == "annuler":
            return [IsAuthenticated(), AbonnementActifPourEcriture()]
        return [IsAuthenticated()]

    def get_queryset(self):
        return demandes_accessibles_par(self.request.user).select_related(
            "client",
            "agent",
            "fournisseur",
        )

    def list(self, request):
        page = self.paginate_queryset(self.get_queryset())
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        demande = self.get_object()
        return Response(self.get_serializer(demande).data)

    def create(self, request):
        serializer = CreationDemandeDecaissementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        client = request.user.client_profile
        demande = creer_demande_decaissement(
            client=client,
            montant=serializer.validated_data["montant"],
            commentaire=serializer.validated_data.get("commentaire", ""),
            cle_idempotence=serializer.validated_data.get("cle_idempotence"),
        )
        return Response(
            self.get_serializer(demande).data,
            status=status.HTTP_201_CREATED,
        )

    def _reponse(self, demande, code=status.HTTP_200_OK):
        return Response(self.get_serializer(demande).data, status=code)

    @action(detail=True, methods=["post"])
    def confirmer(self, request, pk=None):
        demande = self.get_object()
        demande = confirmer_demande_decaissement(
            demande,
            agent=request.user.agent_profile,
        )
        return self._reponse(demande)

    @action(detail=True, methods=["post"])
    def approuver(self, request, pk=None):
        demande = self.get_object()
        demande = approuver_demande_decaissement(
            demande,
            fournisseur=request.user.fournisseur_profile,
        )
        return self._reponse(demande)

    @action(detail=True, methods=["post"])
    def payer(self, request, pk=None):
        demande = self.get_object()
        demande = payer_demande_decaissement(
            demande,
            fournisseur=request.user.fournisseur_profile,
        )
        return self._reponse(demande)

    @action(detail=True, methods=["post"])
    def refuser(self, request, pk=None):
        serializer = RefusDemandeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        demande = self.get_object()
        demande = refuser_demande_decaissement(
            demande,
            fournisseur=request.user.fournisseur_profile,
            motif=serializer.validated_data["motif"],
        )
        return self._reponse(demande)

    @action(detail=True, methods=["post"])
    def annuler(self, request, pk=None):
        demande = self.get_object()
        demande = annuler_demande_decaissement(demande, request.user)
        return self._reponse(demande)


class SoldeClientView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if hasattr(request.user, "client_profile"):
            client = request.user.client_profile
        elif hasattr(request.user, "fournisseur_profile"):
            client_id = request.query_params.get("client")
            if not client_id:
                raise ValidationError(
                    {"client": "Le paramètre client est requis pour un fournisseur."}
                )
            client = (
                Client.objects.filter(
                    pk=client_id,
                    fournisseur=request.user.fournisseur_profile,
                )
                .select_related("utilisateur", "fournisseur")
                .first()
            )
            if client is None:
                raise PermissionDenied("Client introuvable pour ce fournisseur.")
        else:
            raise PermissionDenied("Aucun profil client associé à ce compte.")

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


class StatistiquesFinancieresView(APIView):
    permission_classes = [IsAuthenticated, EstFournisseur]

    def get(self, request):
        fournisseur = request.user.fournisseur_profile
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
        demandes = DemandeDecaissement.objects.filter(fournisseur=fournisseur)
        demandes_en_cours = demandes.filter(
            statut__in=DemandeDecaissement.STATUTS_EN_COURS
        )
        return Response(
            {
                "total_credits": credits,
                "total_debits": debits,
                "solde_net": credits - debits,
                "nombre_transactions": transactions.count(),
                "decaissements_en_cours": demandes_en_cours.count(),
                "montant_decaissements_en_cours": (
                    demandes_en_cours.aggregate(total=Sum("montant"))["total"]
                    or Decimal("0")
                ),
                "decaissements_payes": demandes.filter(
                    statut=DemandeDecaissement.Statut.PAYE
                ).count(),
            }
        )
