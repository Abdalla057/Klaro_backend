import hashlib
import hmac
import json
import time
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from apps.comptes.models import Utilisateur
from apps.fournisseurs.models import Fournisseur

from .demandes import initier_paiement_en_ligne
from .exceptions import PrestataireNonConfigure
from .models import (
    Abonnement,
    DemandeAbonnement,
    EvenementAbonnement,
    PaiementAbonnement,
    PlanAbonnement,
    WebhookEvenement,
)
from .tarifs import calculer_montant_plan


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class DemandeAbonnementTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.plan = PlanAbonnement.objects.create(
            code="premium-test",
            nom="Premium test",
            prix=Decimal("10000.00"),
            devise="XOF",
            duree_jours=30,
            max_clients=500,
            max_agents=5,
            est_essai=False,
            actif=True,
        )
        self.fournisseur = self._creer_fournisseur("710000001", "Alpha")
        self.autre = self._creer_fournisseur("710000002", "Beta")
        self.admin = Utilisateur.objects.create_user(
            telephone="710000099",
            password="MotDePasse123",
            role="super_admin",
        )

    def _creer_fournisseur(self, telephone, nom):
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

    def _creer_demande(self, fournisseur=None, cle="demande-001", **extra):
        fournisseur = fournisseur or self.fournisseur
        self.client.force_authenticate(user=fournisseur.utilisateur)
        donnees = {
            "plan": self.plan.pk,
            "cle_idempotence": cle,
            "moyen_paiement": "MOBILE_MONEY",
            **extra,
        }
        return self.client.post(
            "/api/abonnements/demandes/",
            donnees,
            format="json",
        )

    def _soumettre(self, demande, reference="TX-MANUEL-001"):
        return self.client.post(
            f"/api/abonnements/demandes/{demande.pk}/soumettre-paiement/",
            {"reference_transaction": reference},
            format="json",
        )

    def _confirmer(self, demande, reference_transaction="TX-ADMIN-001", **extra):
        self.client.force_authenticate(user=self.admin)
        return self.client.post(
            f"/api/super-admin/demandes-abonnement/{demande.pk}/confirmer/",
            {"reference_transaction": reference_transaction, **extra},
            format="json",
        )

    def test_creation_calcule_le_montant_cote_serveur(self):
        response = self._creer_demande(montant="1.00", prix="1.00")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Decimal(str(response.data["montant"])), self.plan.prix)
        self.assertEqual(response.data["statut"], "EN_ATTENTE")
        self.assertTrue(response.data["reference"].startswith("KLARO-DEM-"))

    def test_cle_idempotence_evite_les_doublons(self):
        premiere = self._creer_demande()
        deuxieme = self._creer_demande()

        self.assertEqual(premiere.status_code, status.HTTP_201_CREATED)
        self.assertEqual(deuxieme.status_code, status.HTTP_201_CREATED)
        self.assertEqual(premiere.data["id"], deuxieme.data["id"])
        self.assertEqual(
            DemandeAbonnement.objects.filter(
                fournisseur=self.fournisseur
            ).count(),
            1,
        )

    def test_client_et_agent_recoivent_403(self):
        for role in ("client", "agent"):
            utilisateur = Utilisateur.objects.create_user(
                telephone=f"71000010{role}",
                password="MotDePasse123",
                role=role,
            )
            self.client.force_authenticate(user=utilisateur)
            response = self.client.get("/api/abonnements/demandes/")
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_isolation_entre_fournisseurs(self):
        premiere = self._creer_demande()
        demande_id = premiere.data["id"]

        self.client.force_authenticate(user=self.autre.utilisateur)
        response = self.client.get(
            f"/api/abonnements/demandes/{demande_id}/"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_confirmation_sans_preuve_ni_reference_est_refusee(self):
        creation = self._creer_demande()
        demande = DemandeAbonnement.objects.get(pk=creation.data["id"])

        response = self._confirmer(demande, reference_transaction="")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeAbonnement.Statut.EN_ATTENTE)
        self.assertFalse(
            Abonnement.objects.filter(fournisseur=self.fournisseur).exists()
        )
        self.assertFalse(
            PaiementAbonnement.objects.filter(
                demande_abonnement=demande
            ).exists()
        )

    def test_confirmation_admin_active_abonnement_et_paiement(self):
        creation = self._creer_demande()
        demande = DemandeAbonnement.objects.get(pk=creation.data["id"])
        self._soumettre(demande)
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeAbonnement.Statut.EN_VERIFICATION)

        response = self._confirmer(demande)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeAbonnement.Statut.CONFIRMEE)
        self.assertIsNotNone(demande.abonnement_genere_id)
        abonnement = Abonnement.objects.get(pk=demande.abonnement_genere_id)
        self.assertEqual(abonnement.statut, Abonnement.Statut.ACTIF)
        paiement = PaiementAbonnement.objects.get(demande_abonnement=demande)
        self.assertEqual(paiement.statut, PaiementAbonnement.Statut.CONFIRME)
        self.assertEqual(paiement.montant, self.plan.prix)
        self.assertTrue(
            EvenementAbonnement.objects.filter(
                demande_abonnement=demande,
                type_evenement=EvenementAbonnement.Type.PAIEMENT_CONFIRME,
            ).exists()
        )

    def test_double_confirmation_ne_cree_qu_un_abonnement(self):
        creation = self._creer_demande()
        demande = DemandeAbonnement.objects.get(pk=creation.data["id"])
        self._soumettre(demande)

        self._confirmer(demande)
        self._confirmer(demande)

        self.assertEqual(
            Abonnement.objects.filter(fournisseur=self.fournisseur).count(),
            1,
        )
        self.assertEqual(
            PaiementAbonnement.objects.filter(
                demande_abonnement=demande,
                statut=PaiementAbonnement.Statut.CONFIRME,
            ).count(),
            1,
        )

    def test_refus_ne_cree_aucun_abonnement(self):
        creation = self._creer_demande()
        demande = DemandeAbonnement.objects.get(pk=creation.data["id"])
        self._soumettre(demande)

        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            f"/api/super-admin/demandes-abonnement/{demande.pk}/refuser/",
            {"motif": "Paiement introuvable."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeAbonnement.Statut.REFUSEE)
        self.assertFalse(
            Abonnement.objects.filter(fournisseur=self.fournisseur).exists()
        )

    def test_demande_confirmee_ne_peut_plus_etre_annulee(self):
        creation = self._creer_demande()
        demande = DemandeAbonnement.objects.get(pk=creation.data["id"])
        self._soumettre(demande)
        self._confirmer(demande)

        self.client.force_authenticate(user=self.fournisseur.utilisateur)
        response = self.client.post(
            f"/api/abonnements/demandes/{demande.pk}/annuler/",
            {"motif": "Trop tard."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_deux_fournisseurs_avec_le_meme_montant_restent_independants(self):
        premiere = self._creer_demande(self.fournisseur, cle="a-001")
        seconde = self._creer_demande(self.autre, cle="b-001")
        demande_a = DemandeAbonnement.objects.get(pk=premiere.data["id"])
        demande_b = DemandeAbonnement.objects.get(pk=seconde.data["id"])
        self.assertEqual(demande_a.montant, demande_b.montant)

        self.client.force_authenticate(user=self.fournisseur.utilisateur)
        self._soumettre(demande_a, reference="TX-A")
        self.client.force_authenticate(user=self.autre.utilisateur)
        self._soumettre(demande_b, reference="TX-B")

        confirmation_a = self._confirmer(
            demande_a,
            reference_transaction="TX-ADMIN-A",
        )
        confirmation_b = self._confirmer(
            demande_b,
            reference_transaction="TX-ADMIN-B",
        )
        self.assertEqual(
            confirmation_a.status_code,
            status.HTTP_200_OK,
            confirmation_a.data,
        )
        self.assertEqual(
            confirmation_b.status_code,
            status.HTTP_200_OK,
            confirmation_b.data,
        )

        self.assertEqual(
            Abonnement.objects.filter(
                statut=Abonnement.Statut.ACTIF
            ).count(),
            2,
        )
        self.assertEqual(
            PaiementAbonnement.objects.filter(
                statut=PaiementAbonnement.Statut.CONFIRME
            ).count(),
            2,
        )
        self.assertNotEqual(
            Abonnement.objects.get(fournisseur=self.fournisseur).pk,
            Abonnement.objects.get(fournisseur=self.autre).pk,
        )

    def test_expiration_commande_sans_activation(self):
        creation = self._creer_demande()
        demande = DemandeAbonnement.objects.get(pk=creation.data["id"])
        DemandeAbonnement.objects.filter(pk=demande.pk).update(
            date_expiration=timezone.now() - timedelta(minutes=1)
        )

        call_command("expirer_demandes_abonnement")
        demande.refresh_from_db()

        self.assertEqual(demande.statut, DemandeAbonnement.Statut.EXPIREE)
        self.assertFalse(
            Abonnement.objects.filter(fournisseur=self.fournisseur).exists()
        )

    def test_renouvellement_prolonge_l_abonnement_actif(self):
        maintenant = timezone.now()
        abonnement = Abonnement.objects.create(
            fournisseur=self.fournisseur,
            plan=self.plan,
            date_debut=maintenant,
            date_fin=maintenant + timedelta(days=10),
            statut=Abonnement.Statut.ACTIF,
            est_essai=False,
            montant=self.plan.prix,
            devise=self.plan.devise,
        )
        ancienne_fin = abonnement.date_fin

        creation = self._creer_demande(
            cle="renouv-001",
            type_operation="RENOUVELLEMENT",
            abonnement_cible=abonnement.pk,
        )
        demande = DemandeAbonnement.objects.get(pk=creation.data["id"])
        self._soumettre(demande, reference="TX-RENOUV")
        self._confirmer(demande)

        abonnement.refresh_from_db()
        self.assertEqual(
            abonnement.date_fin,
            ancienne_fin + timedelta(days=self.plan.duree_jours),
        )
        self.assertEqual(
            Abonnement.objects.filter(fournisseur=self.fournisseur).count(),
            1,
        )


class SeedPlansTests(APITestCase):
    def test_seed_plans_est_idempotent(self):
        call_command("seed_plans")
        premier_total = PlanAbonnement.objects.filter(
            code__in=["simple", "premium", "pro"]
        ).count()
        prix = PlanAbonnement.objects.get(code="simple").prix

        call_command("seed_plans")

        self.assertEqual(premier_total, 3)
        self.assertEqual(
            PlanAbonnement.objects.filter(
                code__in=["simple", "premium", "pro"]
            ).count(),
            3,
        )
        self.assertEqual(PlanAbonnement.objects.get(code="simple").prix, prix)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    ABONNEMENT_PAIEMENT_EN_LIGNE_ACTIF=True,
    PAIEMENT_PRESTATAIRES={
        "wave": {
            "SN": {
                "api_key": "api-key-wave",
                "webhook_secret": "secret-wave",
            }
        }
    },
)
class WebhookPaiementTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.plan = PlanAbonnement.objects.create(
            code="webhook-plan",
            nom="Webhook plan",
            prix=Decimal("5000.00"),
            devise="XOF",
            duree_jours=30,
            max_clients=100,
            max_agents=2,
            est_essai=False,
            actif=True,
        )
        utilisateur = Utilisateur.objects.create_user(
            telephone="720000001",
            password="MotDePasse123",
            role="fournisseur",
        )
        self.fournisseur = Fournisseur.objects.create(
            utilisateur=utilisateur,
            nom_entreprise="Webhook SARL",
            telephone="720000001",
        )
        self.demande = DemandeAbonnement.objects.create(
            fournisseur=self.fournisseur,
            plan=self.plan,
            type_operation=DemandeAbonnement.TypeOperation.SOUSCRIPTION,
            montant=self.plan.prix,
            devise=self.plan.devise,
            statut=DemandeAbonnement.Statut.EN_ATTENTE,
            cle_idempotence="webhook-001",
            date_expiration=timezone.now() + timedelta(hours=24),
        )

    def _payload(self, event_id="evt-001", montant="5000.00"):
        return {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {
                "id": "cos-wave-001",
                "transaction_id": "tx-wave-001",
                "client_reference": str(self.demande.reference),
                "payment_status": "succeeded",
                "amount": montant,
                "currency": "XOF",
            },
        }

    def _poster(self, payload, secret="secret-wave"):
        brut = json.dumps(payload).encode("utf-8")
        horodatage = str(int(time.time()))
        signature = hmac.new(
            secret.encode("utf-8"),
            f"{horodatage}.".encode("utf-8") + brut,
            hashlib.sha256,
        ).hexdigest()
        return self.client.post(
            "/api/paiements/webhooks/wave/SN/",
            data=brut,
            content_type="application/json",
            HTTP_WAVE_SIGNATURE=f"t={horodatage},v1={signature}",
        )

    def test_webhook_confirme_la_demande(self):
        response = self._poster(self._payload())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["statut"], "TRAITE")
        self.demande.refresh_from_db()
        self.assertEqual(self.demande.statut, DemandeAbonnement.Statut.CONFIRMEE)
        self.assertEqual(
            PaiementAbonnement.objects.filter(
                demande_abonnement=self.demande,
                statut=PaiementAbonnement.Statut.CONFIRME,
            ).count(),
            1,
        )

    def test_webhook_rejoue_ne_produit_aucun_effet_supplementaire(self):
        self._poster(self._payload())
        response = self._poster(self._payload())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["rejoue"])
        self.assertEqual(
            PaiementAbonnement.objects.filter(
                demande_abonnement=self.demande,
                statut=PaiementAbonnement.Statut.CONFIRME,
            ).count(),
            1,
        )
        self.assertEqual(
            Abonnement.objects.filter(fournisseur=self.fournisseur).count(),
            1,
        )

    def test_signature_invalide_est_rejetee(self):
        response = self._poster(self._payload(), secret="mauvais-secret")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.demande.refresh_from_db()
        self.assertEqual(self.demande.statut, DemandeAbonnement.Statut.EN_ATTENTE)
        self.assertTrue(
            WebhookEvenement.objects.filter(
                prestataire="wave",
                signature_verifiee=False,
            ).exists()
        )

    def test_webhook_en_erreur_peut_etre_rejoue(self):
        premiere = self._poster(self._payload(), secret="mauvais-secret")
        self.assertEqual(premiere.status_code, status.HTTP_403_FORBIDDEN)

        response = self._poster(self._payload())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["rejoue"])
        self.assertEqual(response.data["statut"], "TRAITE")
        self.demande.refresh_from_db()
        self.assertEqual(self.demande.statut, DemandeAbonnement.Statut.CONFIRMEE)
        evenement = WebhookEvenement.objects.get(
            prestataire="wave",
            provider_event_id="evt-001",
        )
        self.assertEqual(
            evenement.statut_traitement,
            WebhookEvenement.StatutTraitement.TRAITE,
        )
        self.assertEqual(evenement.nombre_tentatives, 2)

    def test_montant_incoherent_place_le_webhook_en_anomalie(self):
        response = self._poster(self._payload(montant="1.00"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["statut"], "ANOMALIE")
        self.demande.refresh_from_db()
        self.assertEqual(self.demande.statut, DemandeAbonnement.Statut.EN_ATTENTE)
        self.assertFalse(
            Abonnement.objects.filter(fournisseur=self.fournisseur).exists()
        )

    def test_pays_webhook_different_place_en_anomalie(self):
        self.demande.pays = "GN"
        self.demande.save(update_fields=["pays"])

        response = self._poster(self._payload(event_id="evt-pays"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["statut"], "ANOMALIE")
        self.demande.refresh_from_db()
        self.assertEqual(self.demande.statut, DemandeAbonnement.Statut.EN_ATTENTE)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    ABONNEMENT_PAIEMENT_EN_LIGNE_ACTIF=True,
    ABONNEMENT_PAIEMENT_PAYS_ACTIFS=["SN"],
    PAIEMENT_PRESTATAIRES={
        "wave": {
            "SN": {
                "api_key": "api-key-wave",
                "webhook_secret": "secret-wave",
            }
        }
    },
)
class InitiationPaiementTests(TestCase):
    def setUp(self):
        self.plan = PlanAbonnement.objects.create(
            code="initiation-plan",
            nom="Initiation plan",
            prix=Decimal("5000.00"),
            devise="XOF",
            duree_jours=30,
            max_clients=100,
            max_agents=2,
            est_essai=False,
            actif=True,
        )
        utilisateur = Utilisateur.objects.create_user(
            telephone="720000002",
            password="MotDePasse123",
            role="fournisseur",
        )
        self.fournisseur = Fournisseur.objects.create(
            utilisateur=utilisateur,
            nom_entreprise="Initiation SARL",
            telephone="720000002",
            pays="SN",
        )
        self.demande = DemandeAbonnement.objects.create(
            fournisseur=self.fournisseur,
            plan=self.plan,
            pays="SN",
            type_operation=DemandeAbonnement.TypeOperation.SOUSCRIPTION,
            montant=self.plan.prix,
            montant_reference=self.plan.prix,
            devise=self.plan.devise,
            statut=DemandeAbonnement.Statut.EN_ATTENTE,
            cle_idempotence="initiation-001",
            date_expiration=timezone.now() + timedelta(hours=24),
        )

    def _reponse_wave(self):
        reponse = Mock(status_code=201)
        reponse.json.return_value = {
            "id": "cos-wave-init",
            "wave_launch_url": "https://pay.wave.com/c/cos-wave-init",
            "payment_status": "processing",
            "client_reference": str(self.demande.reference),
        }
        return reponse

    @patch("apps.abonnements.paiements_en_ligne.httpx.Client")
    def test_initie_une_session_wave(self, client_cls):
        client_cls.return_value.__enter__.return_value.post.return_value = (
            self._reponse_wave()
        )

        paiement, resultat = initier_paiement_en_ligne(self.demande, "wave")

        self.assertEqual(paiement.provider_session_id, "cos-wave-init")
        self.assertEqual(
            paiement.provider_checkout_url,
            "https://pay.wave.com/c/cos-wave-init",
        )
        self.assertEqual(paiement.provider_statut, "processing")
        self.assertEqual(paiement.pays, "SN")
        self.assertEqual(
            resultat["paiement_url"],
            "https://pay.wave.com/c/cos-wave-init",
        )
        appel = client_cls.return_value.__enter__.return_value.post
        url = appel.call_args.args[0]
        payload = appel.call_args.kwargs["json"]
        self.assertTrue(url.endswith("/v1/checkout/sessions"))
        self.assertEqual(payload["amount"], "5000")
        self.assertEqual(payload["currency"], "XOF")
        self.assertEqual(payload["client_reference"], self.demande.reference)
        self.assertIn("/fournisseur/abonnement", payload["success_url"])

    @patch("apps.abonnements.paiements_en_ligne.httpx.Client")
    def test_rejoue_la_session_existante_sans_nouvel_appel(self, client_cls):
        client_cls.return_value.__enter__.return_value.post.return_value = (
            self._reponse_wave()
        )
        initier_paiement_en_ligne(self.demande, "wave")

        paiement, resultat = initier_paiement_en_ligne(self.demande, "wave")

        self.assertTrue(resultat["rejoue"])
        self.assertEqual(paiement.provider_session_id, "cos-wave-init")
        client_cls.return_value.__enter__.return_value.post.assert_called_once()

    @patch(
        "apps.abonnements.paiements_en_ligne.WavePaiement.initier_paiement",
        side_effect=RuntimeError("prestataire indisponible"),
    )
    def test_libere_la_reservation_si_le_prestataire_echoue(self, initier):
        with self.assertRaises(RuntimeError):
            initier_paiement_en_ligne(self.demande, "wave")

        self.assertFalse(
            PaiementAbonnement.objects.filter(demande_abonnement=self.demande).exists()
        )


@override_settings(ABONNEMENT_TAUX_XOF_GNF="1.5")
class TarificationTests(SimpleTestCase):
    def setUp(self):
        self.plan = PlanAbonnement(
            prix=Decimal("5000.00"),
            devise="XOF",
        )

    def test_senegal_reste_en_xof(self):
        resultat = calculer_montant_plan(self.plan, "SN")

        self.assertEqual(resultat["devise"], "XOF")
        self.assertEqual(resultat["montant"], Decimal("5000"))
        self.assertEqual(resultat["taux_conversion"], Decimal("1"))

    def test_guinee_convertit_en_gnf(self):
        resultat = calculer_montant_plan(self.plan, "GN")

        self.assertEqual(resultat["devise"], "GNF")
        self.assertEqual(resultat["montant_reference"], Decimal("5000"))
        self.assertEqual(resultat["montant"], Decimal("7500"))
        self.assertEqual(resultat["taux_conversion"], Decimal("1.5"))

    @override_settings(ABONNEMENT_TAUX_XOF_GNF="0")
    def test_taux_absent_refuse_la_conversion(self):
        with self.assertRaises(ValidationError):
            calculer_montant_plan(self.plan, "GN")


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    ABONNEMENT_PAIEMENT_EN_LIGNE_ACTIF=True,
    ABONNEMENT_PAIEMENT_PAYS_ACTIFS=["SN"],
    PAIEMENT_PRESTATAIRES={
        "paytech": {
            "SN": {
                "api_key": "paytech-key",
                "api_secret": "paytech-secret",
                "environnement": "test",
                "ipn_url": "https://backend.example.com/api/paiements/webhooks/paytech/SN/",
            }
        }
    },
)
class PayTechInitiationTests(TestCase):
    def setUp(self):
        self.plan = PlanAbonnement.objects.create(
            code="paytech-plan",
            nom="PayTech plan",
            prix=Decimal("5000.00"),
            devise="XOF",
            duree_jours=30,
            max_clients=100,
            max_agents=2,
            est_essai=False,
            actif=True,
        )
        utilisateur = Utilisateur.objects.create_user(
            telephone="730000001",
            password="MotDePasse123",
            role="fournisseur",
        )
        fournisseur = Fournisseur.objects.create(
            utilisateur=utilisateur,
            nom_entreprise="PayTech SARL",
            telephone="730000001",
            pays="SN",
        )
        self.demande = DemandeAbonnement.objects.create(
            fournisseur=fournisseur,
            plan=self.plan,
            pays="SN",
            type_operation=DemandeAbonnement.TypeOperation.SOUSCRIPTION,
            montant=self.plan.prix,
            devise=self.plan.devise,
            statut=DemandeAbonnement.Statut.EN_ATTENTE,
            cle_idempotence="paytech-init-001",
            date_expiration=timezone.now() + timedelta(hours=24),
        )

    @patch("apps.abonnements.paiements_en_ligne.httpx.Client")
    def test_initie_une_session_paytech(self, client_cls):
        reponse = Mock(status_code=200)
        reponse.json.return_value = {
            "success": 1,
            "token": "paytech-token-001",
            "redirect_url": "https://paytech.sn/payment/checkout/paytech-token-001",
        }
        client_cls.return_value.__enter__.return_value.post.return_value = reponse

        paiement, resultat = initier_paiement_en_ligne(self.demande, "paytech")

        self.assertEqual(paiement.provider_session_id, "paytech-token-001")
        self.assertEqual(paiement.provider_transaction_id, "paytech-token-001")
        self.assertEqual(
            resultat["paiement_url"],
            "https://paytech.sn/payment/checkout/paytech-token-001",
        )
        appel = client_cls.return_value.__enter__.return_value.post
        payload = appel.call_args.kwargs["json"]
        self.assertEqual(
            appel.call_args.args[0],
            "https://paytech.sn/api/payment/request-payment",
        )
        self.assertEqual(payload["item_price"], "5000")
        self.assertEqual(payload["currency"], "XOF")
        self.assertEqual(payload["ref_command"], self.demande.reference)
        self.assertEqual(payload["env"], "test")
        self.assertEqual(
            payload["ipn_url"],
            "https://backend.example.com/api/paiements/webhooks/paytech/SN/",
        )
        self.assertIn("paiement=succes", payload["success_url"])
        self.assertIn("paiement=annulation", payload["cancel_url"])
        entetes = appel.call_args.kwargs["headers"]
        self.assertEqual(entetes["API_KEY"], "paytech-key")
        self.assertEqual(entetes["API_SECRET"], "paytech-secret")

    @override_settings(
        PAIEMENT_PRESTATAIRES={
            "paytech": {
                "SN": {
                    "api_key": "paytech-key",
                    "api_secret": "paytech-secret",
                    "ipn_url": "http://127.0.0.1:8000/api/paiements/webhooks/paytech/SN/",
                }
            }
        }
    )
    @patch("apps.abonnements.paiements_en_ligne.httpx.Client")
    def test_refuse_une_ipn_http_sans_appel_paytech(self, client_cls):
        with self.assertRaises(PrestataireNonConfigure) as context:
            initier_paiement_en_ligne(self.demande, "paytech")
        self.assertIn("HTTPS", str(context.exception))
        client_cls.assert_not_called()


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    ABONNEMENT_PAIEMENT_EN_LIGNE_ACTIF=True,
    ABONNEMENT_PAIEMENT_PAYS_ACTIFS=["SN"],
    PAIEMENT_PRESTATAIRES={
        "paytech": {
            "SN": {
                "api_key": "paytech-key",
                "api_secret": "paytech-secret",
            }
        }
    },
)
class PayTechWebhookTests(APITestCase):
    def setUp(self):
        self.plan = PlanAbonnement.objects.create(
            code="paytech-webhook-plan",
            nom="PayTech webhook plan",
            prix=Decimal("5000.00"),
            devise="XOF",
            duree_jours=30,
            max_clients=100,
            max_agents=2,
            est_essai=False,
            actif=True,
        )
        utilisateur = Utilisateur.objects.create_user(
            telephone="730000002",
            password="MotDePasse123",
            role="fournisseur",
        )
        fournisseur = Fournisseur.objects.create(
            utilisateur=utilisateur,
            nom_entreprise="PayTech Webhook SARL",
            telephone="730000002",
            pays="SN",
        )
        self.demande = DemandeAbonnement.objects.create(
            fournisseur=fournisseur,
            plan=self.plan,
            pays="SN",
            type_operation=DemandeAbonnement.TypeOperation.SOUSCRIPTION,
            montant=self.plan.prix,
            devise=self.plan.devise,
            statut=DemandeAbonnement.Statut.EN_ATTENTE,
            cle_idempotence="paytech-webhook-001",
            date_expiration=timezone.now() + timedelta(hours=24),
        )

    def _publier(
        self,
        type_event="sale_complete",
        montant="5000",
        jeton="paytech-token-webhook-001",
        hmac_valide=True,
    ):
        corps = {
            "type_event": type_event,
            "ref_command": self.demande.reference,
            "item_name": "Abonnement PayTech plan",
            "item_price": "5000",
            "final_item_price": montant,
            "currency": "XOF",
            "command_name": "Abonnement PayTech plan",
            "token": jeton,
            "env": "test",
            "payment_method": "Wave",
            "api_key_sha256": hashlib.sha256(b"paytech-key").hexdigest(),
            "api_secret_sha256": hashlib.sha256(b"paytech-secret").hexdigest(),
        }
        message = f"{montant}|{self.demande.reference}|paytech-key"
        hmac_calcule = hmac.new(
            b"paytech-secret",
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        corps["hmac_compute"] = hmac_calcule if hmac_valide else "0" * 64
        return self.client.post(
            "/api/paiements/webhooks/paytech/SN/",
            data=urlencode(corps),
            content_type="application/x-www-form-urlencoded",
        )

    def test_webhook_sale_complete_confirme_la_demande(self):
        response = self._publier()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["statut"], "TRAITE")
        self.demande.refresh_from_db()
        self.assertEqual(
            self.demande.statut,
            DemandeAbonnement.Statut.CONFIRMEE,
        )
        self.assertEqual(
            PaiementAbonnement.objects.filter(
                demande_abonnement=self.demande,
                statut=PaiementAbonnement.Statut.CONFIRME,
            ).count(),
            1,
        )
        self.assertTrue(
            Abonnement.objects.filter(fournisseur=self.demande.fournisseur).exists()
        )

    def test_webhook_rejoue_sans_effet_supplementaire(self):
        self._publier()
        response = self._publier()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["rejoue"])
        self.assertEqual(
            PaiementAbonnement.objects.filter(
                demande_abonnement=self.demande,
                statut=PaiementAbonnement.Statut.CONFIRME,
            ).count(),
            1,
        )

    def test_signature_invalide_est_rejetee(self):
        response = self._publier(hmac_valide=False)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.demande.refresh_from_db()
        self.assertEqual(
            self.demande.statut,
            DemandeAbonnement.Statut.EN_ATTENTE,
        )
        self.assertFalse(
            Abonnement.objects.filter(fournisseur=self.demande.fournisseur).exists()
        )

    def test_sale_canceled_ne_confirme_pas_la_demande(self):
        response = self._publier(type_event="sale_canceled")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["statut"], "IGNORE")
        self.demande.refresh_from_db()
        self.assertEqual(
            self.demande.statut,
            DemandeAbonnement.Statut.EN_ATTENTE,
        )
        self.assertFalse(
            Abonnement.objects.filter(fournisseur=self.demande.fournisseur).exists()
        )

    def test_signature_sha256_est_acceptee(self):
        donnees = {
            "type_event": "sale_complete",
            "ref_command": self.demande.reference,
            "item_price": "5000",
            "final_item_price": "5000",
            "currency": "XOF",
            "token": "paytech-token-sha256",
            "api_key_sha256": hashlib.sha256(b"paytech-key").hexdigest(),
            "api_secret_sha256": hashlib.sha256(b"paytech-secret").hexdigest(),
        }
        response = self.client.post(
            "/api/paiements/webhooks/paytech/SN/",
            data=urlencode(donnees),
            content_type="application/x-www-form-urlencoded",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["statut"], "TRAITE")
