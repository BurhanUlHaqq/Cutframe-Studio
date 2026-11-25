from django.urls import include, path

from . import views

urlpatterns = [
    path("api/health/", views.health, name="health"),
    path("api/publishing/", include("publishing.urls")),
    path("api/session/", views.session, name="session"),
    path("api/preferences/", views.preferences, name="preferences"),
    path("api/storage-settings/", views.storage_settings, name="storage_settings"),
    path("api/ai-settings/", views.ai_settings, name="ai_settings"),
    path("api/ai-settings/check/", views.ai_connection, name="ai_connection"),
    path("api/ai-settings/models/", views.ai_connection, {"discover": True}, name="ai_models"),
    path("api/thumbnail-ai-settings/", views.thumbnail_ai_settings, name="thumbnail_ai_settings"),
    path("api/jobs/", views.jobs, name="jobs"),
    path("api/jobs/upload/", views.upload_job, name="upload_job"),
    path("api/jobs/<uuid:job_id>/", views.detail, name="detail"),
    path("api/jobs/<uuid:job_id>/retry/", views.retry, name="retry"),
    path("api/jobs/<uuid:job_id>/clips/<int:index>/", views.edit_clip, name="edit_clip"),
    path("api/jobs/<uuid:job_id>/clips/<int:index>/thumbnail/<str:action>/", views.edit_thumbnail, name="edit_thumbnail"),
    path("api/jobs/<uuid:job_id>/archive/", views.archive, name="archive"),
    path("api/jobs/<uuid:job_id>/media/<int:index>/<str:kind>/", views.media, name="media"),
]
