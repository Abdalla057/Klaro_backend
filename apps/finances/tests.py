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

from .models import DemandeDecaissement, TransactionFinanciere


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class FluxFinanciersTests(APITestCase):
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
            telephone="730000001",
            password=None,
            role="fournisseur",
        )
        self.fournisseur = Fournisseur.objects.create(
            utilisateur=self.fournisseur_user,
            nom_entreprise="Alpha",
            telephone="730000001",
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
        self.autre_fournisseur_user = Utilisateur.objects.create_user(
            telephone="730000009",
            password=None,
            role="fournisseur",
        )
        self.autre_fournisseur = Fournisseur.objects.create(
            utilisateur=self.autre_fournisseur_user,
            nom_entreprise="Beta",
            telephone="730000009",
        )
        Abonnement.objects.create(
            fournisseur=self.autre_fournisseur,
            plan=self.plan,
            date_debut=debut,
            date_fin=debut + timedelta(days=30),
            statut=Abonnement.Statut.ACTIF,
            est_essai=False,
            montant=self.plan.prix,
            devise=self.plan.devise,
        )

        self.client_user = Utilisateur.objects.create_user(
            telephone="730000002",
            password=None,
            role="client",
            is_active=True,
            first_name="Aminata",
            last_name="Diallo",
        )
        self.client = Client.objects.create(
            utilisateur=self.client_user,
            fournisseur=self.fournisseur,
            telephone="730000002",
            adresse="Cocody",
            solde_du=Decimal("0.00"),
            compte_active=True,
        )
        self.agent_user = Utilisateur.objects.create_user(
            telephone="730000003",
            password=None,
            role="agent",
            is_active=True,
            first_name="Moussa",
            last_name="Traore",
        )
        self.agent = Agent.objects.create(
            utilisateur=self.agent_user,
            fournisseur=self.fournisseur,
            telephone="730000003",
            zone="Centre",
            compte_active=True,
        )

        self.provider_api = APIClient()
        self.provider_api.force_authenticate(self.fournisseur_user)
        self.agent_api = APIClient()
        self.agent_api.force_authenticate(self.agent_user)
        self.client_api = APIClient()
        self.client_api.force_authenticate(self.client_user)
        self.other_provider_api = APIClient()
        self.other_provider_api.force_authenticate(self.autre_fournisseur_user)

    def _attribuer(self, montant="1000.00"):
        response = self.provider_api.post(
            f"/api/clients/{self.client.pk}/attribuer-solde/",
            {"montant": montant},
            format="json",
        )
        self.client.refresh_from_db()
        self.client_user._state.fields_cache.pop("client_profile", None)
        return response

    def test_attribution_initiale_une_seule_fois(self):
        response = self._attribuer()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.client.refresh_from_db()
        self.assertEqual(self.client.solde_du, Decimal("1000.00"))
        transaction = TransactionFinanciere.objects.get(client=self.client)
        self.assertEqual(
            transaction.type_transaction,
            TransactionFinanciere.Type.ATTRIBUTION_INITIALE,
        )
        self.assertEqual(transaction.sens, TransactionFinanciere.Sens.CREDIT)

        deuxieme = self._attribuer("500.00")
        self.assertEqual(deuxieme.status_code, status.HTTP_409_CONFLICT)
        self.client.refresh_from_db()
        self.assertEqual(self.client.solde_du, Decimal("1000.00"))
        self.assertEqual(
            TransactionFinanciere.objects.filter(client=self.client).count(),
            1,
        )

    def test_solde_et_solde_disponible(self):
        attribution = self._attribuer()
        self.assertEqual(
            attribution.status_code,
            status.HTTP_201_CREATED,
            attribution.data,
        )
        self.client.refresh_from_db()
        self.assertEqual(self.client.solde_du, Decimal("1000.00"))
        demande = DemandeDecaissement.objects.create(
            fournisseur=self.fournisseur,
            client=self.client,
            montant=Decimal("300.00"),
        )

        response = self.client_api.get("/api/finances/solde/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["solde_du"], Decimal("1000.00"))
        self.assertEqual(response.data["montant_en_cours"], Decimal("300.00"))
        self.assertEqual(response.data["solde_disponible"], Decimal("700.00"))
        self.assertEqual(demande.statut, DemandeDecaissement.Statut.EN_ATTENTE)

    def test_collecte_directe_par_agent(self):
        self._attribuer()

        response = self.agent_api.post(
            "/api/encaissements/collecter/",
            {"client": self.client.pk, "montant": "300.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["statut"], Encaissement.Statut.CONFIRME)
        self.client.refresh_from_db()
        self.assertEqual(self.client.solde_du, Decimal("700.00"))
        transaction = TransactionFinanciere.objects.get(
            client=self.client,
            type_transaction=TransactionFinanciere.Type.ENCAISSEMENT,
        )
        self.assertEqual(transaction.sens, TransactionFinanciere.Sens.DEBIT)
        self.assertEqual(transaction.agent, self.agent)

    def test_encaissement_client_confirme_une_seule_fois(self):
        self._attribuer()
        creation = self.client_api.post(
            "/api/encaissements/",
            {"montant": "250.00"},
            format="json",
        )
        self.assertEqual(creation.status_code, status.HTTP_201_CREATED)

        confirmation = self.agent_api.post(
            f"/api/encaissements/{creation.data['id']}/confirmer/",
            format="json",
        )
        self.assertEqual(confirmation.status_code, status.HTTP_200_OK)
        self.client.refresh_from_db()
        self.assertEqual(self.client.solde_du, Decimal("750.00"))

        replay = self.agent_api.post(
            f"/api/encaissements/{creation.data['id']}/confirmer/",
            format="json",
        )
        self.assertEqual(replay.status_code, status.HTTP_400_BAD_REQUEST)
        self.client.refresh_from_db()
        self.assertEqual(self.client.solde_du, Decimal("750.00"))
        self.assertEqual(
            TransactionFinanciere.objects.filter(
                client=self.client,
                type_transaction=TransactionFinanciere.Type.ENCAISSEMENT,
            ).count(),
            1,
        )

    def test_workflow_decaissement_complet(self):
        self._attribuer()

        creation = self.client_api.post(
            "/api/finances/decaissements/",
            {"montant": "300.00", "commentaire": "Retrait"},
            format="json",
        )
        self.assertEqual(creation.status_code, status.HTTP_201_CREATED)
        demande_id = creation.data["id"]

        confirmation = self.agent_api.post(
            f"/api/finances/decaissements/{demande_id}/confirmer/",
            format="json",
        )
        self.assertEqual(confirmation.status_code, status.HTTP_200_OK)
        self.assertEqual(
            confirmation.data["statut"],
            DemandeDecaissement.Statut.CONFIRMATION_AGENT,
        )

        approbation = self.provider_api.post(
            f"/api/finances/decaissements/{demande_id}/approuver/",
            format="json",
        )
        self.assertEqual(approbation.status_code, status.HTTP_200_OK)
        self.assertEqual(
            approbation.data["statut"],
            DemandeDecaissement.Statut.APPROUVE,
        )

        paiement = self.provider_api.post(
            f"/api/finances/decaissements/{demande_id}/payer/",
            format="json",
        )
        self.assertEqual(paiement.status_code, status.HTTP_200_OK)
        self.assertEqual(
            paiement.data["statut"],
            DemandeDecaissement.Statut.PAYE,
        )
        self.client.refresh_from_db()
        self.assertEqual(self.client.solde_du, Decimal("700.00"))
        self.assertEqual(
            TransactionFinanciere.objects.filter(
                client=self.client,
                type_transaction=TransactionFinanciere.Type.DECAISSEMENT,
            ).count(),
            1,
        )

        replay = self.provider_api.post(
            f"/api/finances/decaissements/{demande_id}/payer/",
            format="json",
        )
        self.assertEqual(replay.status_code, status.HTTP_409_CONFLICT)
        self.client.refresh_from_db()
        self.assertEqual(self.client.solde_du, Decimal("700.00"))

    def test_decaissement_superieur_au_solde_refuse(self):
        self._attribuer()

        response = self.client_api.post(
            "/api/finances/decaissements/",
            {"montant": "1000.01"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            DemandeDecaissement.objects.filter(client=self.client).exists()
        )

    def test_decaissement_refuse_ne_debite_pas(self):
        self._attribuer()
        creation = self.client_api.post(
            "/api/finances/decaissements/",
            {"montant": "200.00"},
            format="json",
        )
        demande_id = creation.data["id"]
        self.agent_api.post(
            f"/api/finances/decaissements/{demande_id}/confirmer/",
            format="json",
        )

        refus = self.provider_api.post(
            f"/api/finances/decaissements/{demande_id}/refuser/",
            {"motif": "Solde non justifié"},
            format="json",
        )

        self.assertEqual(refus.status_code, status.HTTP_200_OK)
        self.assertEqual(refus.data["statut"], DemandeDecaissement.Statut.REFUSE)
        self.client.refresh_from_db()
        self.assertEqual(self.client.solde_du, Decimal("1000.00"))
        self.assertFalse(
            TransactionFinanciere.objects.filter(
                client=self.client,
                type_transaction=TransactionFinanciere.Type.DECAISSEMENT,
            ).exists()
        )

    def test_annulation_par_client_restaure_le_solde_disponible(self):
        self._attribuer()
        creation = self.client_api.post(
            "/api/finances/decaissements/",
            {"montant": "400.00"},
            format="json",
        )

        annulation = self.client_api.post(
            f"/api/finances/decaissements/{creation.data['id']}/annuler/",
            format="json",
        )

        self.assertEqual(annulation.status_code, status.HTTP_200_OK)
        self.assertEqual(annulation.data["statut"], DemandeDecaissement.Statut.ANNULE)
        solde = self.client_api.get("/api/finances/solde/")
        self.assertEqual(solde.data["solde_disponible"], Decimal("1000.00"))

    def test_un_client_ne_peut_pas_attribuer_du_solde(self):
        response = self.client_api.post(
            f"/api/clients/{self.client.pk}/attribuer-solde/",
            {"montant": "100.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.client.refresh_from_db()
        self.assertEqual(self.client.solde_du, Decimal("0.00"))

    def test_fournisseur_ne_peut_pas_attribuer_au_client_d_un_autre(self):
        response = self.other_provider_api.post(
            f"/api/clients/{self.client.pk}/attribuer-solde/",
            {"montant": "100.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.client.refresh_from_db()
        self.assertEqual(self.client.solde_du, Decimal("0.00"))

    def test_statistiques_fournisseur(self):
        self._attribuer()
        self.agent_api.post(
            "/api/encaissements/collecter/",
            {"client": self.client.pk, "montant": "300.00"},
            format="json",
        )

        response = self.provider_api.get("/api/fournisseurs/statistiques/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["nombre_clients"], 1)
        self.assertEqual(response.data["nombre_agents"], 1)
        self.assertEqual(response.data["nombre_encaissements"], 1)
        self.assertEqual(response.data["montant_encaisse"], Decimal("300.00"))
        self.assertEqual(response.data["solde_total_clients"], Decimal("700.00"))

    def test_statistiques_agent(self):
        self._attribuer()
        self.agent_api.post(
            "/api/encaissements/collecter/",
            {"client": self.client.pk, "montant": "300.00"},
            format="json",
        )

        response = self.agent_api.get(
            f"/api/agents/{self.agent.pk}/statistiques/"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["nombre_clients"], 1)
        self.assertEqual(response.data["nombre_recouvrements"], 1)
        self.assertEqual(response.data["montant_collecte"], Decimal("300.00"))

    def test_regularisation_fournisseur(self):
        self._attribuer()

        response = self.provider_api.post(
            "/api/finances/transactions/regulariser/",
            {
                "client": self.client.pk,
                "montant": "50.00",
                "sens": TransactionFinanciere.Sens.CREDIT,
                "motif": "Correction",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.client.refresh_from_db()
        self.assertEqual(self.client.solde_du, Decimal("1050.00"))

    def test_creation_client_avec_credit_initial(self):
        response = self.provider_api.post(
            "/api/clients/",
            {
                "nom": "Kone",
                "prenom": "Awa",
                "telephone": "730000020",
                "adresse": "Yopougon",
                "solde_initial": "2500.00",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        client = Client.objects.get(telephone="730000020")
        self.assertFalse(client.compte_active)
        self.assertEqual(client.solde_du, Decimal("2500.00"))
        self.assertEqual(response.data["montant_credit_initial"], "2500.00")
        self.assertTrue(response.data["code_activation"])
        transaction = TransactionFinanciere.objects.get(client=client)
        self.assertEqual(
            transaction.type_transaction,
            TransactionFinanciere.Type.ATTRIBUTION_INITIALE,
        )
        self.assertEqual(transaction.sens, TransactionFinanciere.Sens.CREDIT)
        self.assertEqual(transaction.montant, Decimal("2500.00"))

        liste = self.provider_api.get("/api/clients/")
        self.assertEqual(liste.status_code, status.HTTP_200_OK)
        client_data = next(
            item for item in liste.data["results"] if item["id"] == client.pk
        )
        self.assertEqual(client_data["montant_credit_initial"], "2500.00")
        self.assertEqual(client_data["solde_du"], "2500.00")

    def test_creation_client_sans_credit_initial(self):
        response = self.provider_api.post(
            "/api/clients/",
            {
                "nom": "Kone",
                "prenom": "Awa",
                "telephone": "730000021",
                "adresse": "Yopougon",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        client = Client.objects.get(telephone="730000021")
        self.assertEqual(client.solde_du, Decimal("0.00"))
        self.assertIsNone(response.data["montant_credit_initial"])
        self.assertFalse(
            TransactionFinanciere.objects.filter(client=client).exists()
        )

    def test_attribution_initiale_avant_activation(self):
        self.client.compte_active = False
        self.client.save(update_fields=["compte_active"])

        response = self._attribuer()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.client.refresh_from_db()
        self.assertEqual(self.client.solde_du, Decimal("1000.00"))
