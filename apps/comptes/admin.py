from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from .models import Utilisateur


class UtilisateurCreationForm(UserCreationForm):
    class Meta:
        model = Utilisateur
        fields = ("telephone", "role")

    def save(self, commit=True):
        utilisateur = super().save(commit=False)
        utilisateur.username = utilisateur.telephone
        if commit:
            utilisateur.save()
        return utilisateur


class UtilisateurChangeForm(UserChangeForm):
    class Meta:
        model = Utilisateur
        fields = "__all__"


class UtilisateurAdmin(UserAdmin):
    model = Utilisateur
    form = UtilisateurChangeForm
    add_form = UtilisateurCreationForm

    list_display = ("telephone", "role", "is_staff", "is_active")
    list_filter = ("role", "is_staff", "is_active")
    ordering = ("telephone",)
    search_fields = ("telephone", "first_name", "last_name")

    fieldsets = (
        (None, {"fields": ("telephone", "password")}),
        (
            "Informations",
            {"fields": ("first_name", "last_name", "email", "role")},
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_staff",
                    "is_active",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "telephone",
                    "role",
                    "password1",
                    "password2",
                ),
            },
        ),
    )


admin.site.register(Utilisateur, UtilisateurAdmin)
