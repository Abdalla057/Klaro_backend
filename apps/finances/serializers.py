from rest_framework import serializers

from apps.clients.models import Client

from .models import DemandeDecaissement, TransactionFinanciere


class TransactionFinanciereSerializer(serializers.ModelSerializer):
    type_transaction_display = serializers.CharField(
        source="get_type_transaction_display",
        read_only=True,
    )
    sens_display = serializers.CharField(source="get_sens_display", read_only=True)
    statut_display = serializers.CharField(
        source="get_statut_display",
        read_only=True,
    )
    client_nom = serializers.SerializerMethodField()
    client_telephone = serializers.CharField(
        source="client.telephone",
        read_only=True,
    )
    agent_nom = serializers.SerializerMethodField()

    class Meta:
        model = TransactionFinanciere
        fields = [
            "id",
            "reference",
            "fournisseur",
            "client",
            "client_nom",
            "client_telephone",
            "agent",
            "agent_nom",
            "encaissement",
            "decaissement",
            "type_transaction",
            "type_transaction_display",
            "sens",
            "sens_display",
            "montant",
            "statut",
            "statut_display",
            "commentaire",
            "date_creation",
            "date_validation",
        ]
        read_only_fields = fields

    def get_client_nom(self, obj):
        return obj.client.nom_complet

    def get_agent_nom(self, obj):
        if obj.agent_id is None:
            return None
        return obj.agent.nom_complet


class DemandeDecaissementSerializer(serializers.ModelSerializer):
    statut_display = serializers.CharField(
        source="get_statut_display",
        read_only=True,
    )
    client_nom = serializers.SerializerMethodField()
    client_telephone = serializers.CharField(
        source="client.telephone",
        read_only=True,
    )
    agent_nom = serializers.SerializerMethodField()

    class Meta:
        model = DemandeDecaissement
        fields = [
            "id",
            "reference",
            "fournisseur",
            "client",
            "client_nom",
            "client_telephone",
            "agent",
            "agent_nom",
            "montant",
            "statut",
            "statut_display",
            "commentaire",
            "motif_refus",
            "date_creation",
            "date_confirmation_agent",
            "date_decision",
            "date_paiement",
        ]
        read_only_fields = fields

    def get_client_nom(self, obj):
        return obj.client.nom_complet

    def get_agent_nom(self, obj):
        if obj.agent_id is None:
            return None
        return obj.agent.nom_complet


class CreationDemandeDecaissementSerializer(serializers.Serializer):
    montant = serializers.DecimalField(max_digits=14, decimal_places=2)
    commentaire = serializers.CharField(required=False, allow_blank=True, default="")
    cle_idempotence = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
    )


class RefusDemandeSerializer(serializers.Serializer):
    motif = serializers.CharField()


class RegularisationSerializer(serializers.Serializer):
    client = serializers.PrimaryKeyRelatedField(queryset=Client.objects.all())
    montant = serializers.DecimalField(max_digits=14, decimal_places=2)
    sens = serializers.ChoiceField(choices=TransactionFinanciere.Sens.choices)
    motif = serializers.CharField()
    cle_idempotence = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
    )
