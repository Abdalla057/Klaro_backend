from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError

from .validators import normaliser_telephone, valider_mot_de_passe


def _modeles_activation():
    from apps.agents.models import Agent
    from apps.clients.models import Client

    return (Agent, Client)


def trouver_profil_activation(telephone, verrou=False):
    telephone = normaliser_telephone(telephone)
    for modele in _modeles_activation():
        queryset = modele.objects.select_related("utilisateur")
        if verrou:
            queryset = queryset.select_for_update()
        profil = queryset.filter(telephone=telephone).first()
        if profil is not None:
            return profil
    return None


def _profil_ou_erreur(telephone, verrou=False):
    profil = trouver_profil_activation(telephone, verrou=verrou)
    if profil is None:
        raise NotFound(
            "Aucun compte client ou agent ne correspond à ce numéro de téléphone."
        )
    return profil


def verifier_code_activation(telephone, code):
    # Pas de transaction englobante : un échec doit persister le compteur de
    # tentatives, même si la réponse HTTP est une erreur.
    profil = _profil_ou_erreur(telephone, verrou=False)
    try:
        token = profil.verifier_code(code)
    except DjangoValidationError as exc:
        raise ValidationError(exc.messages) from exc
    return profil, token


@transaction.atomic
def definir_mot_de_passe_activation(telephone, token, password, confirmation=None):
    profil = _profil_ou_erreur(telephone, verrou=True)
    valider_mot_de_passe(password, user=profil.utilisateur)
    try:
        profil.activer_avec_jeton(token, password)
    except DjangoValidationError as exc:
        raise ValidationError(exc.messages) from exc
    return profil


@transaction.atomic
def activer_par_code(telephone, code, password):
    profil = _profil_ou_erreur(telephone, verrou=True)
    valider_mot_de_passe(password, user=profil.utilisateur)
    try:
        profil.activer(code, password)
    except DjangoValidationError as exc:
        raise ValidationError(exc.messages) from exc
    return profil


@transaction.atomic
def renvoyer_code_activation(telephone):
    profil = _profil_ou_erreur(telephone, verrou=True)
    if profil.compte_active:
        raise ValidationError("Ce compte est déjà activé.")
    code = profil.generer_nouveau_code()
    return profil, code


def statut_activation(telephone):
    profil = trouver_profil_activation(telephone)
    if profil is None:
        raise NotFound(
            "Aucun compte client ou agent ne correspond à ce numéro de téléphone."
        )
    return {
        "telephone": profil.telephone,
        "role": profil.utilisateur.role,
        "compte_active": profil.compte_active,
        "date_creation": profil.date_creation,
        "date_activation": profil.date_activation,
        "code_expiration": (
            profil.code_expiration if not profil.compte_active else None
        ),
        "tentatives_restantes": (
            max(0, 5 - profil.code_tentatives)
            if not profil.compte_active
            else 0
        ),
    }
