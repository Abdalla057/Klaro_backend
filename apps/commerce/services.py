from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import (
    CommandeAchat,
    DetteCommerciale,
    EvenementFacture,
    Facture,
    LigneCommandeAchat,
    LigneReception,
    LigneVente,
    MouvementStock,
    NotificationClient,
    NotificationExterne,
    PaiementVente,
    Produit,
    Reception,
    Vente,
)
from .notifications import preparer_notifications_externes

DEVISE_PAR_DEFAUT = "XOF"


def devise_fournisseur(fournisseur):
    """Retourne la devise du plan actif du fournisseur (snapshot à l'émission)."""
    abonnement = (
        fournisseur.abonnements.select_related("plan")
        .order_by("-date_debut", "-id")
        .first()
    )
    if abonnement is None:
        return DEVISE_PAR_DEFAUT
    return abonnement.devise or (
        abonnement.plan.devise if abonnement.plan_id else DEVISE_PAR_DEFAUT
    )


def _decimal(valeur, champ="valeur", minimum=None):
    try:
        resultat = Decimal(str(valeur))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({champ: "Valeur numérique invalide."}) from exc
    if not resultat.is_finite():
        raise ValidationError({champ: "Valeur numérique invalide."})
    if minimum is not None and resultat < minimum:
        raise ValidationError(
            {champ: f"La valeur minimale autorisée est {minimum}."}
        )
    return resultat


def _montant(valeur, champ="montant", minimum=None):
    return _decimal(valeur, champ, minimum).quantize(Decimal("0.01"))


def _quantite(valeur, champ="quantite", minimum=Decimal("0.001")):
    return _decimal(valeur, champ, minimum).quantize(Decimal("0.001"))


def _verifier_client(fournisseur, client):
    if client.fournisseur_id != fournisseur.pk:
        raise PermissionDenied(
            "Ce client n'appartient pas à votre entreprise."
        )


def _appliquer_mouvement_stock(
    *,
    produit,
    type_mouvement,
    quantite_base,
    motif="",
    vente=None,
    reception=None,
    auteur=None,
    cout_unitaire=None,
):
    """Écrit un mouvement de stock et met à jour le produit verrouillé.

    Pour une entrée avec ``cout_unitaire``, le prix d'achat du produit est
    recalculé en coût moyen pondéré (CUMP).
    """
    quantite_base = _quantite(quantite_base)
    produit = Produit.objects.select_for_update().get(pk=produit.pk)

    if type_mouvement == MouvementStock.Type.ENTREE:
        nouveau_stock = produit.stock_actuel + quantite_base
        if cout_unitaire is not None and nouveau_stock > 0:
            cout = _montant(cout_unitaire, "cout_unitaire", Decimal("0"))
            valeur_totale = (
                produit.stock_actuel * produit.prix_achat
                + quantite_base * cout
            )
            produit.prix_achat = (valeur_totale / nouveau_stock).quantize(
                Decimal("0.01")
            )
    elif type_mouvement == MouvementStock.Type.SORTIE:
        if produit.stock_actuel < quantite_base:
            raise ValidationError(
                {
                    "detail": (
                        f"Stock insuffisant pour « {produit.nom} » : "
                        f"disponible {produit.stock_actuel}, "
                        f"demandé {quantite_base}."
                    )
                }
            )
        nouveau_stock = produit.stock_actuel - quantite_base
    else:
        nouveau_stock = quantite_base

    produit.stock_actuel = nouveau_stock
    produit.save(
        update_fields=["stock_actuel", "prix_achat", "date_modification"]
    )

    return MouvementStock.objects.create(
        fournisseur=produit.fournisseur,
        produit=produit,
        type_mouvement=type_mouvement,
        quantite_base=quantite_base,
        stock_apres=nouveau_stock,
        motif=motif,
        vente=vente,
        reception=reception,
        auteur=auteur,
    )


