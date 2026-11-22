# FEUILLE DE ROUTE TECHNIQUE — ABONNEMENTS ET PAIEMENTS KLARO
# COMMENTAIRE DU CODE EN FRANCAIS

**Version :** 2.0 — Spécification backend renforcée  
**Statut :** Préparation fonctionnelle et technique  
**Objectif :** Concevoir une architecture fiable, sécurisée, évolutive et résistante aux paiements simultanés.

## Décisions multi-pays validées

- Le pays est saisi à l'inscription fournisseur et backfillé à `SN` pour l'existant.
- Le pays est immuable après inscription ; seule une correction administrateur est autorisée.
- Les plans restent libellés en XOF, devise de référence.
- Le montant local est calculé et figé au moment de la demande :
  - `XOF` pour le Sénégal et la Côte d'Ivoire ;
  - `GNF` pour la Guinée, avec un taux fixe configurable via `ABONNEMENT_TAUX_XOF_GNF`.
- Les montants XOF/GNF sont arrondis à l'unité entière.
- Wave Sénégal est le premier prestataire en ligne activé.
- Orange Money sera intégré après réception de la spécification technique par pays.
- Les webhooks sont routés par prestataire et par pays :
  `/api/paiements/webhooks/<prestataire>/<pays>/`.
- La confirmation d'un paiement repose uniquement sur un webhook signé ou une vérification serveur à serveur.
- Le retour navigateur ne confirme jamais un paiement.
- Le paiement manuel reste disponible comme solution de repli.

---

# 1. OBJECTIF DU PROJET

Mettre en place un système complet de gestion des abonnements fournisseurs dans KLARO.

Le système doit permettre :

- La consultation des plans d'abonnement.
- La souscription à un abonnement payant.
- Le renouvellement d'un abonnement.
- Le paiement manuel ou en ligne.
- La validation manuelle des paiements par le super administrateur.
- La confirmation automatique des paiements en ligne après vérification du prestataire.
- L'activation de l'abonnement uniquement après confirmation effective du paiement.
- La gestion des expirations et des suspensions.
- La consultation de l'historique des abonnements et des paiements.
- La traçabilité de toutes les opérations financières.

## 1.1 Acteurs concernés

Seuls les acteurs suivants peuvent accéder à cette fonctionnalité :

- **Fournisseur :** consulte les plans, crée ses demandes, paie et consulte son historique.
- **Super administrateur :** gère les plans, contrôle les demandes et valide les paiements manuels.

Les clients et les agents ne doivent jamais accéder aux fonctionnalités d'abonnement ou de paiement d'abonnement.

---

# 2. PLANS COMMERCIAUX

## 2.1 Tarification initiale

| Plan | Prix | Durée |
|---|---:|---:|
| Simple | 5 000 XOF | 30 jours |
| Premium | 10 000 XOF | 30 jours |
| Pro | 15 000 XOF | 30 jours |

## 2.2 Quotas proposés

| Plan | Clients | Agents |
|---|---:|---:|
| Simple | 100 | 2 |
| Premium | 500 | 5 |
| Pro | Illimité | Illimité |

Les quotas restent à valider avant l'implémentation.

## 2.3 Configuration technique

Chaque plan doit contenir :

- `code`
- `nom`
- `description`
- `prix`
- `devise`
- `duree_jours`
- `max_clients`
- `max_agents`
- `est_essai`
- `actif`
- `date_creation`
- `date_modification`

Valeurs :

- Devise : `XOF`.
- Durée : 30 jours pour les plans payants.
- `max_clients = NULL` signifie illimité.
- `max_agents = NULL` signifie illimité.

## 2.4 Initialisation

Créer une commande Django :

`python manage.py seed_plans`

Cette commande doit :

- Créer les plans Simple, Premium et Pro s'ils n'existent pas.
- Ne jamais créer de doublons.
- Ne jamais supprimer un plan existant.
- Ne pas écraser silencieusement les modifications de prix effectuées par le super administrateur.
- Conserver le plan d'essai existant.
- Afficher un rapport d'exécution.

La commande doit pouvoir être exécutée plusieurs fois sans effet secondaire.

---

# 3. PRINCIPES MÉTIER NON NÉGOCIABLES

Les règles suivantes doivent être respectées dans tous les services backend.

1. Un fournisseur ne peut jamais activer lui-même un abonnement payant.
2. Le montant est toujours calculé par le backend à partir du plan.
3. Le frontend ne peut jamais imposer ou modifier le montant attendu.
4. Un abonnement payant ne devient actif qu'après confirmation du paiement.
5. Un paiement en attente, refusé, annulé ou échoué ne peut jamais activer ou prolonger un abonnement.
6. Le super administrateur est le seul acteur autorisé à confirmer manuellement un paiement.
7. Un fournisseur ne peut consulter que ses propres demandes, paiements et abonnements.
8. Un client ou un agent reçoit une réponse `403 Forbidden` pour toute tentative d'accès aux endpoints d'abonnement.
9. Toute confirmation doit être atomique.
10. Toute opération financière doit être idempotente.
11. Une confirmation ne doit jamais créer deux abonnements pour une même opération.
12. L'historique financier confirmé ne doit jamais être supprimé ou modifié directement.
13. Chaque paiement doit être rattaché à une demande précise.
14. Chaque demande doit appartenir à un fournisseur précis.
15. Le montant ne doit jamais servir à identifier le propriétaire d'un paiement.
16. Les notifications de paiement doivent être vérifiées avant toute activation.
17. Les paiements de deux fournisseurs différents doivent être traités indépendamment, même s'ils ont le même montant et arrivent au même instant.

---

# 4. ARCHITECTURE GÉNÉRALE DU SYSTÈME

## 4.1 Architecture logique

Le système doit séparer clairement les responsabilités.

