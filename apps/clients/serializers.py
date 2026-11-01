from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.comptes.serializers import ActivationParCodeSerializer
from apps.comptes.validators import normaliser_telephone

from .models import Client
from .services import activer_client, creer_client

Utilisateur = get_user_model()


def montant_credit_initial(obj):
    if hasattr(obj, "_montant_credit_initial"):
        montant = obj._montant_credit_initial
    else:
        from apps.finances.models import TransactionFinanciere

        montant = (
            TransactionFinanciere.objects.filter(
                client=obj,
                type_transaction=TransactionFinanciere.Type.ATTRIBUTION_INITIALE,
                statut=TransactionFinanciere.Statut.VALIDEE,
            )
            .order_by("date_creation")
            .values_list("montant", flat=True)
            .first()
        )
    return str(montant) if montant is not None else None


class ClientSerializer(serializers.ModelSerializer):
    nom = serializers.CharField(required=False)
    prenom = serializers.CharField(required=False)
    solde_disponible = serializers.SerializerMethodField()
    montant_credit_initial = serializers.SerializerMethodField()
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
            "montant_credit_initial",
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
            "montant_credit_initial",
            "compte_active",
            "code_activation",
            "date_creation",
            "date_activation",
        ]

    def get_solde_disponible(self, obj):
        from apps.finances.services import solde_disponible_client

        return solde_disponible_client(obj)

    def get_montant_credit_initial(self, obj):
        return montant_credit_initial(obj)

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
    solde_initial = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
        write_only=True,
    )
    solde_disponible = serializers.SerializerMethodField()
    montant_credit_initial = serializers.SerializerMethodField()
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
            "solde_initial",
            "montant_credit_initial",
            "compte_active",
            "code_activation",
            "date_creation",
        ]
        read_only_fields = [
            "solde_du",
            "solde_disponible",
            "montant_credit_initial",
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

    def get_montant_credit_initial(self, obj):
        return montant_credit_initial(obj)

    def get_code_activation(self, obj):
        return getattr(obj, "_code_activation_clair", None)

    def create(self, validated_data):
        fournisseur = validated_data.pop("fournisseur")
        solde_initial = validated_data.pop("solde_initial", None)
        request = self.context.get("request")
        return creer_client(
            fournisseur=fournisseur,
            solde_initial=solde_initial,
            auteur=request.user if request else None,
            **validated_data,
        )


class ClientActivationSerializer(ActivationParCodeSerializer):
    def create(self, validated_data):
        return activer_client(**validated_data)
