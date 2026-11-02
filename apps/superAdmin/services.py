from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.abonnements.models import (
    Abonnement,
    EvenementAbonnement,
    PaiementAbonnement,
)
from apps.abonnements.services import etat_abonnement_fournisseur
from apps.agents.models import Agent
from apps.clients.models import Client
from apps.encaissements.models import Encaissement
from apps.fournisseurs.models import Fournisseur

MONTANT_DECIMAL = DecimalField(max_digits=12, decimal_places=2)
ZERO = Decimal("0")


def _arrondir(valeur):
    return valeur.quantize(Decimal("0.01"))


def _bornes_dt(debut, fin):
    """Bornes temporelles aware, fin exclusive, dans le fuseau du projet."""
    fuseau = timezone.get_current_timezone()
    debut_dt = timezone.make_aware(
        datetime.combine(debut, time.min),
        fuseau,
    )
    fin_dt = timezone.make_aware(
        datetime.combine(fin + timedelta(days=1), time.min),
        fuseau,
    )
    return debut_dt, fin_dt


def _bornes_semaine(reference):
    lundi = reference - timedelta(days=reference.weekday())
    return lundi, lundi + timedelta(days=6)


def _bornes_mois(reference):
    debut = reference.replace(day=1)
    if debut.month == 12:
        suivant = debut.replace(year=debut.year + 1, month=1)
    else:
        suivant = debut.replace(month=debut.month + 1)
    return debut, suivant - timedelta(days=1)


def _bornes_annee(reference):
    return date(reference.year, 1, 1), date(reference.year, 12, 31)


def _mois_precedent(reference):
    debut_mois, _ = _bornes_mois(reference)
    return debut_mois - timedelta(days=1)


def _totaux_par_devise(debut, fin, base=None):
    """Retourne {devise: (montant, nombre)} pour les paiements confirmés."""
    base = base if base is not None else PaiementAbonnement.objects.all()
    debut_dt, fin_dt = _bornes_dt(debut, fin)
    lignes = (
        base.filter(
            statut=PaiementAbonnement.Statut.CONFIRME,
            date_confirmation__gte=debut_dt,
            date_confirmation__lt=fin_dt,
        )
        .values("devise")
        .annotate(
            total=Coalesce(
                Sum("montant"),
                Value(ZERO),
                output_field=MONTANT_DECIMAL,
            ),
            nombre=Count("id"),
        )
    )
    return {
        ligne["devise"]: (ligne["total"] or ZERO, ligne["nombre"])
        for ligne in lignes
    }


def _point_variation(actuel, precedent):
    actuel = actuel or ZERO
    precedent = precedent or ZERO
    if precedent == 0:
        return None
    return _arrondir(((actuel - precedent) / precedent) * 100)


def _lister_buckets(debut, fin, granularite):
    buckets = []
    if granularite == "mois":
        courant = debut.replace(day=1)
        while courant <= fin:
            buckets.append(courant)
            if courant.month == 12:
                courant = courant.replace(year=courant.year + 1, month=1)
            else:
                courant = courant.replace(month=courant.month + 1)
        return buckets

    if granularite == "semaine":
        courant = debut - timedelta(days=debut.weekday())
        while courant <= fin:
            buckets.append(courant)
            courant += timedelta(days=7)
        return buckets

    courant = debut
    while courant <= fin:
        buckets.append(courant)
        courant += timedelta(days=1)
    return buckets


def _cle_bucket(moment, granularite):
    jour = timezone.localtime(moment).date()
    if granularite == "mois":
        return jour.replace(day=1)
    if granularite == "semaine":
        return jour - timedelta(days=jour.weekday())
    return jour


def _serie_confirmee(debut, fin, granularite):
    """Agrège les paiements confirmés par devise et par bucket temporel."""
    debut_dt, fin_dt = _bornes_dt(debut, fin)
    paiements = PaiementAbonnement.objects.filter(
        statut=PaiementAbonnement.Statut.CONFIRME,
        date_confirmation__gte=debut_dt,
        date_confirmation__lt=fin_dt,
    ).values("devise", "montant", "date_confirmation")

    buckets = _lister_buckets(debut, fin, granularite)
    donnees = {}
    for paiement in paiements:
        devise = paiement["devise"]
        cle = _cle_bucket(paiement["date_confirmation"], granularite)
        agregat = donnees.setdefault(devise, {})
        montant, nombre = agregat.get(cle, (ZERO, 0))
        agregat[cle] = (montant + paiement["montant"], nombre + 1)

    return buckets, donnees


