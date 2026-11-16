from django.db import migrations


def creer_unites_base_manquantes(apps, schema_editor):
    Produit = apps.get_model("commerce", "Produit")
    ProduitUnite = apps.get_model("commerce", "ProduitUnite")

    for produit in Produit.objects.iterator():
        unites = ProduitUnite.objects.filter(produit_id=produit.pk)
        base = unites.filter(est_base=True).first()

        if base is None:
            candidat = unites.filter(nom__iexact=produit.unite_base).first()
            if candidat is None:
                ProduitUnite.objects.create(
                    produit_id=produit.pk,
                    nom=produit.unite_base,
                    facteur_conversion=1,
                    prix_vente=None,
                    est_base=True,
                )
            elif candidat.facteur_conversion == 1:
                candidat.est_base = True
                candidat.prix_vente = None
                candidat.save(update_fields=["est_base", "prix_vente"])
        elif base.prix_vente is not None:
            base.prix_vente = None
            base.save(update_fields=["prix_vente"])


class Migration(migrations.Migration):

    dependencies = [
        ("commerce", "0003_normalisation_donnees"),
    ]

    operations = [
        migrations.RunPython(
            creer_unites_base_manquantes,
            migrations.RunPython.noop,
        ),
    ]
