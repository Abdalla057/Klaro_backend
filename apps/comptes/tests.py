from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from apps.abonnements.models import Abonnement, PlanAbonnement
from apps.agents.models import Agent
from apps.clients.models import Client
from apps.fournisseurs.models import Fournisseur
from apps.fournisseurs.services import suspendre_fournisseur

from .models import Utilisateur


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class AuthentificationTests(APITestCase):
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
        self.fournisseur_user = Utilisateur.objects.create_user(
            telephone="720000001",
            password="MotDePasse123",
            role="fournisseur",
        )
        self.fournisseur = Fournisseur.objects.create(
            utilisateur=self.fournisseur_user,
            nom_entreprise="Alpha",
            telephone="720000001",
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

        self.client_user = Utilisateur.objects.create_user(
            telephone="720000002",
            password="MotDePasse123",
            role="client",
            is_active=True,
        )
        self.client_profile = Client.objects.create(
            utilisateur=self.client_user,
            fournisseur=self.fournisseur,
            telephone="720000002",
            compte_active=True,
        )
        self.agent_user = Utilisateur.objects.create_user(
            telephone="720000003",
            password="MotDePasse123",
            role="agent",
            is_active=True,
        )
        self.agent_profile = Agent.objects.create(
            utilisateur=self.agent_user,
            fournisseur=self.fournisseur,
            telephone="720000003",
            compte_active=True,
        )

    def _login(self, telephone="720000001", password="MotDePasse123"):
        return self.client.post(
            "/api/comptes/login/",
            {"telephone": telephone, "password": password},
            format="json",
        )

    def _authentifier(self, access):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    def test_login_retourne_les_jetons_et_le_profil(self):
        response = self._login()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["utilisateur"]["role"], "fournisseur")
        self.assertEqual(response.data["utilisateur"]["telephone"], "720000001")

        token = AccessToken(response.data["access"])
        self.assertEqual(token["role"], "fournisseur")
        self.assertEqual(token["telephone"], "720000001")
        self.assertEqual(token["fournisseur_id"], self.fournisseur.pk)

    def test_login_normalise_le_telephone(self):
        response = self._login(telephone=" 720 000 001 ")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_login_client_expose_le_fournisseur(self):
        response = self._login(telephone=self.client_user.telephone)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        token = AccessToken(response.data["access"])
        self.assertEqual(token["role"], "client")
        self.assertEqual(token["fournisseur_id"], self.fournisseur.pk)

    def test_login_refuse_un_mauvais_mot_de_passe(self):
        response = self._login(password="MauvaisMotDePasse123")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_refuse_un_compte_inactif(self):
        self.fournisseur_user.is_active = False
        self.fournisseur_user.save(update_fields=["is_active"])

        response = self._login()

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_refuse_un_fournisseur_suspendu(self):
        suspendre_fournisseur(self.fournisseur, "Test de suspension")

        response = self._login()

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_exige_un_jeton(self):
        response = self.client.get("/api/comptes/me/")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_retourne_l_utilisateur_connecte(self):
        login = self._login()
        self._authentifier(login.data["access"])

        response = self.client.get("/api/comptes/me/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["telephone"], "720000001")
        self.assertEqual(response.data["role"], "fournisseur")

    def test_refresh_effectue_une_rotation_et_blackliste_l_ancien(self):
        login = self._login()
        ancien_refresh = login.data["refresh"]

        response = self.client.post(
            "/api/comptes/refresh/",
            {"refresh": ancien_refresh},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertNotEqual(response.data["refresh"], ancien_refresh)

        refused = self.client.post(
            "/api/comptes/refresh/",
            {"refresh": ancien_refresh},
            format="json",
        )
        self.assertEqual(refused.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_blackliste_le_refresh(self):
        login = self._login()
        self._authentifier(login.data["access"])

        response = self.client.post(
            "/api/comptes/logout/",
            {"refresh": login.data["refresh"]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_205_RESET_CONTENT)
        refused = self.client.post(
            "/api/comptes/refresh/",
            {"refresh": login.data["refresh"]},
            format="json",
        )
        self.assertEqual(refused.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_refuse_le_refresh_d_un_autre_utilisateur(self):
        login_client = self._login(telephone=self.client_user.telephone)
        login_fournisseur = self._login()
        self._authentifier(login_fournisseur.data["access"])

        response = self.client.post(
            "/api/comptes/logout/",
            {"refresh": login_client.data["refresh"]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_changement_de_mot_de_passe(self):
        login = self._login()
        ancien_refresh = login.data["refresh"]
        self._authentifier(login.data["access"])

        response = self.client.post(
            "/api/comptes/mot-de-passe/",
            {
                "ancien_mot_de_passe": "MotDePasse123",
                "nouveau_mot_de_passe": "NouveauMotDePasse456",
                "confirmation": "NouveauMotDePasse456",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.client.credentials()
        refuse_ancien = self._login(password="MotDePasse123")
        self.assertEqual(
            refuse_ancien.status_code, status.HTTP_401_UNAUTHORIZED
        )

        accepte_nouveau = self._login(password="NouveauMotDePasse456")
        self.assertEqual(accepte_nouveau.status_code, status.HTTP_200_OK)

        refuse_refresh = self.client.post(
            "/api/comptes/refresh/",
            {"refresh": ancien_refresh},
            format="json",
        )
        self.assertEqual(
            refuse_refresh.status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_changement_de_mot_de_passe_refuse_un_mot_de_passe_faible(self):
        login = self._login()
        self._authentifier(login.data["access"])

        response = self.client.post(
            "/api/comptes/mot-de-passe/",
            {
                "ancien_mot_de_passe": "MotDePasse123",
                "nouveau_mot_de_passe": "12345678",
                "confirmation": "12345678",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("nouveau_mot_de_passe", response.data)

    def test_changement_de_mot_de_passe_refuse_un_ancien_incorrect(self):
        login = self._login()
        self._authentifier(login.data["access"])

        response = self.client.post(
            "/api/comptes/mot-de-passe/",
            {
                "ancien_mot_de_passe": "Incorrect123",
                "nouveau_mot_de_passe": "NouveauMotDePasse456",
                "confirmation": "NouveauMotDePasse456",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_est_limite_par_le_throttling(self):
        for _ in range(5):
            response = self._login(password="MauvaisMotDePasse123")
            self.assertEqual(
                response.status_code, status.HTTP_401_UNAUTHORIZED
            )

        response = self._login(password="MauvaisMotDePasse123")
        self.assertEqual(
            response.status_code, status.HTTP_429_TOO_MANY_REQUESTS
        )

    def test_inscription_normalise_le_telephone(self):
        response = APIClient().post(
            "/api/fournisseurs/",
            {
                "nom_entreprise": "Beta",
                "telephone": " 720 000 010 ",
                "password": "MotDePasse123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Utilisateur.objects.filter(telephone="720000010").exists()
        )
        self.assertTrue(
            Fournisseur.objects.filter(telephone="720000010").exists()
        )

    def test_inscription_refuse_un_mot_de_passe_faible(self):
        response = APIClient().post(
            "/api/fournisseurs/",
            {
                "nom_entreprise": "Gamma",
                "telephone": "720000011",
                "password": "12345678",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            Utilisateur.objects.filter(telephone="720000011").exists()
        )

    def test_client_ne_peut_pas_modifier_ni_supprimer_son_profil(self):
        self.client.force_authenticate(user=self.client_user)

        modification = self.client.patch(
            f"/api/clients/{self.client_profile.pk}/",
            {"adresse": "Nouvelle adresse"},
            format="json",
        )
        suppression = self.client.delete(
            f"/api/clients/{self.client_profile.pk}/"
        )
        creation = self.client.post(
            "/api/clients/",
            {"telephone": "720000020"},
            format="json",
        )

        self.assertEqual(
            modification.status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            suppression.status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(creation.status_code, status.HTTP_403_FORBIDDEN)

    def test_agent_ne_peut_pas_modifier_ni_supprimer_son_profil(self):
        self.client.force_authenticate(user=self.agent_user)

        modification = self.client.patch(
            f"/api/agents/{self.agent_profile.pk}/",
            {"zone": "Nouvelle zone"},
            format="json",
        )
        suppression = self.client.delete(
            f"/api/agents/{self.agent_profile.pk}/"
        )
        creation = self.client.post(
            "/api/agents/",
            {"telephone": "720000021"},
            format="json",
        )

        self.assertEqual(
            modification.status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            suppression.status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(creation.status_code, status.HTTP_403_FORBIDDEN)

    def test_fournisseur_modifie_un_client_sans_changer_le_telephone(self):
        self.client.force_authenticate(user=self.fournisseur_user)

        response = self.client.patch(
            f"/api/clients/{self.client_profile.pk}/",
            {
                "adresse": "Nouvelle adresse",
                "telephone": "720000099",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.client_profile.refresh_from_db()
        self.assertEqual(self.client_profile.adresse, "Nouvelle adresse")
        self.assertEqual(self.client_profile.telephone, "720000002")

    def test_telephone_du_fournisseur_est_non_modifiable(self):
        self.client.force_authenticate(user=self.fournisseur_user)

        response = self.client.patch(
            f"/api/fournisseurs/{self.fournisseur.pk}/",
            {
                "nom_entreprise": "Alpha 2",
                "telephone": "720000099",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.fournisseur.refresh_from_db()
        self.assertEqual(self.fournisseur.nom_entreprise, "Alpha 2")
        self.assertEqual(self.fournisseur.telephone, "720000001")

    def test_telephone_de_l_agent_est_non_modifiable(self):
        self.client.force_authenticate(user=self.fournisseur_user)

        response = self.client.patch(
            f"/api/agents/{self.agent_profile.pk}/",
            {
                "zone": "Zone 2",
                "telephone": "720000099",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.agent_profile.refresh_from_db()
        self.assertEqual(self.agent_profile.zone, "Zone 2")
        self.assertEqual(self.agent_profile.telephone, "720000003")

    def test_fournisseur_cree_un_client_avec_un_telephone_normalise(self):
        self.client.force_authenticate(user=self.fournisseur_user)

        response = self.client.post(
            "/api/clients/",
            {
                "telephone": "720 000 030",
                "nom": "Diallo",
                "prenom": "Aminata",
                "adresse": "Cocody",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Client.objects.filter(telephone="720000030").exists()
        )
        self.assertTrue(
            Utilisateur.objects.filter(telephone="720000030").exists()
        )


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class ActivationSecuriseeTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.plan = PlanAbonnement.objects.create(
            code="mensuel",
            nom="Mensuel",
            prix=Decimal("10000.00"),
            devise="XOF",
            duree_jours=30,
            max_clients=10,
            max_agents=5,
            est_essai=False,
            actif=True,
        )
        self.fournisseur_user = Utilisateur.objects.create_user(
            telephone="740000001",
            password=None,
            role="fournisseur",
        )
        self.fournisseur = Fournisseur.objects.create(
            utilisateur=self.fournisseur_user,
            nom_entreprise="Alpha",
            telephone="740000001",
        )
        debut = timezone.now()
        Abonnement.objects.create(
            fournisseur=self.fournisseur,
            plan=self.plan,
            date_debut=debut,
            date_fin=debut + timedelta(days=30),
            statut=Abonnement.Statut.ACTIF,
            est_essai=False,
            montant=self.plan.prix,
            devise=self.plan.devise,
        )
        self.agent_user = Utilisateur.objects.create_user(
            telephone="740000003",
            password=None,
            role="agent",
            is_active=True,
        )
        self.agent = Agent.objects.create(
            utilisateur=self.agent_user,
            fournisseur=self.fournisseur,
            telephone="740000003",
            compte_active=False,
        )
        self.provider_api = APIClient()
        self.provider_api.force_authenticate(self.fournisseur_user)
        self.agent_api = APIClient()
        self.agent_api.force_authenticate(self.agent_user)

    def _creer_client(self, telephone="740000002"):
        return self.provider_api.post(
            "/api/clients/",
            {
                "telephone": telephone,
                "nom": "Diallo",
                "prenom": "Aminata",
                "adresse": "Cocody",
            },
            format="json",
        )

    def test_creation_retourne_un_code_hache_et_activation_en_deux_etapes(self):
        creation = self._creer_client()
        self.assertEqual(creation.status_code, status.HTTP_201_CREATED)
        code = creation.data["code_activation"]
        self.assertRegex(code, r"^\d{6}$")

        client = Client.objects.get(telephone="740000002")
        self.assertTrue(client.code_activation_hash)
        self.assertNotEqual(client.code_activation_hash, code)

        verification = APIClient().post(
            "/api/comptes/activation/verifier-code/",
            {"telephone": "740000002", "code": code},
            format="json",
        )
        self.assertEqual(verification.status_code, status.HTTP_200_OK)
        token = verification.data["token"]

        activation = APIClient().post(
            "/api/comptes/activation/definir-mot-de-passe/",
            {
                "telephone": "740000002",
                "token": token,
                "password": "MotDePasse123",
                "confirmation": "MotDePasse123",
            },
            format="json",
        )
        self.assertEqual(activation.status_code, status.HTTP_200_OK)

        client.refresh_from_db()
        client.utilisateur.refresh_from_db()
        self.assertTrue(client.compte_active)
        self.assertTrue(client.utilisateur.is_active)
        self.assertTrue(client.utilisateur.check_password("MotDePasse123"))
        self.assertFalse(client.code_activation_hash)
        self.assertFalse(client.jeton_activation_hash)

    def test_code_invalide_refuse_et_consomme_une_tentative(self):
        creation = self._creer_client()
        code = creation.data["code_activation"]
        mauvais_code = "000000" if code != "000000" else "111111"

        response = APIClient().post(
            "/api/comptes/activation/verifier-code/",
            {"telephone": "740000002", "code": mauvais_code},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        client = Client.objects.get(telephone="740000002")
        self.assertEqual(client.code_tentatives, 1)

    def test_le_renvoi_de_code_est_reserve_au_fournisseur(self):
        creation = self._creer_client()
        code_initial_hash = Client.objects.get(
            telephone="740000002"
        ).code_activation_hash

        refus = self.agent_api.post(
            "/api/clients/renvoyer-code/",
            {"telephone": "740000002"},
            format="json",
        )
        self.assertEqual(refus.status_code, status.HTTP_403_FORBIDDEN)

        renvoi = self.provider_api.post(
            "/api/clients/renvoyer-code/",
            {"telephone": "740000002"},
            format="json",
        )
        self.assertEqual(renvoi.status_code, status.HTTP_200_OK)
        nouveau_code = renvoi.data["code_activation"]
        self.assertRegex(nouveau_code, r"^\d{6}$")

        client = Client.objects.get(telephone="740000002")
        self.assertNotEqual(client.code_activation_hash, code_initial_hash)
        self.assertEqual(creation.data["id"], client.pk)

    def test_l_endpoint_client_ne_peut_pas_activer_un_agent(self):
        code = self.agent.generer_nouveau_code()

        response = APIClient().post(
            "/api/clients/activer/",
            {
                "telephone": "740000003",
                "code": code,
                "password": "MotDePasse123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.agent.refresh_from_db()
        self.assertFalse(self.agent.compte_active)
