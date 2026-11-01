from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import DecimalField, OuterRef, Subquery
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.abonnements.services import verifier_quota_clients
from apps.comptes.services import activer_par_code, renvoyer_code_activation
from apps.comptes.validators import normaliser_telephone

from .models import Client

Utilisateur = get_user_model()


def _clients_avec_credit_initial():
    from apps.finances.models import TransactionFinanciere

    credit_initial = TransactionFinanciere.objects.filter(
        client=OuterRef("pk"),
        type_transaction=TransactionFinanciere.Type.ATTRIBUTION_INITIALE,
        statut=TransactionFinanciere.Statut.VALIDEE,
    ).order_by("date_creation")
    return Client.objects.annotate(
        _montant_credit_initial=Subquery(
            credit_initial.values("montant")[:1],
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    )


def clients_accessibles_par(utilisateur):
    queryset = _clients_avec_credit_initial().select_related(
        "utilisateur",
        "fournisseur",
    ).order_by("-date_creation")
    if hasattr(utilisateur, "fournisseur_profile"):
        return queryset.filter(
            fournisseur=utilisateur.fournisseur_profile
        )
    if hasattr(utilisateur, "agent_profile"):
        return queryset.filter(
            fournisseur=utilisateur.agent_profile.fournisseur
        )
    if hasattr(utilisateur, "client_profile"):
        return queryset.filter(pk=utilisateur.client_profile.pk)
    return Client.objects.none()


@transaction.atomic
def creer_client(
    fournisseur,
    telephone,
    nom,
    prenom,
    adresse="",
    solde_initial=None,
    auteur=None,
):
    telephone = normaliser_telephone(telephone)
    verifier_quota_clients(fournisseur)

    utilisateur = Utilisateur.objects.create_user(
        telephone=telephone,
        password=None,
        is_active=False,
        role="client",
        first_name=prenom,
        last_name=nom,
    )
    client = Client.objects.create(
        utilisateur=utilisateur,
        fournisseur=fournisseur,
        telephone=telephone,
        adresse=adresse,
    )
    code = client.generer_nouveau_code()
    client._code_activation_clair = code
    client._montant_credit_initial = None

    if solde_initial is not None and Decimal(str(solde_initial)) > 0:
        from apps.finances.services import attribuer_solde_initial

        mouvement = attribuer_solde_initial(
            fournisseur=fournisseur,
            client=client,
            montant=solde_initial,
            auteur=auteur,
            commentaire="Solde initial attribué lors de la création du client.",
        )
        client.solde_du = mouvement.montant
        client._montant_credit_initial = mouvement.montant

    return client


def activer_client(telephone, code, password):
    from apps.comptes.services import trouver_profil_activation

    profil = trouver_profil_activation(telephone)
    if not isinstance(profil, Client):
        raise ValidationError(
            "Aucun compte client en attente d'activation avec ce numéro."
        )
    return activer_par_code(telephone, code, password)


@transaction.atomic
def renvoyer_code_client(fournisseur, telephone):
    telephone = normaliser_telephone(telephone)
    client = Client.objects.select_for_update().filter(telephone=telephone).first()
    if client is None:
        raise ValidationError("Aucun client trouvé avec ce numéro.")
    if client.fournisseur_id != fournisseur.pk:
        raise PermissionDenied(
            "Vous ne pouvez pas renvoyer un code pour un client d'un autre fournisseur."
        )
    if client.compte_active:
        raise ValidationError("Ce compte client est déjà activé.")
    client, code = renvoyer_code_activation(telephone)
    client._code_activation_clair = code
    return client
