from datetime import timedelta

from django.db import migrations
from django.utils import timezone

NOTE_TRIAL_MIGRATION = "Essai attribué automatiquement lors de la migration."


def creer_plan_essai(apps, schema_editor):
    PlanAbonnement = apps.get_model("abonnements", "PlanAbonnement")
    Abonnement = apps.get_model("abonnements", "Abonnement")
    EvenementAbonnement = apps.get_model("abonnements", "EvenementAbonnement")
    Fournisseur = apps.get_model("fournisseurs", "Fournisseur")

    plan, _ = PlanAbonnement.objects.get_or_create(
        code="essai",
        defaults={
            "nom": "Essai gratuit",
            "description": (
                "Période d'essai offerte à chaque nouvelle entreprise "
                "lors de son inscription."
            ),
            "prix": 0,
            "devise": "XOF",
            "duree_jours": 30,
            "max_clients": 50,
            "max_agents": 1,
            "est_essai": True,
            "actif": True,
        },
    )

    maintenant = timezone.now()
    for fournisseur in Fournisseur.objects.all().iterator():
        if Abonnement.objects.filter(
            fournisseur_id=fournisseur.pk,
            statut="ACTIF",
        ).exists():
            continue
        if Abonnement.objects.filter(
            fournisseur_id=fournisseur.pk,
            est_essai=True,
        ).exists():
            continue

        abonnement = Abonnement.objects.create(
            fournisseur_id=fournisseur.pk,
            plan_id=plan.pk,
            date_debut=maintenant,
            date_fin=maintenant + timedelta(days=plan.duree_jours),
            statut="ACTIF",
            est_essai=True,
            montant=0,
            devise=plan.devise,
            notes=NOTE_TRIAL_MIGRATION,
        )
        EvenementAbonnement.objects.create(
            abonnement_id=abonnement.pk,
            type_evenement="CREATION",
            nouveau_statut="ACTIF",
            motif=NOTE_TRIAL_MIGRATION,
            montant=0,
        )


def supprimer_plan_essai(apps, schema_editor):
    PlanAbonnement = apps.get_model("abonnements", "PlanAbonnement")
    Abonnement = apps.get_model("abonnements", "Abonnement")

    Abonnement.objects.filter(notes=NOTE_TRIAL_MIGRATION).delete()
    PlanAbonnement.objects.filter(
        code="essai",
        abonnements__isnull=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("abonnements", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(creer_plan_essai, supprimer_plan_essai),
    ]
