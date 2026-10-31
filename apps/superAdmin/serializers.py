from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.abonnements.serializers import (
    AbonnementResumeSerializer,
    FournisseurAbonnementResumeSerializer,
    FournisseurAbonnementSerializer,
    UTILISATEUR_REF_SCHEMA,
)
from apps.abonnements.services import abonnement_actif
from apps.fournisseurs.models import Fournisseur


class UtilisateurResumeSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    telephone = serializers.CharField(read_only=True)
    role = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)


class FournisseurSupervisionSerializer(serializers.ModelSerializer):
    utilisateur = UtilisateurResumeSerializer(read_only=True)
    statut = serializers.SerializerMethodField()
    suspendu_par = serializers.SerializerMethodField()
    abonnement = serializers.SerializerMethodField()
    nombre_clients = serializers.SerializerMethodField()
    nombre_agents = serializers.SerializerMethodField()

    class Meta:
        model = Fournisseur
        fields = [
            "id",
            "nom_entreprise",
            "code_entreprise",
            "telephone",
            "adresse",
            "date_creation",
            "statut",
            "est_suspendu",
            "date_suspension",
            "motif_suspension",
            "suspendu_par",
            "utilisateur",
            "nombre_clients",
            "nombre_agents",
            "abonnement",
        ]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.STR)
    def get_statut(self, obj):
        return "SUSPENDU" if obj.est_suspendu else "ACTIF"

    @extend_schema_field(UTILISATEUR_REF_SCHEMA)
    def get_suspendu_par(self, obj):
        if obj.suspendu_par is None:
            return None
        return {
            "id": obj.suspendu_par_id,
            "telephone": obj.suspendu_par.telephone,
        }

    @extend_schema_field(AbonnementResumeSerializer)
    def get_abonnement(self, obj):
        abonnements = getattr(obj, "abonnements_actifs", None)
        if abonnements is not None:
            abonnement = abonnements[0] if abonnements else None
        else:
            abonnement = abonnement_actif(obj)
        if abonnement is None:
            return None
        return AbonnementResumeSerializer(abonnement).data

    @extend_schema_field(OpenApiTypes.INT)
    def get_nombre_clients(self, obj):
        if hasattr(obj, "nombre_clients"):
            return obj.nombre_clients
        return obj.clients.count()

    @extend_schema_field(OpenApiTypes.INT)
    def get_nombre_agents(self, obj):
        if hasattr(obj, "nombre_agents"):
            return obj.nombre_agents
        return obj.agents.count()


class SuspensionFournisseurSerializer(serializers.Serializer):
    motif = serializers.CharField()


class StatistiquesClientsSerializer(serializers.Serializer):
    total = serializers.IntegerField(read_only=True)
    actifs = serializers.IntegerField(read_only=True)


class StatistiquesAgentsSerializer(serializers.Serializer):
    total = serializers.IntegerField(read_only=True)
    actifs = serializers.IntegerField(read_only=True)


class StatistiquesEncaissementsSerializer(serializers.Serializer):
    total = serializers.IntegerField(read_only=True)
    en_attente = serializers.IntegerField(read_only=True)
    confirmes = serializers.IntegerField(read_only=True)
    annules = serializers.IntegerField(read_only=True)
    montant_confirme = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )
    montant_en_attente = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )


class StatistiquesFournisseurSerializer(serializers.Serializer):
    fournisseur = FournisseurAbonnementResumeSerializer(read_only=True)
    clients = StatistiquesClientsSerializer(read_only=True)
    agents = StatistiquesAgentsSerializer(read_only=True)
    encaissements = StatistiquesEncaissementsSerializer(read_only=True)
    abonnement = FournisseurAbonnementSerializer(read_only=True)


class StatistiquesFournisseursGlobalesSerializer(serializers.Serializer):
    total = serializers.IntegerField(read_only=True)
    actifs = serializers.IntegerField(read_only=True)
    suspendus = serializers.IntegerField(read_only=True)


class StatistiquesAbonnementsGlobalesSerializer(serializers.Serializer):
    actifs = serializers.IntegerField(read_only=True)
    expires = serializers.IntegerField(read_only=True)
    suspendus = serializers.IntegerField(read_only=True)
    annules = serializers.IntegerField(read_only=True)
    essais_actifs = serializers.IntegerField(read_only=True)


class TableauDeBordSerializer(serializers.Serializer):
    fournisseurs = StatistiquesFournisseursGlobalesSerializer(read_only=True)
    clients = StatistiquesClientsSerializer(read_only=True)
    agents = StatistiquesAgentsSerializer(read_only=True)
    encaissements = StatistiquesEncaissementsSerializer(read_only=True)
    abonnements = StatistiquesAbonnementsGlobalesSerializer(read_only=True)
    montant_facture_total = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )
