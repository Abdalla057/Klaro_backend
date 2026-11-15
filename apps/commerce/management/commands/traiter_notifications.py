import time

from django.core.management.base import BaseCommand

from apps.commerce.notifications import traiter_envois_dus


class Command(BaseCommand):
    help = (
        "Traite les notifications externes en attente (SMS/WhatsApp) avec "
        "retries et backoff. Utilisable en cron ou en boucle continue."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limite",
            type=int,
            default=50,
            help="Nombre maximum d'envois traités par passage.",
        )
        parser.add_argument(
            "--boucle",
            action="store_true",
            help="Reste actif et traite en continu (worker simple).",
        )
        parser.add_argument(
            "--intervalle",
            type=float,
            default=5,
            help="Secondes entre deux passages en mode boucle.",
        )

    def handle(self, *args, **options):
        while True:
            envois = traiter_envois_dus(limite=options["limite"])
            if envois:
                envoyes = sum(
                    1 for envoi in envois if envoi.statut == "ENVOYE"
                )
                echoues = sum(
                    1 for envoi in envois if envoi.statut == "ECHOUE"
                )
                self.stdout.write(
                    f"{len(envois)} envoi(s) traité(s) : "
                    f"{envoyes} envoyé(s), {echoues} échoué(s)."
                )
            if not options["boucle"]:
                return
            time.sleep(options["intervalle"])