def statistiques_financieres(date_reference=None):
    """Indicateurs de chiffre d'affaires, séparés par devise."""
    reference = date_reference or timezone.localdate()

    semaine_debut, semaine_fin = _bornes_semaine(reference)
    semaine_precedente = (
        semaine_debut - timedelta(days=7),
        semaine_fin - timedelta(days=7),
    )
    mois_debut, mois_fin = _bornes_mois(reference)
    mois_precedent_ref = _mois_precedent(reference)
    mois_precedent_debut, mois_precedent_fin = _bornes_mois(mois_precedent_ref)
    annee_debut, annee_fin = _bornes_annee(reference)
    annee_precedente = (
        date(reference.year - 1, 1, 1),
        date(reference.year - 1, 12, 31),
    )

    tous = PaiementAbonnement.objects.filter(
        statut=PaiementAbonnement.Statut.CONFIRME
    )
    total_global = _totaux_par_devise(
        date(1970, 1, 1),
        date(2999, 12, 31),
        base=tous,
    )
    semaine = _totaux_par_devise(semaine_debut, semaine_fin)
    semaine_avant = _totaux_par_devise(*semaine_precedente)
    mois = _totaux_par_devise(mois_debut, mois_fin)
    mois_avant = _totaux_par_devise(mois_precedent_debut, mois_precedent_fin)
    annee = _totaux_par_devise(annee_debut, annee_fin)
    annee_avant = _totaux_par_devise(*annee_precedente)

    devises = sorted(
        set(total_global)
        | set(semaine)
        | set(mois)
        | set(annee)
    )
    if not devises:
        devises = ["XOF"]

    details = []
    for devise in devises:
        total, nombre_total = total_global.get(devise, (ZERO, 0))
        total_semaine = semaine.get(devise, (ZERO, 0))[0]
        total_mois = mois.get(devise, (ZERO, 0))[0]
        total_annee = annee.get(devise, (ZERO, 0))[0]
        moyenne = _arrondir(total / nombre_total) if nombre_total else ZERO
        details.append(
            {
                "devise": devise,
                "chiffre_affaires_total": total,
                "chiffre_affaires_semaine": total_semaine,
                "chiffre_affaires_mois": total_mois,
                "chiffre_affaires_annee": total_annee,
                "nombre_paiements_confirmes": nombre_total,
                "montant_moyen_paiement": moyenne,
                "evolution": {
                    "semaine": _point_variation(
                        total_semaine,
                        semaine_avant.get(devise, (ZERO, 0))[0],
                    ),
                    "mois": _point_variation(
                        total_mois,
                        mois_avant.get(devise, (ZERO, 0))[0],
                    ),
                    "annee": _point_variation(
                        total_annee,
                        annee_avant.get(devise, (ZERO, 0))[0],
                    ),
                },
            }
        )

    principale = max(
        details,
        key=lambda item: (item["chiffre_affaires_total"], item["devise"]),
    )
    resultat = dict(principale)
    resultat["multi_devise"] = len(details) > 1
    resultat["devises"] = details
    return resultat


def serie_chiffre_affaires(
    periode="hebdomadaire",
    granularite=None,
    date_reference=None,
):
    """Série de revenus confirmés par période et par devise."""
    reference = date_reference or timezone.localdate()
    if periode in ("mensuel", "mois"):
        periode = "mensuelle"
    if periode in ("annuel", "annee"):
        periode = "annuelle"

    if periode == "mensuelle":
        debut, fin = _bornes_mois(reference)
        granularite = granularite or "jour"
        if granularite not in ("jour", "semaine"):
            granularite = "jour"
    elif periode == "annuelle":
        debut, fin = _bornes_annee(reference)
        granularite = "mois"
    else:
        periode = "hebdomadaire"
        debut, fin = _bornes_semaine(reference)
        granularite = "jour"

    buckets, donnees = _serie_confirmee(debut, fin, granularite)
    devises_existantes = list(
        PaiementAbonnement.objects.filter(
            statut=PaiementAbonnement.Statut.CONFIRME
        )
        .values_list("devise", flat=True)
        .distinct()
        .order_by("devise")
    )
    for devise in donnees:
        if devise not in devises_existantes:
            devises_existantes.append(devise)
    devises_existantes.sort()

    series = []
    for devise in devises_existantes:
        agregats = donnees.get(devise, {})
        points = []
        total = ZERO
        nombre = 0
        for bucket in buckets:
            montant, compte = agregats.get(bucket, (ZERO, 0))
            total += montant
            nombre += compte
            points.append(
                {
                    "date": bucket.isoformat(),
                    "montant": montant,
                    "nombre_paiements": compte,
                }
            )
        series.append(
            {
                "devise": devise,
                "total": total,
                "nombre_paiements": nombre,
                "points": points,
            }
        )

    return {
        "periode": periode,
        "granularite": granularite,
        "date_debut": debut.isoformat(),
        "date_fin": fin.isoformat(),
        "devises": series,
    }


