from django.core.management.base import BaseCommand

from apps.abonnements.demandes import expirer_demandes_abonnement


class Command(BaseCommand):
    help = (
        "Expire les demandes d'abonnement non payées dont le délai est dépassé. "
        "Aucun abonnement n'est créé ou prolongé par cette opération."
    )

    def handle(self, *args, **options):
        expirees = expirer_demandes_abonnement()
        self.stdout.write(
            self.style.SUCCESS(f"{expirees} demande(s) d'abonnement expirée(s).")
        )
