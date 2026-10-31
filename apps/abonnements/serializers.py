from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.fournisseurs.models import Fournisseur

from .models import Abonnement, EvenementAbonnement, PlanAbonnement
from .services import renouveler, souscrire

UTILISATEUR_REF_SCHEMA = {
    "type": "object",
    "nullable": True,
    "properties": {
        "id": {"type": "integer"},
        "telephone": {"type": "string"},
    },
}


class PlanAbonnementSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanAbonnement
        fields = [
            "id",
            "code",
            "nom",
            "description",
            "prix",
            "devise",
            "duree_jours",
            "max_clients",
            "max_agents",
            "est_essai",
            "actif",
            "date_creation",
            "date_modification",
        ]
        read_only_fields = ["id", "date_creation", "date_modification"]

    def validate(self, attrs):
        est_essai = attrs.get(
            "est_essai",
            getattr(self.instance, "est_essai", False),
        )
        actif = attrs.get("actif", getattr(self.instance, "actif", True))
        prix = attrs.get("prix", getattr(self.instance, "prix", 0))
        if est_essai and prix != 0:
            raise serializers.ValidationError(
                {"prix": "Un plan d'essai doit obligatoirement être gratuit."}
            )

        if est_essai and actif:
            autres_essais = PlanAbonnement.objects.filter(
                est_essai=True,
                actif=True,
            )
            if self.instance is not None:
                autres_essais = autres_essais.exclude(pk=self.instance.pk)
            if autres_essais.exists():
                raise serializers.ValidationError(
                    {"est_essai": "Un plan d'essai actif existe déjà."}
                )

        max_clients = attrs.get(
            "max_clients",
            getattr(self.instance, "max_clients", None),
        )
        if max_clients is not None and max_clients < 1:
            raise serializers.ValidationError(
                {"max_clients": "Le quota de clients doit être supérieur à zéro."}
            )

        max_agents = attrs.get(
            "max_agents",
            getattr(self.instance, "max_agents", None),
        )
        if max_agents is not None and max_agents < 1:
            raise serializers.ValidationError(
                {"max_agents": "Le quota d'agents doit être supérieur à zéro."}
            )
        return attrs


class FournisseurAbonnementResumeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Fournisseur
        fields = ["id", "nom_entreprise", "code_entreprise", "telephone"]


class AbonnementResumeSerializer(serializers.ModelSerializer):
    plan_nom = serializers.CharField(source="plan.nom", read_only=True)
    statut_effectif = serializers.CharField(read_only=True)
    est_actif = serializers.BooleanField(read_only=True)
    jours_restants = serializers.IntegerField(read_only=True)

    class Meta:
        model = Abonnement
        fields = [
            "id",
            "plan_nom",
            "statut",
            "statut_effectif",
            "est_actif",
            "est_essai",
            "date_debut",
            "date_fin",
            "jours_restants",
            "montant",
            "devise",
        ]


class AbonnementSerializer(serializers.ModelSerializer):
    fournisseur = FournisseurAbonnementResumeSerializer(read_only=True)
    plan = PlanAbonnementSerializer(read_only=True)
    statut_effectif = serializers.CharField(read_only=True)
    est_actif = serializers.BooleanField(read_only=True)
    jours_restants = serializers.IntegerField(read_only=True)
    cree_par = serializers.SerializerMethodField()

    class Meta:
        model = Abonnement
        fields = [
            "id",
            "fournisseur",
            "plan",
            "date_debut",
            "date_fin",
            "statut",
            "statut_effectif",
            "est_actif",
            "est_essai",
            "jours_restants",
            "montant",
            "devise",
            "reference_paiement",
            "notes",
            "cree_par",
            "date_creation",
            "date_modification",
        ]
        read_only_fields = fields

    @extend_schema_field(UTILISATEUR_REF_SCHEMA)
    def get_cree_par(self, obj):
        if obj.cree_par is None:
            return None
        return {
            "id": obj.cree_par_id,
            "telephone": obj.cree_par.telephone,
        }


