from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tickets", "0018_developer_inspection_views")]

    operations = [
        migrations.AddField(
            model_name="supportticket",
            name="requester_rating",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="supportticket",
            name="requester_review_comment",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="supportticket",
            name="requester_reviewed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
