from rest_framework.permissions import BasePermission


class EstFournisseur(BasePermission):
    message = "Cette action est réservée au fournisseur."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == "fournisseur"
            and hasattr(request.user, "fournisseur_profile")
        )


class EstAgent(BasePermission):
    message = "Cette action est réservée à un agent."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == "agent"
            and hasattr(request.user, "agent_profile")
        )


class EstClient(BasePermission):
    message = "Cette action est réservée à un client."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == "client"
            and hasattr(request.user, "client_profile")
        )