def statistiques_abonnements(periode="mensuelle", date_reference=None):
    """Statuts courants et évolution des souscriptions/renouvellements."""
    reference = date_reference or timezone.localdate()
    maintenant = timezone.now()

    if periode in ("mensuel", "mois"):
        periode = "mensuelle"
    if periode in ("annuel", "annee"):
        periode = "annuelle"

    if periode == "hebdomadaire":
        debut, fin = _bornes_semaine(reference)
        granularite = "jour"
    elif periode == "annuelle":
        debut, fin = _bornes_annee(reference)
        granularite = "mois"
    else:
        periode = "mensuelle"
        debut, fin = _bornes_mois(reference)
        granularite = "jour"

    abonnements = Abonnement.objects.all()
    actifs = abonnements.filter(
        statut=Abonnement.Statut.ACTIF,
        date_debut__lte=maintenant,
        date_fin__gt=maintenant,
    )
    expires = abonnements.filter(
        Q(statut=Abonnement.Statut.EXPIRE)
        | (
            Q(statut__in=[Abonnement.Statut.ACTIF, Abonnement.Statut.SUSPENDU])
            & Q(date_fin__lte=maintenant)
        )
    )
    suspendus = abonnements.filter(
        statut=Abonnement.Statut.SUSPENDU,
        date_fin__gt=maintenant,
    )
    annules = abonnements.filter(statut=Abonnement.Statut.ANNULE)

    debut_dt, fin_dt = _bornes_dt(debut, fin)
    evenements = EvenementAbonnement.objects.filter(
        date_creation__gte=debut_dt,
        date_creation__lt=fin_dt,
        type_evenement__in=[
            EvenementAbonnement.Type.CREATION,
            EvenementAbonnement.Type.RENOUVELLEMENT,
        ],
    )
    nouvelles = evenements.filter(
        type_evenement=EvenementAbonnement.Type.CREATION
    ).count()
    renouvellements = evenements.filter(
        type_evenement=EvenementAbonnement.Type.RENOUVELLEMENT
    ).count()

    buckets = _lister_buckets(debut, fin, granularite)
    evenements_par_bucket = {}
    for evenement in evenements.values("type_evenement", "date_creation"):
        cle = _cle_bucket(evenement["date_creation"], granularite)
        item = evenements_par_bucket.setdefault(
            cle,
            {"nouvelle": 0, "renouvellement": 0},
        )
        if evenement["type_evenement"] == EvenementAbonnement.Type.CREATION:
            item["nouvelle"] += 1
        else:
            item["renouvellement"] += 1

    creations_par_bucket = {}
    for creation in abonnements.filter(
        date_creation__gte=debut_dt,
        date_creation__lt=fin_dt,
    ).values("date_creation"):
        cle = _cle_bucket(creation["date_creation"], granularite)
        creations_par_bucket[cle] = creations_par_bucket.get(cle, 0) + 1

    total_avant = abonnements.filter(date_creation__lt=debut_dt).count()
    points = []
    cumul = total_avant
    for bucket in buckets:
        comptes = evenements_par_bucket.get(
            bucket,
            {"nouvelle": 0, "renouvellement": 0},
        )
        cumul += creations_par_bucket.get(bucket, 0)
        points.append(
            {
                "date": bucket.isoformat(),
                "nouvelles_souscriptions": comptes["nouvelle"],
                "renouvellements": comptes["renouvellement"],
                "total_abonnements": cumul,
            }
        )

    return {
        "periode": periode,
        "granularite": granularite,
        "date_debut": debut.isoformat(),
        "date_fin": fin.isoformat(),
        "statuts": {
            "total": abonnements.count(),
            "actifs": actifs.count(),
            "expires": expires.count(),
            "suspendus": suspendus.count(),
            "annules": annules.count(),
            "essais_actifs": actifs.filter(est_essai=True).count(),
        },
        "periode_courante": {
            "nouvelles_souscriptions": nouvelles,
            "renouvellements": renouvellements,
        },
        "points": points,
    }


