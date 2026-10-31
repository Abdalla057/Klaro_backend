from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import Utilisateur
from .validators import normaliser_telephone, valider_mot_de_passe


class UtilisateurSerializer(serializers.ModelSerializer):
    """Serializer de lecture : n'expose jamais le mot de passe."""

    role_display = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = Utilisateur
        fields = ["id", "telephone", "role", "role_display", "date_joined"]
        read_only_fields = ["id", "date_joined"]


class ConnexionSerializer(TokenObtainPairSerializer):
    """Connexion par téléphone et mot de passe, avec enrichissement du JWT."""

    default_error_messages = {
        "no_active_account": (
            "Aucun compte actif trouvé avec ces identifiants."
        ),
    }

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["telephone"] = user.telephone
        token["role"] = user.role

        fournisseur_id = None
        if hasattr(user, "fournisseur_profile"):
            fournisseur_id = user.fournisseur_profile.pk
        elif hasattr(user, "client_profile"):
            fournisseur_id = user.client_profile.fournisseur_id
        elif hasattr(user, "agent_profile"):
            fournisseur_id = user.agent_profile.fournisseur_id

        if fournisseur_id is not None:
            token["fournisseur_id"] = fournisseur_id
        return token

    def validate_telephone(self, value):
        return normaliser_telephone(value)

    def validate(self, attrs):
        data = super().validate(attrs)
        data["utilisateur"] = UtilisateurSerializer(self.user).data
        return data


class ConnexionResponseSerializer(serializers.Serializer):
    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)
    utilisateur = UtilisateurSerializer(read_only=True)


class DetailSerializer(serializers.Serializer):
    detail = serializers.CharField(read_only=True)


class DeconnexionSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class ChangementMotDePasseSerializer(serializers.Serializer):
    ancien_mot_de_passe = serializers.CharField(write_only=True)
    nouveau_mot_de_passe = serializers.CharField(write_only=True, min_length=8)
    confirmation = serializers.CharField(write_only=True)

    def validate_ancien_mot_de_passe(self, value):
        utilisateur = self.context["request"].user
        if not utilisateur.check_password(value):
            raise serializers.ValidationError(
                "L'ancien mot de passe est incorrect."
            )
        return value

    def validate(self, attrs):
        if attrs["nouveau_mot_de_passe"] != attrs["confirmation"]:
            raise serializers.ValidationError(
                {"confirmation": "Les deux mots de passe ne correspondent pas."}
            )
        if attrs["nouveau_mot_de_passe"] == attrs["ancien_mot_de_passe"]:
            raise serializers.ValidationError(
                {
                    "nouveau_mot_de_passe": (
                        "Le nouveau mot de passe doit être différent de l'ancien."
                    )
                }
            )

        try:
            valider_mot_de_passe(
                attrs["nouveau_mot_de_passe"],
                user=self.context["request"].user,
            )
        except serializers.ValidationError as exc:
            raise serializers.ValidationError(
                {"nouveau_mot_de_passe": exc.detail}
            ) from exc

        return attrs

    def save(self):
        utilisateur = self.context["request"].user
        utilisateur.set_password(self.validated_data["nouveau_mot_de_passe"])
        utilisateur.save(update_fields=["password"])
        return utilisateur


class VerificationCodeSerializer(serializers.Serializer):
    telephone = serializers.CharField()
    code = serializers.RegexField(r"^\d{6}$")

    def validate_telephone(self, value):
        return normaliser_telephone(value)


class DefinitionMotDePasseSerializer(serializers.Serializer):
    telephone = serializers.CharField()
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=8)
    confirmation = serializers.CharField(write_only=True)

    def validate_telephone(self, value):
        return normaliser_telephone(value)

    def validate(self, attrs):
        if attrs["password"] != attrs["confirmation"]:
            raise serializers.ValidationError(
                {"confirmation": "Les deux mots de passe ne correspondent pas."}
            )
        valider_mot_de_passe(attrs["password"])
        return attrs


class ActivationParCodeSerializer(serializers.Serializer):
    telephone = serializers.CharField()
    code = serializers.RegexField(r"^\d{6}$")
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_telephone(self, value):
        return normaliser_telephone(value)


class RenvoyerCodeSerializer(serializers.Serializer):
    telephone = serializers.CharField()

    def validate_telephone(self, value):
        return normaliser_telephone(value)


class StatutActivationSerializer(serializers.Serializer):
    telephone = serializers.CharField(read_only=True)
    role = serializers.CharField(read_only=True)
    compte_active = serializers.BooleanField(read_only=True)
    date_creation = serializers.DateTimeField(read_only=True)
    date_activation = serializers.DateTimeField(read_only=True, allow_null=True)
    code_expiration = serializers.DateTimeField(read_only=True, allow_null=True)
    tentatives_restantes = serializers.IntegerField(read_only=True)


class ReponseVerificationCodeSerializer(serializers.Serializer):
    telephone = serializers.CharField(read_only=True)
    role = serializers.CharField(read_only=True)
    token = serializers.CharField(read_only=True)
    expire_dans_minutes = serializers.IntegerField(read_only=True)
