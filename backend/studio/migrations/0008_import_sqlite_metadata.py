"""Import the previous Docker storage database once when PostgreSQL starts."""

import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC

from django.conf import settings
from django.db import migrations
from django.utils import timezone


def sqlite_value(field, value):
    if field.get_internal_type() == "JSONField":
        return json.loads(value) if value is not None else None
    value = field.to_python(value)
    if field.get_internal_type() == "DateTimeField" and value is not None and timezone.is_naive(value):
        return timezone.make_aware(value, UTC)
    return value


def import_sqlite_metadata(apps, schema_editor):
    destination = schema_editor.connection
    if destination.vendor != "postgresql" or destination.settings_dict["NAME"] != os.getenv("STUDIO_DB_NAME", "cutframe"):
        return
    legacy = settings.STUDIO_DATA_DIR / "studio.sqlite3"
    if not legacy.is_file():
        return

    models = [
        apps.get_model("studio", "Job"),
        apps.get_model("studio", "WorkspacePreferences"),
        apps.get_model("studio", "WorkspaceAISettings"),
        apps.get_model("sessions", "Session"),
    ]
    if any(model.objects.using(destination.alias).exists() for model in models):
        return

    quote = destination.ops.quote_name
    with closing(sqlite3.connect(f"{legacy.as_uri()}?mode=ro", uri=True)) as source, destination.cursor() as target:
        source.row_factory = sqlite3.Row
        source.execute("BEGIN")
        tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for model in models:
            table = model._meta.db_table
            if table not in tables:
                continue
            fields = model._meta.concrete_fields
            columns = ", ".join(quote(field.column) for field in fields)
            placeholders = ", ".join(["%s"] * len(fields))
            insert = f"INSERT INTO {quote(table)} ({columns}) VALUES ({placeholders})"
            with closing(source.execute(f"SELECT * FROM {quote(table)}")) as rows:
                available = {column[0] for column in rows.description}
                while batch := rows.fetchmany(256):
                    values = [
                        [
                            field.get_db_prep_save(
                                sqlite_value(field, row[field.column]) if field.column in available else field.get_default(),
                                connection=destination,
                            )
                            for field in fields
                        ]
                        for row in batch
                    ]
                    target.executemany(insert, values)


class Migration(migrations.Migration):
    dependencies = [("studio", "0007_streamer_framing"), ("sessions", "0001_initial")]
    operations = [migrations.RunPython(import_sqlite_metadata, migrations.RunPython.noop)]