```text
Fournisseur
    |
    v
DemandeAbonnement
    |
    +----------------------+
    |                      |
    v                      v
Paiement manuel       Paiement en ligne
    |                      |
    v                      v
Vérification admin     Prestataire externe
    |                      |
    |                  Webhook signé
    |                      |
    +----------+-----------+
               |
               v
       Vérification financière
               |
               v
       Confirmation atomique
               |
       +-------+--------+
       |       |        |
       v       v        v
   Paiement Abonnement Événement
       confirmé activé   historique
```

## 4.2 Principe fondamental

Une demande d'abonnement représente une intention de souscription ou de renouvellement.

Elle doit exister avant le paiement et avant la création de l'abonnement actif.

Le système ne doit donc jamais créer un abonnement actif au moment où le fournisseur clique sur « Souscrire ».

L'ordre obligatoire est :

1. Création de la demande.
2. Paiement.
3. Vérification.
4. Confirmation.
5. Création ou prolongation de l'abonnement.
6. Journalisation.

---

# 5. MODÈLES DE DONNÉES DJANGO

Réutiliser les modèles existants :

- `PlanAbonnement`
- `Abonnement`
- `PaiementAbonnement`
- `EvenementAbonnement`

Ajouter les modèles nécessaires à la nouvelle architecture.

## 5.1 Modèle DemandeAbonnement

Ce modèle est le centre du processus de souscription et de renouvellement.

Champs recommandés :

| Champ | Type | Description |
|---|---|---|
| `id` | UUIDField | Identifiant interne unique |
| `reference` | CharField unique | Référence KLARO publique |
| `fournisseur` | ForeignKey | Propriétaire de la demande |
| `plan` | ForeignKey | Plan choisi |
| `type_operation` | ChoiceField | SOUSCRIPTION ou RENOUVELLEMENT |
| `abonnement_cible` | ForeignKey nullable | Abonnement concerné par le renouvellement |
| `montant` | DecimalField | Montant figé |
| `devise` | CharField | XOF |
| `moyen_paiement` | ChoiceField | Moyen sélectionné |
| `prestataire` | CharField nullable | Wave, Orange Money, etc. |
| `reference_transaction` | CharField nullable | Référence fournie pour un paiement manuel |
| `preuve_paiement` | FileField nullable | Justificatif privé |
| `statut` | ChoiceField | État de la demande |
| `cle_idempotence` | CharField | Clé anti-doublon |
| `commentaire_fournisseur` | TextField | Commentaire éventuel |
| `motif_traitement` | TextField | Motif de refus ou d'annulation |
| `cree_par` | ForeignKey | Utilisateur créateur |
| `traite_par` | ForeignKey nullable | Administrateur ayant traité |
| `date_traitement` | DateTime nullable | Date de décision |
| `date_expiration` | DateTime nullable | Date limite |
| `abonnement_genere` | ForeignKey nullable | Abonnement créé |
| `date_creation` | DateTime | Création |
| `date_modification` | DateTime | Modification |

### Contraintes de base de données

- `reference` doit être unique.
- `cle_idempotence` doit être unique dans le périmètre défini.
- Le montant doit être positif.
- La devise doit être connue.
- Une demande confirmée ne doit pas être confirmée une deuxième fois.
- Une demande ne doit pas générer plusieurs abonnements.
- Un renouvellement doit cibler un abonnement appartenant au même fournisseur.

La clé d'idempotence doit être associée au fournisseur et à l'opération. Une contrainte composée `(fournisseur, cle_idempotence)` est préférable si la clé n'est pas globalement unique.

## 5.2 Modèle PaiementAbonnement

Le paiement doit représenter une opération financière réelle.

Champs recommandés :

- `id`
- `reference_interne` unique
- `demande_abonnement`
- `abonnement` nullable selon la compatibilité du modèle existant
- `fournisseur`
- `prestataire`
- `provider_transaction_id`
- `reference_transaction`
- `montant`
- `devise`
- `statut_paiement`
- `date_initiation`
- `date_confirmation`
- `date_creation`

### Contraintes importantes

- Une transaction prestataire ne peut être enregistrée qu'une seule fois pour un même prestataire.
- Le couple `(prestataire, provider_transaction_id)` doit être unique lorsque l'identifiant existe.
- Une demande ne peut pas avoir plusieurs paiements confirmés pour une seule opération.
- Un paiement confirmé ne peut pas être supprimé.
- Le montant et la devise enregistrés doivent correspondre aux valeurs vérifiées.

**Important :** si le modèle actuel impose un abonnement obligatoire, faire évoluer sa relation de manière compatible. Dans le nouveau flux, le paiement est initié avant la création de l'abonnement. Il faut donc permettre au paiement d'être rattaché à la demande avant d'être associé à l'abonnement généré.

## 5.3 Modèle EvenementAbonnement

Ce modèle conserve l'historique des opérations.

Événements à enregistrer :

- DEMANDE_CREEE
- PAIEMENT_SOUMIS
- PAIEMENT_INITIE
- PAIEMENT_CONFIRME
- PAIEMENT_REFUSE
- DEMANDE_ANNULEE
- DEMANDE_EXPIREE
- ABONNEMENT_ACTIVE
- ABONNEMENT_RENOUVELE
- ABONNEMENT_SUSPENDU
- ABONNEMENT_REACTIVE
- ABONNEMENT_ANNULE
- ANOMALIE_PAIEMENT

Chaque événement doit conserver :

- L'acteur.
- L'objet concerné.
- L'ancien état.
- Le nouvel état.
- La date.
- Le motif éventuel.
- Un identifiant de corrélation.
- Les métadonnées utiles, sans secrets ni données sensibles inutiles.

Les événements financiers confirmés sont conservés sans modification destructive.

