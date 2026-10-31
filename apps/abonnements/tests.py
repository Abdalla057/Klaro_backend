from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from apps.agents.models import Agent
from apps.clients.models import Client
from apps.comptes.models import Utilisateur
from apps.encaissements.models import Encaissement
from apps.fournisseurs.models import Fournisseur

from .exceptions import QuotaDepasse
from .models import Abonnement, EvenementAbonnement, PlanAbonnement
from .services import (
    demarrer_essai,
    reactiver,
    renouveler,
    souscrire,
    suspendre,
    verifier_quota_agents,
)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class AbonnementTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.plan_essai, _ = PlanAbonnement.objects.get_or_create(
            code="essai",
            defaults={
                "nom": "Essai gratuit",
                "prix": 0,
                "devise": "XOF",
                "duree_jours": 30,
                "max_clients": 50,
                "max_agents": 1,
                "est_essai": True,
                "actif": True,
            },
        )
        self.plan_mensuel = PlanAbonnement.objects.create(
            code="mensuel",
            nom="Mensuel",
            prix=Decimal("10000.00"),
            devise="XOF",
            duree_jours=30,
            max_clients=1,
            max_agents=1,
            est_essai=False,
            actif=True,
        )

    def creer_fournisseur(self, telephone="700000001", nom="Alpha"):
        utilisateur = Utilisateur.objects.create_user(
            telephone=telephone,
            password="MotDePasse123",
            role="fournisseur",
        )
        return Fournisseur.objects.create(
            utilisateur=utilisateur,
            nom_entreprise=nom,
            telephone=telephone,
        )

    def creer_abonnement(
        self,
        fournisseur,
        plan=None,
        date_fin=None,
        statut=Abonnement.Statut.ACTIF,
    ):
        plan = plan or self.plan_mensuel
        fin = date_fin or timezone.now() + timedelta(days=plan.duree_jours)
        return Abonnement.objects.create(
            fournisseur=fournisseur,
            plan=plan,
            date_debut=fin - timedelta(days=plan.duree_jours),
            date_fin=fin,
            statut=statut,
            est_essai=plan.est_essai,
            montant=0 if plan.est_essai else plan.prix,
            devise=plan.devise,
        )

    def test_inscription_fournisseur_cree_un_essai(self):
        response = self.client.post(
            "/api/fournisseurs/",
            {
                "telephone": "700000100",
                "password": "MotDePasse123",
                "nom_entreprise": "Beta",
                "adresse": "Abidjan",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        fournisseur = Fournisseur.objects.get(telephone="700000100")
        self.assertEqual(fournisseur.utilisateur.role, "fournisseur")
        self.assertEqual(fournisseur.utilisateur.username, "700000100")
        self.assertEqual(response.data["code_entreprise"], fournisseur.code_entreprise)

        abonnement = Abonnement.objects.get(fournisseur=fournisseur)
        self.assertTrue(abonnement.est_essai)
        self.assertEqual(abonnement.plan_id, self.plan_essai.pk)
        self.assertEqual(abonnement.statut, Abonnement.Statut.ACTIF)
        self.assertEqual(abonnement.montant, 0)

    def test_inscription_sans_plan_essai_retourne_503(self):
        self.plan_essai.delete()

        response = self.client.post(
            "/api/fournisseurs/",
            {
                "telephone": "700000101",
                "password": "MotDePasse123",
                "nom_entreprise": "Gamma",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(
            Utilisateur.objects.filter(telephone="700000101").exists()
        )
        self.assertFalse(
            Fournisseur.objects.filter(telephone="700000101").exists()
        )

    def test_abonnement_expire_bloque_creation_client(self):
        fournisseur = self.creer_fournisseur()
        abonnement = self.creer_abonnement(
            fournisseur,
            date_fin=timezone.now() - timedelta(days=1),
        )
        self.client.force_authenticate(user=fournisseur.utilisateur)

        response = self.client.post(
            "/api/clients/",
            {
                "telephone": "700000201",
                "nom": "Test",
                "prenom": "Client",
                "adresse": "Adresse test",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(response.data["code"], "abonnement_inactif")
        self.assertEqual(Client.objects.count(), 0)

        abonnement.refresh_from_db()
        self.assertEqual(abonnement.statut, Abonnement.Statut.EXPIRE)

    def test_quota_clients_bloque_la_deuxieme_creation(self):
        fournisseur = self.creer_fournisseur()
        self.creer_abonnement(fournisseur)
        self.client.force_authenticate(user=fournisseur.utilisateur)

        premiere = self.client.post(
            "/api/clients/",
            {
                "telephone": "700000202",
                "nom": "Test",
                "prenom": "Client",
                "adresse": "Adresse test",
            },
            format="json",
        )
        self.assertEqual(premiere.status_code, status.HTTP_201_CREATED)

        deuxieme = self.client.post(
            "/api/clients/",
            {
                "telephone": "700000203",
                "nom": "Test",
                "prenom": "Client",
                "adresse": "Adresse test",
            },
            format="json",
        )
        self.assertEqual(deuxieme.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(deuxieme.data["code"], "quota_clients_atteint")
        self.assertEqual(Client.objects.count(), 1)

    def test_quota_agents_service(self):
        fournisseur = self.creer_fournisseur()
        self.creer_abonnement(fournisseur)
        utilisateur = Utilisateur.objects.create_user(
            telephone="700000301",
            password="MotDePasse123",
            role="agent",
        )
        Agent.objects.create(
            utilisateur=utilisateur,
            fournisseur=fournisseur,
            telephone="700000301",
        )

        with self.assertRaises(QuotaDepasse):
            verifier_quota_agents(fournisseur)

    def test_renouvellement_prolonge_le_meme_abonnement(self):
        fournisseur = self.creer_fournisseur()
        abonnement = self.creer_abonnement(fournisseur)
        ancienne_fin = abonnement.date_fin

        nouveau = renouveler(abonnement)
        nouveau.refresh_from_db()

        self.assertEqual(nouveau.pk, abonnement.pk)
        self.assertEqual(
            nouveau.date_fin,
            ancienne_fin + timedelta(days=self.plan_mensuel.duree_jours),
        )
        self.assertTrue(
            EvenementAbonnement.objects.filter(
                abonnement=nouveau,
                type_evenement=EvenementAbonnement.Type.RENOUVELLEMENT,
            ).exists()
        )

    def test_souscription_payante_cloture_l_essai(self):
        fournisseur = self.creer_fournisseur()
        essai = demarrer_essai(fournisseur)

        payant = souscrire(fournisseur, self.plan_mensuel)
        essai.refresh_from_db()

        self.assertEqual(essai.statut, Abonnement.Statut.EXPIRE)
        self.assertEqual(payant.statut, Abonnement.Statut.ACTIF)
        self.assertFalse(payant.est_essai)
        self.assertEqual(payant.montant, self.plan_mensuel.prix)
        self.assertTrue(
            EvenementAbonnement.objects.filter(
                abonnement=essai,
                type_evenement=EvenementAbonnement.Type.EXPIRATION,
            ).exists()
        )
        self.assertTrue(
            EvenementAbonnement.objects.filter(
                abonnement=payant,
                type_evenement=EvenementAbonnement.Type.CREATION,
            ).exists()
        )

    def test_souscription_refusee_si_abonnement_suspendu(self):
        fournisseur = self.creer_fournisseur()
        abonnement = self.creer_abonnement(fournisseur)
        suspendre(abonnement, "Impayé")

        with self.assertRaises(ValidationError):
            souscrire(fournisseur, self.plan_mensuel)

    def test_suspension_et_reactivation(self):
        fournisseur = self.creer_fournisseur()
        abonnement = self.creer_abonnement(fournisseur)

        suspendre(abonnement, "Contrôle")
        abonnement.refresh_from_db()
        self.assertEqual(abonnement.statut, Abonnement.Statut.SUSPENDU)

        reactiver(abonnement)
        abonnement.refresh_from_db()
        self.assertEqual(abonnement.statut, Abonnement.Statut.ACTIF)
        self.assertTrue(
            EvenementAbonnement.objects.filter(
                abonnement=abonnement,
                type_evenement=EvenementAbonnement.Type.REACTIVATION,
            ).exists()
        )

    def test_commande_expiration_journalise_l_evenement(self):
        fournisseur = self.creer_fournisseur()
        abonnement = self.creer_abonnement(
            fournisseur,
            date_fin=timezone.now() - timedelta(minutes=1),
        )

        call_command("expirer_abonnements")
        abonnement.refresh_from_db()

        self.assertEqual(abonnement.statut, Abonnement.Statut.EXPIRE)
        self.assertTrue(
            EvenementAbonnement.objects.filter(
                abonnement=abonnement,
                type_evenement=EvenementAbonnement.Type.EXPIRATION,
            ).exists()
        )

    def test_fournisseur_suspendu_bloque_les_ecritures_du_client(self):
        fournisseur = self.creer_fournisseur()
        self.creer_abonnement(fournisseur)
        self.client.force_authenticate(user=fournisseur.utilisateur)
        creation = self.client.post(
            "/api/clients/",
            {
                "telephone": "700000204",
                "nom": "Test",
                "prenom": "Client",
                "adresse": "Adresse test",
            },
            format="json",
        )
        self.assertEqual(creation.status_code, status.HTTP_201_CREATED)

        Fournisseur.objects.filter(pk=fournisseur.pk).update(est_suspendu=True)
        client_profil = Client.objects.get(telephone="700000204")
        self.client.force_authenticate(user=client_profil.utilisateur)

        response = self.client.post(
            "/api/encaissements/",
            {"montant": "100.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(Encaissement.objects.count(), 0)
