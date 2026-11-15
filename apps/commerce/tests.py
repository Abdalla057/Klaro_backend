from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.abonnements.models import Abonnement, PlanAbonnement
from apps.agents.models import Agent
from apps.clients.models import Client
from apps.comptes.models import Utilisateur
from apps.fournisseurs.models import Fournisseur

from .models import (
    CommandeAchat,
    ConfigurationNotification,
    DetteCommerciale,
    Facture,
    FournisseurMarchandise,
    NotificationClient,
    NotificationExterne,
    Produit,
    Vente,
)
from .notifications import dispatcher_notifications, envoyer_whatsapp


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    SMS_API_URL="",
    SMS_API_KEY="",
    WHATSAPP_TOKEN="",
    WHATSAPP_PHONE_ID="",
)
class CommerceTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.plan = PlanAbonnement.objects.create(
            code="mensuel-commerce",
            nom="Mensuel Commerce",
            prix=Decimal("10000.00"),
            devise="XOF",
            duree_jours=30,
            max_clients=100,
            max_agents=10,
            est_essai=False,
            actif=True,
        )
        self.fournisseur = self._creer_fournisseur("740000001", "Commerce Alpha")
        self.autre_fournisseur = self._creer_fournisseur(
            "740000009", "Commerce Beta"
        )
        self.fournisseur_user = self.fournisseur.utilisateur
        self.autre_fournisseur_user = self.autre_fournisseur.utilisateur

        self.client_profil = self._creer_client(
            self.fournisseur,
            "740000002",
            "Aminata",
            "Diallo",
        )
        self.client_user = self.client_profil.utilisateur
        self.autre_client_profil = self._creer_client(
            self.autre_fournisseur,
            "740000010",
            "Bintou",
            "Kone",
        )

        agent_user = Utilisateur.objects.create_user(
            telephone="740000003",
            password="MotDePasse123",
            role="agent",
            is_active=True,
            first_name="Moussa",
            last_name="Traore",
        )
        self.agent = Agent.objects.create(
            utilisateur=agent_user,
            fournisseur=self.fournisseur,
            telephone="740000003",
            zone="Centre",
            compte_active=True,
        )

        self.api_fournisseur = self._api(self.fournisseur_user)
        self.api_autre_fournisseur = self._api(self.autre_fournisseur_user)
        self.api_client = self._api(self.client_user)
        self.api_agent = self._api(agent_user)

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
            date_fin=debut + timedelta(days=30),
            statut=Abonnement.Statut.ACTIF,
            est_essai=False,
            montant=self.plan.prix,
            devise=self.plan.devise,
        )
        return fournisseur

    def _creer_client(self, fournisseur, telephone, prenom, nom):
        utilisateur = Utilisateur.objects.create_user(
            telephone=telephone,
            password="MotDePasse123",
            role="client",
            is_active=True,
            first_name=prenom,
            last_name=nom,
        )
        return Client.objects.create(
            utilisateur=utilisateur,
            fournisseur=fournisseur,
            telephone=telephone,
            adresse="Cocody",
            solde_du=Decimal("0.00"),
            compte_active=True,
        )

    def _api(self, utilisateur):
        client = APIClient()
        client.force_authenticate(user=utilisateur)
        return client

    def _creer_produit(
        self,
        api=None,
        nom="Riz parfumé",
        prix_vente="75000.00",
        prix_achat="60000.00",
        stock="20.000",
    ):
        api = api or self.api_fournisseur
        reponse = api.post(
            "/api/commerce/produits/",
            {
                "nom": nom,
                "unite_base": "sac",
                "prix_achat": prix_achat,
                "prix_vente": prix_vente,
                "seuil_alerte": "2.000",
            },
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED, reponse.data)
        produit = reponse.data
        if stock is not None:
            reponse = api.post(
                f"/api/commerce/produits/{produit['id']}/entree-stock/",
                {"quantite": stock, "motif": "Stock initial"},
                format="json",
            )
            self.assertEqual(
                reponse.status_code,
                status.HTTP_201_CREATED,
                reponse.data,
            )
        return api.get(f"/api/commerce/produits/{produit['id']}/").data

    def _vendre(
        self,
        produit,
        quantite="1.000",
        montant_paye="75000.00",
        mode="ESPECES",
        client=None,
        api=None,
        cle_idempotence=None,
    ):
        api = api or self.api_fournisseur
        payload = {
            "client": (client or self.client_profil).pk,
            "lignes": [
                {
                    "produit": produit["id"],
                    "unite": produit["unites"][0]["id"],
                    "quantite": quantite,
                }
            ],
            "montant_paye": montant_paye,
            "mode_paiement": mode,
        }
        if cle_idempotence:
            payload["cle_idempotence"] = cle_idempotence
        return api.post("/api/commerce/ventes/", payload, format="json")

    def test_paiement_complet(self):
        produit = self._creer_produit()
        reponse = self._vendre(produit, montant_paye="75000.00")
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED, reponse.data)
        vente = Vente.objects.get(pk=reponse.data["id"])
        self.assertEqual(vente.montant_total, Decimal("75000.00"))
        self.assertEqual(vente.montant_paye, Decimal("75000.00"))
        self.assertEqual(vente.reste_a_payer, Decimal("0.00"))
        self.assertFalse(
            DetteCommerciale.objects.filter(vente=vente).exists()
        )
        self.assertEqual(vente.paiements.count(), 1)

        produit_db = Produit.objects.get(pk=produit["id"])
        self.assertEqual(produit_db.stock_actuel, Decimal("19.000"))

        facture = Facture.objects.get(vente=vente)
        annee = timezone.localdate(vente.date_creation).year
        self.assertEqual(facture.numero, f"FAC-{annee}-{vente.pk:06d}")
        self.assertEqual(vente.numero_facture, facture.numero)
        self.assertEqual(len(facture.lignes), 1)
        self.assertEqual(facture.lignes[0]["nom_produit"], "Riz parfumé")
        self.assertEqual(
            NotificationClient.objects.filter(vente=vente).count(),
            1,
        )

        pdf = self.api_fournisseur.get(
            f"/api/commerce/factures/{facture.pk}/pdf/"
        )
        self.assertEqual(pdf.status_code, status.HTTP_200_OK)
        self.assertEqual(pdf["Content-Type"], "application/pdf")

        token = self._token_facture(facture)
        public = self.api_client.get(
            f"/api/commerce/factures/lien/{token}/pdf/"
        )
        self.assertEqual(public.status_code, status.HTTP_200_OK)

    def _token_facture(self, facture):
        from .notifications import generer_token_facture

        return generer_token_facture(facture)

    def test_paiement_partiel_75_50_25(self):
        produit = self._creer_produit(prix_vente="150000.00")
        reponse = self._vendre(
            produit,
            montant_paye="75000.00",
            mode="WAVE",
        )
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED, reponse.data)
        vente = Vente.objects.get(pk=reponse.data["id"])
        self.assertEqual(vente.reste_a_payer, Decimal("75000.00"))
        self.assertEqual(vente.mode_paiement, Vente.ModePaiement.WAVE)
        dette = DetteCommerciale.objects.get(vente=vente)
        self.assertEqual(dette.statut, DetteCommerciale.Statut.PARTIELLE)
        self.assertEqual(dette.montant_restant, Decimal("75000.00"))

        for montant, reste_attendu, statut_attendu in (
            ("50000.00", "25000.00", DetteCommerciale.Statut.PARTIELLE),
            ("25000.00", "0.00", DetteCommerciale.Statut.SOLDEE),
        ):
            paiement = self.api_fournisseur.post(
                f"/api/commerce/ventes/{vente.pk}/paiements/",
                {"montant": montant, "mode_paiement": "ESPECES"},
                format="json",
            )
            self.assertEqual(
                paiement.status_code,
                status.HTTP_201_CREATED,
                paiement.data,
            )
            vente.refresh_from_db()
            dette.refresh_from_db()
            self.assertEqual(vente.reste_a_payer, Decimal(reste_attendu))
            self.assertEqual(dette.montant_restant, Decimal(reste_attendu))
            self.assertEqual(dette.statut, statut_attendu)

        self.assertEqual(vente.montant_paye, Decimal("150000.00"))
        self.assertEqual(vente.paiements.count(), 3)
        self.assertEqual(
            NotificationClient.objects.filter(vente=vente).count(),
            3,
        )

        trop = self.api_fournisseur.post(
            f"/api/commerce/ventes/{vente.pk}/paiements/",
            {"montant": "100.00", "mode_paiement": "ESPECES"},
            format="json",
        )
        self.assertEqual(trop.status_code, status.HTTP_400_BAD_REQUEST)

    def test_vente_a_credit(self):
        produit = self._creer_produit()
        reponse = self._vendre(produit, montant_paye="0.00", mode="CREDIT")
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED, reponse.data)
        vente = Vente.objects.get(pk=reponse.data["id"])
        self.assertEqual(vente.mode_paiement, Vente.ModePaiement.CREDIT)
        self.assertEqual(vente.montant_paye, Decimal("0.00"))
        self.assertEqual(vente.reste_a_payer, Decimal("75000.00"))
        self.assertEqual(vente.paiements.count(), 0)
        dette = DetteCommerciale.objects.get(vente=vente)
        self.assertEqual(dette.statut, DetteCommerciale.Statut.OUVERTE)
        self.assertEqual(dette.montant_initial, Decimal("75000.00"))
        facture = Facture.objects.get(vente=vente)
        self.assertEqual(facture.montant_paye, Decimal("0.00"))
        self.assertEqual(facture.reste_a_payer, Decimal("75000.00"))

    def test_echec_notification_externe_sans_bloquer_la_vente(self):
        ConfigurationNotification.objects.create(
            fournisseur=self.fournisseur,
            sms_actif=True,
            whatsapp_actif=False,
        )
        produit = self._creer_produit()
        reponse = self._vendre(produit, montant_paye="75000.00")
        self.assertEqual(reponse.status_code, status.HTTP_201_CREATED, reponse.data)
        vente = Vente.objects.get(pk=reponse.data["id"])
        self.assertEqual(vente.montant_total, Decimal("75000.00"))
        envoi = NotificationExterne.objects.get(
            notification__vente=vente,
            canal=NotificationExterne.Canal.SMS,
        )
        self.assertEqual(envoi.statut, NotificationExterne.Statut.EN_ATTENTE)
        dispatcher_notifications(envoi.notification_id)
        envoi.refresh_from_db()
        self.assertEqual(envoi.statut, NotificationExterne.Statut.ECHOUE)
        self.assertIn("pas configuré", envoi.derniere_erreur)
        self.assertEqual(envoi.tentatives, 1)

    def test_isolation_des_donnees_par_fournisseur(self):
        produit_a = self._creer_produit()
        vente_a = self._vendre(produit_a, montant_paye="75000.00").data
        facture_a = Facture.objects.get(pk=vente_a["facture_id"])

        produit_b = self._creer_produit(
            api=self.api_autre_fournisseur,
            nom="Huile",
            prix_vente="10000.00",
        )

        produits_b = self.api_autre_fournisseur.get("/api/commerce/produits/")
        self.assertEqual(produits_b.status_code, status.HTTP_200_OK)
        ids_b = [item["id"] for item in produits_b.data["results"]]
        self.assertNotIn(produit_a["id"], ids_b)
        self.assertIn(produit_b["id"], ids_b)

        ventes_b = self.api_autre_fournisseur.get("/api/commerce/ventes/")
        self.assertEqual(ventes_b.status_code, status.HTTP_200_OK)
        self.assertEqual(ventes_b.data["count"], 0)

        detail_autre = self.api_autre_fournisseur.get(
            f"/api/commerce/produits/{produit_a['id']}/"
        )
        self.assertEqual(detail_autre.status_code, status.HTTP_404_NOT_FOUND)

        facture_autre = self.api_autre_fournisseur.get(
            f"/api/commerce/factures/{facture_a.pk}/"
        )
        self.assertEqual(facture_autre.status_code, status.HTTP_404_NOT_FOUND)

        facture_client = self.api_client.get(
            f"/api/commerce/factures/{facture_a.pk}/"
        )
        self.assertEqual(facture_client.status_code, status.HTTP_200_OK)

        client_autre = self._api(self.autre_client_profil.utilisateur)
        facture_client_autre = client_autre.get(
            f"/api/commerce/factures/{facture_a.pk}/"
        )
        self.assertEqual(
            facture_client_autre.status_code,
            status.HTTP_404_NOT_FOUND,
        )

        vente_croisee = self._vendre(
            produit_b,
            montant_paye="10000.00",
            client=self.client_profil,
            api=self.api_autre_fournisseur,
        )
        self.assertEqual(
            vente_croisee.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_agent_ne_peut_pas_acceder_au_module_commerce(self):
        for url in (
            "/api/commerce/produits/",
            "/api/commerce/ventes/",
            "/api/commerce/factures/",
            "/api/commerce/notifications/",
            "/api/commerce/statistiques/",
            "/api/commerce/admin/statistiques/",
        ):
            reponse = self.api_agent.get(url)
            self.assertIn(
                reponse.status_code,
                (status.HTTP_403_FORBIDDEN, status.HTTP_401_UNAUTHORIZED),
                f"{url} a renvoyé {reponse.status_code}",
            )

        agents = self.api_agent.get("/api/agents/")
        self.assertEqual(agents.status_code, status.HTTP_200_OK)

    def test_idempotence_de_la_vente(self):
        produit = self._creer_produit()
        premiere = self._vendre(
            produit,
            montant_paye="75000.00",
            cle_idempotence="vente-unique-1",
        )
        self.assertEqual(
            premiere.status_code,
            status.HTTP_201_CREATED,
            premiere.data,
        )
        seconde = self._vendre(
            produit,
            montant_paye="75000.00",
            cle_idempotence="vente-unique-1",
        )
        self.assertEqual(
            seconde.status_code,
            status.HTTP_201_CREATED,
            seconde.data,
        )
        self.assertEqual(premiere.data["id"], seconde.data["id"])
        self.assertEqual(Vente.objects.count(), 1)
        produit_db = Produit.objects.get(pk=produit["id"])
        self.assertEqual(produit_db.stock_actuel, Decimal("19.000"))

    def _creer_commande_achat(
        self,
        produit,
        quantite="10.000",
        prix_achat_unitaire="100.00",
        statut="COMMANDEE",
    ):
        fournisseur_marchandise = FournisseurMarchandise.objects.create(
            fournisseur=self.fournisseur,
            nom="Grossiste Central",
        )
        reponse = self.api_fournisseur.post(
            "/api/commerce/commandes-achat/",
            {
                "fournisseur_marchandise": fournisseur_marchandise.pk,
                "statut": statut,
                "lignes": [
                    {
                        "produit": produit["id"],
                        "unite": produit["unites"][0]["id"],
                        "quantite": quantite,
                        "prix_achat_unitaire": prix_achat_unitaire,
                    }
                ],
            },
            format="json",
        )
        return reponse

    def test_whatsapp_utilise_le_format_meta_cloud(self):
        with override_settings(
            WHATSAPP_API_URL="https://graph.example.com/v20.0/",
            WHATSAPP_TOKEN="jeton",
            WHATSAPP_PHONE_ID="12345",
        ):
            with patch(
                "apps.commerce.notifications._poster_json",
                return_value={"messages": [{"id": "wamid.1"}]},
            ) as poster:
                identifiant = envoyer_whatsapp("2250700000000", "Bonjour")
        self.assertEqual(identifiant, "wamid.1")
        self.assertEqual(
            poster.call_args.args[0],
            "https://graph.example.com/v20.0/12345/messages",
        )

    def test_creation_commande_statut_recue_refusee(self):
        produit = self._creer_produit(stock=None)
        reponse = self._creer_commande_achat(produit, statut="RECUE")
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_annulation_commande_partiellement_recue_refusee(self):
        produit = self._creer_produit(stock=None)
        commande = self._creer_commande_achat(produit)
        self.assertEqual(
            commande.status_code,
            status.HTTP_201_CREATED,
            commande.data,
        )
        ligne = commande.data["lignes"][0]
        reception = self.api_fournisseur.post(
            f"/api/commerce/commandes-achat/{commande.data['id']}/receptionner/",
            {"lignes": [{"ligne_commande": ligne["id"], "quantite": "5.000"}]},
            format="json",
        )
        self.assertEqual(
            reception.status_code,
            status.HTTP_201_CREATED,
            reception.data,
        )
        annulation = self.api_fournisseur.post(
            f"/api/commerce/commandes-achat/{commande.data['id']}/annuler/"
        )
        self.assertEqual(annulation.status_code, status.HTTP_400_BAD_REQUEST)

    def test_suppression_produit_lie_renvoie_409(self):
        produit = self._creer_produit()
        self._vendre(produit, montant_paye="75000.00")
        suppression = self.api_fournisseur.delete(
            f"/api/commerce/produits/{produit['id']}/"
        )
        self.assertEqual(suppression.status_code, status.HTTP_409_CONFLICT)

    def test_facture_immuable_apres_emission(self):
        produit = self._creer_produit()
        vente = self._vendre(produit, montant_paye="75000.00").data
        facture = Facture.objects.get(pk=vente["facture_id"])
        facture.montant_paye = Decimal("0.00")
        with self.assertRaises(ValidationError):
            facture.save()

    def test_paiement_avec_mode_virtuel_refuse(self):
        produit = self._creer_produit()
        vente = self._vendre(
            produit,
            montant_paye="0.00",
            mode="CREDIT",
        ).data
        paiement = self.api_fournisseur.post(
            f"/api/commerce/ventes/{vente['id']}/paiements/",
            {"montant": "1000.00", "mode_paiement": "PARTIEL"},
            format="json",
        )
        self.assertEqual(paiement.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cump_apres_receptions(self):
        produit = self._creer_produit(
            prix_achat="0.00",
            stock=None,
        )
        premiere = self._creer_commande_achat(
            produit,
            prix_achat_unitaire="100.00",
        )
        self.assertEqual(
            premiere.status_code,
            status.HTTP_201_CREATED,
            premiere.data,
        )
        self.api_fournisseur.post(
            f"/api/commerce/commandes-achat/{premiere.data['id']}/receptionner/",
            {
                "lignes": [
                    {
                        "ligne_commande": premiere.data["lignes"][0]["id"],
                        "quantite": "10.000",
                    }
                ]
            },
            format="json",
        )
        produit_db = Produit.objects.get(pk=produit["id"])
        self.assertEqual(produit_db.prix_achat, Decimal("100.00"))

        seconde = self._creer_commande_achat(
            produit,
            prix_achat_unitaire="200.00",
        )
        self.api_fournisseur.post(
            f"/api/commerce/commandes-achat/{seconde.data['id']}/receptionner/",
            {
                "lignes": [
                    {
                        "ligne_commande": seconde.data["lignes"][0]["id"],
                        "quantite": "10.000",
                    }
                ]
            },
            format="json",
        )
        produit_db.refresh_from_db()
        self.assertEqual(produit_db.prix_achat, Decimal("150.00"))
        self.assertEqual(produit_db.stock_actuel, Decimal("20.000"))

    def test_annulation_vente_credit_restaure_le_stock(self):
        produit = self._creer_produit()
        vente = self._vendre(
            produit,
            quantite="2.000",
            montant_paye="0.00",
            mode="CREDIT",
        ).data
        produit_db = Produit.objects.get(pk=produit["id"])
        self.assertEqual(produit_db.stock_actuel, Decimal("18.000"))

        annulation = self.api_fournisseur.post(
            f"/api/commerce/ventes/{vente['id']}/annuler/",
            {"motif": "Erreur de saisie"},
            format="json",
        )
        self.assertEqual(
            annulation.status_code,
            status.HTTP_200_OK,
            annulation.data,
        )
        produit_db.refresh_from_db()
        vente_db = Vente.objects.get(pk=vente["id"])
        dette = DetteCommerciale.objects.get(vente=vente_db)
        self.assertEqual(produit_db.stock_actuel, Decimal("20.000"))
        self.assertEqual(vente_db.statut, Vente.Statut.ANNULEE)
        self.assertEqual(dette.statut, DetteCommerciale.Statut.ANNULEE)
        self.assertTrue(
            NotificationClient.objects.filter(
                vente=vente_db,
                type_notification=NotificationClient.Type.VENTE_ANNULEE,
            ).exists()
        )

    def test_annulation_vente_partiellement_payee_refusee(self):
        produit = self._creer_produit()
        vente = self._vendre(produit, montant_paye="75000.00").data
        annulation = self.api_fournisseur.post(
            f"/api/commerce/ventes/{vente['id']}/annuler/"
        )
        self.assertEqual(annulation.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unite_base_avec_facteur_non_un_refusee(self):
        reponse = self.api_fournisseur.post(
            "/api/commerce/produits/",
            {
                "nom": "Sucre",
                "unite_base": "sac",
                "prix_achat": "1000.00",
                "prix_vente": "1200.00",
                "unites_ecriture": [
                    {
                        "nom": "sac",
                        "facteur_conversion": "2.000",
                        "est_base": True,
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(reponse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_devise_de_la_facture_est_un_snapshot(self):
        produit = self._creer_produit()
        vente = self._vendre(produit, montant_paye="75000.00").data
        facture = Facture.objects.get(pk=vente["facture_id"])
        self.assertEqual(facture.devise, "XOF")

        Abonnement.objects.filter(fournisseur=self.fournisseur).update(
            devise="USD"
        )
        seconde = self._vendre(produit, montant_paye="75000.00").data
        facture_seconde = Facture.objects.get(pk=seconde["facture_id"])
        self.assertEqual(facture_seconde.devise, "USD")

    def test_client_accede_a_ses_factures_et_notifications(self):
        produit = self._creer_produit()
        vente = self._vendre(produit, montant_paye="75000.00").data

        factures = self.api_client.get("/api/commerce/factures/")
        self.assertEqual(factures.status_code, status.HTTP_200_OK)
        self.assertEqual(factures.data["count"], 1)

        notifications = self.api_client.get("/api/commerce/notifications/")
        self.assertEqual(notifications.status_code, status.HTTP_200_OK)
        self.assertEqual(notifications.data["count"], 1)
        notification_id = notifications.data["results"][0]["id"]

        non_lues = self.api_client.get(
            "/api/commerce/notifications/non-lues/"
        )
        self.assertEqual(non_lues.data["nombre"], 1)

        marquee = self.api_client.post(
            f"/api/commerce/notifications/{notification_id}/marquer-lue/"
        )
        self.assertEqual(marquee.status_code, status.HTTP_200_OK)
        self.assertTrue(marquee.data["lue"])

        notification = NotificationClient.objects.get(pk=notification_id)
        self.assertEqual(notification.vente_id, vente["id"])

        interdits = self.api_client.get("/api/commerce/produits/")
        self.assertEqual(interdits.status_code, status.HTTP_403_FORBIDDEN)
