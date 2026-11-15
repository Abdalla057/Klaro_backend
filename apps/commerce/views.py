from decimal import Decimal
from io import BytesIO

from django.core import signing
from django.db.models import F, Q
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.abonnements.permissions import AbonnementActifPourEcriture
from apps.superAdmin.permissions import IsSuperAdmin

from .models import (
    CategorieProduit,
    CommandeAchat,
    ConfigurationNotification,
    DetteCommerciale,
    Facture,
    FournisseurMarchandise,
    MouvementStock,
    NotificationClient,
    NotificationExterne,
    Produit,
    ProduitUnite,
    Reception,
    Vente,
)
from .notifications import (
    lire_token_facture,
    renvoyer_notifications_externes,
)
from .pdf import generer_pdf_facture
from .permissions import (
    EstClientCommerce,
    EstFournisseurCommerce,
    EstFournisseurOuClient,
)
from .serializers import (
    CategorieProduitSerializer,
    CommandeAchatCreationSerializer,
    CommandeAchatSerializer,
    ConfigurationNotificationSerializer,
    DetteCommercialeSerializer,
    EntreeStockSerializer,
    FactureSerializer,
    FournisseurMarchandiseSerializer,
    MouvementStockSerializer,
    NotificationClientSerializer,
    NotificationExterneSerializer,
    PaiementVenteCreationSerializer,
    PaiementVenteSerializer,
    ProduitSerializer,
    ProduitUniteEcritureSerializer,
    ProduitUniteSerializer,
    ReceptionCreationSerializer,
    ReceptionSerializer,
    VenteCreationSerializer,
    VenteSerializer,
)
from .services import (
    annuler_vente,
    entree_stock,
    receptionner_commande,
    statistiques_fournisseur,
    statistiques_globales_commerce,
)

PERMISSIONS_FOURNISSEUR = [
    permissions.IsAuthenticated,
    EstFournisseurCommerce,
    AbonnementActifPourEcriture,
]


def _fournisseur(request):
    fournisseur = getattr(request.user, "fournisseur_profile", None)
    if fournisseur is None:
        raise PermissionDenied("Aucun profil fournisseur associé à ce compte.")
    return fournisseur


def _client(request):
    client = getattr(request.user, "client_profile", None)
    if client is None:
        raise PermissionDenied("Aucun profil client associé à ce compte.")
    return client


def _booleen_parametre(valeur):
    if valeur is None:
        return None
    if valeur.lower() in ("true", "1", "oui"):
        return True
    if valeur.lower() in ("false", "0", "non"):
        return False
    return None


class CategorieProduitViewSet(viewsets.ModelViewSet):
    serializer_class = CategorieProduitSerializer
    permission_classes = PERMISSIONS_FOURNISSEUR

    def get_queryset(self):
        queryset = CategorieProduit.objects.filter(
            fournisseur=_fournisseur(self.request)
        )
        recherche = self.request.query_params.get("recherche")
        if recherche:
            queryset = queryset.filter(nom__icontains=recherche)
        actif = _booleen_parametre(self.request.query_params.get("actif"))
        if actif is not None:
            queryset = queryset.filter(actif=actif)
        return queryset.order_by("nom")

    def perform_create(self, serializer):
        serializer.save(fournisseur=_fournisseur(self.request))


