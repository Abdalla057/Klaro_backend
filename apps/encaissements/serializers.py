from decimal import Decimal

from rest_framework import serializers

from apps.clients.models import Client

from .models import Encaissement
from .services import creer_encaissement


class EncaissementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Encaissement
        fields = [
            "id",
            "client",
            "agent",
            "montant",
            "statut",
            "code_confirmation",
            "date_creation",
            "date_confirmation",
            "commentaire",
        ]
        # Le client ne fournit que "montant" (et "commentaire") à la création.
        # "client" est déduit de l'utilisateur connecté, "agent"/"statut" sont
        # fixés uniquement par les services de confirmation/annulation.
        read_only_fields = [
            "client",
            "agent",
            "statut",
            "code_confirmation",
            "date_creation",
            "date_confirmation",
        ]

    def create(self, validated_data):
        client = validated_data.pop("client", None)
        return creer_encaissement(client=client, **validated_data)


class EncaissementAgentSerializer(serializers.Serializer):
    client = serializers.PrimaryKeyRelatedField(queryset=Client.objects.all())
    montant = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    commentaire = serializers.CharField(required=False, allow_blank=True, default="")