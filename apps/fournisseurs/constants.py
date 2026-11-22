from django.db import models


class Pays(models.TextChoices):
    SENEGAL = "SN", "Sénégal"
    COTE_DIVOIRE = "CI", "Côte d'Ivoire"
    GUINEE = "GN", "Guinée"


DEVISE_PAR_PAYS = {
    Pays.SENEGAL: "XOF",
    Pays.COTE_DIVOIRE: "XOF",
    Pays.GUINEE: "GNF",
}

PRESTATAIRES_PAR_PAYS = {
    Pays.SENEGAL: ("paytech", "wave", "orange_money"),
    Pays.COTE_DIVOIRE: ("paytech", "wave", "orange_money"),
    Pays.GUINEE: ("wave", "orange_money"),
}

PAYS_PAR_DEVISE = {
    "XOF": (Pays.SENEGAL, Pays.COTE_DIVOIRE),
    "GNF": (Pays.GUINEE,),
}


def devise_pour_pays(pays):
    return DEVISE_PAR_PAYS.get(pays, "XOF")


def prestataires_pour_pays(pays):
    return PRESTATAIRES_PAR_PAYS.get(pays, ())
