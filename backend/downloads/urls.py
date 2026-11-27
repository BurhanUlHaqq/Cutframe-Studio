from django.urls import path

from . import views


app_name = "downloads"
urlpatterns = [
    path("", views.downloads, name="list"),
    path("<uuid:job_id>/file/", views.file, name="file"),
    path("<uuid:job_id>/items/", views.items, name="items"),
    path("<uuid:job_id>/archive/", views.archive, name="archive"),
    path("<uuid:job_id>/<str:command>/", views.action, name="action"),
]
