from django.db import models


class LegacySourceMap(models.Model):
    source_system = models.CharField(max_length=50, default="legacy")
    source_type = models.CharField(max_length=50)
    source_id = models.CharField(max_length=100)
    target_model = models.CharField(max_length=100)
    target_id = models.BigIntegerField()
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["source_type", "source_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_system", "source_type", "source_id"],
                name="legacy_source_map_source_unique",
            )
        ]

    def __str__(self):
        return f"{self.source_type}:{self.source_id} -> {self.target_model}:{self.target_id}"
