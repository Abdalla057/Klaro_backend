from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from rest_framework import serializers

from apps.abonnements.exceptions import InscriptionIndisponible
from apps.abonnements.services import demarrer_essai
from apps.comptes.validators import normaliser_telephone, valider_mot_de_passe

from .models import Fournisseur

Utilisateur = get_user_model()


class FournisseurSerializer(serializers.ModelSerializer):
    """Utilisé pour la lecture et la mise à jour du profil (pas la création)."""

    class Meta:
        model = Fournisseur
        fields = [
            "id",
            "utilisateur",
            "nom_entreprise",
            "code_entreprise",
            "adresse",
            "telephone",
            "est_suspendu",
            "date_suspension",
            "motif_suspension",
            "date_creation",
        ]
        # utilisateur ne doit jamais être réassigné après la création ;
        # code_entreprise est généré automatiquement, jamais modifiable ;
        # les champs de suspension sont pilotés exclusivement par le
        # super administrateur.
        read_only_fields = [
            "utilisateur",
            "telephone",
            "code_entreprise",
            "est_suspendu",
            "date_suspension",
            "motif_suspension",
            "date_creation",
        ]


class FournisseurInscriptionSerializer(serializers.ModelSerializer):
    """Inscription publique : crée le compte et le profil en une transaction.

    Le numéro de téléphone sert à la fois d'identifiant de connexion et de
    coordonnée de l'entreprise. Un essai gratuit est immédiatement attribué.
    """

    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = Fournisseur
        fields = [
            "id",
            "password",
            "nom_entreprise",
            "code_entreprise",
            "adresse",
            "telephone",
            "date_creation",
        ]
        read_only_fields = ["code_entreprise", "date_creation"]
        extra_kwargs = {
            "telephone": {
                "required": True,
                "allow_blank": False,
            },
        }

    def validate_telephone(self, value):
        value = normaliser_telephone(value)
        if Utilisateur.objects.filter(telephone=value).exists():
            raise serializers.ValidationError(
                "Un compte existe déjà avec ce numéro de téléphone."
            )
        if Fournisseur.objects.filter(telephone=value).exists():
            raise serializers.ValidationError(
                "Ce numéro est déjà utilisé par une entreprise."
            )
        return value

    def validate_password(self, value):
        telephone = normaliser_telephone(
            self.initial_data.get("telephone", "")
        )
        utilisateur = Utilisateur(
            telephone=telephone,
            username=telephone,
            role="fournisseur",
        )
        return valider_mot_de_passe(value, user=utilisateur)

    @transaction.atomic
    def create(self, validated_data):
        telephone = validated_data["telephone"]
        password = validated_data.pop("password")

        utilisateur = Utilisateur.objects.create_user(
            telephone=telephone,
            password=password,
            role="fournisseur",
        )
        fournisseur = Fournisseur.objects.create(
            utilisateur=utilisateur,
            **validated_data,
        )
        try:
            demarrer_essai(fournisseur)
        except ImproperlyConfigured:
            raise InscriptionIndisponible()
        return fournisseur
