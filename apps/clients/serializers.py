from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.comptes.serializers import ActivationParCodeSerializer
from apps.comptes.validators import normaliser_telephone

from .models import Client
from .services import activer_client, creer_client

Utilisateur = get_user_model()


class ClientSerializer(serializers.ModelSerializer):
    nom = serializers.CharField(required=False)
    prenom = serializers.CharField(required=False)
    solde_disponible = serializers.SerializerMethodField()
    code_activation = serializers.SerializerMethodField()

    class Meta:
        model = Client
        fields = [
            "id",
            "utilisateur",
            "fournisseur",
            "nom",
            "prenom",
            "telephone",
            "adresse",
            "solde_du",
            "solde_disponible",
            "compte_active",
            "code_activation",
            "date_creation",
            "date_activation",
        ]
        read_only_fields = [
            "utilisateur",
            "fournisseur",
            "telephone",
            "solde_du",
            "solde_disponible",
            "compte_active",
            "code_activation",
            "date_creation",
            "date_activation",
        ]

    def get_solde_disponible(self, obj):
        from apps.finances.services import solde_disponible_client

        return solde_disponible_client(obj)

    def get_code_activation(self, obj):
        return getattr(obj, "_code_activation_clair", None)

    def update(self, instance, validated_data):
        nom = validated_data.pop("nom", None)
        prenom = validated_data.pop("prenom", None)
        instance = super().update(instance, validated_data)
        champs = []
        if nom is not None:
            instance.utilisateur.last_name = nom
            champs.append("last_name")
        if prenom is not None:
            instance.utilisateur.first_name = prenom
            champs.append("first_name")
        if champs:
            instance.utilisateur.save(update_fields=champs)
        return instance


class ClientCreationSerializer(serializers.ModelSerializer):
    nom = serializers.CharField()
    prenom = serializers.CharField()
    adresse = serializers.CharField()
    solde_disponible = serializers.SerializerMethodField()
    code_activation = serializers.SerializerMethodField()

    class Meta:
        model = Client
        fields = [
            "id",
            "nom",
            "prenom",
            "telephone",
            "adresse",
            "solde_du",
            "solde_disponible",
            "compte_active",
            "code_activation",
            "date_creation",
        ]
        read_only_fields = [
            "solde_du",
            "solde_disponible",
            "compte_active",
            "code_activation",
            "date_creation",
        ]

    def validate_telephone(self, value):
        value = normaliser_telephone(value)
        if Client.objects.filter(telephone=value).exists():
            raise serializers.ValidationError(
                "Ce numéro est déjà enregistré comme client."
            )
        if Utilisateur.objects.filter(telephone=value).exists():
            raise serializers.ValidationError(
                "Un compte existe déjà avec ce numéro."
            )
        return value

    def get_solde_disponible(self, obj):
        from apps.finances.services import solde_disponible_client

        return solde_disponible_client(obj)

    def get_code_activation(self, obj):
        return getattr(obj, "_code_activation_clair", None)

    def create(self, validated_data):
        fournisseur = validated_data.pop("fournisseur")
        return creer_client(fournisseur=fournisseur, **validated_data)


class ClientActivationSerializer(ActivationParCodeSerializer):
    def create(self, validated_data):
        return activer_client(**validated_data)
