import os
import shutil
import tempfile

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .export_import import (
    ExportStreamer,
    collect_media_files,
    export_data_files,
    import_from_zip,
)
from .forms import CreateUserForm, EditUserForm, LoginForm, SiteSettingsForm
from .models import SiteSettings

User = get_user_model()


def is_staff(user):
    return user.is_staff


# ---------------------------------------------------------------------------
# Authentication views
# ---------------------------------------------------------------------------


def login_view(request):
    """Handle user login via GET (show form) and POST (authenticate)."""
    if request.user.is_authenticated:
        return redirect("projects:list")

    if request.method == "POST":
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            next_url = request.GET.get("next") or request.POST.get("next")
            return redirect(next_url or "projects:list")
    else:
        form = LoginForm(request)

    return render(request, "accounts/login.html", {"form": form})


def logout_view(request):
    """Log the user out (POST only for CSRF safety)."""
    if request.method == "POST":
        logout(request)
    return redirect("accounts:login")


# ---------------------------------------------------------------------------
# Admin dashboard views
# ---------------------------------------------------------------------------


@login_required
@user_passes_test(is_staff, login_url="/accounts/login/")
def admin_dashboard_view(request):
    """
    Staff-only dashboard showing all users, a create-user form, and
    site-wide settings.
    """
    users = User.objects.all()
    create_user_form = CreateUserForm()
    site_settings = SiteSettings.load()
    site_settings_form = SiteSettingsForm(instance=site_settings)

    # Disk storage info
    storage = _get_storage_info()

    return render(
        request,
        "accounts/dashboard.html",
        {
            "users": users,
            "create_user_form": create_user_form,
            "site_settings": site_settings,
            "site_settings_form": site_settings_form,
            "storage": storage,
        },
    )


def _get_storage_info():
    """Return disk usage stats for the media directory."""
    media_root = str(django_settings.MEDIA_ROOT)

    # Disk-level stats
    try:
        disk = shutil.disk_usage(media_root)
        disk_total = disk.total
        disk_used = disk.used
        disk_free = disk.free
    except OSError:
        disk_total = disk_used = disk_free = 0

    # Media directory size (videos + thumbnails)
    media_bytes = 0
    video_count = 0
    for dirpath, _dirnames, filenames in os.walk(media_root):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                media_bytes += os.path.getsize(fp)
            except OSError:
                pass
            if f.endswith(('.webm', '.mp4', '.mkv')):
                video_count += 1

    return {
        'disk_total': disk_total,
        'disk_used': disk_used,
        'disk_free': disk_free,
        'disk_used_pct': round(disk_used / disk_total * 100, 1) if disk_total else 0,
        'media_bytes': media_bytes,
        'video_count': video_count,
    }


@login_required
@user_passes_test(is_staff, login_url="/accounts/login/")
def create_user_view(request):
    """Staff-only view to create a new user account."""
    if request.method != "POST":
        return redirect("accounts:dashboard")

    form = CreateUserForm(request.POST)
    if form.is_valid():
        user = form.save(commit=False)
        user.created_by = request.user
        user.save()
        messages.success(request, f'User "{user.username}" created successfully.')
        return redirect("accounts:dashboard")

    # Re-render the dashboard with form errors.
    users = User.objects.all()
    site_settings = SiteSettings.load()
    site_settings_form = SiteSettingsForm(instance=site_settings)

    return render(
        request,
        "accounts/dashboard.html",
        {
            "users": users,
            "create_user_form": form,
            "site_settings": site_settings,
            "site_settings_form": site_settings_form,
        },
    )


@login_required
@user_passes_test(is_staff, login_url="/accounts/login/")
def edit_user_view(request, user_id):
    """Staff-only view to edit a user's settings."""
    if request.method != "POST":
        return redirect("accounts:dashboard")

    user = get_object_or_404(User, pk=user_id)
    form = EditUserForm(request.POST, instance=user)
    if form.is_valid():
        form.save()
        messages.success(request, f'User "{user.username}" updated successfully.')
    else:
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")

    return redirect("accounts:dashboard")


@login_required
@user_passes_test(is_staff, login_url="/accounts/login/")
def delete_user_view(request, user_id):
    """Staff-only view to delete a user account."""
    if request.method != "POST":
        return redirect("accounts:dashboard")

    user = get_object_or_404(User, pk=user_id)

    # Prevent deleting yourself
    if user == request.user:
        messages.error(request, "You cannot delete your own account.")
        return redirect("accounts:dashboard")

    username = user.username
    user.delete()
    messages.success(request, f'User "{username}" has been deleted.')
    return redirect("accounts:dashboard")


@login_required
@user_passes_test(is_staff, login_url="/accounts/login/")
def reset_password_view(request, user_id):
    """Staff-only view to reset a user's password."""
    if request.method != "POST":
        return redirect("accounts:dashboard")

    user = get_object_or_404(User, pk=user_id)
    new_password = request.POST.get("new_password", "").strip()

    if not new_password:
        messages.error(request, "Password cannot be empty.")
        return redirect("accounts:dashboard")

    if len(new_password) < 6:
        messages.error(request, "Password must be at least 6 characters.")
        return redirect("accounts:dashboard")

    user.set_password(new_password)
    user.save()
    messages.success(request, f'Password for "{user.username}" has been reset.')
    return redirect("accounts:dashboard")


@login_required
@user_passes_test(is_staff, login_url="/accounts/login/")
def site_settings_view(request):
    """Staff-only view to update site-wide settings."""
    if request.method != "POST":
        return redirect("accounts:dashboard")

    site_settings = SiteSettings.load()
    form = SiteSettingsForm(request.POST, instance=site_settings)
    if form.is_valid():
        form.save()
        messages.success(request, "Site settings updated successfully.")
    else:
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")

    return redirect("accounts:dashboard")


# ---------------------------------------------------------------------------
# Export / Import
# ---------------------------------------------------------------------------


@login_required
@user_passes_test(is_staff, login_url="/accounts/login/")
def export_data_view(request):
    """Stream a zip archive containing all data and media files."""
    streamer = ExportStreamer()
    json_entries = list(export_data_files())
    media_entries = collect_media_files()

    response = StreamingHttpResponse(
        streamer.stream_export(json_entries, media_entries),
        content_type="application/zip",
    )
    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    response["Content-Disposition"] = f'attachment; filename="vpm-export-{timestamp}.zip"'
    return response


@login_required
@user_passes_test(is_staff, login_url="/accounts/login/")
def import_data_view(request):
    """Accept an export zip and restore all data and media."""
    if request.method != "POST":
        return redirect("accounts:dashboard")

    archive = request.FILES.get("archive")
    if not archive:
        messages.error(request, "No file uploaded.")
        return redirect("accounts:dashboard")

    # Get a seekable file path for zipfile
    if hasattr(archive, "temporary_file_path"):
        zip_path = archive.temporary_file_path()
    else:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        for chunk in archive.chunks():
            tmp.write(chunk)
        tmp.close()
        zip_path = tmp.name

    try:
        stats = import_from_zip(zip_path)
        messages.success(request, f"Import complete: {stats}.")
    except Exception as e:
        messages.error(request, f"Import failed: {e}")

    return redirect("accounts:dashboard")
