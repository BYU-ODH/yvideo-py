from django import template

from ..utils import ANNOTATION_ICONS

register = template.Library()


@register.filter
def annotation_icon(annotation_type):
    return ANNOTATION_ICONS.get(annotation_type, "")
