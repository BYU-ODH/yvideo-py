from django import template

from ..utils import ANNOTATION_ICONS
from ..utils import seconds2hms

register = template.Library()


@register.filter
def annotation_icon(annotation_type):
    return ANNOTATION_ICONS[annotation_type]


@register.filter
def hms_time(seconds):
    """Render editor time values consistently as H:MM:SS.SS."""
    return seconds2hms(float(seconds))
