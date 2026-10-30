from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'dev-only-not-a-secret'
DEBUG = True
ALLOWED_HOSTS = [
    'localhost',
    '127.0.0.1',
    '63ce-45-140-167-194.ngrok-free.app',  
]

CSRF_TRUSTED_ORIGINS = [
    'https://63ce-45-140-167-194.ngrok-free.app' # Ваш точный адрес ngrok
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'nested_admin',
    'rest_framework',
    'drf_spectacular',
    'drf_spectacular_sidecar',
    'workflow',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
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

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'Europe/Moscow'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'admin:login'

# Модели хоста, с которыми работает движок workflow.
# Заявка обязана иметь поля: project, status, _status, workflow_status, reviewed_by, reviewed_at.
WORKFLOW_SUBJECT_MODEL = 'core.TicketApplication'
WORKFLOW_PROJECT_MODEL = 'core.Project'

# Аналог PDC_SHOW_EXPEL_AND_FINISH_BUTTONS: используется guard-ом setting_flag в демо-конфиге.
SHOW_EXPEL_AND_FINISH_BUTTONS = True

DEMO_API_KEY = 'demo-key'

SPECTACULAR_SETTINGS = {
    'TITLE': 'cat-workflow API',
    'VERSION': '1.0.0',
    # Swagger UI из пакета, а не с CDN: страница документации работает без внешней сети.
    'SWAGGER_UI_DIST': 'SIDECAR',
    'SWAGGER_UI_SETTINGS': {
        'deepLinking': True,
        'persistAuthorization': True,
        'displayRequestDuration': True,
    },
}

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'loggers': {
        'workflow': {'handlers': ['console'], 'level': 'INFO'},
        'core': {'handlers': ['console'], 'level': 'INFO'},
    },
}
