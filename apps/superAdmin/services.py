from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.abonnements.models import Abonnement
from apps.abonnements.services import etat_abonnement_fournisseur
from apps.agents.models import Agent
from apps.clients.models import Client
from apps.encaissements.models import Encaissement
from apps.fournisseurs.models import Fournisseur

MONTANT_DECIMAL = DecimalField(max_digits=12, decimal_places=2)


def _agreger_encaissements(queryset):
    return queryset.aggregate(
        total=Count("id"),
        en_attente=Count(
            "id",
            filter=Q(statut=Encaissement.Statut.EN_ATTENTE),
        ),
        confirmes=Count(
            "id",
            filter=Q(statut=Encaissement.Statut.CONFIRME),
        ),
        annules=Count(
            "id",
            filter=Q(statut=Encaissement.Statut.ANNULE),
        ),
        montant_confirme=Coalesce(
            Sum(
                "montant",
                filter=Q(statut=Encaissement.Statut.CONFIRME),
            ),
            Value(Decimal("0")),
            output_field=MONTANT_DECIMAL,
        ),
        montant_en_attente=Coalesce(
            Sum(
                "montant",
                filter=Q(statut=Encaissement.Statut.EN_ATTENTE),
            ),
            Value(Decimal("0")),
            output_field=MONTANT_DECIMAL,
        ),
    )


def statistiques_fournisseur(fournisseur):
    clients = Client.objects.filter(fournisseur=fournisseur)
    agents = Agent.objects.filter(fournisseur=fournisseur)
    encaissements = Encaissement.objects.filter(
        client__fournisseur=fournisseur
    )

    return {
        "fournisseur": fournisseur,
        "clients": {
            "total": clients.count(),
            "actifs": clients.filter(compte_active=True).count(),
        },
        "agents": {
            "total": agents.count(),
            "actifs": agents.filter(compte_active=True).count(),
        },
        "encaissements": _agreger_encaissements(encaissements),
        "abonnement": etat_abonnement_fournisseur(fournisseur),
    }


def statistiques_globales():
    maintenant = timezone.now()
    abonnements = Abonnement.objects.all()

    return {
        "fournisseurs": {
            "total": Fournisseur.objects.count(),
            "actifs": Fournisseur.objects.filter(est_suspendu=False).count(),
            "suspendus": Fournisseur.objects.filter(est_suspendu=True).count(),
        },
        "clients": {
            "total": Client.objects.count(),
            "actifs": Client.objects.filter(compte_active=True).count(),
        },
        "agents": {
            "total": Agent.objects.count(),
            "actifs": Agent.objects.filter(compte_active=True).count(),
        },
        "encaissements": _agreger_encaissements(Encaissement.objects.all()),
        "abonnements": {
            "actifs": abonnements.filter(
                statut=Abonnement.Statut.ACTIF,
                date_debut__lte=maintenant,
                date_fin__gt=maintenant,
            ).count(),
            "expires": abonnements.filter(
                statut=Abonnement.Statut.EXPIRE
            ).count(),
            "suspendus": abonnements.filter(
                statut=Abonnement.Statut.SUSPENDU
            ).count(),
            "annules": abonnements.filter(
                statut=Abonnement.Statut.ANNULE
            ).count(),
            "essais_actifs": abonnements.filter(
                est_essai=True,
                statut=Abonnement.Statut.ACTIF,
                date_debut__lte=maintenant,
                date_fin__gt=maintenant,
            ).count(),
        },
        "montant_facture_total": abonnements.filter(
            est_essai=False
        ).aggregate(
            total=Coalesce(
                Sum("montant"),
                Value(Decimal("0")),
                output_field=MONTANT_DECIMAL,
            )
        )["total"],
    }
