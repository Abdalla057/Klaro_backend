"""Calcul centralisé des montants d'abonnement par pays.

Le catalogue commercial reste exprimé en XOF. La Guinée reçoit un montant
converti en GNF à partir d'un taux configurable, figé au moment de la demande.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.conf import settings
from rest_framework.exceptions import ValidationError

from apps.fournisseurs.constants import Pays, devise_pour_pays

DEVISE_REFERENCE = "XOF"
PAYS_SANS_DECIMALES = {"XOF", "GNF"}


def _decimal(value, defaut="0"):
    try:
        resultat = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(defaut)
    if not resultat.is_finite():
        return Decimal(defaut)
    return resultat


def quantizer_montant(montant, devise):
    """Applique la précision réelle de la devise (XOF/GNF sans décimales)."""
    montant = _decimal(montant)
    if devise in PAYS_SANS_DECIMALES:
        return montant.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return montant.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def taux_conversion_xof_gnf():
    taux = _decimal(
        getattr(settings, "ABONNEMENT_TAUX_XOF_GNF", "0"),
    )
    if taux <= 0:
        raise ValidationError(
            "Le taux de conversion XOF vers GNF n'est pas configuré."
        )
    return taux


def calculer_montant_plan(plan, pays):
    """Retourne les montants et la devise locale d'un plan pour un pays."""
    pays = pays or Pays.SENEGAL
    devise = devise_pour_pays(pays)
    montant_reference = quantizer_montant(plan.prix, DEVISE_REFERENCE)

    if devise == DEVISE_REFERENCE:
        return {
            "pays": pays,
            "devise": devise,
            "montant": montant_reference,
            "montant_reference": montant_reference,
            "taux_conversion": Decimal("1"),
        }

    taux = taux_conversion_xof_gnf()
    montant = quantizer_montant(montant_reference * taux, devise)
    return {
        "pays": pays,
        "devise": devise,
        "montant": montant,
        "montant_reference": montant_reference,
        "taux_conversion": taux,
    }
