import os
from pathlib import Path

from django.core.management.utils import get_random_secret_key
from dotenv import load_dotenv
from filelock import FileLock

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR.parent
load_dotenv(PROJECT_DIR / ".env")

STUDIO_DATA_DIR = Path(os.getenv("STUDIO_DATA_DIR") or PROJECT_DIR / ".studio").resolve()
STUDIO_DATA_DIR.mkdir(parents=True, exist_ok=True)


def local_secret_key():
    path = STUDIO_DATA_DIR / "django.secret"
    with FileLock(STUDIO_DATA_DIR / "django.secret.lock"):
        if not path.exists():
            pending = path.with_suffix(".pending")
            pending.touch(mode=0o600, exist_ok=True)
            pending.write_text(get_random_secret_key(), encoding="utf-8")
            pending.replace(path)
        return path.read_text(encoding="utf-8")


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY") or local_secret_key()
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],backend").split(",")
CSRF_TRUSTED_ORIGINS = list(filter(None, os.getenv(
    "DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:5031,http://127.0.0.1:5031",
).split(",")))
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_NAME = "cutframe_session"
CSRF_COOKIE_NAME = "cutframe_csrf"
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = os.getenv("DJANGO_SECURE_COOKIES", "0") == "1"
ROOT_URLCONF = "studio.urls"
WSGI_APPLICATION = "studio.wsgi.application"

INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.sessions", "studio"]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "studio.uploads.UploadLimitMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]
def studio_database():
    host = os.getenv("STUDIO_DB_HOST")
    if host:
        password_file = os.getenv("STUDIO_DB_PASSWORD_FILE")
        return {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": host,
            "PORT": os.getenv("STUDIO_DB_PORT", "5432"),
            "NAME": os.getenv("STUDIO_DB_NAME", "cutframe"),
            "USER": os.getenv("STUDIO_DB_USER", "cutframe"),
            "PASSWORD": (Path(password_file).read_text(encoding="utf-8").strip() if password_file
                         else os.getenv("STUDIO_DB_PASSWORD", "")),
            "OPTIONS": {"connect_timeout": 5},
        }
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": STUDIO_DATA_DIR / "studio.sqlite3",
        "OPTIONS": {"timeout": 20},
    }


DATABASES = {"default": studio_database()}
DATA_UPLOAD_MAX_MEMORY_SIZE = 16 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES = 1
STUDIO_MAX_UPLOAD_BYTES = int(os.getenv("STUDIO_MAX_UPLOAD_BYTES", str(2 * 1024**3)))
FILE_UPLOAD_TEMP_DIR = STUDIO_DATA_DIR / "upload-tmp"
FILE_UPLOAD_TEMP_DIR.mkdir(exist_ok=True)
TIME_ZONE = "UTC"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
