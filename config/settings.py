import sys
from datetime import timedelta
from pathlib import Path

import dj_database_url
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config('DEBUG', default=False, cast=bool)

TESTING = 'test' in sys.argv

ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='127.0.0.1,localhost', cast=Csv())
RENDER_EXTERNAL_HOSTNAME = config('RENDER_EXTERNAL_HOSTNAME', default='')
if RENDER_EXTERNAL_HOSTNAME and RENDER_EXTERNAL_HOSTNAME not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='', cast=Csv())
if RENDER_EXTERNAL_HOSTNAME:
    origine_render = f'https://{RENDER_EXTERNAL_HOSTNAME}'
    if origine_render not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(origine_render)

FRONTEND_URL = config('FRONTEND_URL', default='http://localhost:5173').rstrip('/')

CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', default='', cast=Csv())
if not CORS_ALLOWED_ORIGINS:
    CORS_ALLOWED_ORIGINS = [
        'http://localhost:5173',
        'http://127.0.0.1:5173',
    ]
if FRONTEND_URL and FRONTEND_URL not in CORS_ALLOWED_ORIGINS:
    CORS_ALLOWED_ORIGINS.append(FRONTEND_URL)
CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
]
CORS_ALLOW_METHODS = [
    'DELETE',
    'GET',
    'OPTIONS',
    'PATCH',
    'POST',
    'PUT',
]


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'drf_spectacular',
    'apps.comptes',
    'apps.fournisseurs',
    'apps.clients',
    'apps.agents',
    'apps.encaissements',
    'apps.finances',
    'apps.abonnements',
    'apps.superAdmin',
    'apps.commerce',
]
AUTH_USER_MODEL = 'comptes.Utilisateur'

REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'EXCEPTION_HANDLER': 'config.exception_handler.api_exception_handler',
    'DEFAULT_THROTTLE_RATES': {
        'login': '5/min',
        'refresh': '30/min',
        'activation': '5/min',
        'renvoi_code': '3/10min',
        'inscription': '10/min',
        'password_change': '5/min',
        'facture_lien': '30/min',
    },
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    'TOKEN_OBTAIN_SERIALIZER': 'apps.comptes.serializers.ConnexionSerializer',
}

if not DEBUG and not TESTING:
    SECURE_SSL_REDIRECT = config('SECURE_SSL_REDIRECT', default=True, cast=bool)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_REFERRER_POLICY = 'same-origin'

SPECTACULAR_SETTINGS = {
    'TITLE': 'API - App Remboursement',
    'DESCRIPTION': 'API pour la gestion des fournisseurs, clients et agents d\'encaissement',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'ENUM_NAME_OVERRIDES': {
        'AbonnementStatutEnum': 'apps.abonnements.models.Abonnement.Statut',
        'EncaissementStatutEnum': 'apps.encaissements.models.Encaissement.Statut',
        'TransactionFinanciereTypeEnum': (
            'apps.finances.models.TransactionFinanciere.Type'
        ),
        'TransactionFinanciereSensEnum': (
            'apps.finances.models.TransactionFinanciere.Sens'
        ),
        'TransactionFinanciereStatutEnum': (
            'apps.finances.models.TransactionFinanciere.Statut'
        ),
        'DemandeDecaissementStatutEnum': (
            'apps.finances.models.DemandeDecaissement.Statut'
        ),
    },
}

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASE_URL = config('DATABASE_URL', default='')
DB_CONN_MAX_AGE = config(
    'DB_CONN_MAX_AGE',
    default=60 if DATABASE_URL else 0,
    cast=int,
)
DB_SSLMODE = config('DB_SSLMODE', default='')

if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=DB_CONN_MAX_AGE,
        )
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': config('BD_NAME'),
            'USER': config('BD_USER'),
            'PASSWORD': config('BD_PASSWORD'),
            'HOST': config('BD_HOST'),
            'PORT': config('BD_PORT'),
            'CONN_MAX_AGE': DB_CONN_MAX_AGE,
        }
    }

if DB_SSLMODE:
    DATABASES['default'].setdefault('OPTIONS', {})['sslmode'] = DB_SSLMODE



# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = config('MEDIA_URL', default='/media/')
MEDIA_ROOT = BASE_DIR / 'media'

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

