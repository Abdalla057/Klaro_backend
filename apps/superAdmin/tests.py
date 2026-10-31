from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.abonnements.models import Abonnement, PlanAbonnement
from apps.agents.models import Agent
from apps.clients.models import Client
from apps.comptes.models import Utilisateur
from apps.fournisseurs.models import Fournisseur


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class SuperAdminTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = Utilisateur.objects.create_superuser(
            telephone="700000999",
            password="MotDePasse123",
        )
        self.fournisseur = self._creer_fournisseur(
            telephone="700000001",
            nom="Alpha",
        )
        self.plan = PlanAbonnement.objects.create(
            code="mensuel",
            nom="Mensuel",
            prix=Decimal("10000.00"),
            devise="XOF",
            duree_jours=30,
            max_clients=10,
            max_agents=2,
            est_essai=False,
            actif=True,
        )
        debut = timezone.now()
        self.abonnement = Abonnement.objects.create(
            fournisseur=self.fournisseur,
            plan=self.plan,
            date_debut=debut,
            date_fin=debut + timedelta(days=self.plan.duree_jours),
            statut=Abonnement.Statut.ACTIF,
            est_essai=False,
            montant=self.plan.prix,
            devise=self.plan.devise,
        )

        client_user = Utilisateur.objects.create_user(
            telephone="700000002",
            password="MotDePasse123",
            role="client",
            is_active=True,
        )
        Client.objects.create(
            utilisateur=client_user,
            fournisseur=self.fournisseur,
            telephone="700000002",
            compte_active=True,
        )
        agent_user = Utilisateur.objects.create_user(
            telephone="700000003",
            password="MotDePasse123",
            role="agent",
            is_active=True,
        )
        Agent.objects.create(
            utilisateur=agent_user,
            fournisseur=self.fournisseur,
            telephone="700000003",
        )

        self.client.force_authenticate(user=self.admin)

    def _creer_fournisseur(self, telephone="700000010", nom="Beta"):
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

    def test_liste_fournisseurs_avec_compteurs(self):
        response = self.client.get("/api/super-admin/fournisseurs/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        fournisseur = response.data["results"][0]
        self.assertEqual(fournisseur["id"], self.fournisseur.pk)
        self.assertEqual(fournisseur["nombre_clients"], 1)
        self.assertEqual(fournisseur["nombre_agents"], 1)
        self.assertEqual(fournisseur["statut"], "ACTIF")
        self.assertEqual(
            fournisseur["utilisateur"]["telephone"],
            self.fournisseur.utilisateur.telephone,
        )

    def test_statistiques_fournisseur(self):
        response = self.client.get(
            f"/api/super-admin/fournisseurs/{self.fournisseur.pk}/statistiques/"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["clients"]["total"], 1)
        self.assertEqual(response.data["agents"]["total"], 1)
        self.assertTrue(response.data["abonnement"]["est_actif"])
        self.assertEqual(response.data["abonnement"]["utilisation"]["clients"], 1)
        self.assertEqual(response.data["abonnement"]["quotas"]["clients"], 10)

    def test_suspendre_puis_reactiver_un_fournisseur(self):
        response = self.client.post(
            f"/api/super-admin/fournisseurs/{self.fournisseur.pk}/suspendre/",
            {"motif": "Fraude avérée"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["statut"], "SUSPENDU")
        self.assertEqual(
            response.data["suspendu_par"]["telephone"],
            self.admin.telephone,
        )
        self.fournisseur.refresh_from_db()
        self.fournisseur.utilisateur.refresh_from_db()
        self.assertTrue(self.fournisseur.est_suspendu)
        self.assertFalse(self.fournisseur.utilisateur.is_active)
        self.abonnement.refresh_from_db()
        self.assertEqual(self.abonnement.statut, Abonnement.Statut.ACTIF)

        response = self.client.post(
            f"/api/super-admin/fournisseurs/{self.fournisseur.pk}/reactiver/",
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["statut"], "ACTIF")
        self.assertIsNone(response.data["suspendu_par"])
        self.fournisseur.refresh_from_db()
        self.fournisseur.utilisateur.refresh_from_db()
        self.assertFalse(self.fournisseur.est_suspendu)
        self.assertTrue(self.fournisseur.utilisateur.is_active)

    def test_tableau_de_bord_global(self):
        response = self.client.get("/api/super-admin/tableau-de-bord/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["fournisseurs"]["total"], 1)
        self.assertEqual(response.data["clients"]["total"], 1)
        self.assertEqual(response.data["agents"]["total"], 1)
        self.assertEqual(response.data["abonnements"]["actifs"], 1)
        self.assertEqual(response.data["encaissements"]["total"], 0)
        self.assertEqual(
            Decimal(response.data["montant_facture_total"]),
            Decimal("10000.00"),
        )

    def test_plan_utilise_ne_peut_pas_etre_supprime(self):
        response = self.client.delete(
            f"/api/abonnements/plans/{self.plan.pk}/"
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "plan_utilise")
        self.assertTrue(PlanAbonnement.objects.filter(pk=self.plan.pk).exists())

    def test_super_admin_peut_souscrire_pour_un_fournisseur(self):
        autre = self._creer_fournisseur(telephone="700000011", nom="Beta")

        response = self.client.post(
            "/api/abonnements/abonnements/",
            {"fournisseur": autre.pk, "plan": self.plan.pk},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["statut"], Abonnement.Statut.ACTIF)
        self.assertEqual(response.data["fournisseur"]["id"], autre.pk)
        self.assertEqual(response.data["cree_par"]["telephone"], self.admin.telephone)
        self.assertEqual(Decimal(response.data["montant"]), self.plan.prix)

    def test_acces_reserve_au_super_admin(self):
        self.client.force_authenticate(user=self.fournisseur.utilisateur)

        self.assertEqual(
            self.client.get("/api/super-admin/fournisseurs/").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.get("/api/super-admin/tableau-de-bord/").status_code,
            status.HTTP_403_FORBIDDEN,
        )
