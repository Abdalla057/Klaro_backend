from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.abonnements.models import Abonnement


class Command(BaseCommand):
    help = (
        "Marque comme expirés les abonnements actifs dont la date de fin "
        "est dépassée et journalise l'événement d'expiration."
    )

    def handle(self, *args, **options):
        maintenant = timezone.now()
        identifiants = list(
            Abonnement.objects.filter(
                statut__in=[Abonnement.Statut.ACTIF, Abonnement.Statut.SUSPENDU],
                date_fin__lte=maintenant,
            ).values_list("pk", flat=True)
        )

        expires = 0
        for abonnement_id in identifiants:
            with transaction.atomic():
                abonnement = (
                    Abonnement.objects.select_for_update()
                    .filter(pk=abonnement_id)
                    .first()
                )
                if (
                    abonnement is None
                    or abonnement.statut
                    not in (Abonnement.Statut.ACTIF, Abonnement.Statut.SUSPENDU)
                    or abonnement.date_fin > maintenant
                ):
                    continue
                abonnement.actualiser_statut()
                expires += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"{expires} abonnement(s) expiré(s) sur {len(identifiants)} "
                "candidat(s)."
            )
        )