@transaction.atomic
def entree_stock(produit, quantite, motif="", auteur=None):
    """Ajoute du stock via un mouvement d'entrée tracé."""
    quantite = _quantite(quantite)
    return _appliquer_mouvement_stock(
        produit=produit,
        type_mouvement=MouvementStock.Type.ENTREE,
        quantite_base=quantite,
        motif=motif or "Entrée de stock",
        auteur=auteur,
    )


@transaction.atomic
def creer_commande_achat(
    *,
    fournisseur,
    fournisseur_marchandise,
    lignes,
    notes="",
    statut=CommandeAchat.Statut.COMMANDEE,
    auteur=None,
):
    if statut not in (
        CommandeAchat.Statut.BROUILLON,
        CommandeAchat.Statut.COMMANDEE,
    ):
        raise ValidationError(
            {
                "statut": (
                    "Une commande ne peut être créée qu'en brouillon ou "
                    "commandée. Utilisez la réception pour l'évolution du statut."
                )
            }
        )
    if fournisseur_marchandise.fournisseur_id != fournisseur.pk:
        raise PermissionDenied(
            "Ce fournisseur de marchandises n'appartient pas à votre entreprise."
        )
    if not lignes:
        raise ValidationError({"lignes": "Au moins une ligne est requise."})

    ids_produits = sorted(
        {ligne["produit"].pk for ligne in lignes if ligne.get("produit")}
    )
    produits = {
        produit.pk: produit
        for produit in Produit.objects.select_for_update().filter(
            pk__in=ids_produits,
            fournisseur=fournisseur,
        )
    }
    if len(produits) != len(ids_produits):
        raise ValidationError(
            {"lignes": "Un produit est introuvable ou n'appartient pas à votre entreprise."}
        )

    prepared = []
    total = Decimal("0")
    for ligne in lignes:
        produit = produits[ligne["produit"].pk]
        unite = ligne["unite"]
        if unite.produit_id != produit.pk:
            raise ValidationError(
                {"lignes": f"L'unité ne correspond pas au produit « {produit.nom} »."}
            )
        quantite = _quantite(ligne["quantite"])
        quantite_base = (quantite * unite.facteur_conversion).quantize(
            Decimal("0.001")
        )
        prix = _montant(
            ligne.get("prix_achat_unitaire", produit.prix_achat),
            "prix_achat_unitaire",
            Decimal("0"),
        )
        sous_total = (prix * quantite).quantize(Decimal("0.01"))
        total += sous_total
        prepared.append(
            {
                "produit": produit,
                "unite": unite,
                "quantite": quantite,
                "quantite_base": quantite_base,
                "prix_achat_unitaire": prix,
                "sous_total": sous_total,
            }
        )

    commande = CommandeAchat.objects.create(
        fournisseur=fournisseur,
        fournisseur_marchandise=fournisseur_marchandise,
        statut=statut,
        montant_total=total,
        notes=notes,
        date_commande=timezone.now() if statut != CommandeAchat.Statut.BROUILLON else None,
    )
    for item in prepared:
        LigneCommandeAchat.objects.create(
            commande=commande,
            produit=item["produit"],
            unite=item["unite"],
            quantite=item["quantite"],
            quantite_base=item["quantite_base"],
            prix_achat_unitaire=item["prix_achat_unitaire"],
            sous_total=item["sous_total"],
        )
    return commande


