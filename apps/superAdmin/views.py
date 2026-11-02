from datetime import date

from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
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
    SerieChiffreAffairesSerializer,
    StatistiquesAbonnementsEvolutionSerializer,
    StatistiquesFinancieresSerializer,
    StatistiquesFournisseurSerializer,
    SuspensionFournisseurSerializer,
    TableauDeBordSerializer,
)
from .services import (
    serie_chiffre_affaires,
    statistiques_abonnements,
    statistiques_financieres,
    statistiques_fournisseur,
    statistiques_globales,
)


def _date_reference_parametre(request):
    valeur = request.query_params.get("date_reference")
    if not valeur:
        return None
    try:
        return date.fromisoformat(valeur)
    except ValueError as exc:
        raise ValidationError(
            {"date_reference": "Format attendu : AAAA-MM-JJ."}
        ) from exc


PERIODE_ABONNEMENT = {"hebdomadaire", "mensuelle", "annuelle"}
PERIODE_FINANCE = {"hebdomadaire", "mensuelle", "annuelle"}
GRANULARITE = {"jour", "semaine", "mois"}


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


class StatistiquesFinancieresView(APIView):
    """Indicateurs de chiffre d'affaires confirmé, par devise."""

    permission_classes = [IsSuperAdmin]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "date_reference",
                OpenApiTypes.DATE,
                description="Date de référence (AAAA-MM-JJ). Défaut : aujourd'hui.",
            )
        ],
        responses=StatistiquesFinancieresSerializer,
    )
    def get(self, request):
        donnees = statistiques_financieres(
            date_reference=_date_reference_parametre(request)
        )
        return Response(StatistiquesFinancieresSerializer(donnees).data)


class SerieChiffreAffairesView(APIView):
    """Évolution du chiffre d'affaires confirmé (semaine, mois, année)."""

    permission_classes = [IsSuperAdmin]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "periode",
                OpenApiTypes.STR,
                enum=["hebdomadaire", "mensuelle", "annuelle"],
                description="Période d'analyse. Défaut : hebdomadaire.",
            ),
            OpenApiParameter(
                "granularite",
                OpenApiTypes.STR,
                enum=["jour", "semaine", "mois"],
                description=(
                    "Granularité des points. Mensuelle : jour ou semaine. "
                    "Annuelle : mois (forcé)."
                ),
            ),
            OpenApiParameter(
                "date_reference",
                OpenApiTypes.DATE,
                description="Date de référence (AAAA-MM-JJ). Défaut : aujourd'hui.",
            ),
        ],
        responses=SerieChiffreAffairesSerializer,
    )
    def get(self, request):
        periode = request.query_params.get("periode", "hebdomadaire")
        granularite = request.query_params.get("granularite")
        if periode in ("mensuel", "mois"):
            periode = "mensuelle"
        if periode in ("annuel", "annee"):
            periode = "annuelle"
        if periode not in PERIODE_FINANCE:
            raise ValidationError(
                {
                    "periode": (
                        "Valeurs autorisées : hebdomadaire, mensuelle, annuelle."
                    )
                }
            )
        if granularite and granularite not in GRANULARITE:
            raise ValidationError(
                {"granularite": "Valeurs autorisées : jour, semaine, mois."}
            )
        donnees = serie_chiffre_affaires(
            periode=periode,
            granularite=granularite,
            date_reference=_date_reference_parametre(request),
        )
        return Response(SerieChiffreAffairesSerializer(donnees).data)


class StatistiquesAbonnementsView(APIView):
    """Statuts des abonnements et évolution des souscriptions/renouvellements."""

    permission_classes = [IsSuperAdmin]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "periode",
                OpenApiTypes.STR,
                enum=["hebdomadaire", "mensuelle", "annuelle"],
                description="Période d'analyse. Défaut : mensuelle.",
            ),
            OpenApiParameter(
                "date_reference",
                OpenApiTypes.DATE,
                description="Date de référence (AAAA-MM-JJ). Défaut : aujourd'hui.",
            ),
        ],
        responses=StatistiquesAbonnementsEvolutionSerializer,
    )
    def get(self, request):
        periode = request.query_params.get("periode", "mensuelle")
        if periode in ("mensuel", "mois"):
            periode = "mensuelle"
        if periode in ("annuel", "annee"):
            periode = "annuelle"
        if periode not in PERIODE_ABONNEMENT:
            raise ValidationError(
                {
                    "periode": (
                        "Valeurs autorisées : hebdomadaire, mensuelle, annuelle."
                    )
                }
            )
        donnees = statistiques_abonnements(
            periode=periode,
            date_reference=_date_reference_parametre(request),
        )
        return Response(
            StatistiquesAbonnementsEvolutionSerializer(donnees).data
        )
