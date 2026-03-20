from django import forms

from .models import Project


class ProjectForm(forms.ModelForm):
    """Form for creating and editing projects."""

    class Meta:
        model = Project
        fields = ["name", "description"]
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Project name",
            }),
            "description": forms.Textarea(attrs={
                "class": "form-control",
                "placeholder": "Optional description",
                "rows": 3,
            }),
        }


class VideoUploadForm(forms.Form):
    """Form for manually uploading a video file."""

    file = forms.FileField(
        widget=forms.ClearableFileInput(attrs={
            "class": "form-control",
            "accept": "video/*",
        }),
    )
