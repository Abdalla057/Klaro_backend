from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.superAdmin.permissions import IsSuperAdmin

from .exceptions import PlanUtilise
from .models import Abonnement, PlanAbonnement
from .serializers import (
    AbonnementCreationSerializer,
    AbonnementSerializer,
    AnnulationAbonnementSerializer,
    EvenementAbonnementSerializer,
    PlanAbonnementSerializer,
    RenouvellementAbonnementSerializer,
    SuspensionAbonnementSerializer,
)
from .services import annuler, reactiver, suspendre


class PlanAbonnementViewSet(viewsets.ModelViewSet):
    """Catalogue des plans et administration par le super administrateur."""

    serializer_class = PlanAbonnementSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsSuperAdmin()]

    def get_queryset(self):
        utilisateur = self.request.user
        queryset = PlanAbonnement.objects.all()
        if not (
            utilisateur.is_authenticated
            and utilisateur.role == "super_admin"
        ):
            queryset = queryset.filter(actif=True)
        return queryset.order_by("prix", "duree_jours", "nom")

    def perform_destroy(self, instance):
        if instance.abonnements.exists():
            raise PlanUtilise()
        instance.delete()


class AbonnementViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Gestion des abonnements par le super administrateur."""

    permission_classes = [IsSuperAdmin]

    def get_queryset(self):
        queryset = Abonnement.objects.select_related(
            "fournisseur",
            "plan",
            "cree_par",
        )
        fournisseur_id = self.request.query_params.get("fournisseur")
        if fournisseur_id:
            queryset = queryset.filter(fournisseur_id=fournisseur_id)

        statut = self.request.query_params.get("statut")
        if statut:
            queryset = queryset.filter(statut=statut)

        est_essai = self.request.query_params.get("est_essai")
        if est_essai in ("true", "1"):
            queryset = queryset.filter(est_essai=True)
        elif est_essai in ("false", "0"):
            queryset = queryset.filter(est_essai=False)

        return queryset

    def get_serializer_class(self):
        if self.action == "create":
            return AbonnementCreationSerializer
        return AbonnementSerializer

    @extend_schema(
        request=AbonnementCreationSerializer,
        responses={201: AbonnementSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        abonnement = serializer.save()
        return Response(
            AbonnementSerializer(abonnement).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=RenouvellementAbonnementSerializer,
        responses=AbonnementSerializer,
    )
    @action(detail=True, methods=["post"], url_path="renouveler")
    def renouveler(self, request, pk=None):
        abonnement = self.get_object()
        serializer = RenouvellementAbonnementSerializer(
            data=request.data,
            context={"request": request, "abonnement": abonnement},
        )
        serializer.is_valid(raise_exception=True)
        nouveau = serializer.save()
        return Response(AbonnementSerializer(nouveau).data)

    @extend_schema(
        request=SuspensionAbonnementSerializer,
        responses=AbonnementSerializer,
    )
    @action(detail=True, methods=["post"], url_path="suspendre")
    def suspendre(self, request, pk=None):
        abonnement = self.get_object()
        serializer = SuspensionAbonnementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        abonnement = suspendre(
            abonnement,
            serializer.validated_data["motif"],
            effectue_par=request.user,
        )
        return Response(AbonnementSerializer(abonnement).data)

    @extend_schema(responses=AbonnementSerializer)
    @action(detail=True, methods=["post"], url_path="reactiver")
    def reactiver(self, request, pk=None):
        abonnement = self.get_object()
        abonnement = reactiver(abonnement, effectue_par=request.user)
        return Response(AbonnementSerializer(abonnement).data)

    @extend_schema(
        request=AnnulationAbonnementSerializer,
        responses=AbonnementSerializer,
    )
    @action(detail=True, methods=["post"], url_path="annuler")
    def annuler(self, request, pk=None):
        abonnement = self.get_object()
        serializer = AnnulationAbonnementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        abonnement = annuler(
            abonnement,
            serializer.validated_data["motif"],
            effectue_par=request.user,
        )
        return Response(AbonnementSerializer(abonnement).data)

    @extend_schema(responses=EvenementAbonnementSerializer(many=True))
    @action(detail=True, methods=["get"], url_path="evenements")
    def evenements(self, request, pk=None):
        abonnement = self.get_object()
        queryset = abonnement.evenements.select_related("effectue_par").all()
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = EvenementAbonnementSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = EvenementAbonnementSerializer(queryset, many=True)
        return Response(serializer.data)