@transaction.atomic
def receptionner_commande(
    commande,
    lignes,
    *,
    notes="",
    auteur=None,
):
    commande = (
        CommandeAchat.objects.select_for_update()
        .select_related("fournisseur", "fournisseur_marchandise")
        .get(pk=commande.pk)
    )
    if commande.statut not in (
        CommandeAchat.Statut.COMMANDEE,
        CommandeAchat.Statut.PARTIELLEMENT_RECUE,
    ):
        raise ValidationError(
            "Seule une commande commandée ou partiellement reçue peut être réceptionnée."
        )
    if not lignes:
        raise ValidationError({"lignes": "Au moins une ligne est requise."})

    reception = Reception.objects.create(
        commande=commande,
        notes=notes,
        auteur=auteur,
    )

    for item in lignes:
        ligne = (
            LigneCommandeAchat.objects.select_for_update()
            .select_related("produit", "unite")
            .filter(pk=item["ligne_commande"].pk, commande=commande)
            .first()
        )
        if ligne is None:
            raise ValidationError(
                {"lignes": "Cette ligne n'appartient pas à la commande."}
            )
        quantite = _quantite(item["quantite"])
        quantite_base = (quantite * ligne.unite.facteur_conversion).quantize(
            Decimal("0.001")
        )
        restant = ligne.quantite_base - ligne.quantite_recue_base
        if quantite_base > restant:
            raise ValidationError(
                {
                    "lignes": (
                        f"La quantité reçue pour « {ligne.produit.nom} » "
                        f"dépasse le restant commandé ({restant})."
                    )
                }
            )
        LigneReception.objects.create(
            reception=reception,
            ligne_commande=ligne,
            quantite=quantite,
            quantite_base=quantite_base,
        )
        ligne.quantite_recue_base += quantite_base
        ligne.save(update_fields=["quantite_recue_base"])
        _appliquer_mouvement_stock(
            produit=ligne.produit,
            type_mouvement=MouvementStock.Type.ENTREE,
            quantite_base=quantite_base,
            motif=f"Réception {reception.reference}",
            reception=reception,
            auteur=auteur,
            cout_unitaire=ligne.prix_achat_unitaire,
        )

    reste = commande.lignes.filter(
        quantite_recue_base__lt=F("quantite_base")
    ).exists()
    commande.statut = (
        CommandeAchat.Statut.PARTIELLEMENT_RECUE
        if reste
        else CommandeAchat.Statut.RECUE
    )
    commande.save(update_fields=["statut"])
    return reception


def _creer_facture(vente, prepared):
    annee = timezone.localdate(vente.date_creation).year
    numero = f"FAC-{annee}-{vente.pk:06d}"
    fournisseur = vente.fournisseur
    client = vente.client
    facture = Facture.objects.create(
        vente=vente,
        numero=numero,
        nom_commerce=fournisseur.nom_entreprise,
        fournisseur_adresse=fournisseur.adresse,
        fournisseur_telephone=fournisseur.telephone,
        client_nom=client.nom_complet,
        client_telephone=client.telephone,
        client_adresse=client.adresse,
        montant_total=vente.montant_total,
        montant_paye=vente.montant_paye,
        reste_a_payer=vente.reste_a_payer,
        devise=devise_fournisseur(fournisseur),
        modes_paiement=[vente.mode_paiement],
        lignes=[
            {
                "nom_produit": item["nom_produit"],
                "unite_nom": item["unite_nom"],
                "quantite": str(item["quantite"]),
                "prix_unitaire": str(item["prix_unitaire"]),
                "sous_total": str(item["sous_total"]),
            }
            for item in prepared
        ],
    )
    EvenementFacture.objects.create(
        facture=facture,
        type_evenement=EvenementFacture.Type.CREATION,
        description="Facture générée automatiquement à la validation de la vente.",
        donnees={
            "vente": vente.reference,
            "numero": numero,
            "montant_total": str(vente.montant_total),
        },
    )
    return facture


def _creer_notification_vente(vente, facture):
    reste = vente.reste_a_payer
    message = (
        f"Une nouvelle facture de {vente.montant_total} {facture.devise} a "
        f"été émise à votre nom. Reste à payer : {reste} {facture.devise}."
    )
    notification = NotificationClient.objects.create(
        client=vente.client,
        fournisseur=vente.fournisseur,
        vente=vente,
        facture=facture,
        type_notification=NotificationClient.Type.NOUVELLE_FACTURE,
        titre="NOUVELLE FACTURE DISPONIBLE",
        message=message,
        lien=f"/client/factures?facture={facture.pk}",
    )
    preparer_notifications_externes(notification)
    return notification