class ProduitViewSet(viewsets.ModelViewSet):
    serializer_class = ProduitSerializer
    permission_classes = PERMISSIONS_FOURNISSEUR

    def get_queryset(self):
        queryset = (
            Produit.objects.filter(fournisseur=_fournisseur(self.request))
            .select_related("categorie")
            .prefetch_related("unites")
        )
        params = self.request.query_params
        recherche = params.get("recherche")
        if recherche:
            queryset = queryset.filter(
                Q(nom__icontains=recherche)
                | Q(reference__icontains=recherche)
                | Q(description__icontains=recherche)
            )
        categorie = params.get("categorie")
        if categorie:
            queryset = queryset.filter(categorie_id=categorie)
        actif = _booleen_parametre(params.get("actif"))
        if actif is not None:
            queryset = queryset.filter(actif=actif)
        if _booleen_parametre(params.get("rupture")):
            queryset = queryset.filter(stock_actuel__lte=0)
        if _booleen_parametre(params.get("alerte")):
            queryset = queryset.filter(stock_actuel__lte=F("seuil_alerte"))
        return queryset.order_by("nom")

    @action(detail=True, methods=["get", "post"], url_path="unites")
    def unites(self, request, pk=None):
        produit = self.get_object()
        if request.method == "GET":
            return Response(
                ProduitUniteSerializer(produit.unites.all(), many=True).data
            )

        serializer = ProduitUniteEcritureSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data
        if donnees.get("est_base"):
            if produit.unites.filter(est_base=True).exists():
                raise ValidationError(
                    {"est_base": "Le produit possède déjà une unité de base."}
                )
            if donnees.get("facteur_conversion", Decimal("1")) != Decimal("1"):
                raise ValidationError(
                    {
                        "facteur_conversion": (
                            "L'unité de base doit avoir un facteur de "
                            "conversion égal à 1."
                        )
                    }
                )
            if donnees["nom"] != produit.unite_base:
                raise ValidationError(
                    {
                        "nom": (
                            "Le nom de l'unité de base doit correspondre à "
                            "l'unité de base du produit."
                        )
                    }
                )
        if produit.unites.filter(nom__iexact=donnees["nom"]).exists():
            raise ValidationError(
                {"nom": "Une unité porte déjà ce nom pour ce produit."}
            )
        unite = ProduitUnite.objects.create(produit=produit, **donnees)
        return Response(
            ProduitUniteSerializer(unite).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="entree-stock")
    def entree_stock(self, request, pk=None):
        produit = self.get_object()
        serializer = EntreeStockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mouvement = entree_stock(
            produit=produit,
            auteur=request.user,
            **serializer.validated_data,
        )
        return Response(
            MouvementStockSerializer(mouvement).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"], url_path="mouvements")
    def mouvements(self, request, pk=None):
        produit = self.get_object()
        queryset = produit.mouvements.select_related(
            "produit",
            "vente",
            "reception",
        ).order_by("-date_creation")
        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response(
                MouvementStockSerializer(page, many=True).data
            )
        return Response(MouvementStockSerializer(queryset, many=True).data)


class FournisseurMarchandiseViewSet(viewsets.ModelViewSet):
    serializer_class = FournisseurMarchandiseSerializer
    permission_classes = PERMISSIONS_FOURNISSEUR

    def get_queryset(self):
        queryset = FournisseurMarchandise.objects.filter(
            fournisseur=_fournisseur(self.request)
        )
        recherche = self.request.query_params.get("recherche")
        if recherche:
            queryset = queryset.filter(
                Q(nom__icontains=recherche)
                | Q(telephone__icontains=recherche)
            )
        actif = _booleen_parametre(self.request.query_params.get("actif"))
        if actif is not None:
            queryset = queryset.filter(actif=actif)
        return queryset.order_by("nom")

    def perform_create(self, serializer):
        serializer.save(fournisseur=_fournisseur(self.request))


class CommandeAchatViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = PERMISSIONS_FOURNISSEUR

    def get_queryset(self):
        queryset = (
            CommandeAchat.objects.filter(fournisseur=_fournisseur(self.request))
            .select_related("fournisseur_marchandise")
            .prefetch_related("lignes__produit", "lignes__unite")
            .order_by("-date_creation", "-id")
        )
        params = self.request.query_params
        statut = params.get("statut")
        if statut:
            queryset = queryset.filter(statut=statut)
        fournisseur_marchandise = params.get("fournisseur_marchandise")
        if fournisseur_marchandise:
            queryset = queryset.filter(
                fournisseur_marchandise_id=fournisseur_marchandise
            )
        recherche = params.get("recherche")
        if recherche:
            queryset = queryset.filter(
                Q(reference__icontains=recherche)
                | Q(fournisseur_marchandise__nom__icontains=recherche)
            )
        return queryset

    def get_serializer_class(self):
        if self.action == "create":
            return CommandeAchatCreationSerializer
        return CommandeAchatSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        commande = serializer.save()
        return Response(
            CommandeAchatSerializer(
                commande,
                context=self.get_serializer_context(),
            ).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="receptionner")
    def receptionner(self, request, pk=None):
        commande = self.get_object()
        serializer = ReceptionCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reception = receptionner_commande(
            commande,
            serializer.validated_data["lignes"],
            notes=serializer.validated_data.get("notes", ""),
            auteur=request.user,
        )
        return Response(
            ReceptionSerializer(
                reception,
                context=self.get_serializer_context(),
            ).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"], url_path="receptions")
    def receptions(self, request, pk=None):
        commande = self.get_object()
        queryset = (
            commande.receptions.select_related("commande")
            .prefetch_related(
                "lignes__ligne_commande__produit",
                "lignes__ligne_commande__unite",
            )
            .order_by("-date_reception")
        )
        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response(
                ReceptionSerializer(page, many=True).data
            )
        return Response(ReceptionSerializer(queryset, many=True).data)

    @action(detail=True, methods=["post"], url_path="annuler")
    def annuler(self, request, pk=None):
        commande = self.get_object()
        if commande.statut == CommandeAchat.Statut.RECUE:
            raise ValidationError(
                "Une commande entièrement reçue ne peut pas être annulée."
            )
        if commande.receptions.exists():
            raise ValidationError(
                "Une commande partiellement reçue ne peut pas être annulée : "
                "utilisez un retour de marchandises."
            )
        if commande.statut != CommandeAchat.Statut.ANNULEE:
            commande.statut = CommandeAchat.Statut.ANNULEE
            commande.save(update_fields=["statut"])
        return Response(
            CommandeAchatSerializer(
                commande,
                context=self.get_serializer_context(),
            ).data
        )


class ReceptionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ReceptionSerializer
    permission_classes = PERMISSIONS_FOURNISSEUR

    def get_queryset(self):
        queryset = (
            Reception.objects.filter(
                commande__fournisseur=_fournisseur(self.request)
            )
            .select_related("commande")
            .prefetch_related(
                "lignes__ligne_commande__produit",
                "lignes__ligne_commande__unite",
            )
            .order_by("-date_reception", "-id")
        )
        commande = self.request.query_params.get("commande")
        if commande:
            queryset = queryset.filter(commande_id=commande)
        return queryset


class MouvementStockViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MouvementStockSerializer
    permission_classes = PERMISSIONS_FOURNISSEUR

    def get_queryset(self):
        queryset = (
            MouvementStock.objects.filter(
                fournisseur=_fournisseur(self.request)
            )
            .select_related("produit", "vente", "reception")
            .order_by("-date_creation", "-id")
        )
        params = self.request.query_params
        produit = params.get("produit")
        if produit:
            queryset = queryset.filter(produit_id=produit)
        type_mouvement = params.get("type_mouvement")
        if type_mouvement:
            queryset = queryset.filter(type_mouvement=type_mouvement)
        return queryset


class VenteViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = PERMISSIONS_FOURNISSEUR

    def get_queryset(self):
        queryset = (
            Vente.objects.filter(fournisseur=_fournisseur(self.request))
            .select_related("client", "client__utilisateur", "fournisseur")
            .prefetch_related("lignes", "paiements")
            .order_by("-date_creation", "-id")
        )
        params = self.request.query_params
        statut = params.get("statut")
        if statut:
            queryset = queryset.filter(statut=statut)
        client = params.get("client")
        if client:
            queryset = queryset.filter(client_id=client)
        mode_paiement = params.get("mode_paiement")
        if mode_paiement:
            queryset = queryset.filter(mode_paiement=mode_paiement)
        payee = _booleen_parametre(params.get("payee"))
        if payee is True:
            queryset = queryset.filter(reste_a_payer__lte=0)
        elif payee is False:
            queryset = queryset.filter(reste_a_payer__gt=0)
        recherche = params.get("recherche")
        if recherche:
            queryset = queryset.filter(
                Q(reference__icontains=recherche)
                | Q(numero_facture__icontains=recherche)
                | Q(client__utilisateur__first_name__icontains=recherche)
                | Q(client__utilisateur__last_name__icontains=recherche)
                | Q(client__telephone__icontains=recherche)
            )
        date_debut = params.get("date_debut")
        if date_debut:
            queryset = queryset.filter(date_creation__date__gte=date_debut)
        date_fin = params.get("date_fin")
        if date_fin:
            queryset = queryset.filter(date_creation__date__lte=date_fin)
        return queryset

    def get_serializer_class(self):
        if self.action == "create":
            return VenteCreationSerializer
        if self.action == "paiements":
            if self.request.method == "POST":
                return PaiementVenteCreationSerializer
            return PaiementVenteSerializer
        return VenteSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vente = serializer.save()
        return Response(
            VenteSerializer(
                vente,
                context=self.get_serializer_context(),
            ).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get", "post"], url_path="paiements")
    def paiements(self, request, pk=None):
        vente = self.get_object()
        if request.method == "GET":
            queryset = vente.paiements.order_by("-date_creation", "-id")
            page = self.paginate_queryset(queryset)
            if page is not None:
                return self.get_paginated_response(
                    PaiementVenteSerializer(page, many=True).data
                )
            return Response(
                PaiementVenteSerializer(queryset, many=True).data
            )

        serializer = PaiementVenteCreationSerializer(
            data=request.data,
            context={"request": request, "vente": vente},
        )
        serializer.is_valid(raise_exception=True)
        paiement = serializer.save()
        return Response(
            PaiementVenteSerializer(paiement).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="annuler")
    def annuler(self, request, pk=None):
        vente = self.get_object()
        vente = annuler_vente(
            vente,
            motif=request.data.get("motif", ""),
            auteur=request.user,
        )
        return Response(
            VenteSerializer(
                vente,
                context=self.get_serializer_context(),
            ).data
        )

    @action(detail=True, methods=["get"], url_path="facture")
    def facture(self, request, pk=None):
        vente = self.get_object()
        facture = Facture.objects.filter(vente=vente).first()
        if facture is None:
            raise Http404("Aucune facture associée à cette vente.")
        return Response(
            FactureSerializer(
                facture,
                context=self.get_serializer_context(),
            ).data
        )


class FactureViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = FactureSerializer
    permission_classes = [permissions.IsAuthenticated, EstFournisseurOuClient]

    def get_queryset(self):
        utilisateur = self.request.user
        queryset = Facture.objects.select_related(
            "vente",
            "vente__client",
            "vente__client__utilisateur",
            "vente__fournisseur",
        )
        if utilisateur.role == "fournisseur":
            queryset = queryset.filter(
                vente__fournisseur=_fournisseur(self.request)
            )
        elif utilisateur.role == "client":
            queryset = queryset.filter(vente__client=_client(self.request))
        else:
            return Facture.objects.none()

        params = self.request.query_params
        recherche = params.get("recherche")
        if recherche:
            queryset = queryset.filter(
                Q(numero__icontains=recherche)
                | Q(client_nom__icontains=recherche)
                | Q(client_telephone__icontains=recherche)
            )
        payee = _booleen_parametre(params.get("payee"))
        if payee is True:
            queryset = queryset.filter(vente__reste_a_payer__lte=0)
        elif payee is False:
            queryset = queryset.filter(vente__reste_a_payer__gt=0)
        client = params.get("client")
        if client and utilisateur.role == "fournisseur":
            queryset = queryset.filter(vente__client_id=client)
        return queryset.order_by("-date_emission", "-id")

    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf(self, request, pk=None):
        facture = self.get_object()
        contenu = generer_pdf_facture(facture)
        response = FileResponse(
            BytesIO(contenu),
            content_type="application/pdf",
        )
        response["Content-Disposition"] = (
            f'inline; filename="facture-{facture.numero}.pdf"'
        )
        return response


class FactureParLienView(APIView):
    """Téléchargement public d'une facture via un lien signé temporaire."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "facture_lien"

    def get(self, request, token):
        try:
            donnees = lire_token_facture(token)
        except signing.BadSignature as exc:
            raise Http404("Lien de facture invalide ou expiré.") from exc

        facture = (
            Facture.objects.select_related(
                "vente",
                "vente__client",
                "vente__fournisseur",
            )
            .filter(
                pk=donnees.get("facture"),
                vente__client_id=donnees.get("client"),
            )
            .first()
        )
        if facture is None:
            raise Http404("Facture introuvable.")
        contenu = generer_pdf_facture(facture)
        response = FileResponse(
            BytesIO(contenu),
            content_type="application/pdf",
        )
        response["Content-Disposition"] = (
            f'inline; filename="facture-{facture.numero}.pdf"'
        )
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response


class NotificationClientViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = NotificationClientSerializer
    permission_classes = [permissions.IsAuthenticated, EstClientCommerce]

    def get_queryset(self):
        queryset = NotificationClient.objects.filter(
            client=_client(self.request)
        ).select_related("fournisseur", "vente", "facture")
        params = self.request.query_params
        lue = _booleen_parametre(params.get("lue"))
        if lue is not None:
            queryset = queryset.filter(lue=lue)
        type_notification = params.get("type_notification")
        if type_notification:
            queryset = queryset.filter(type_notification=type_notification)
        return queryset.order_by("-date_creation", "-id")

    @action(detail=True, methods=["post"], url_path="marquer-lue")
    def marquer_lue(self, request, pk=None):
        notification = self.get_object()
        notification.marquer_lue()
        return Response(NotificationClientSerializer(notification).data)

    @action(detail=False, methods=["post"], url_path="tout-marquer-lu")
    def tout_marquer_lu(self, request):
        maintenant = timezone.now()
        nombre = NotificationClient.objects.filter(
            client=_client(self.request),
            lue=False,
        ).update(
            lue=True,
            date_lecture=maintenant,
        )
        return Response(
            {
                "detail": "Toutes les notifications ont été marquées comme lues.",
                "mises_a_jour": nombre,
            }
        )

    @action(detail=False, methods=["get"], url_path="non-lues")
    def non_lues(self, request):
        return Response(
            {"nombre": self.get_queryset().filter(lue=False).count()}
        )


class NotificationExterneViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationExterneSerializer
    permission_classes = [permissions.IsAuthenticated, EstFournisseurCommerce]

    def get_queryset(self):
        queryset = NotificationExterne.objects.filter(
            notification__fournisseur=_fournisseur(self.request)
        ).select_related(
            "notification",
            "notification__client",
            "notification__fournisseur",
        )
        params = self.request.query_params
        statut = params.get("statut")
        if statut:
            queryset = queryset.filter(statut=statut)
        canal = params.get("canal")
        if canal:
            queryset = queryset.filter(canal=canal)
        return queryset.order_by("-date_creation", "-id")

    @action(detail=True, methods=["post"], url_path="renvoyer")
    def renvoyer(self, request, pk=None):
        envoi = self.get_object()
        envois = renvoyer_notifications_externes(envoi.notification)
        return Response(
            NotificationExterneSerializer(envois, many=True).data
        )


class DetteCommercialeViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DetteCommercialeSerializer
    permission_classes = [permissions.IsAuthenticated, EstFournisseurCommerce]

    def get_queryset(self):
        queryset = DetteCommerciale.objects.filter(
            fournisseur=_fournisseur(self.request)
        ).select_related("client", "client__utilisateur", "vente")
        params = self.request.query_params
        statut = params.get("statut")
        if statut:
            queryset = queryset.filter(statut=statut)
        client = params.get("client")
        if client:
            queryset = queryset.filter(client_id=client)
        recherche = params.get("recherche")
        if recherche:
            queryset = queryset.filter(
                Q(client__utilisateur__first_name__icontains=recherche)
                | Q(client__utilisateur__last_name__icontains=recherche)
                | Q(client__telephone__icontains=recherche)
                | Q(vente__numero_facture__icontains=recherche)
            )
        return queryset.order_by("-date_creation", "-id")


class ConfigurationNotificationView(APIView):
    permission_classes = [
        permissions.IsAuthenticated,
        EstFournisseurCommerce,
        AbonnementActifPourEcriture,
    ]

    def _obtenir(self, request):
        configuration, _ = ConfigurationNotification.objects.get_or_create(
            fournisseur=_fournisseur(request)
        )
        return configuration

    def get(self, request):
        return Response(
            ConfigurationNotificationSerializer(self._obtenir(request)).data
        )

    def patch(self, request):
        configuration = self._obtenir(request)
        serializer = ConfigurationNotificationSerializer(
            configuration,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def put(self, request):
        configuration = self._obtenir(request)
        serializer = ConfigurationNotificationSerializer(
            configuration,
            data=request.data,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class StatistiquesCommerceView(APIView):
    permission_classes = [permissions.IsAuthenticated, EstFournisseurCommerce]

    def get(self, request):
        return Response(statistiques_fournisseur(_fournisseur(request)))


class StatistiquesCommerceAdminView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        return Response(statistiques_globales_commerce())