## 5.4 Modèle WebhookEvenement

Créer une table dédiée à la réception des notifications externes.

Champs :

- `id`
- `prestataire`
- `provider_event_id`
- `provider_transaction_id`
- `reference_demande`
- `payload_brut_protege`
- `signature_verifiee`
- `statut_traitement`
- `nombre_tentatives`
- `date_reception`
- `date_traitement`
- `message_erreur`

Contraintes :

- L'identifiant d'événement doit être unique par prestataire.
- Un événement déjà traité ne doit pas produire un nouvel effet financier.
- Les secrets, jetons et données sensibles doivent être masqués ou exclus des journaux.

Si un prestataire ne fournit pas d'identifiant d'événement, définir une stratégie de déduplication spécifique à son API.

---

# 6. GESTION DES PAIEMENTS SIMULTANÉS ET PRÉVENTION DES DOUBLONS

Cette partie est une priorité de sécurité.

KLARO doit pouvoir traiter plusieurs fournisseurs qui effectuent des paiements simultanément, y compris lorsque les montants sont identiques.

## 6.1 Scénario à protéger

Exemple :

| Fournisseur | Plan | Montant | Référence |
|---|---|---:|---|
| Fournisseur A | Premium | 10 000 XOF | KLARO-PAY-A8F21C |
| Fournisseur B | Premium | 10 000 XOF | KLARO-PAY-B7D93E |

Les deux paiements peuvent arriver à la même seconde.

Le backend doit obligatoirement identifier chaque paiement grâce à sa demande et à ses références uniques.

Il ne doit jamais rechercher un paiement uniquement avec :

- Le montant.
- Le nom du plan.
- Le numéro de téléphone.
- L'heure de paiement.
- Le nom du fournisseur.

Ces informations peuvent aider au contrôle, mais ne constituent pas une identité transactionnelle.

## 6.2 Références uniques

À la création de chaque demande, le backend génère :

- Un UUID interne.
- Une référence KLARO unique.
- Une clé d'idempotence.

À l'initialisation d'un paiement en ligne, le backend transmet une référence de commande unique au prestataire.

Chaque paiement doit pouvoir être retrouvé sans ambiguïté.

## 6.3 Isolation des fournisseurs

Chaque demande est rattachée à un fournisseur.

Toutes les requêtes fournisseur doivent filtrer les données par l'identité authentifiée :

`request.user.fournisseur_profile`

Ne jamais accepter un `fournisseur_id` fourni librement par le frontend pour déterminer le propriétaire de la demande.

Pour les actions sensibles, le backend doit revérifier la relation entre :

- Le fournisseur.
- La demande.
- Le paiement.
- L'abonnement ciblé.

## 6.4 Protection contre les doubles clics

Le frontend doit transmettre une clé d'idempotence lors de la création d'une demande.

Le backend doit :

1. Vérifier si cette clé existe déjà pour ce fournisseur.
2. Si elle existe, retourner la demande initialement créée.
3. Si elle n'existe pas, créer la demande.
4. Enregistrer la clé dans la même transaction.
5. Empêcher deux créations concurrentes avec une contrainte unique en base.

Une répétition de la même requête ne doit donc pas créer une deuxième demande.

## 6.5 Protection contre les paiements répétés

Ne pas bloquer tous les paiements ayant le même montant pendant dix minutes.

Cette règle empêcherait deux fournisseurs de payer légitimement le même abonnement au même moment.

La protection doit reposer sur :

- Une clé d'idempotence par opération.
- Une référence KLARO unique par demande.
- Une référence de transaction prestataire unique.
- Une contrainte d'unicité en base de données.
- Un contrôle de l'état de la demande.
- Un verrouillage transactionnel.
- Une détection des paiements réellement dupliqués.

Un paiement supplémentaire réellement encaissé doit être placé en anomalie et traité, jamais ignoré silencieusement.

## 6.6 Confirmation concurrente

Au moment de confirmer une demande :

- Utiliser `transaction.atomic()`.
- Verrouiller la demande avec `select_for_update()`.
- Verrouiller les objets concernés dans un ordre cohérent.
- Relire le statut après acquisition du verrou.
- Vérifier l'absence d'un paiement déjà confirmé.
- Vérifier l'absence d'un abonnement déjà généré pour cette opération.
- Créer ou prolonger l'abonnement.
- Enregistrer le paiement.
- Enregistrer les événements.
- Valider la transaction.

Si deux traitements tentent de confirmer la même demande simultanément, un seul doit réussir.

Les demandes de deux fournisseurs différents doivent rester indépendantes.

## 6.7 Gestion des transactions et des contraintes

Les contraintes de base de données constituent une protection supplémentaire aux contrôles applicatifs.

Prévoir notamment :

- Des contraintes `UniqueConstraint`.
- Des contraintes `CheckConstraint` pour les montants positifs.
- Des clés étrangères cohérentes.
- Des index sur les références et statuts utilisés fréquemment.
- Des contrôles d'intégrité sur les associations fournisseur-demande-paiement-abonnement.

La base de données doit être considérée comme le dernier rempart contre les doublons.

---

# 7. MACHINE À ÉTATS

Il faut séparer les statuts des demandes, des paiements et des abonnements.

## 7.1 Statuts de DemandeAbonnement

- `EN_ATTENTE`
- `EN_VERIFICATION`
- `CONFIRMEE`
- `REFUSEE`
- `ANNULEE`
- `EXPIREE`
- `ECHEC`

Transitions autorisées :

```text
EN_ATTENTE
   |
   +----> EN_VERIFICATION
   |          |
   |          +----> CONFIRMEE
   |          |
   |          +----> REFUSEE
   |
   +----> CONFIRMEE
   |      (après vérification en ligne)
   |
   +----> ANNULEE
   |
   +----> EXPIREE
   |
   +----> ECHEC
```

