from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.abonnements.models import PlanAbonnement

PLANS = [
    {
        "code": "simple",
        "nom": "Simple",
        "description": "Plan d'entrée pour les petites structures.",
        "prix": Decimal("5000.00"),
        "devise": "XOF",
        "duree_jours": 30,
        "max_clients": 100,
        "max_agents": 2,
        "est_essai": False,
        "actif": True,
    },
    {
        "code": "premium",
        "nom": "Premium",
        "description": "Plan intermédiaire pour les structures en croissance.",
        "prix": Decimal("10000.00"),
        "devise": "XOF",
        "duree_jours": 30,
        "max_clients": 500,
        "max_agents": 5,
        "est_essai": False,
        "actif": True,
    },
    {
        "code": "pro",
        "nom": "Pro",
        "description": "Plan avancé avec quotas illimités.",
        "prix": Decimal("15000.00"),
        "devise": "XOF",
        "duree_jours": 30,
        "max_clients": None,
        "max_agents": None,
        "est_essai": False,
        "actif": True,
    },
]


class Command(BaseCommand):
    help = (
        "Crée les plans Simple, Premium et Pro sans doublon. "
        "Les plans existants et le plan d'essai ne sont jamais modifiés."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        crees = 0
        conserves = 0

        for definition in PLANS:
            code = definition["code"]
            plan = PlanAbonnement.objects.filter(code=code).first()
            if plan is not None:
                conserves += 1
                self.stdout.write(
                    f"- {code} : déjà existant, conservé tel quel "
                    f"({plan.prix} {plan.devise})."
                )
                continue
            PlanAbonnement.objects.create(**definition)
            crees += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"- {code} : créé ({definition['prix']} "
                    f"{definition['devise']}, {definition['duree_jours']} jours)."
                )
            )

        essai = PlanAbonnement.objects.filter(est_essai=True, actif=True).first()
        if essai is None:
            self.stdout.write(
                self.style.WARNING(
                    "Aucun plan d'essai actif n'est configuré. "
                    "Il n'a pas été recréé par cette commande."
                )
            )
        else:
            self.stdout.write(f"- essai : conservé ({essai.nom}).")

        self.stdout.write(
            self.style.SUCCESS(
                f"Terminé : {crees} plan(s) créé(s), {conserves} conservé(s)."
            )
        )