@transaction.atomic
def creer_vente(
    *,
    fournisseur,
    client,
    lignes,
    montant_paye=0,
    mode_paiement=Vente.ModePaiement.ESPECES,
    cle_idempotence=None,
    auteur=None,
):
    """Crée une vente atomique : stock, facture, paiement, dette, notification."""
    _verifier_client(fournisseur, client)

    if cle_idempotence:
        existante = (
            Vente.objects.select_related("client", "fournisseur", "facture")
            .filter(fournisseur=fournisseur, cle_idempotence=cle_idempotence)
            .first()
        )
        if existante is not None:
            return existante

    if not lignes:
        raise ValidationError({"lignes": "Au moins une ligne est requise."})

    ids_produits = sorted(
        {ligne["produit"].pk for ligne in lignes if ligne.get("produit")}
    )
    produits = {
        produit.pk: produit
        for produit in Produit.objects.select_for_update().filter(
            pk__in=ids_produits,
            fournisseur=fournisseur,
        )
    }
    if len(produits) != len(ids_produits):
        raise ValidationError(
            {"lignes": "Un produit est introuvable ou n'appartient pas à votre entreprise."}
        )

    prepared = []
    besoins = {}
    total = Decimal("0")
    for ligne in lignes:
        produit = produits[ligne["produit"].pk]
        unite = ligne["unite"]
        if unite.produit_id != produit.pk:
            raise ValidationError(
                {"lignes": f"L'unité ne correspond pas au produit « {produit.nom} »."}
            )
        quantite = _quantite(ligne["quantite"])
        quantite_base = (quantite * unite.facteur_conversion).quantize(
            Decimal("0.001")
        )
        prix_unitaire = _montant(
            ligne.get("prix_unitaire", unite.prix_effectif),
            "prix_unitaire",
            Decimal("0"),
        )
        sous_total = (prix_unitaire * quantite).quantize(Decimal("0.01"))
        total += sous_total
        besoins[produit.pk] = besoins.get(produit.pk, Decimal("0")) + quantite_base
        prepared.append(
            {
                "produit": produit,
                "unite": unite,
                "nom_produit": produit.nom,
                "unite_nom": unite.nom,
                "quantite": quantite,
                "quantite_base": quantite_base,
                "prix_unitaire": prix_unitaire,
                "prix_achat_unitaire": produit.prix_achat,
                "sous_total": sous_total,
            }
        )

    total = total.quantize(Decimal("0.01"))
    for produit_id, besoin in besoins.items():
        if produits[produit_id].stock_actuel < besoin:
            produit = produits[produit_id]
            raise ValidationError(
                {
                    "detail": (
                        f"Stock insuffisant pour « {produit.nom} » : "
                        f"disponible {produit.stock_actuel}, demandé {besoin}."
                    )
                }
            )

    montant_paye = _montant(montant_paye, "montant_paye", Decimal("0"))
    if montant_paye > total:
        raise ValidationError(
            {"montant_paye": "Le montant payé ne peut pas dépasser le total."}
        )

    if mode_paiement == Vente.ModePaiement.CREDIT and montant_paye != 0:
        raise ValidationError(
            {"mode_paiement": "Une vente à crédit ne peut pas avoir de paiement initial."}
        )
    if montant_paye == 0:
        mode_paiement = Vente.ModePaiement.CREDIT
    elif mode_paiement in (
        Vente.ModePaiement.CREDIT,
        Vente.ModePaiement.PARTIEL,
    ):
        raise ValidationError(
            {
                "mode_paiement": (
                    "Choisissez un moyen de paiement réel (espèces, Wave, "
                    "Orange Money, virement) lorsque montant_paye > 0."
                )
            }
        )

    try:
        with transaction.atomic():
            vente = Vente.objects.create(
                fournisseur=fournisseur,
                client=client,
                montant_total=total,
                montant_paye=montant_paye,
                reste_a_payer=total - montant_paye,
                mode_paiement=mode_paiement,
                cle_idempotence=cle_idempotence or None,
                auteur=auteur,
            )
    except IntegrityError:
        existante = (
            Vente.objects.select_related("client", "fournisseur", "facture")
            .filter(
                fournisseur=fournisseur,
                cle_idempotence=cle_idempotence,
            )
            .first()
        )
        if existante is None:
            raise
        return existante

    for item in prepared:
        LigneVente.objects.create(
            vente=vente,
            produit=item["produit"],
            unite=item["unite"],
            nom_produit=item["nom_produit"],
            unite_nom=item["unite_nom"],
            quantite=item["quantite"],
            quantite_base=item["quantite_base"],
            prix_unitaire=item["prix_unitaire"],
            prix_achat_unitaire=item["prix_achat_unitaire"],
            sous_total=item["sous_total"],
        )
        _appliquer_mouvement_stock(
            produit=item["produit"],
            type_mouvement=MouvementStock.Type.SORTIE,
            quantite_base=item["quantite_base"],
            motif=f"Vente {vente.reference}",
            vente=vente,
            auteur=auteur,
        )

    facture = _creer_facture(vente, prepared)
    vente.numero_facture = facture.numero
    vente.save(update_fields=["numero_facture"])

    if montant_paye > 0:
        PaiementVente.objects.create(
            vente=vente,
            montant=montant_paye,
            mode_paiement=mode_paiement,
            commentaire="Paiement enregistré lors de la vente.",
            auteur=auteur,
        )

    if vente.reste_a_payer > 0:
        DetteCommerciale.objects.create(
            fournisseur=fournisseur,
            client=client,
            vente=vente,
            montant_initial=vente.reste_a_payer,
            montant_restant=vente.reste_a_payer,
            statut=(
                DetteCommerciale.Statut.PARTIELLE
                if montant_paye > 0
                else DetteCommerciale.Statut.OUVERTE
            ),
        )

    _creer_notification_vente(vente, facture)
    return Vente.objects.select_related(
        "client", "fournisseur", "facture"
    ).get(pk=vente.pk)