Une demande confirmée ne peut plus revenir à l'état en attente.

## 7.2 Statuts de PaiementAbonnement

Prévoir des statuts indépendants :

- `INITIE`
- `EN_ATTENTE`
- `EN_VERIFICATION`
- `CONFIRME`
- `REFUSE`
- `ECHEC`
- `ANNULE`
- `REMBOURSE` si cette fonctionnalité est ajoutée ultérieurement.

Une demande peut être en vérification alors que son paiement n'est pas encore confirmé.

Il ne faut pas confondre l'état administratif de la demande avec l'état financier du paiement.

## 7.3 Statuts d'Abonnement

Conserver les statuts métier existants si compatibles :

- `ACTIF`
- `SUSPENDU`
- `EXPIRE`
- `ANNULE`

Les transitions doivent être contrôlées par les services métier.

---

# 8. SERVICES BACKEND À DÉVELOPPER

Les vues et serializers ne doivent pas contenir directement toute la logique financière.

La logique doit être centralisée dans des services métier testables.

## 8.1 Services de demandes

Créer :

- `creer_demande_abonnement()`
- `soumettre_preuve_paiement()`
- `annuler_demande_abonnement()`
- `refuser_demande_abonnement()`
- `expirer_demandes_abonnement()`

## 8.2 Services de confirmation

Créer :

- `confirmer_demande_abonnement()`
- `verifier_eligibilite_confirmation()`
- `verifier_unicite_transaction()`
- `creer_paiement_confirme()`
- `activer_ou_renouveler_abonnement()`

La confirmation doit être exécutée dans une transaction atomique.

## 8.3 Services de paiement en ligne

Créer :

- `initier_paiement_en_ligne()`
- `traiter_webhook_paiement()`
- `verifier_signature_webhook()`
- `verifier_statut_prestataire()`
- `reconcilier_paiements_en_attente()`

## 8.4 Responsabilité des services existants

Adapter les services existants :

- `souscrire`
- `renouveler`
- `suspendre`
- `reactiver`
- `annuler`
- `confirmer_paiement`
- `_creer_paiement`

Les opérations de souscription et de renouvellement payants doivent être déclenchées après validation de la demande et confirmation financière.

Il ne faut pas conserver deux chemins métier concurrents permettant de créer directement un abonnement payant actif sans paiement confirmé.

---

# 9. ALGORITHME DE CONFIRMATION ATOMIQUE

La fonction `confirmer_demande_abonnement()` est l'un des composants les plus sensibles de KLARO.

Elle doit suivre cette logique :

```text
DÉBUT TRANSACTION

1. Verrouiller la demande.
2. Recharger son état.
3. Vérifier qu'elle peut être confirmée.
4. Vérifier le fournisseur propriétaire.
5. Vérifier le montant et la devise.
6. Vérifier la référence de transaction.
7. Vérifier qu'aucun paiement confirmé ne lui est déjà associé.
8. Vérifier qu'aucun abonnement n'a déjà été généré.
9. Créer le paiement confirmé.
10. Créer ou prolonger l'abonnement.
11. Associer l'abonnement à la demande.
12. Enregistrer les événements financiers et métier.
13. Marquer la demande comme CONFIRMEE.

VALIDER TRANSACTION

APRÈS VALIDATION :
14. Déclencher les notifications.
```

Si une étape échoue avant la validation, toutes les modifications financières doivent être annulées.

Les notifications ne doivent pas être envoyées avant le commit de la transaction.

Pour fiabiliser les traitements asynchrones, utiliser un mécanisme `transaction.on_commit()` ou une table d'outbox transactionnelle.

---

# 10. ABSTRACTION DES PRESTATAIRES DE PAIEMENT

Créer une couche indépendante des prestataires.

Structure indicative :

```text
apps/
  abonnements/
    models.py
    serializers.py
    views.py
    permissions.py
    services/
      demandes.py
      confirmations.py
      abonnements.py
      paiements.py
      reconciliation.py
    paiements/
      base.py
      providers/
        wave.py
        orange_money.py
    tasks.py
    selectors.py
    tests/
```

## 10.1 Interface commune

Chaque prestataire doit respecter une interface commune :

- `initier_paiement(demande)`
- `verifier_signature(requete)`
- `verifier_statut(reference_prestataire)`
- `normaliser_statut(statut_prestataire)`
- `annuler_paiement(reference_prestataire)` si disponible.

## 10.2 Prestataires envisagés

Priorité proposée :

1. Wave.
2. Orange Money.
3. Agrégateur de paiement par carte si nécessaire.

Le choix définitif dépendra des API, des contrats marchands et des possibilités de vérification serveur à serveur.

## 10.3 Sécurité des prestataires

- Les clés API sont stockées dans les variables d'environnement ou un gestionnaire de secrets.
- Aucun secret ne doit être présent dans le frontend.
- Les communications utilisent HTTPS.
- Les signatures doivent être vérifiées selon la documentation officielle du prestataire.
- Le montant et la devise doivent être comparés avec les données de la demande.
- L'identité de la transaction doit être vérifiée.
- Le retour du navigateur ne constitue jamais une preuve de paiement.

Aucune donnée de carte bancaire ne doit être stockée dans KLARO. Utiliser une page de paiement hébergée par le prestataire.

---

# 11. WEBHOOKS ET RÉCONCILIATION

## 11.1 Endpoint public

Créer :

`POST /api/paiements/webhooks/{prestataire}/`

Cet endpoint est public et ne nécessite pas de JWT, mais la vérification cryptographique de la signature est obligatoire.

## 11.2 Ordre de traitement

