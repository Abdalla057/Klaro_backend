from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.comptes.serializers import ActivationParCodeSerializer
from apps.comptes.validators import normaliser_telephone

from .models import Agent
from .services import activer_agent, creer_agent

Utilisateur = get_user_model()


class AgentSerializer(serializers.ModelSerializer):
    nom = serializers.CharField(required=False, allow_blank=True)
    prenom = serializers.CharField(required=False, allow_blank=True)
    code_activation = serializers.SerializerMethodField()

    class Meta:
        model = Agent
        fields = [
            "id",
            "utilisateur",
            "fournisseur",
            "nom",
            "prenom",
            "telephone",
            "zone",
            "compte_active",
            "code_activation",
            "date_creation",
            "date_activation",
        ]
        read_only_fields = [
            "utilisateur",
            "fournisseur",
            "telephone",
            "compte_active",
            "code_activation",
            "date_creation",
            "date_activation",
        ]

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


class AgentCreationSerializer(serializers.ModelSerializer):
    nom = serializers.CharField(required=False, allow_blank=True, default="")
    prenom = serializers.CharField(required=False, allow_blank=True, default="")
    code_activation = serializers.SerializerMethodField()

    class Meta:
        model = Agent
        fields = [
            "id",
            "nom",
            "prenom",
            "telephone",
            "zone",
            "compte_active",
            "code_activation",
            "date_creation",
        ]
        read_only_fields = [
            "compte_active",
            "code_activation",
            "date_creation",
        ]

    def validate_telephone(self, value):
        value = normaliser_telephone(value)
        if Agent.objects.filter(telephone=value).exists():
            raise serializers.ValidationError(
                "Ce numéro est déjà enregistré comme agent."
            )
        if Utilisateur.objects.filter(telephone=value).exists():
            raise serializers.ValidationError(
                "Un compte existe déjà avec ce numéro."
            )
        return value

    def get_code_activation(self, obj):
        return getattr(obj, "_code_activation_clair", None)

    def create(self, validated_data):
        fournisseur = validated_data.pop("fournisseur")
        return creer_agent(fournisseur=fournisseur, **validated_data)


class AgentActivationSerializer(ActivationParCodeSerializer):
    def create(self, validated_data):
        return activer_agent(**validated_data)
