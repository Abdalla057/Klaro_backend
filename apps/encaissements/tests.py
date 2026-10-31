from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.abonnements.models import Abonnement, PlanAbonnement
from apps.agents.models import Agent
from apps.clients.models import Client
from apps.comptes.models import Utilisateur
from apps.encaissements.models import Encaissement
from apps.fournisseurs.models import Fournisseur


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class EncaissementTests(APITestCase):
    def setUp(self):
        cache.clear()
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
        self.fournisseur = self._creer_fournisseur(
            telephone="710000001",
            nom="Maison A",
        )
        self.autre_fournisseur = self._creer_fournisseur(
            telephone="710000002",
            nom="Maison B",
        )
        self.fournisseur_user = self.fournisseur.utilisateur
        self.autre_fournisseur_user = self.autre_fournisseur.utilisateur

        self.agent_profil = self._creer_et_activer_agent(
            self.fournisseur_user,
            "710000003",
        )
        self.agent_user = self.agent_profil.utilisateur
        self.client_profil = self._creer_et_activer_client(
            self.fournisseur_user,
            "710000004",
        )
        self.client_user = self.client_profil.utilisateur

        agent_autre = Utilisateur.objects.create_user(
            telephone="710000005",
            password="MotDePasse123",
            role="agent",
            is_active=True,
        )
        self.agent_autre_profil = Agent.objects.create(
            utilisateur=agent_autre,
            fournisseur=self.autre_fournisseur,
            telephone="710000005",
        )

    def _creer_fournisseur(self, telephone, nom):
        utilisateur = Utilisateur.objects.create_user(
            telephone=telephone,
            password="MotDePasse123",
            role="fournisseur",
        )
        fournisseur = Fournisseur.objects.create(
            utilisateur=utilisateur,
            nom_entreprise=nom,
            telephone=telephone,
        )
        debut = timezone.now()
        Abonnement.objects.create(
            fournisseur=fournisseur,
            plan=self.plan,
            date_debut=debut,
            date_fin=debut + timedelta(days=self.plan.duree_jours),
            statut=Abonnement.Statut.ACTIF,
            est_essai=False,
            montant=self.plan.prix,
            devise=self.plan.devise,
        )
        return fournisseur

    def _api(self, utilisateur):
        client = APIClient()
        client.force_authenticate(user=utilisateur)
        return client

    def _creer_et_activer_client(self, fournisseur_user, telephone):
        creation = self._api(fournisseur_user).post(
            "/api/clients/",
            {
                "telephone": telephone,
                "nom": "Test",
                "prenom": "Client",
                "adresse": "Adresse test",
            },
            format="json",
        )
        self.assertEqual(creation.status_code, status.HTTP_201_CREATED)
        profil = Client.objects.get(telephone=telephone)
        activation = APIClient().post(
            "/api/clients/activer/",
            {
                "telephone": telephone,
                "code": creation.data["code_activation"],
                "password": "MotDePasse123",
            },
            format="json",
        )
        self.assertEqual(activation.status_code, status.HTTP_200_OK)
        profil.refresh_from_db()
        return profil

    def _creer_et_activer_agent(self, fournisseur_user, telephone):
        creation = self._api(fournisseur_user).post(
            "/api/agents/",
            {"telephone": telephone},
            format="json",
        )
        self.assertEqual(creation.status_code, status.HTTP_201_CREATED)
        profil = Agent.objects.get(telephone=telephone)
        activation = APIClient().post(
            "/api/agents/activer/",
            {
                "telephone": telephone,
                "code": creation.data["code_activation"],
                "password": "MotDePasse123",
            },
            format="json",
        )
        self.assertEqual(activation.status_code, status.HTTP_200_OK)
        profil.refresh_from_db()
        return profil

    def test_creation_client_cree_un_compte_inactif(self):
        response = self._api(self.fournisseur_user).post(
            "/api/clients/",
            {
                "telephone": "710000006",
                "nom": "Test",
                "prenom": "Client",
                "adresse": "Adresse test",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        profil = Client.objects.get(telephone="710000006")
        self.assertFalse(profil.compte_active)
        self.assertFalse(profil.utilisateur.is_active)
        self.assertEqual(profil.utilisateur.role, "client")
        self.assertEqual(profil.utilisateur.username, "710000006")

    def test_activation_avec_code_invalide_refusee(self):
        self._api(self.fournisseur_user).post(
            "/api/clients/",
            {"telephone": "710000007"},
            format="json",
        )

        response = APIClient().post(
            "/api/clients/activer/",
            {
                "telephone": "710000007",
                "code": "000000",
                "password": "MotDePasse123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_isolation_liste_clients(self):
        response = self._api(self.autre_fournisseur_user).get("/api/clients/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)

        response = self._api(self.fournisseur_user).get("/api/clients/")
        self.assertEqual(response.data["count"], 1)

    def test_agent_et_client_ne_creent_pas_de_profils(self):
        agent_api = self._api(self.agent_user)
        self.assertEqual(
            agent_api.post(
                "/api/clients/",
                {"telephone": "710000008"},
                format="json",
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        client_api = self._api(self.client_user)
        self.assertEqual(
            client_api.post(
                "/api/agents/",
                {"telephone": "710000009"},
                format="json",
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_client_cree_un_encaissement_en_attente(self):
        response = self._api(self.client_user).post(
            "/api/encaissements/",
            {"montant": "400.00", "commentaire": "Acompte"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["statut"], Encaissement.Statut.EN_ATTENTE)
        self.assertEqual(response.data["client"], self.client_profil.pk)
        encaissement = Encaissement.objects.get(pk=response.data["id"])
        self.assertEqual(encaissement.client_id, self.client_profil.pk)

    def test_montant_non_positif_refuse(self):
        api = self._api(self.client_user)

        for montant in ("0", "-10.00"):
            response = api.post(
                "/api/encaissements/",
                {"montant": montant},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_confirmation_diminue_le_solde_et_anti_replay(self):
        Client.objects.filter(pk=self.client_profil.pk).update(
            solde_du=Decimal("1000.00")
        )
        creation = self._api(self.client_user).post(
            "/api/encaissements/",
            {"montant": "400.00"},
            format="json",
        )
        encaissement_id = creation.data["id"]
        agent_api = self._api(self.agent_user)

        confirmation = agent_api.post(
            f"/api/encaissements/{encaissement_id}/confirmer/",
            format="json",
        )
        self.assertEqual(confirmation.status_code, status.HTTP_200_OK)
        self.assertEqual(
            confirmation.data["statut"],
            Encaissement.Statut.CONFIRME,
        )
        self.client_profil.refresh_from_db()
        self.assertEqual(self.client_profil.solde_du, Decimal("600.00"))

        replay = agent_api.post(
            f"/api/encaissements/{encaissement_id}/confirmer/",
            format="json",
        )
        self.assertEqual(replay.status_code, status.HTTP_400_BAD_REQUEST)
        self.client_profil.refresh_from_db()
        self.assertEqual(self.client_profil.solde_du, Decimal("600.00"))

    def test_confirmation_par_code(self):
        creation = self._api(self.client_user).post(
            "/api/encaissements/",
            {"montant": "150.00"},
            format="json",
        )
        encaissement = Encaissement.objects.get(pk=creation.data["id"])

        response = self._api(self.agent_user).post(
            "/api/encaissements/confirmer-par-code/",
            {"code": str(encaissement.code_confirmation)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["statut"], Encaissement.Statut.CONFIRME)

    def test_code_de_confirmation_invalide_refuse(self):
        response = self._api(self.agent_user).post(
            "/api/encaissements/confirmer-par-code/",
            {"code": "pas-un-uuid"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_agent_autre_fournisseur_ne_confirme_pas(self):
        creation = self._api(self.client_user).post(
            "/api/encaissements/",
            {"montant": "75.00"},
            format="json",
        )
        encaissement = Encaissement.objects.get(pk=creation.data["id"])

        par_pk = self._api(self.agent_autre_profil.utilisateur).post(
            f"/api/encaissements/{encaissement.pk}/confirmer/",
            format="json",
        )
        self.assertEqual(par_pk.status_code, status.HTTP_404_NOT_FOUND)

        par_code = self._api(self.agent_autre_profil.utilisateur).post(
            "/api/encaissements/confirmer-par-code/",
            {"code": str(encaissement.code_confirmation)},
            format="json",
        )
        self.assertEqual(par_code.status_code, status.HTTP_400_BAD_REQUEST)

        encaissement.refresh_from_db()
        self.assertEqual(encaissement.statut, Encaissement.Statut.EN_ATTENTE)
        self.assertIsNone(encaissement.agent)

    def test_annulation_par_le_client(self):
        creation = self._api(self.client_user).post(
            "/api/encaissements/",
            {"montant": "90.00"},
            format="json",
        )

        response = self._api(self.client_user).post(
            f"/api/encaissements/{creation.data['id']}/annuler/",
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["statut"], Encaissement.Statut.ANNULE)

    def test_annulation_par_le_fournisseur(self):
        creation = self._api(self.client_user).post(
            "/api/encaissements/",
            {"montant": "90.00"},
            format="json",
        )

        response = self._api(self.fournisseur_user).post(
            f"/api/encaissements/{creation.data['id']}/annuler/",
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["statut"], Encaissement.Statut.ANNULE)

    def test_isolation_liste_fournisseurs(self):
        self.assertEqual(
            self._api(self.agent_user).get("/api/fournisseurs/").data["count"],
            0,
        )
        self.assertEqual(
            self._api(self.client_user).get("/api/fournisseurs/").data["count"],
            0,
        )
