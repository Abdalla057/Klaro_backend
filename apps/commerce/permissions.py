from rest_framework.permissions import BasePermission


class EstFournisseurOuClient(BasePermission):
    """Autorise uniquement les rôles fournisseur et client.

    Les agents et les super administrateurs n'ont pas accès aux factures
    commerciales via cette permission.
    """

    message = "Cette action est réservée aux fournisseurs et aux clients."

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.user.role == "fournisseur":
            return getattr(request.user, "fournisseur_profile", None) is not None
        if request.user.role == "client":
            return getattr(request.user, "client_profile", None) is not None
        return False


class EstClientCommerce(BasePermission):
    message = "Cette action est réservée aux clients."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == "client"
            and getattr(request.user, "client_profile", None) is not None
        )


class EstFournisseurCommerce(BasePermission):
    message = "Cette action est réservée aux fournisseurs."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == "fournisseur"
            and getattr(request.user, "fournisseur_profile", None) is not None
        )
