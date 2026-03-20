from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render

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

    return render(
        request,
        "accounts/dashboard.html",
        {
            "users": users,
            "create_user_form": create_user_form,
            "site_settings": site_settings,
            "site_settings_form": site_settings_form,
        },
    )


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
