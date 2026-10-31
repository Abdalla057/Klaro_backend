import re

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers


def normaliser_telephone(value):
    """Retire les espaces et séparateurs usuels et normalise le préfixe 00."""
    if value is None:
        return value
    numero = re.sub(r"[\s.\-()/]", "", str(value).strip())
    if numero.startswith("00"):
        numero = "+" + numero[2:]
    return numero


def valider_mot_de_passe(value, user=None):
    """Applique les validateurs Django et convertit les erreurs pour DRF."""
    try:
        validate_password(value, user=user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(exc.messages) from exc
    return value