1. Recevoir la notification.
2. Vérifier la signature avec la méthode officielle du prestataire.
3. Vérifier l'identifiant de l'événement.
4. Vérifier la référence de transaction.
5. Retrouver la demande correspondante.
6. Vérifier le montant.
7. Vérifier la devise.
8. Vérifier le statut réel de la transaction.
9. Vérifier que la transaction n'a pas déjà été traitée.
10. Confirmer atomiquement la demande si toutes les conditions sont satisfaites.
11. Enregistrer le résultat du webhook.
12. Retourner une réponse conforme aux attentes du prestataire.

Ne jamais retrouver une demande uniquement par le montant ou le numéro de téléphone.

## 11.3 Webhook rejoué

Si un événement déjà traité est reçu :

- Ne pas recréer le paiement.
- Ne pas prolonger l'abonnement.
- Ne pas générer une deuxième activation.
- Retourner une réponse conforme au protocole du prestataire.
- Conserver une trace de la répétition si nécessaire.

## 11.4 Anomalies

Si le montant, la devise ou la référence ne correspond pas :

- Ne pas activer l'abonnement.
- Marquer la transaction comme anomalie.
- Enregistrer les informations nécessaires à l'enquête.
- Alerter le super administrateur.
- Ne pas modifier les données financières confirmées.

## 11.5 Réconciliation

Un traitement périodique doit rechercher les paiements qui restent en attente alors que leur statut peut avoir changé chez le prestataire.

Il doit interroger l'API officielle du prestataire et comparer son statut avec celui de KLARO.

Ce mécanisme protège contre les webhooks perdus, retardés ou indisponibles.

---

# 12. API REST À EXPOSER

Les endpoints doivent respecter les permissions et les filtres par fournisseur.

## 12.1 Endpoints fournisseur

| Méthode | Endpoint | Fonction |
|---|---|---|
| GET | `/api/abonnements/plans/` | Liste des plans actifs |
| GET | `/api/fournisseurs/abonnement/` | Abonnement et quotas actuels |
| POST | `/api/fournisseurs/abonnement/demandes/` | Créer une demande |
| GET | `/api/fournisseurs/abonnement/demandes/` | Lister ses demandes |
| GET | `/api/fournisseurs/abonnement/demandes/{id}/` | Détail d'une demande |
| POST | `/api/fournisseurs/abonnement/demandes/{id}/soumettre-paiement/` | Soumettre référence et preuve |
| POST | `/api/fournisseurs/abonnement/demandes/{id}/annuler/` | Annuler une demande éligible |
| POST | `/api/fournisseurs/abonnement/demandes/{id}/initier-paiement/` | Initier le paiement en ligne |

### Règles

- Le fournisseur est déterminé depuis l'utilisateur authentifié.
- Le montant n'est jamais accepté depuis le frontend.
- Le plan doit être actif.
- Le renouvellement doit cibler un abonnement du fournisseur connecté.
- La clé d'idempotence doit être prise en charge.
- Les demandes d'un autre fournisseur doivent être inaccessibles.

## 12.2 Endpoints super administrateur

| Méthode | Endpoint | Fonction |
|---|---|---|
| GET | `/api/super-admin/demandes-abonnement/` | Toutes les demandes |
| GET | `/api/super-admin/demandes-abonnement/{id}/` | Détail d'une demande |
| POST | `/api/super-admin/demandes-abonnement/{id}/confirmer/` | Confirmer un paiement manuel |
| POST | `/api/super-admin/demandes-abonnement/{id}/refuser/` | Refuser avec motif |
| POST | `/api/super-admin/demandes-abonnement/{id}/annuler/` | Annuler administrativement |

Filtres :

- Fournisseur.
- Plan.
- Statut.
- Type d'opération.
- Prestataire.
- Moyen de paiement.
- Date de début.
- Date de fin.
- Référence KLARO.
- Référence transaction.

Conserver les endpoints existants de gestion des plans, abonnements et paiements, en les adaptant sans casser leur compatibilité.

## 12.3 Réponses API

Utiliser des codes HTTP cohérents :

- `200` : opération réussie.
- `201` : ressource créée.
- `400` : données invalides ou transition interdite.
- `401` : authentification absente.
- `403` : rôle interdit.
- `404` : ressource inexistante ou non accessible.
- `409` : conflit d'état ou doublon.
- `429` : limitation de débit.

Ne pas exposer d'informations permettant de consulter les données d'un autre fournisseur.

---

# 13. FLUX MÉTIER COMPLETS

## 13.1 Souscription manuelle

1. Le fournisseur ouvre « Mon abonnement ».
2. Il consulte les plans Simple, Premium et Pro.
3. Il choisit un plan.
4. Le frontend envoie la demande avec sa clé d'idempotence.
5. Le backend vérifie son identité et son éligibilité.
6. Le backend calcule le montant et génère une référence KLARO.
7. La demande est créée en `EN_ATTENTE`.
8. Le fournisseur effectue son paiement.
9. Il soumet la référence et éventuellement un justificatif.
10. La demande passe en `EN_VERIFICATION`.
11. Le super administrateur vérifie le paiement auprès de la source financière appropriée.
12. Il confirme ou refuse.
13. Si confirmé, le backend active l'abonnement et enregistre le paiement dans une transaction atomique.
14. Le fournisseur reçoit la confirmation et retrouve son historique.

Un simple screenshot ne constitue pas une preuve suffisante à lui seul. Le super administrateur doit vérifier que le paiement a réellement été reçu.

## 13.2 Paiement en ligne

1. Le fournisseur choisit un plan.
2. Le backend crée une demande unique.
3. Le backend initialise une transaction chez le prestataire.
4. Le prestataire retourne une URL de paiement.
5. Le fournisseur effectue le paiement.
6. Le prestataire envoie un webhook.
7. KLARO vérifie la signature et la transaction.
8. KLARO confirme atomiquement le paiement.
9. L'abonnement est activé ou renouvelé.
10. Le fournisseur reçoit une notification.