USE_S3_STORAGE = config('USE_S3_STORAGE', default=False, cast=bool)
if USE_S3_STORAGE:
    AWS_ACCESS_KEY_ID = config('AWS_ACCESS_KEY_ID', default='')
    AWS_SECRET_ACCESS_KEY = config('AWS_SECRET_ACCESS_KEY', default='')
    AWS_STORAGE_BUCKET_NAME = config('AWS_STORAGE_BUCKET_NAME')
    AWS_S3_REGION_NAME = config('AWS_S3_REGION_NAME', default='')
    AWS_S3_ENDPOINT_URL = config('AWS_S3_ENDPOINT_URL', default='') or None
    AWS_S3_CUSTOM_DOMAIN = config('AWS_S3_CUSTOM_DOMAIN', default='') or None
    AWS_QUERYSTRING_AUTH = config('AWS_QUERYSTRING_AUTH', default=True, cast=bool)
    AWS_S3_FILE_OVERWRITE = False
    STORAGES['default'] = {'BACKEND': 'storages.backends.s3.S3Storage'}
    if AWS_S3_CUSTOM_DOMAIN and not config('MEDIA_URL', default=''):
        MEDIA_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/media/'

# Abonnements et paiements
# Le paiement en ligne reste désactivé par défaut jusqu'à validation des
# contrats prestataires et des tests de sécurité.
ABONNEMENT_PAIEMENT_EN_LIGNE_ACTIF = config(
    'ABONNEMENT_PAIEMENT_EN_LIGNE_ACTIF',
    default=False,
    cast=bool,
)
ABONNEMENT_DEMANDE_EXPIRATION_HEURES = config(
    'ABONNEMENT_DEMANDE_EXPIRATION_HEURES',
    default=24,
    cast=int,
)
ABONNEMENT_PAIEMENT_PAYS_ACTIFS = {
    valeur.strip().upper()
    for valeur in config(
        'ABONNEMENT_PAIEMENT_PAYS_ACTIFS',
        default='SN',
    ).split(',')
    if valeur.strip()
}
ABONNEMENT_TAUX_XOF_GNF = config(
    'ABONNEMENT_TAUX_XOF_GNF',
    default='0',
)
PAIEMENT_WEBHOOK_TOLERANCE_SECONDES = config(
    'PAIEMENT_WEBHOOK_TOLERANCE_SECONDES',
    default=300,
    cast=int,
)
PAIEMENT_TIMEOUT_SECONDS = config(
    'PAIEMENT_TIMEOUT_SECONDS',
    default=8,
    cast=int,
)
PAIEMENT_PRESTATAIRES = {
    'paytech': {
        'SN': {
            'api_key': config('PAYTECH_SN_API_KEY', default=''),
            'api_secret': config('PAYTECH_SN_API_SECRET', default=''),
            'environnement': config('PAYTECH_ENV', default='test'),
            'base_url': config(
                'PAYTECH_BASE_URL',
                default='https://paytech.sn/api',
            ),
            'ipn_url': config('PAYTECH_SN_IPN_URL', default=''),
            'target_payment': config('PAYTECH_TARGET_PAYMENT', default=''),
            'timeout_seconds': config(
                'PAYTECH_TIMEOUT_SECONDS',
                default=PAIEMENT_TIMEOUT_SECONDS,
                cast=int,
            ),
        },
        'CI': {
            'api_key': config('PAYTECH_CI_API_KEY', default=''),
            'api_secret': config('PAYTECH_CI_API_SECRET', default=''),
            'environnement': config('PAYTECH_ENV', default='test'),
            'base_url': config(
                'PAYTECH_BASE_URL',
                default='https://paytech.sn/api',
            ),
            'ipn_url': config('PAYTECH_CI_IPN_URL', default=''),
            'target_payment': config('PAYTECH_TARGET_PAYMENT', default=''),
            'timeout_seconds': config(
                'PAYTECH_TIMEOUT_SECONDS',
                default=PAIEMENT_TIMEOUT_SECONDS,
                cast=int,
            ),
        },
    },
    'wave': {
        'SN': {
            'api_key': config('WAVE_SN_API_KEY', default=''),
            'signing_secret': config('WAVE_SN_SIGNING_SECRET', default=''),
            'webhook_secret': config('WAVE_SN_WEBHOOK_SECRET', default=''),
            'base_url': config(
                'WAVE_SN_BASE_URL',
                default='https://api.wave.com',
            ),
        },
        'CI': {
            'api_key': config('WAVE_CI_API_KEY', default=''),
            'signing_secret': config('WAVE_CI_SIGNING_SECRET', default=''),
            'webhook_secret': config('WAVE_CI_WEBHOOK_SECRET', default=''),
            'base_url': config(
                'WAVE_CI_BASE_URL',
                default='https://api.wave.com',
            ),
        },
        'GN': {
            'api_key': config('WAVE_GN_API_KEY', default=''),
            'signing_secret': config('WAVE_GN_SIGNING_SECRET', default=''),
            'webhook_secret': config('WAVE_GN_WEBHOOK_SECRET', default=''),
            'base_url': config(
                'WAVE_GN_BASE_URL',
                default='https://api.wave.com',
            ),
        },
    },
    'orange_money': {
        'SN': {
            'client_id': config('ORANGE_SN_CLIENT_ID', default=''),
            'client_secret': config('ORANGE_SN_CLIENT_SECRET', default=''),
            'merchant_key': config('ORANGE_SN_MERCHANT_KEY', default=''),
            'webhook_secret': config('ORANGE_SN_WEBHOOK_SECRET', default=''),
            'base_url': config('ORANGE_SN_BASE_URL', default=''),
        },
        'CI': {
            'client_id': config('ORANGE_CI_CLIENT_ID', default=''),
            'client_secret': config('ORANGE_CI_CLIENT_SECRET', default=''),
            'merchant_key': config('ORANGE_CI_MERCHANT_KEY', default=''),
            'webhook_secret': config('ORANGE_CI_WEBHOOK_SECRET', default=''),
            'base_url': config('ORANGE_CI_BASE_URL', default=''),
        },
        'GN': {
            'client_id': config('ORANGE_GN_CLIENT_ID', default=''),
            'client_secret': config('ORANGE_GN_CLIENT_SECRET', default=''),
            'merchant_key': config('ORANGE_GN_MERCHANT_KEY', default=''),
            'webhook_secret': config('ORANGE_GN_WEBHOOK_SECRET', default=''),
            'base_url': config('ORANGE_GN_BASE_URL', default=''),
        },
    },
}
PAIEMENT_WEBHOOK_SECRETS = {
    'wave': config(
        'WAVE_WEBHOOK_SECRET',
        default=config('WAVE_SN_WEBHOOK_SECRET', default=''),
    ),
    'orange_money': config('ORANGE_MONEY_WEBHOOK_SECRET', default=''),
}