def _agreger_encaissements(queryset):
    return queryset.aggregate(
        total=Count("id"),
        en_attente=Count(
            "id",
            filter=Q(statut=Encaissement.Statut.EN_ATTENTE),
        ),
        confirmes=Count(
            "id",
            filter=Q(statut=Encaissement.Statut.CONFIRME),
        ),
        annules=Count(
            "id",
            filter=Q(statut=Encaissement.Statut.ANNULE),
        ),
        montant_confirme=Coalesce(
            Sum(
                "montant",
                filter=Q(statut=Encaissement.Statut.CONFIRME),
            ),
            Value(Decimal("0")),
            output_field=MONTANT_DECIMAL,
        ),
        montant_en_attente=Coalesce(
            Sum(
                "montant",
                filter=Q(statut=Encaissement.Statut.EN_ATTENTE),
            ),
            Value(Decimal("0")),
            output_field=MONTANT_DECIMAL,
        ),
    )


def statistiques_fournisseur(fournisseur):
    clients = Client.objects.filter(fournisseur=fournisseur)
    agents = Agent.objects.filter(fournisseur=fournisseur)
    encaissements = Encaissement.objects.filter(
        client__fournisseur=fournisseur
    )

    return {
        "fournisseur": fournisseur,
        "clients": {
            "total": clients.count(),
            "actifs": clients.filter(compte_active=True).count(),
        },
        "agents": {
            "total": agents.count(),
            "actifs": agents.filter(compte_active=True).count(),
        },
        "encaissements": _agreger_encaissements(encaissements),
        "abonnement": etat_abonnement_fournisseur(fournisseur),
    }


def statistiques_globales():
    maintenant = timezone.now()
    abonnements = Abonnement.objects.all()

    return {
        "fournisseurs": {
            "total": Fournisseur.objects.count(),
            "actifs": Fournisseur.objects.filter(est_suspendu=False).count(),
            "suspendus": Fournisseur.objects.filter(est_suspendu=True).count(),
        },
        "clients": {
            "total": Client.objects.count(),
            "actifs": Client.objects.filter(compte_active=True).count(),
        },
        "agents": {
            "total": Agent.objects.count(),
            "actifs": Agent.objects.filter(compte_active=True).count(),
        },
        "encaissements": _agreger_encaissements(Encaissement.objects.all()),
        "abonnements": {
            "total": abonnements.count(),
            "actifs": abonnements.filter(
                statut=Abonnement.Statut.ACTIF,
                date_debut__lte=maintenant,
                date_fin__gt=maintenant,
            ).count(),
            "expires": abonnements.filter(
                Q(statut=Abonnement.Statut.EXPIRE)
                | (
                    Q(
                        statut__in=[
                            Abonnement.Statut.ACTIF,
                            Abonnement.Statut.SUSPENDU,
                        ]
                    )
                    & Q(date_fin__lte=maintenant)
                )
            ).count(),
            "suspendus": abonnements.filter(
                statut=Abonnement.Statut.SUSPENDU,
                date_fin__gt=maintenant,
            ).count(),
            "annules": abonnements.filter(
                statut=Abonnement.Statut.ANNULE
            ).count(),
            "essais_actifs": abonnements.filter(
                est_essai=True,
                statut=Abonnement.Statut.ACTIF,
                date_debut__lte=maintenant,
                date_fin__gt=maintenant,
            ).count(),
        },
        "montant_facture_total": abonnements.filter(
            est_essai=False
        ).aggregate(
            total=Coalesce(
                Sum("montant"),
                Value(Decimal("0")),
                output_field=MONTANT_DECIMAL,
            )
        )["total"],
        "finances": statistiques_financieres(),
    }