class AbonnementCreationSerializer(serializers.Serializer):
    fournisseur = serializers.PrimaryKeyRelatedField(
        queryset=Fournisseur.objects.all()
    )
    plan = serializers.PrimaryKeyRelatedField(
        queryset=PlanAbonnement.objects.all()
    )
    date_debut = serializers.DateTimeField(required=False, allow_null=True)
    reference_paiement = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
    )
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_plan(self, plan):
        if not plan.actif:
            raise serializers.ValidationError(
                "Ce plan est inactif et ne peut pas être souscrit."
            )
        if plan.est_essai:
            raise serializers.ValidationError(
                "L'essai gratuit est attribué automatiquement à l'inscription. "
                "Choisissez un plan payant."
            )
        return plan

    def create(self, validated_data):
        request = self.context.get("request")
        return souscrire(
            fournisseur=validated_data["fournisseur"],
            plan=validated_data["plan"],
            effectue_par=request.user if request else None,
            date_debut=validated_data.get("date_debut"),
            reference_paiement=validated_data.get("reference_paiement", ""),
            notes=validated_data.get("notes", ""),
        )


class RenouvellementAbonnementSerializer(serializers.Serializer):
    plan = serializers.PrimaryKeyRelatedField(
        queryset=PlanAbonnement.objects.all(),
        required=False,
        allow_null=True,
    )
    reference_paiement = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
    )
    notes = serializers.CharField(required=False, allow_blank=True)

    def create(self, validated_data):
        request = self.context.get("request")
        return renouveler(
            self.context["abonnement"],
            plan=validated_data.get("plan"),
            effectue_par=request.user if request else None,
            reference_paiement=validated_data.get("reference_paiement", ""),
            notes=validated_data.get("notes", ""),
        )


class SuspensionAbonnementSerializer(serializers.Serializer):
    motif = serializers.CharField()


class AnnulationAbonnementSerializer(serializers.Serializer):
    motif = serializers.CharField()


class EvenementAbonnementSerializer(serializers.ModelSerializer):
    type_evenement_display = serializers.CharField(
        source="get_type_evenement_display",
        read_only=True,
    )
    effectue_par = serializers.SerializerMethodField()

    class Meta:
        model = EvenementAbonnement
        fields = [
            "id",
            "type_evenement",
            "type_evenement_display",
            "ancien_statut",
            "nouveau_statut",
            "montant",
            "reference_paiement",
            "motif",
            "effectue_par",
            "date_creation",
        ]

    @extend_schema_field(UTILISATEUR_REF_SCHEMA)
    def get_effectue_par(self, obj):
        if obj.effectue_par is None:
            return None
        return {
            "id": obj.effectue_par_id,
            "telephone": obj.effectue_par.telephone,
        }


class QuotasAbonnementSerializer(serializers.Serializer):
    clients = serializers.IntegerField(read_only=True, allow_null=True)
    agents = serializers.IntegerField(read_only=True, allow_null=True)


class UtilisationAbonnementSerializer(serializers.Serializer):
    clients = serializers.IntegerField(read_only=True)
    agents = serializers.IntegerField(read_only=True)


class FournisseurAbonnementSerializer(serializers.Serializer):
    abonnement = AbonnementResumeSerializer(read_only=True, allow_null=True)
    plan = PlanAbonnementSerializer(read_only=True, allow_null=True)
    statut = serializers.CharField(read_only=True, allow_null=True)
    date_debut = serializers.DateTimeField(read_only=True, allow_null=True)
    date_fin = serializers.DateTimeField(read_only=True, allow_null=True)
    jours_restants = serializers.IntegerField(read_only=True)
    est_actif = serializers.BooleanField(read_only=True)
    quotas = QuotasAbonnementSerializer(read_only=True)
    utilisation = UtilisationAbonnementSerializer(read_only=True)
    peut_creer_client = serializers.BooleanField(read_only=True)
    peut_creer_agent = serializers.BooleanField(read_only=True)
