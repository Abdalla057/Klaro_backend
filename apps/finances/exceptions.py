from rest_framework import status
from rest_framework.exceptions import APIException


class EtatInvalide(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "L'opération n'est pas autorisée dans l'état actuel."
    default_code = "etat_invalide"


class OperationDejaEffectuee(EtatInvalide):
    default_detail = "Cette opération financière a déjà été effectuée."
    default_code = "operation_deja_effectuee"
