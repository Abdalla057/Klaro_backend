from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.abonnements.services import verifier_quota_agents
from apps.comptes.services import activer_par_code, renvoyer_code_activation
from apps.comptes.validators import normaliser_telephone

from .models import Agent

Utilisateur = get_user_model()


def agents_accessibles_par(utilisateur):
    if hasattr(utilisateur, "fournisseur_profile"):
        return Agent.objects.filter(
            fournisseur=utilisateur.fournisseur_profile
        ).select_related("utilisateur", "fournisseur").order_by("-date_creation")
    if hasattr(utilisateur, "agent_profile"):
        return Agent.objects.filter(
            pk=utilisateur.agent_profile.pk
        ).select_related("utilisateur", "fournisseur").order_by("-date_creation")
    return Agent.objects.none()


@transaction.atomic
def creer_agent(fournisseur, telephone, nom="", prenom="", zone=""):
    telephone = normaliser_telephone(telephone)
    verifier_quota_agents(fournisseur)

    utilisateur = Utilisateur.objects.create_user(
        telephone=telephone,
        password=None,
        is_active=False,
        role="agent",
        first_name=prenom,
        last_name=nom,
    )
    agent = Agent.objects.create(
        utilisateur=utilisateur,
        fournisseur=fournisseur,
        telephone=telephone,
        zone=zone,
    )
    code = agent.generer_nouveau_code()
    agent._code_activation_clair = code
    return agent


def activer_agent(telephone, code, password):
    from apps.comptes.services import trouver_profil_activation

    profil = trouver_profil_activation(telephone)
    if not isinstance(profil, Agent):
        raise ValidationError(
            "Aucun compte agent en attente d'activation avec ce numéro."
        )
    return activer_par_code(telephone, code, password)


@transaction.atomic
def renvoyer_code_agent(fournisseur, telephone):
    telephone = normaliser_telephone(telephone)
    agent = Agent.objects.select_for_update().filter(telephone=telephone).first()
    if agent is None:
        raise ValidationError("Aucun agent trouvé avec ce numéro.")
    if agent.fournisseur_id != fournisseur.pk:
        raise PermissionDenied(
            "Vous ne pouvez pas renvoyer un code pour un agent d'un autre fournisseur."
        )
    if agent.compte_active:
        raise ValidationError("Ce compte agent est déjà activé.")
    agent, code = renvoyer_code_activation(telephone)
    agent._code_activation_clair = code
    return agent
