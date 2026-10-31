import logging

logger = logging.getLogger(__name__)


def envoyer_sms(numero, message):
    """
    Point d'intégration avec le prestataire SMS (Twilio, Orange SMS API,
    Wave, etc.). Pour l'instant, ne fait que logguer le message pour ne pas
    bloquer le développement — à remplacer par un véritable appel API.

    Exemple avec Twilio :
        from twilio.rest import Client as TwilioClient
        client = TwilioClient(settings.TWILIO_SID, settings.TWILIO_TOKEN)
        client.messages.create(to=numero, from_=settings.TWILIO_FROM, body=message)
    """
    logger.info("SMS -> %s : %s", numero, message)