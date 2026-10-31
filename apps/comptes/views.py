from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .serializers import (
    ActivationParCodeSerializer,
    ChangementMotDePasseSerializer,
    ConnexionResponseSerializer,
    ConnexionSerializer,
    DeconnexionSerializer,
    DefinitionMotDePasseSerializer,
    DetailSerializer,
    ReponseVerificationCodeSerializer,
    StatutActivationSerializer,
    UtilisateurSerializer,
    VerificationCodeSerializer,
)
from .services import (
    activer_par_code,
    definir_mot_de_passe_activation,
    statut_activation,
    verifier_code_activation,
)


class MeView(generics.RetrieveAPIView):
    """Renvoie les informations de l'utilisateur actuellement connecté."""

    serializer_class = UtilisateurSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class ConnexionView(TokenObtainPairView):
    """Connexion par téléphone et mot de passe."""

    serializer_class = ConnexionSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(
        request=ConnexionSerializer,
        responses={200: ConnexionResponseSerializer},
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class RafraichirView(TokenRefreshView):
    """Renouvelle l'access token à partir du refresh token."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "refresh"


class DeconnexionView(APIView):
    """Invalide le refresh token fourni (blacklist)."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        request=DeconnexionSerializer,
        responses={205: None},
    )
    def post(self, request):
        serializer = DeconnexionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            token = RefreshToken(serializer.validated_data["refresh"])
        except TokenError:
            return Response(
                {"detail": "Refresh token invalide ou expiré."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if str(token["user_id"]) != str(request.user.pk):
            return Response(
                {"detail": "Ce refresh token n'appartient pas à cet utilisateur."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        token.blacklist()
        return Response(status=status.HTTP_205_RESET_CONTENT)


class ChangementMotDePasseView(APIView):
    """Change le mot de passe et invalide toutes les sessions existantes."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_change"

    @extend_schema(
        request=ChangementMotDePasseSerializer,
        responses={200: DetailSerializer},
    )
    def post(self, request):
        serializer = ChangementMotDePasseSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        utilisateur = serializer.save()

        for jeton in OutstandingToken.objects.filter(user=utilisateur):
            BlacklistedToken.objects.get_or_create(token=jeton)

        return Response(
            {
                "detail": (
                    "Mot de passe modifié. Reconnectez-vous avec le nouveau "
                    "mot de passe."
                )
            }
        )


class VerifierCodeActivationView(APIView):
    """Vérifie le code d'activation et renvoie un jeton temporaire."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "activation"

    @extend_schema(
        request=VerificationCodeSerializer,
        responses={200: ReponseVerificationCodeSerializer},
    )
    def post(self, request):
        serializer = VerificationCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profil, token = verifier_code_activation(**serializer.validated_data)
        return Response(
            {
                "telephone": profil.telephone,
                "role": profil.utilisateur.role,
                "token": token,
                "expire_dans_minutes": 10,
            }
        )


class DefinirMotDePasseActivationView(APIView):
    """Définit le mot de passe à partir du jeton d'activation temporaire."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "activation"

    @extend_schema(
        request=DefinitionMotDePasseSerializer,
        responses={200: DetailSerializer},
    )
    def post(self, request):
        serializer = DefinitionMotDePasseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profil = definir_mot_de_passe_activation(**serializer.validated_data)
        return Response(
            {
                "detail": "Compte activé avec succès.",
                "telephone": profil.telephone,
                "role": profil.utilisateur.role,
            }
        )


class ActiverCompteView(APIView):
    """Active directement le compte avec le code et le mot de passe."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "activation"

    @extend_schema(
        request=ActivationParCodeSerializer,
        responses={200: DetailSerializer},
    )
    def post(self, request):
        serializer = ActivationParCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profil = activer_par_code(**serializer.validated_data)
        return Response(
            {
                "detail": "Compte activé avec succès.",
                "telephone": profil.telephone,
                "role": profil.utilisateur.role,
            }
        )


class StatutActivationView(APIView):
    """Consulte l'état d'activation d'un compte client ou agent."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "activation"

    @extend_schema(responses={200: StatutActivationSerializer})
    def get(self, request):
        telephone = request.query_params.get("telephone")
        if not telephone:
            return Response(
                {"telephone": "Le paramètre telephone est requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        donnees = statut_activation(telephone)
        return Response(StatutActivationSerializer(donnees).data)