Le navigateur ne peut jamais confirmer lui-même le paiement.

## 13.3 Renouvellement

- La demande doit cibler l'abonnement du fournisseur.
- Si l'abonnement est encore actif et que le plan est identique, ajouter 30 jours à la date de fin actuelle.
- Si l'abonnement est expiré ou suspendu, appliquer la règle de création d'un nouvel abonnement après confirmation.
- Un paiement en attente ne prolonge jamais la période.
- Un changement de plan est autorisé à l'expiration pour le MVP.
- Aucun prorata n'est prévu initialement.

Les renouvellements simultanés d'un même abonnement doivent être verrouillés pour éviter les doubles prolongations.

## 13.4 Refus, annulation et expiration

- Un refus administratif ne crée aucun abonnement.
- Une annulation fournisseur n'est possible que lorsque la demande est encore annulable.
- Une demande expirée ne peut plus être confirmée automatiquement sans contrôle spécifique.
- Un paiement échoué n'active aucun abonnement.
- Une transaction reçue après expiration doit être vérifiée et mise en anomalie si nécessaire, plutôt que d'être ignorée.

---

# 14. SÉCURITÉ ET CONTRÔLE DES ACCÈS

## 14.1 Matrice des permissions

| Action | Fournisseur | Super admin | Client | Agent |
|---|---|---|---|---|
| Voir les plans | Oui | Oui | Non | Non |
| Voir son abonnement | Oui | Oui | Non | Non |
| Créer une demande | Oui | Selon autorisation | Non | Non |
| Voir ses demandes | Oui | Oui | Non | Non |
| Voir toutes les demandes | Non | Oui | Non | Non |
| Confirmer un paiement manuel | Non | Oui | Non | Non |
| Refuser une demande | Non | Oui | Non | Non |
| Gérer les plans | Non | Oui | Non | Non |
| Suspendre un abonnement | Non | Oui | Non | Non |
| Réactiver un abonnement | Non | Oui | Non | Non |

## 14.2 Contrôles obligatoires

- Vérification du rôle sur chaque endpoint sensible.
- Filtrage systématique des données par fournisseur.
- Validation des objets liés entre eux.
- Montant calculé exclusivement côté serveur.
- Références uniques.
- Clés d'idempotence.
- Contraintes d'intégrité en base.
- Transactions atomiques.
- Verrouillage des lignes sensibles.
- Vérification cryptographique des webhooks.
- Limitation de débit.
- Protection contre les accès non autorisés aux justificatifs.
- Journalisation des opérations administratives.
- Aucun secret dans les logs.
- Aucun paiement confirmé modifiable ou supprimable directement.

## 14.3 Sécurité du super administrateur

Les actions financières doivent être réservées à des comptes administrateurs autorisés.

Prévoir :

- Une authentification renforcée, idéalement MFA.
- Une réauthentification pour les opérations particulièrement sensibles.
- Une traçabilité de chaque confirmation, refus et ajustement.
- Une protection contre les tentatives répétées.
- Une politique de session sécurisée.

Pour des volumes financiers plus importants, envisager une validation à deux personnes pour certaines opérations exceptionnelles.

## 14.4 Sécurité des justificatifs

- Stockage privé.
- Accès uniquement après autorisation.
- Taille maximale définie.
- Vérification du type réel de fichier.
- Noms de fichiers générés côté serveur.
- Protection contre les fichiers malveillants.
- Aucun lien public permanent.

---

# 15. GESTION DES MONTANTS ET HISTORIQUE FINANCIER

Le prix attendu est figé lors de la création de la demande.

Si le prix du plan change ensuite, les anciennes demandes conservent leur montant initial.

## 15.1 Ajustement exceptionnel

Si le super administrateur doit accorder une réduction :

- Enregistrer le prix initial.
- Enregistrer la réduction.
- Enregistrer le montant final.
- Enregistrer le motif.
- Enregistrer l'administrateur responsable.
- Interdire la modification après confirmation.

## 15.2 Historique immuable

Un paiement confirmé ne doit pas être corrigé par une modification directe de son montant.

Une éventuelle correction financière devra passer par une opération distincte et traçable : avoir, ajustement ou remboursement.

Les remboursements ne font pas partie du MVP initial.

---

# 16. ABONNEMENTS, QUOTAS ET EXPIRATIONS

## 16.1 Activation

L'abonnement devient actif uniquement après confirmation financière.

Si un plan d'essai est encore actif, il est clôturé au moment de l'activation du plan payant.

## 16.2 Quotas

Les quotas doivent être contrôlés côté backend.

Le frontend peut afficher les limites, mais ne doit jamais décider seul si un fournisseur peut créer un client ou un agent.

Les règles doivent être appliquées sur les opérations concernées.

## 16.3 Expiration

Conserver la commande ou le traitement existant :

`expirer_abonnements`

Un abonnement expiré ne doit plus autoriser les opérations soumises aux quotas.

Le MVP ne prévoit pas de période de grâce, sauf décision contraire.

## 16.4 Demandes expirées

Une demande non payée doit expirer automatiquement après un délai à valider : 24 h ou 48 h.

Cette expiration ne doit jamais activer ou prolonger un abonnement.

---

# 17. MIGRATIONS ET COMPATIBILITÉ

Les modifications doivent préserver les données existantes.

- Ajouter `DemandeAbonnement` par migration non destructive.
- Faire évoluer les relations de `PaiementAbonnement` de manière compatible.
- Ajouter les références uniques nécessaires.
- Ajouter `WebhookEvenement`.
- Ajouter les index et contraintes d'intégrité.
- Ne pas supprimer les abonnements existants.
- Ne pas supprimer les endpoints administratifs existants.
- Vérifier la compatibilité des statistiques financières.
- Prévoir une stratégie de retour arrière.