# Notifications commerciales (SMS / WhatsApp)
# Aucun envoi n'est simulé : si ces variables sont absentes, l'envoi est
# marqué comme échoué avec une erreur explicite.
SMS_API_URL = config('SMS_API_URL', default='')
SMS_API_KEY = config('SMS_API_KEY', default='')
SMS_SENDER = config('SMS_SENDER', default='')
WHATSAPP_API_URL = config(
    'WHATSAPP_API_URL',
    default='https://graph.facebook.com/v20.0',
)
WHATSAPP_TOKEN = config('WHATSAPP_TOKEN', default='')
WHATSAPP_PHONE_ID = config('WHATSAPP_PHONE_ID', default='')
BACKEND_URL = config('BACKEND_URL', default='').rstrip('/')
if not BACKEND_URL:
    if RENDER_EXTERNAL_HOSTNAME:
        BACKEND_URL = f'https://{RENDER_EXTERNAL_HOSTNAME}'
    else:
        BACKEND_URL = 'http://127.0.0.1:8000'
NOTIFICATION_HTTP_TIMEOUT = config(
    'NOTIFICATION_HTTP_TIMEOUT',
    default=5,
    cast=int,
)
NOTIFICATION_TENTATIVES_MAX = config(
    'NOTIFICATION_TENTATIVES_MAX',
    default=5,
    cast=int,
)
FACTURE_LIEN_DUREE_SECONDES = config(
    'FACTURE_LIEN_DUREE_SECONDES',
    default=60 * 60 * 24 * 30,
    cast=int,
)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'console': {
            'format': '{asctime} {levelname} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'console',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': config('LOG_LEVEL', default='INFO'),
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': config('DJANGO_LOG_LEVEL', default='INFO'),
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'apps.comptes.sms': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'apps.fournisseurs.sms': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
}

# Default primary key field type
# https://docs.djangoproject.com/en/6.0/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
