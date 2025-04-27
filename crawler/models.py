from django.db import models
import json

class Session(models.Model):
    start_time = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, default='running')

class Entity(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='entities', null=True, blank=True)
    url = models.URLField(unique=True)
    university = models.TextField(blank=True)
    location = models.TextField(blank=True)  # JSON string
    website = models.URLField(blank=True)
    edurank = models.TextField(blank=True)   # JSON string
    department = models.TextField(blank=True)  # JSON string
    publications = models.TextField(blank=True)  # JSON string
    related = models.TextField(blank=True)
    point_of_contact = models.TextField(blank=True)  # JSON string
    scopes = models.TextField(blank=True)    # JSON string
    research_abstract = models.TextField(blank=True)
    lab_equipment = models.TextField(blank=True)  # JSON string
    timestamp = models.DateTimeField(auto_now_add=True)

    def set_json_field(self, field_name, value):
        setattr(self, field_name, json.dumps(value))

    def get_json_field(self, field_name):
        value = getattr(self, field_name)
        return json.loads(value) if value else ({} if 'scopes' not in field_name else [])

    class Meta:
        db_table = 'entities'