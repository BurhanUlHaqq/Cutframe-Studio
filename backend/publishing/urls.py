from django.urls import path

from . import views


urlpatterns = [
    path("settings/", views.platform_settings, name="publish_settings"),
    path("settings/<str:section>/", views.save_platform_settings, name="publish_save_settings"),
    path("accounts/", views.accounts, name="publish_accounts"),
    path("connect/<str:platform>/", views.connect, name="publish_connect"),
    path("callback/<str:platform>/", views.callback, name="publish_callback"),
    path("finish/", views.finish, name="publish_finish"),
    path("disconnect/<str:platform>/", views.disconnect, name="publish_disconnect"),
    path("posts/", views.posts, name="publish_posts"),
    path("posts/bulk/", views.bulk_posts, name="publish_bulk_posts"),
    path("posts/<uuid:post_id>/<str:action>/", views.post_action, name="publish_action"),
]