@transaction.atomic
def ajouter_paiement_vente(
    vente,
    montant,
    mode_paiement=Vente.ModePaiement.ESPECES,
    reference_transaction="",
    commentaire="",
    auteur=None,
):
    vente = (
        Vente.objects.select_for_update()
        .select_related("client", "fournisseur")
        .get(pk=vente.pk)
    )
    if vente.statut != Vente.Statut.VALIDEE:
        raise ValidationError("Seule une vente validée peut recevoir un paiement.")
    if mode_paiement in (
        Vente.ModePaiement.CREDIT,
        Vente.ModePaiement.PARTIEL,
    ):
        raise ValidationError(
            {
                "mode_paiement": (
                    "Un paiement doit utiliser un moyen réel (espèces, Wave, "
                    "Orange Money, virement)."
                )
            }
        )

    montant = _montant(montant, "montant", Decimal("0.01"))
    devise = devise_fournisseur(vente.fournisseur)
    if montant > vente.reste_a_payer:
        raise ValidationError(
            {
                "montant": (
                    f"Le montant dépasse le reste à payer "
                    f"({vente.reste_a_payer} {devise})."
                )
            }
        )

    paiement = PaiementVente.objects.create(
        vente=vente,
        montant=montant,
        mode_paiement=mode_paiement,
        reference_transaction=reference_transaction,
        commentaire=commentaire,
        auteur=auteur,
    )

    vente.montant_paye += montant
    vente.reste_a_payer -= montant
    vente.save(update_fields=["montant_paye", "reste_a_payer"])

    dette = getattr(vente, "dette_commerciale", None)
    if dette is not None:
        dette.montant_restant = vente.reste_a_payer
        if vente.reste_a_payer == 0:
            dette.statut = DetteCommerciale.Statut.SOLDEE
        elif dette.montant_restant < dette.montant_initial:
            dette.statut = DetteCommerciale.Statut.PARTIELLE
        dette.save(update_fields=["montant_restant", "statut", "date_modification"])

    facture = Facture.objects.filter(vente=vente).first()
    notification = NotificationClient.objects.create(
        client=vente.client,
        fournisseur=vente.fournisseur,
        vente=vente,
        facture=facture,
        type_notification=NotificationClient.Type.PAIEMENT_RECU,
        titre="Paiement reçu",
        message=(
            f"Nous avons reçu un paiement de {montant} {devise}. "
            f"Reste à payer : {vente.reste_a_payer} {devise}."
        ),
        lien=(
            f"/client/factures?facture={facture.pk}"
            if facture
            else "/client/factures"
        ),
    )
    preparer_notifications_externes(notification)
    return paiement


