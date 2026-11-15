from django.db import migrations


def normaliser_donnees(apps, schema_editor):
    Facture = apps.get_model("commerce", "Facture")
    Vente = apps.get_model("commerce", "Vente")
    PaiementVente = apps.get_model("commerce", "PaiementVente")
    Abonnement = apps.get_model("abonnements", "Abonnement")

    for facture in Facture.objects.select_related("vente").iterator():
        fournisseur_id = facture.vente.fournisseur_id
        abonnement = (
            Abonnement.objects.filter(fournisseur_id=fournisseur_id)
            .select_related("plan")
            .order_by("-date_debut", "-id")
            .first()
        )
        devise = None
        if abonnement is not None:
            devise = abonnement.devise or (
                abonnement.plan.devise if abonnement.plan_id else None
            )
        if devise:
            facture.devise = devise
            facture.save(update_fields=["devise"])

    modes_reels = {"ESPECES", "WAVE", "ORANGE_MONEY", "VIREMENT"}
    for vente in Vente.objects.filter(mode_paiement="PARTIEL").iterator():
        paiement = (
            PaiementVente.objects.filter(
                vente_id=vente.pk,
                mode_paiement__in=modes_reels,
            )
            .order_by("date_creation", "id")
            .first()
        )
        if paiement is not None:
            vente.mode_paiement = paiement.mode_paiement
            vente.save(update_fields=["mode_paiement"])


class Migration(migrations.Migration):

    dependencies = [
        ("commerce", "0002_facture_devise_and_more"),
        ("abonnements", "0003_paiementabonnement"),
    ]

    operations = [
        migrations.RunPython(normaliser_donnees, migrations.RunPython.noop),
    ]
