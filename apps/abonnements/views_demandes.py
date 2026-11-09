"""Vues des demandes d'abonnement et des webhooks de paiement."""

from django.http import FileResponse
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.superAdmin.permissions import IsSuperAdmin

from .confirmations import confirmer_demande_abonnement
from .demandes import (
    annuler_demande_abonnement,
    initier_paiement_en_ligne,
    refuser_demande_abonnement,
    soumettre_preuve_paiement,
)
from .exceptions import PrestataireNonConfigure, SignatureWebhookInvalide
from .models import DemandeAbonnement
from .paiements_en_ligne import traiter_webhook_paiement
from .permissions import EstFournisseur
from .selectors import (
    demandes_du_fournisseur,
    filtrer_demandes_pour_admin,
)
from .serializers import (
    ConfirmationDemandeSerializer,
    DemandeAbonnementCreationSerializer,
    DemandeAbonnementSerializer,
    InitierPaiementSerializer,
    MotifDemandeSerializer,
    PaiementAbonnementSerializer,
    SoumissionPreuvePaiementSerializer,
)


def _reponse_preuve(demande):
    if not demande.preuve_paiement:
        return Response(
            {"detail": "Aucun justificatif n'est disponible."},
            status=status.HTTP_404_NOT_FOUND,
        )
    try:
        fichier = demande.preuve_paiement.open("rb")
    except FileNotFoundError:
        return Response(
            {"detail": "Le justificatif est introuvable."},
            status=status.HTTP_404_NOT_FOUND,
        )
    return FileResponse(
        fichier,
        as_attachment=True,
        filename=f"preuve-{demande.reference}.pdf",
    )


class DemandeAbonnementFournisseurViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Espace fournisseur : créer, suivre et payer ses propres demandes."""

    permission_classes = [IsAuthenticated, EstFournisseur]
    serializer_class = DemandeAbonnementSerializer

    def get_queryset(self):
        fournisseur = getattr(self.request.user, "fournisseur_profile", None)
        if fournisseur is None:
            return DemandeAbonnement.objects.none()
        return demandes_du_fournisseur(fournisseur)

    def get_serializer_class(self):
        if self.action == "create":
            return DemandeAbonnementCreationSerializer
        if self.action == "soumettre_paiement":
            return SoumissionPreuvePaiementSerializer
        if self.action == "initier_paiement":
            return InitierPaiementSerializer
        if self.action == "annuler":
            return MotifDemandeSerializer
        return DemandeAbonnementSerializer

    def get_serializer_context(self):
        contexte = super().get_serializer_context()
        contexte["fournisseur"] = getattr(
            self.request.user,
            "fournisseur_profile",
            None,
        )
        return contexte

    @extend_schema(
        request=DemandeAbonnementCreationSerializer,
        responses={201: DemandeAbonnementSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        demande = serializer.save()
        return Response(
            DemandeAbonnementSerializer(demande).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=SoumissionPreuvePaiementSerializer,
        responses=DemandeAbonnementSerializer,
    )
    @action(detail=True, methods=["post"], url_path="soumettre-paiement")
    def soumettre_paiement(self, request, pk=None):
        demande = self.get_object()
        serializer = SoumissionPreuvePaiementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        demande = soumettre_preuve_paiement(
            demande,
            reference_transaction=serializer.validated_data.get(
                "reference_transaction",
                "",
            ),
            preuve_paiement=serializer.validated_data.get("preuve_paiement"),
            moyen_paiement=serializer.validated_data.get(
                "moyen_paiement",
                "",
            ),
            prestataire=serializer.validated_data.get("prestataire", ""),
            commentaire=serializer.validated_data.get("commentaire", ""),
            effectue_par=request.user,
        )
        return Response(DemandeAbonnementSerializer(demande).data)

    @extend_schema(
        request=MotifDemandeSerializer,
        responses=DemandeAbonnementSerializer,
    )
    @action(detail=True, methods=["post"], url_path="annuler")
    def annuler(self, request, pk=None):
        demande = self.get_object()
        serializer = MotifDemandeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        demande = annuler_demande_abonnement(
            demande,
            motif=serializer.validated_data.get("motif", ""),
            effectue_par=request.user,
        )
        return Response(DemandeAbonnementSerializer(demande).data)

    @extend_schema(
        request=InitierPaiementSerializer,
        responses=DemandeAbonnementSerializer,
    )
    @action(detail=True, methods=["post"], url_path="initier-paiement")
    def initier_paiement(self, request, pk=None):
        demande = self.get_object()
        serializer = InitierPaiementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        paiement, resultat = initier_paiement_en_ligne(
            demande,
            serializer.validated_data["prestataire"],
            effectue_par=request.user,
        )
        donnees = DemandeAbonnementSerializer(demande).data
        donnees["paiement"] = PaiementAbonnementSerializer(paiement).data
        donnees["paiement_url"] = resultat.get("paiement_url", "")
        donnees["rejoue"] = bool(resultat.get("rejoue"))
        return Response(donnees)

    @extend_schema(responses={200: None, 404: None})
    @action(detail=True, methods=["get"], url_path="preuve")
    def preuve(self, request, pk=None):
        return _reponse_preuve(self.get_object())


class DemandeAbonnementAdminViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Espace super administrateur : contrôle et validation des demandes."""

    permission_classes = [IsSuperAdmin]
    serializer_class = DemandeAbonnementSerializer

    def get_queryset(self):
        queryset = DemandeAbonnement.objects.select_related(
            "fournisseur",
            "plan",
            "abonnement_cible",
            "abonnement_genere",
            "traite_par",
        )
        return filtrer_demandes_pour_admin(
            queryset,
            self.request.query_params,
        )

    def get_serializer_class(self):
        if self.action == "confirmer":
            return ConfirmationDemandeSerializer
        if self.action in ("refuser", "annuler"):
            return MotifDemandeSerializer
        return DemandeAbonnementSerializer

    @extend_schema(
        request=ConfirmationDemandeSerializer,
        responses=DemandeAbonnementSerializer,
    )
    @action(detail=True, methods=["post"], url_path="confirmer")
    def confirmer(self, request, pk=None):
        demande = self.get_object()
        serializer = ConfirmationDemandeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        demande, paiement, nouvelle = confirmer_demande_abonnement(
            demande,
            reference_transaction=serializer.validated_data.get(
                "reference_transaction",
                "",
            ),
            prestataire=serializer.validated_data.get("prestataire", ""),
            provider_transaction_id=serializer.validated_data.get(
                "provider_transaction_id",
                "",
            ),
            moyen_paiement=serializer.validated_data.get(
                "moyen_paiement",
                "",
            ),
            motif=serializer.validated_data.get("motif", ""),
            effectue_par=request.user,
        )
        donnees = DemandeAbonnementSerializer(demande).data
        donnees["paiement"] = (
            PaiementAbonnementSerializer(paiement).data if paiement else None
        )
        donnees["nouvelle_confirmation"] = nouvelle
        return Response(donnees)

    @extend_schema(
        request=MotifDemandeSerializer,
        responses=DemandeAbonnementSerializer,
    )
    @action(detail=True, methods=["post"], url_path="refuser")
    def refuser(self, request, pk=None):
        serializer = MotifDemandeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not serializer.validated_data.get("motif", "").strip():
            return Response(
                {"motif": ["Le motif du refus est obligatoire."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        demande = refuser_demande_abonnement(
            self.get_object(),
            motif=serializer.validated_data["motif"],
            effectue_par=request.user,
        )
        return Response(DemandeAbonnementSerializer(demande).data)

    @extend_schema(
        request=MotifDemandeSerializer,
        responses=DemandeAbonnementSerializer,
    )
    @action(detail=True, methods=["post"], url_path="annuler")
    def annuler(self, request, pk=None):
        serializer = MotifDemandeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        demande = annuler_demande_abonnement(
            self.get_object(),
            motif=serializer.validated_data.get("motif", ""),
            effectue_par=request.user,
        )
        return Response(DemandeAbonnementSerializer(demande).data)

    @extend_schema(responses={200: None, 404: None})
    @action(detail=True, methods=["get"], url_path="preuve")
    def preuve(self, request, pk=None):
        return _reponse_preuve(self.get_object())

    @extend_schema(responses=PaiementAbonnementSerializer(many=True))
    @action(detail=True, methods=["get"], url_path="paiements")
    def paiements(self, request, pk=None):
        demande = self.get_object()
        queryset = demande.paiements.select_related(
            "fournisseur",
            "plan",
            "abonnement",
            "confirme_par",
        ).all()
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = PaiementAbonnementSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = PaiementAbonnementSerializer(queryset, many=True)
        return Response(serializer.data)


class WebhookPaiementView(APIView):
    """Endpoint public de réception des notifications de prestataires.

    Aucun JWT n'est requis, mais la signature cryptographique est contrôlée
    avant tout effet financier.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    def post(self, request, prestataire):
        try:
            evenement, rejoue = traiter_webhook_paiement(
                prestataire,
                request.body,
                request.headers,
            )
        except SignatureWebhookInvalide as exc:
            return Response(
                {"detail": exc.detail, "code": exc.default_code},
                status=status.HTTP_403_FORBIDDEN,
            )
        except PrestataireNonConfigure as exc:
            return Response(
                {"detail": exc.detail, "code": exc.default_code},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {
                "statut": evenement.statut_traitement,
                "rejoue": rejoue,
                "evenement": evenement.pk,
            },
            status=status.HTTP_200_OK,
        )