@transaction.atomic
def annuler_vente(vente, motif="", auteur=None):
    """Annule une vente à crédit non payée et restaure le stock.

    Les ventes déjà partiellement ou totalement payées nécessitent un flux de
    remboursement qui n'est pas pris en charge ici : elles sont refusées.
    """
    vente = (
        Vente.objects.select_for_update()
        .select_related("client", "fournisseur")
        .get(pk=vente.pk)
    )
    if vente.statut != Vente.Statut.VALIDEE:
        raise ValidationError("Seule une vente validée peut être annulée.")
    if vente.montant_paye != 0:
        raise ValidationError(
            "Une vente déjà payée ne peut pas être annulée sans remboursement."
        )

    for ligne in vente.lignes.select_related("produit"):
        _appliquer_mouvement_stock(
            produit=ligne.produit,
            type_mouvement=MouvementStock.Type.ENTREE,
            quantite_base=ligne.quantite_base,
            motif=motif or f"Annulation vente {vente.reference}",
            vente=vente,
            auteur=auteur,
        )

    vente.statut = Vente.Statut.ANNULEE
    vente.save(update_fields=["statut"])

    dette = getattr(vente, "dette_commerciale", None)
    if dette is not None:
        dette.montant_restant = Decimal("0.00")
        dette.statut = DetteCommerciale.Statut.ANNULEE
        dette.save(update_fields=["montant_restant", "statut", "date_modification"])

    facture = Facture.objects.filter(vente=vente).first()
    devise = facture.devise if facture else devise_fournisseur(vente.fournisseur)
    notification = NotificationClient.objects.create(
        client=vente.client,
        fournisseur=vente.fournisseur,
        vente=vente,
        facture=facture,
        type_notification=NotificationClient.Type.VENTE_ANNULEE,
        titre="Vente annulée",
        message=(
            f"La vente {vente.reference} d'un montant de "
            f"{vente.montant_total} {devise} a été annulée."
        ),
        lien=(
            f"/client/factures?facture={facture.pk}"
            if facture
            else "/client/factures"
        ),
    )
    preparer_notifications_externes(notification)
    return vente