Configuration :

`ABONNEMENT_PAIEMENT_EN_LIGNE_ACTIF=False`

Le paiement en ligne doit rester désactivé par défaut jusqu'à la validation des tests et des contrats prestataires.

Le paiement manuel peut être activé indépendamment.

---

# 18. TESTS AUTOMATISÉS OBLIGATOIRES

Les tests doivent couvrir les règles métier, les permissions, les transactions et les cas de concurrence.

## 18.1 Tests des plans

- Création idempotente des plans.
- Prix corrects.
- Quotas corrects.
- Plan d'essai conservé.
- Plan inactif non souscriptible.

## 18.2 Tests des demandes

- Un fournisseur crée sa propre demande.
- Un fournisseur ne peut pas créer une demande pour un autre fournisseur.
- Un client reçoit `403`.
- Un agent reçoit `403`.
- Le montant est calculé côté serveur.
- Une clé d'idempotence répétée ne crée pas de doublon.
- Une demande confirmée ne peut pas être annulée.

## 18.3 Tests de paiements simultanés

Scénarios indispensables :

1. Fournisseur A et fournisseur B paient 10 000 XOF au même instant.
2. Les deux paiements sont correctement associés à leurs demandes respectives.
3. Les deux abonnements sont activés indépendamment.
4. Deux webhooks arrivent simultanément pour deux fournisseurs.
5. Le même webhook est reçu plusieurs fois.
6. Deux requêtes tentent de confirmer la même demande.
7. Deux demandes de renouvellement ciblent simultanément le même abonnement.
8. Deux requêtes utilisent la même clé d'idempotence.

Résultats attendus :

- Aucun mélange de fournisseurs.
- Aucun paiement perdu.
- Aucun double abonnement.
- Aucune double prolongation.
- Aucune double confirmation.

Ces tests doivent être exécutés sur le moteur de base de données utilisé en production, avec de véritables transactions concurrentes. Les tests unitaires seuls ne suffisent pas.

## 18.4 Tests de sécurité

- Fournisseur A ne peut pas consulter les demandes de B.
- Fournisseur A ne peut pas télécharger les justificatifs de B.
- Un client ne peut accéder à aucun endpoint d'abonnement.
- Un agent ne peut accéder à aucun endpoint d'abonnement.
- Une référence de transaction déjà utilisée est rejetée ou mise en anomalie.
- Un montant modifié côté frontend est ignoré.
- Un faux webhook est rejeté.
- Un webhook rejoué ne produit aucun effet financier supplémentaire.
- Un paiement incomplet ne déclenche aucune activation.

## 18.5 Tests de confirmation

- Confirmation admin : paiement confirmé + abonnement actif + événement.
- Double confirmation : une seule activation.
- Refus : aucun abonnement créé.
- Annulation : aucun changement financier.
- Expiration : aucun abonnement créé.
- Erreur pendant la confirmation : rollback complet.
- Notification envoyée uniquement après commit.

## 18.6 Non-régression

Exécuter :

- Les tests existants des abonnements.
- Les tests des paiements existants.
- Les tests super administrateur.
- Les tests des statistiques financières.
- `python manage.py check`
- Les vérifications de migrations.
- La génération du schéma OpenAPI.

---

# 19. INTERFACE ET INTÉGRATION FRONTEND

Le frontend doit consommer exclusivement les endpoints autorisés.

## 19.1 Espace fournisseur

Page « Mon abonnement » :

- Présentation des trois plans.
- Comparaison des prix et quotas.
- Souscription et renouvellement.
- Sélection du moyen de paiement.
- Affichage de la référence KLARO.
- Instructions de paiement.
- Dépôt du justificatif.
- Suivi du statut de la demande.
- Historique des paiements.
- Historique des abonnements.
- État de l'abonnement actuel.
- Alertes en cas d'expiration ou de suspension.

Le frontend doit gérer les doubles clics et conserver la même clé d'idempotence lors d'une nouvelle tentative de la même opération.

## 19.2 Espace super administrateur

Prévoir :

- `AdminPlansPage`
- `AdminAbonnementsPage`
- `AdminPaiementsPage`
- `AdminDemandesAbonnementPage`

La gestion des demandes doit proposer :

- Une liste filtrable.
- Le nom et l'identifiant du fournisseur.
- La référence KLARO.
- Le plan.
- Le montant.
- Le moyen de paiement.
- La référence de transaction.
- La date de création.
- Le statut.
- La consultation sécurisée du justificatif.
- La confirmation.
- Le refus avec motif.
- La détection des anomalies.
- L'historique des actions administratives.

La référence KLARO doit être clairement visible pour éviter les confusions entre fournisseurs.

## 19.3 Clients et agents

Aucun écran, aucune route et aucun endpoint de souscription ou de paiement d'abonnement ne doivent être exposés dans leurs espaces.

---

# 20. DÉCOUPAGE DU DÉVELOPPEMENT

## LOT A — SOCLE ABONNEMENT ET PAIEMENT MANUEL

**Priorité : 1**

Développer :

- Audit du code existant.
- Modèle `DemandeAbonnement`.
- Évolution de `PaiementAbonnement`.
- Modèle `WebhookEvenement` pour préparer la suite.
- Contraintes d'intégrité.
- Commande `seed_plans`.
- Création des demandes.
- Clés d'idempotence.
- Références uniques.
- Soumission de preuves.
- Validation et refus par le super administrateur.
- Confirmation atomique.
- Gestion des abonnements et renouvellements.
- Permissions.
- Endpoints.
- Interfaces fournisseur et administrateur.
- Tests automatisés.

**Livrable :** un fournisseur peut demander un abonnement, effectuer un paiement manuel et obtenir l'activation après vérification par le super administrateur.

