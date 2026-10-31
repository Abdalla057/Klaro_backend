import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

DUREE_CODE_MINUTES = 10
MAX_TENTATIVES_CODE = 5
DUREE_JETON_MINUTES = 10


def generer_code_activation():
    return f"{secrets.randbelow(1_000_000):06d}"


class ProfilActivableParTelephone(models.Model):
    """Activation commune aux comptes Client et Agent.

    Un code à 6 chiffres est généré de façon cryptographiquement sûre, haché
    avant stockage et valable dix minutes. Après cinq échecs le code est
    invalidé. La vérification réussie produit un jeton temporaire permettant
    de définir le mot de passe sans jamais renvoyer le code.
    """

    telephone = models.CharField(max_length=20, unique=True)
    compte_active = models.BooleanField(
        default=False,
        help_text="Passe à True après validation du code et définition du mot de passe.",
    )
    code_activation_hash = models.CharField(
        max_length=128,
        blank=True,
        editable=False,
    )
    code_expiration = models.DateTimeField(null=True, blank=True)
    code_genere_le = models.DateTimeField(null=True, blank=True)
    code_tentatives = models.PositiveSmallIntegerField(default=0)
    jeton_activation_hash = models.CharField(
        max_length=128,
        blank=True,
        editable=False,
    )
    jeton_activation_expiration = models.DateTimeField(null=True, blank=True)
    date_activation = models.DateTimeField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True

    @property
    def nom(self):
        return self.utilisateur.last_name

    @property
    def prenom(self):
        return self.utilisateur.first_name

    @property
    def nom_complet(self):
        return self.utilisateur.get_full_name() or self.telephone

    def generer_nouveau_code(self, validite_minutes=DUREE_CODE_MINUTES):
        code = generer_code_activation()
        maintenant = timezone.now()
        self.code_activation_hash = make_password(code)
        self.code_expiration = maintenant + timedelta(minutes=validite_minutes)
        self.code_genere_le = maintenant
        self.code_tentatives = 0
        self.jeton_activation_hash = ""
        self.jeton_activation_expiration = None
        self.save(
            update_fields=[
                "code_activation_hash",
                "code_expiration",
                "code_genere_le",
                "code_tentatives",
                "jeton_activation_hash",
                "jeton_activation_expiration",
            ]
        )
        return code

    def code_est_valide(self, code):
        return bool(
            not self.compte_active
            and self.code_activation_hash
            and self.code_expiration is not None
            and timezone.now() <= self.code_expiration
            and self.code_tentatives < MAX_TENTATIVES_CODE
            and check_password(str(code), self.code_activation_hash)
        )

    def _invalider_code(self):
        self.code_activation_hash = ""
        self.code_expiration = None
        self.code_tentatives = 0
        self.jeton_activation_hash = ""
        self.jeton_activation_expiration = None

    def verifier_code(self, code):
        if self.compte_active:
            raise ValidationError("Ce compte est déjà activé.")
        if not self.code_activation_hash or self.code_expiration is None:
            raise ValidationError("Aucun code d'activation actif.")
        if timezone.now() > self.code_expiration:
            raise ValidationError("Code expiré. Demandez un nouveau code.")
        if self.code_tentatives >= MAX_TENTATIVES_CODE:
            raise ValidationError(
                "Nombre maximal de tentatives atteint. Demandez un nouveau code."
            )

        if not check_password(str(code), self.code_activation_hash):
            self.code_tentatives += 1
            champs = ["code_tentatives"]
            if self.code_tentatives >= MAX_TENTATIVES_CODE:
                self._invalider_code()
                champs = [
                    "code_activation_hash",
                    "code_expiration",
                    "code_tentatives",
                    "jeton_activation_hash",
                    "jeton_activation_expiration",
                ]
                message = (
                    "Nombre maximal de tentatives atteint. "
                    "Demandez un nouveau code."
                )
            else:
                restantes = MAX_TENTATIVES_CODE - self.code_tentatives
                message = f"Code invalide. Tentatives restantes : {restantes}."
            self.save(update_fields=champs)
            raise ValidationError(message)

        token = secrets.token_urlsafe(32)
        self.code_tentatives = 0
        self.jeton_activation_hash = make_password(token)
        self.jeton_activation_expiration = timezone.now() + timedelta(
            minutes=DUREE_JETON_MINUTES
        )
        self.save(
            update_fields=[
                "code_tentatives",
                "jeton_activation_hash",
                "jeton_activation_expiration",
            ]
        )
        return token

    def jeton_est_valide(self, token):
        return bool(
            not self.compte_active
            and self.jeton_activation_hash
            and self.jeton_activation_expiration is not None
            and timezone.now() <= self.jeton_activation_expiration
            and check_password(str(token), self.jeton_activation_hash)
        )

    def _finaliser_activation(self, password):
        self.utilisateur.set_password(password)
        self.utilisateur.is_active = True
        self.utilisateur.save(update_fields=["password", "is_active"])
        self.compte_active = True
        self.date_activation = timezone.now()
        self._invalider_code()
        self.save(
            update_fields=[
                "compte_active",
                "date_activation",
                "code_activation_hash",
                "code_expiration",
                "code_tentatives",
                "jeton_activation_hash",
                "jeton_activation_expiration",
            ]
        )

    def activer_avec_jeton(self, token, password):
        if not self.jeton_est_valide(token):
            raise ValidationError("Jeton d'activation invalide ou expiré.")
        self._finaliser_activation(password)
        return self

    def activer(self, code, password):
        if not self.code_est_valide(code):
            raise ValidationError("Code invalide ou expiré.")
        self._finaliser_activation(password)
        return self
