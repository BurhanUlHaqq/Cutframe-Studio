from django.urls import include, path

from . import library, views

urlpatterns = [
    path("api/health/", views.health, name="health"),
    path("api/publishing/", include("publishing.urls")),
    path("api/downloads/", include("downloads.urls")),
    path("api/session/", views.session, name="session"),
    path("api/preferences/", views.preferences, name="preferences"),
    path("api/storage-settings/", views.storage_settings, name="storage_settings"),
    path("api/ai-settings/", views.ai_settings, name="ai_settings"),
    path("api/ai-settings/check/", views.ai_connection, name="ai_connection"),
    path("api/ai-settings/models/", views.ai_connection, {"discover": True}, name="ai_models"),
    path("api/description-settings/", views.description_settings, name="description_settings"),
    path("api/thumbnail-ai-settings/", views.thumbnail_ai_settings, name="thumbnail_ai_settings"),
    path("api/thumbnail-ai-settings/check/", views.thumbnail_ai_connection, name="thumbnail_ai_check"),
    path("api/thumbnail-ai-settings/models/", views.thumbnail_ai_connection, {"discover": True}, name="thumbnail_ai_models"),
    path("api/library/", library.library, name="render_library"),
    path("api/library/delete/", library.delete_renders, name="delete_renders"),
    path("api/library/clear-work/", library.delete_renders, {"work_only": True}, name="clear_render_work"),
    path("api/jobs/", views.jobs, name="jobs"),
    path("api/jobs/upload/", views.upload_job, name="upload_job"),
    path("api/jobs/<uuid:job_id>/", views.detail, name="detail"),
    path("api/jobs/<uuid:job_id>/alternate/", views.create_alternate, name="create_alternate"),
    path("api/jobs/<uuid:job_id>/cancel/", views.cancel_render, name="cancel_render"),
    path("api/jobs/<uuid:job_id>/skip-ai-covers/", views.skip_ai_covers, name="skip_ai_covers"),
    path("api/jobs/<uuid:job_id>/retry/", views.retry, name="retry"),
    path("api/jobs/<uuid:job_id>/clips/<int:index>/", views.edit_clip, name="edit_clip"),
    path("api/jobs/<uuid:job_id>/clips/<int:index>/description/", views.draft_description, name="draft_description"),
    path("api/jobs/<uuid:job_id>/clips/<int:index>/thumbnail/<str:action>/", views.edit_thumbnail, name="edit_thumbnail"),
    path("api/jobs/<uuid:job_id>/archive/", views.archive, name="archive"),
    path("api/jobs/<uuid:job_id>/media/<int:index>/<str:kind>/", views.media, name="media"),
]