def statistiques_fournisseur(fournisseur):
    produits = Produit.objects.filter(fournisseur=fournisseur)
    ventes = Vente.objects.filter(
        fournisseur=fournisseur,
        statut=Vente.Statut.VALIDEE,
    )
    agregats = ventes.aggregate(
        total=Sum("montant_total"),
        encaisse=Sum("montant_paye"),
        creances=Sum("reste_a_payer"),
    )
    marge = (
        LigneVente.objects.filter(vente__in=ventes).aggregate(
            marge=Sum(
                ExpressionWrapper(
                    F("sous_total") - F("prix_achat_unitaire") * F("quantite"),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            )
        )["marge"]
        or Decimal("0")
    )
    top_produits = list(
        LigneVente.objects.filter(vente__in=ventes)
        .values("nom_produit")
        .annotate(
            quantite=Sum("quantite_base"),
            montant=Sum("sous_total"),
        )
        .order_by("-montant")[:5]
    )
    dettes = DetteCommerciale.objects.filter(
        fournisseur=fournisseur,
        statut__in=[
            DetteCommerciale.Statut.OUVERTE,
            DetteCommerciale.Statut.PARTIELLE,
        ],
    )
    return {
        "produits": {
            "total": produits.count(),
            "actifs": produits.filter(actif=True).count(),
            "en_alerte": produits.filter(
                actif=True,
                stock_actuel__lte=F("seuil_alerte"),
            ).count(),
            "en_rupture": produits.filter(actif=True, stock_actuel__lte=0).count(),
        },
        "ventes": {
            "nombre": ventes.count(),
            "montant_total": str(agregats["total"] or 0),
            "montant_encaisse": str(agregats["encaisse"] or 0),
            "creances": str(agregats["creances"] or 0),
            "marge": str(marge),
            "nombre_clients_acheteurs": ventes.values("client_id")
            .distinct()
            .count(),
        },
        "dettes": {
            "nombre_ouvertes": dettes.count(),
            "montant_restant": str(
                dettes.aggregate(total=Sum("montant_restant"))["total"] or 0
            ),
        },
        "notifications": {
            "echouees": NotificationExterne.objects.filter(
                notification__fournisseur=fournisseur,
                statut=NotificationExterne.Statut.ECHOUE,
            ).count(),
        },
        "top_produits": [
            {
                "nom_produit": item["nom_produit"],
                "quantite": str(item["quantite"] or 0),
                "montant": str(item["montant"] or 0),
            }
            for item in top_produits
        ],
    }


def statistiques_globales_commerce():
    ventes_par_fournisseur = {
        ligne["fournisseur"]: ligne
        for ligne in Vente.objects.filter(statut=Vente.Statut.VALIDEE)
        .values("fournisseur")
        .annotate(
            total=Sum("montant_total"),
            encaisse=Sum("montant_paye"),
            creances=Sum("reste_a_payer"),
            nombre=Count("id"),
        )
    }
    produits_par_fournisseur = {
        ligne["fournisseur"]: ligne["nombre"]
        for ligne in Produit.objects.values("fournisseur").annotate(
            nombre=Count("id")
        )
    }

    par_fournisseur = []
    total_produits = 0
    total_ventes = 0
    volume_ventes = Decimal("0")
    montant_encaisse = Decimal("0")
    creances = Decimal("0")

    for fournisseur in fournisseur_queryset():
        ventes = ventes_par_fournisseur.get(fournisseur.pk, {})
        nombre_produits = produits_par_fournisseur.get(fournisseur.pk, 0)
        nombre_ventes = ventes.get("nombre", 0) or 0
        total = ventes.get("total") or Decimal("0")
        encaisse = ventes.get("encaisse") or Decimal("0")
        creance = ventes.get("creances") or Decimal("0")

        total_produits += nombre_produits
        total_ventes += nombre_ventes
        volume_ventes += total
        montant_encaisse += encaisse
        creances += creance

        if nombre_produits or nombre_ventes:
            par_fournisseur.append(
                {
                    "fournisseur": fournisseur.pk,
                    "nom_entreprise": fournisseur.nom_entreprise,
                    "code_entreprise": fournisseur.code_entreprise,
                    "nombre_produits": nombre_produits,
                    "nombre_ventes": nombre_ventes,
                    "volume_ventes": str(total),
                    "montant_encaisse": str(encaisse),
                    "creances": str(creance),
                }
            )

    anomalies_ventes = (
        Vente.objects.filter(statut=Vente.Statut.VALIDEE)
        .filter(
            Q(montant_paye__gt=F("montant_total"))
            | ~Q(
                reste_a_payer=F("montant_total") - F("montant_paye")
            )
        )
        .count()
    )

    return {
        "totaux": {
            "fournisseurs_actifs": len(par_fournisseur),
            "produits": total_produits,
            "ventes": total_ventes,
            "volume_ventes": str(volume_ventes),
            "montant_encaisse": str(montant_encaisse),
            "creances": str(creances),
        },
        "anomalies": {
            "ventes_incoherentes": anomalies_ventes,
            "produits_stock_negatif": Produit.objects.filter(
                stock_actuel__lt=0
            ).count(),
            "notifications_echouees": NotificationExterne.objects.filter(
                statut=NotificationExterne.Statut.ECHOUE
            ).count(),
        },
        "par_fournisseur": sorted(
            par_fournisseur,
            key=lambda item: item["nom_entreprise"].lower(),
        ),
    }


def fournisseur_queryset():
    from apps.fournisseurs.models import Fournisseur

    return Fournisseur.objects.all().order_by("nom_entreprise")