## LOT B — PAIEMENT EN LIGNE

**Priorité : 2**

Développer :

- Architecture d'abstraction des prestataires.
- Intégration Wave.
- Intégration Orange Money.
- Initiation de paiement.
- Gestion des références prestataire.
- Webhooks signés.
- Déduplication des événements.
- Vérification serveur à serveur.
- Réconciliation des paiements.
- Gestion des anomalies.
- Tests de concurrence.
- Tests de sécurité.

**Livrable :** un fournisseur peut payer en ligne et son abonnement est activé après confirmation fiable du prestataire.

## LOT C — EXPLOITATION ET AUTOMATISATION

**Priorité : 3**

Développer :

- Expiration automatique des demandes.
- Relances automatiques.
- Notifications SMS et email.
- Reçus et factures.
- Tableau de bord des revenus par plan.
- Historique financier avancé.
- Export comptable.
- Renouvellement automatique éventuel.
- Prorata éventuel.
- Traitement des remboursements.

**Livrable :** un système exploitable, traçable et évolutif.

---

# 21. CRITÈRES D'ACCEPTATION FINAUX

Le développement est accepté uniquement si :

1. Les plans Simple, Premium et Pro sont correctement configurés.
2. Le fournisseur ne voit que ses propres abonnements et paiements.
3. Un abonnement n'est jamais activé avant confirmation financière.
4. La confirmation est atomique.
5. Les demandes possèdent des références uniques.
6. Les paiements possèdent des références transactionnelles uniques.
7. Deux fournisseurs peuvent payer simultanément sans confusion.
8. Deux paiements de même montant ne sont pas considérés automatiquement comme des doublons.
9. Un double clic ne crée pas deux demandes.
10. Un webhook rejoué ne crée pas deux paiements.
11. Une confirmation concurrente ne crée pas deux abonnements.
12. Les clients et agents n'ont aucun accès à cette logique.
13. Le montant ne peut pas être falsifié par le frontend.
14. Les paiements confirmés sont conservés dans un historique immuable.
15. Les justificatifs ne sont pas accessibles publiquement.
16. Les opérations administratives sont auditées.
17. Les anomalies financières sont détectées et consultables.
18. Les tests de concurrence passent sur le moteur de base de données cible.
19. Les tests existants restent fonctionnels.
20. La documentation OpenAPI est mise à jour.

---

# 22. CONVENTIONS DE CODE

Tous les nouveaux commentaires, docstrings et TODO doivent être rédigés en français.

Les commentaires doivent expliquer les règles métier et les décisions techniques, et non simplement répéter le code.

Documenter particulièrement :

- Les transactions `transaction.atomic()`.
- Les verrous `select_for_update()`.
- Les clés d'idempotence.
- Les références uniques.
- Les permissions.
- Les transitions d'état.
- Les calculs de montants et de dates.
- Les webhooks.
- La vérification des signatures.
- Les contrôles d'intégrité.
- Les mécanismes de réconciliation.

Les noms des fonctions, variables et modèles doivent rester cohérents avec le vocabulaire métier existant.

Ne pas introduire de code commenté obsolète ou de TODO sans contexte.

---

# 23. POINTS À VALIDER AVANT LE DÉVELOPPEMENT

Les décisions suivantes doivent être confirmées :

1. Les quotas définitifs des plans.
2. La durée et les quotas du plan d'essai.
3. Le premier prestataire à intégrer : Wave ou Orange Money.
4. Les moyens acceptés pour le paiement manuel.
5. Le délai d'expiration : 24 h ou 48 h.
6. Le renouvellement automatique ou manuel.
7. L'existence éventuelle d'une période de grâce.
8. Les règles de changement de plan.
9. La nécessité des reçus et factures dès le premier lot.
10. Les règles de traitement des paiements reçus après expiration.
11. Les seuils éventuels de double validation administrative.

---

# 24. HORS PÉRIMÈTRE DU MVP

Les éléments suivants seront traités dans des lots séparés :

- Remboursements automatiques.
- Comptabilité générale.
- Facturation fiscale.
- Gestion avancée des litiges.
- Prorata des changements de plan.
- Renouvellement automatique.
- Paiements fractionnés.
- Gestion de plusieurs devises.

---

# 25. INSTRUCTION FINALE POUR L'AGENT DE DÉVELOPPEMENT

Avant toute modification du code, effectuer un audit du backend existant.

Identifier précisément :

- Les modèles d'abonnement et de paiement.
- Les services métier.
- Les vues et serializers.
- Les permissions.
- Les relations entre fournisseur, abonnement et paiement.
- Les migrations existantes.
- Les endpoints déjà consommés par le frontend.
- Les statistiques qui dépendent des paiements.
- Les éventuelles opérations qui activent actuellement un abonnement avant confirmation financière.

Ensuite, présenter les fichiers à modifier, les nouveaux fichiers à créer et les migrations prévues.

**Ne pas réécrire inutilement le système existant. Réutiliser ce qui est fiable, faire évoluer ce qui doit l'être et préserver les fonctionnalités déjà opérationnelles.**

Développer par lots, avec tests à chaque étape.

Ne jamais considérer le paiement comme confirmé sur la seule base d'une information fournie par le frontend.

Ne jamais associer un paiement à un fournisseur en utilisant uniquement son montant, son numéro de téléphone ou son heure de transaction.

La règle centrale de KLARO est :

**UNE DEMANDE UNIQUE → UN FOURNISSEUR IDENTIFIÉ → UN PAIEMENT VÉRIFIÉ → UNE ACTIVATION CONTRÔLÉE.**

La priorité absolue est l'intégrité financière, l'isolation des fournisseurs, l'absence de doublons et la traçabilité complète de chaque opération.
