import json
from django import template

register = template.Library()

@register.filter
def get_json_field(entity, field_name):
    """
    A template filter that retrieves and parses a JSON field from an entity.
    """
    value = getattr(entity, field_name, None)
    if value:
        try:
            return json.loads(value)
        except Exception:
            return {}
    # Return an empty list for 'scopes', or an empty dict for other fields.
    return [] if field_name == "scopes" else {}
