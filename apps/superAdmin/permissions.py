from rest_framework.permissions import BasePermission


class IsSuperAdmin(BasePermission):
    """Réserve l'accès aux utilisateurs ayant le rôle ``super_admin``."""

    message = "Accès réservé au super administrateur."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == "super_admin"
        )
