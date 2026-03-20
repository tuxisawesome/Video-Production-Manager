from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm

from .models import SiteSettings

User = get_user_model()


class LoginForm(AuthenticationForm):
    """Login form with styled widgets."""

    username = forms.CharField(
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Username",
                "autofocus": True,
            }
        ),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Password",
            }
        ),
    )


class CreateUserForm(forms.ModelForm):
    """Form for admin users to create new user accounts."""

    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Password",
            }
        ),
    )
    password2 = forms.CharField(
        label="Confirm Password",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Confirm password",
            }
        ),
    )

    class Meta:
        model = User
        fields = ["username", "email", "max_recording_seconds"]
        widgets = {
            "username": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Username",
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Email address",
                }
            ),
            "max_recording_seconds": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": "0",
                }
            ),
        }

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Passwords do not match.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class EditUserForm(forms.ModelForm):
    """Form for admin users to edit an existing user's settings."""

    class Meta:
        model = User
        fields = ["max_recording_seconds", "is_active"]
        widgets = {
            "max_recording_seconds": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": "0",
                }
            ),
            "is_active": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                }
            ),
        }


class SiteSettingsForm(forms.ModelForm):
    """Form for editing site-wide settings."""

    class Meta:
        model = SiteSettings
        fields = ["max_recordings_per_project"]
        widgets = {
            "max_recordings_per_project": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": "0",
                }
            ),
        }
