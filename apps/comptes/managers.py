from django.contrib.auth.models import BaseUserManager


class UtilisateurManager(BaseUserManager):
    def create_user(self, telephone, password=None, **extra_fields):
        if not telephone:
            raise ValueError("Le numéro de téléphone est obligatoire")
        extra_fields.setdefault("username", telephone)
        user = self.model(telephone=telephone, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, telephone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields["role"] = "super_admin"
        return self.create_user(telephone, password, **extra_fields)
    