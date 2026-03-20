from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("dashboard/", views.admin_dashboard_view, name="dashboard"),
    path("dashboard/create-user/", views.create_user_view, name="create_user"),
    path("dashboard/settings/", views.site_settings_view, name="site_settings"),
    path("dashboard/user/<int:user_id>/", views.edit_user_view, name="edit_user"),
    path("dashboard/user/<int:user_id>/delete/", views.delete_user_view, name="delete_user"),
    path("dashboard/user/<int:user_id>/reset-password/", views.reset_password_view, name="reset_password"),
    path("dashboard/export/", views.export_data_view, name="export_data"),
    path("dashboard/import/", views.import_data_view, name="import_data"),
]
