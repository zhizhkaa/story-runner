from django.urls import path

from . import views


app_name = "stories"

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz/", views.healthz, name="healthz"),
    path("runs/<uuid:public_id>/", views.run_detail, name="run_detail"),
    path("runs/<uuid:public_id>/preview.png", views.run_preview, name="run_preview"),
    path("active/<uuid:public_id>/claim/", views.claim_select, name="claim_select"),
    path("claims/<uuid:public_id>/", views.claim_work, name="claim_work"),
    path("manage/login/", views.manage_login, name="manage_login"),
    path("manage/logout/", views.manage_logout, name="manage_logout"),
    path("manage/template/", views.manage_template, name="manage_template"),
    path("manage/", views.manage_dashboard, name="manage_dashboard"),
]
