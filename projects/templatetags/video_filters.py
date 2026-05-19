"""Template filters used across the gallery / sidebar templates."""
from django import template

register = template.Library()


@register.filter(name="duration_mmss")
def duration_mmss(seconds):
    """
    Format a number of seconds as:
        m:ss      for clips under an hour     (e.g. 0:07, 2:01, 14:30)
        h:mm:ss   for clips an hour or longer (e.g. 1:02:15)

    Returns "--" when the value is missing / falsy.
    """
    try:
        total = float(seconds)
    except (TypeError, ValueError):
        return "--"
    if total <= 0:
        return "--"

    total = int(round(total))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
