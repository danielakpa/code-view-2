"""Add a unique membership generation to CodeView group members."""

# Generate unique membership identifiers.
import uuid

# Import Django migration tools.
from django.db import migrations, models


def populate_membership_generations(apps, schema_editor):
    """
    Give every existing group membership its own UUID.

    This prevents existing developers from sharing
    the same membership generation.
    """

    # Get the historical GroupMember model.
    GroupMember = apps.get_model("community", "GroupMember")

    # Use the database connection selected by Django.
    database_alias = schema_editor.connection.alias

    # Find every existing group membership.
    memberships = GroupMember.objects.using(
        database_alias
    ).all()

    # Assign a different UUID to each membership.
    for membership in memberships.iterator():

        membership.membership_generation = uuid.uuid4()

        # Save only the new membership generation.
        membership.save(
            update_fields=["membership_generation"],
            using=database_alias,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("community", "0011_alter_groupcodeboard_group_and_more"),
    ]

    operations = [

        # STEP 1:
        # Add the column as nullable for existing records.
        migrations.AddField(
            model_name="groupmember",
            name="membership_generation",
            field=models.UUIDField(
                null=True,
                editable=False,
            ),
        ),

        # STEP 2:
        # Assign a unique UUID to every existing membership.
        migrations.RunPython(
            populate_membership_generations,
            migrations.RunPython.noop,
        ),

        # STEP 3:
        # Require every membership to have a UUID.
        # New memberships automatically receive one.
        migrations.AlterField(
            model_name="groupmember",
            name="membership_generation",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
            ),
        ),
    ]