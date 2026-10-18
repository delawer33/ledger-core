"""Append-only repair audit: IncidentRepair replaces the four repaired_* fields of Incident."""

import django.db.models.deletion
from django.db import migrations, models

LEGACY_ACTOR = "unknown (before actor was recorded)"

TRIGGER = """
CREATE FUNCTION ledger_incidentrepair_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ledger_incidentrepair is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER ledger_incidentrepair_no_update_delete
    BEFORE UPDATE OR DELETE ON ledger_incidentrepair
    FOR EACH ROW EXECUTE FUNCTION ledger_incidentrepair_immutable();
"""

TRIGGER_REVERSE = """
DROP TRIGGER ledger_incidentrepair_no_update_delete ON ledger_incidentrepair;
DROP FUNCTION ledger_incidentrepair_immutable();
"""


def move_repairs_forward(apps, schema_editor):
    Incident = apps.get_model("ledger", "Incident")
    IncidentRepair = apps.get_model("ledger", "IncidentRepair")
    for incident in Incident.objects.filter(repaired_at__isnull=False):
        repair = IncidentRepair.objects.create(
            incident=incident,
            repaired_from=incident.repaired_from,
            repaired_to=incident.repaired_to,
            note=incident.repair_note,
            actor=LEGACY_ACTOR,
        )
        IncidentRepair.objects.filter(pk=repair.pk).update(repaired_at=incident.repaired_at)


def move_repairs_back(apps, schema_editor):
    Incident = apps.get_model("ledger", "Incident")
    IncidentRepair = apps.get_model("ledger", "IncidentRepair")
    for repair in IncidentRepair.objects.order_by("id"):  # the last one wins
        Incident.objects.filter(pk=repair.incident_id).update(
            repaired_from=repair.repaired_from,
            repaired_to=repair.repaired_to,
            repaired_at=repair.repaired_at,
            repair_note=repair.note,
        )


class Migration(migrations.Migration):
    dependencies = [("ledger", "0005_incident_repair_audit")]

    operations = [
        migrations.CreateModel(
            name="IncidentRepair",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("repaired_from", models.BigIntegerField()),
                ("repaired_to", models.BigIntegerField()),
                ("note", models.TextField()),
                ("actor", models.CharField(max_length=255)),
                ("repaired_at", models.DateTimeField(auto_now_add=True)),
                (
                    "incident",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="repairs",
                        to="ledger.incident",
                    ),
                ),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.RunPython(move_repairs_forward, move_repairs_back),
        migrations.RunSQL(TRIGGER, TRIGGER_REVERSE),
        migrations.RemoveField(model_name="incident", name="repair_note"),
        migrations.RemoveField(model_name="incident", name="repaired_at"),
        migrations.RemoveField(model_name="incident", name="repaired_from"),
        migrations.RemoveField(model_name="incident", name="repaired_to"),
    ]
