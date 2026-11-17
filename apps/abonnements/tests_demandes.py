import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.comptes.models import Utilisateur
from apps.fournisseurs.models import Fournisseur

from .models import (
    Abonnement,
    DemandeAbonnement,
    EvenementAbonnement,
    PaiementAbonnement,
    PlanAbonnement,
    WebhookEvenement,
)


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
    PAIEMENT_WEBHOOK_SECRETS={"wave": "secret-wave"},
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
            "event_id": event_id,
            "transaction_id": "tx-wave-001",
            "reference": self.demande.reference,
            "status": "success",
            "amount": montant,
            "currency": "XOF",
        }

    def _poster(self, payload, secret="secret-wave"):
        brut = json.dumps(payload).encode("utf-8")
        signature = hmac.new(
            secret.encode("utf-8"),
            brut,
            hashlib.sha256,
        ).hexdigest()
        return self.client.post(
            "/api/paiements/webhooks/wave/",
            data=brut,
            content_type="application/json",
            HTTP_X_KLARO_SIGNATURE=signature,
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
