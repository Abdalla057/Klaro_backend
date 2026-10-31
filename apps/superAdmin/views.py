from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.abonnements.models import Abonnement
from apps.abonnements.serializers import AbonnementSerializer
from apps.fournisseurs.models import Fournisseur
from apps.fournisseurs.services import (
    reactiver_fournisseur,
    suspendre_fournisseur,
)

from .permissions import IsSuperAdmin
from .serializers import (
    FournisseurSupervisionSerializer,
    StatistiquesFournisseurSerializer,
    SuspensionFournisseurSerializer,
    TableauDeBordSerializer,
)
from .services import statistiques_fournisseur, statistiques_globales


class FournisseurSupervisionViewSet(viewsets.ReadOnlyModelViewSet):
    """Supervision en lecture des fournisseurs et actions d'administration."""

    permission_classes = [IsSuperAdmin]
    serializer_class = FournisseurSupervisionSerializer

    def get_queryset(self):
        maintenant = timezone.now()
        abonnements_actifs = Abonnement.objects.filter(
            fournisseur=OuterRef("pk"),
            statut=Abonnement.Statut.ACTIF,
            date_debut__lte=maintenant,
            date_fin__gt=maintenant,
        )
        queryset = (
            Fournisseur.objects.select_related("utilisateur", "suspendu_par")
            .annotate(
                nombre_clients=Count("clients", distinct=True),
                nombre_agents=Count("agents", distinct=True),
                a_un_abonnement_actif=Exists(abonnements_actifs),
            )
            .prefetch_related(
                Prefetch(
                    "abonnements",
                    queryset=Abonnement.objects.filter(
                        statut=Abonnement.Statut.ACTIF,
                        date_debut__lte=maintenant,
                        date_fin__gt=maintenant,
                    )
                    .select_related("plan")
                    .order_by("-date_debut"),
                    to_attr="abonnements_actifs",
                )
            )
        )

        params = self.request.query_params
        statut = params.get("statut")
        if statut == "suspendu":
            queryset = queryset.filter(est_suspendu=True)
        elif statut == "actif":
            queryset = queryset.filter(est_suspendu=False)

        abonnement = params.get("abonnement")
        if abonnement == "actif":
            queryset = queryset.filter(a_un_abonnement_actif=True)
        elif abonnement == "inactif":
            queryset = queryset.filter(a_un_abonnement_actif=False)

        recherche = params.get("recherche")
        if recherche:
            queryset = queryset.filter(
                Q(nom_entreprise__icontains=recherche)
                | Q(code_entreprise__icontains=recherche)
                | Q(telephone__icontains=recherche)
                | Q(utilisateur__telephone__icontains=recherche)
            )

        return queryset.order_by("nom_entreprise")

    @extend_schema(
        request=SuspensionFournisseurSerializer,
        responses=FournisseurSupervisionSerializer,
    )
    @action(detail=True, methods=["post"], url_path="suspendre")
    def suspendre(self, request, pk=None):
        fournisseur = self.get_object()
        serializer = SuspensionFournisseurSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fournisseur = suspendre_fournisseur(
            fournisseur,
            serializer.validated_data["motif"],
            effectue_par=request.user,
        )
        return Response(FournisseurSupervisionSerializer(fournisseur).data)

    @extend_schema(responses=FournisseurSupervisionSerializer)
    @action(detail=True, methods=["post"], url_path="reactiver")
    def reactiver(self, request, pk=None):
        fournisseur = reactiver_fournisseur(self.get_object())
        return Response(FournisseurSupervisionSerializer(fournisseur).data)

    @extend_schema(responses=StatistiquesFournisseurSerializer)
    @action(detail=True, methods=["get"], url_path="statistiques")
    def statistiques(self, request, pk=None):
        fournisseur = self.get_object()
        statistiques = statistiques_fournisseur(fournisseur)
        return Response(StatistiquesFournisseurSerializer(statistiques).data)

    @extend_schema(responses=AbonnementSerializer(many=True))
    @action(detail=True, methods=["get"], url_path="abonnements")
    def abonnements(self, request, pk=None):
        fournisseur = self.get_object()
        queryset = fournisseur.abonnements.select_related(
            "plan",
            "fournisseur",
            "cree_par",
        )
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = AbonnementSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = AbonnementSerializer(queryset, many=True)
        return Response(serializer.data)


class TableauDeBordView(APIView):
    """Indicateurs agrégés de la plateforme."""

    permission_classes = [IsSuperAdmin]

    @extend_schema(responses=TableauDeBordSerializer)
    def get(self, request):
        return Response(TableauDeBordSerializer(statistiques_globales()).data)
