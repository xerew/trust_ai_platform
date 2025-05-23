# Generated by Django 4.0.10 on 2024-10-17 15:20

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organization', '0001_initial'),
        ('authoringtool', '0022_alter_activity_score_limit'),
    ]

    operations = [
        migrations.AddField(
            model_name='activity',
            name='is_primary_ev',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='scenario',
            name='is_editable_by_org',
            field=models.BooleanField(default=False, help_text='If checked, members of the selected organization(s) can edit this scenario.'),
        ),
        migrations.AddField(
            model_name='scenario',
            name='organizations',
            field=models.ManyToManyField(blank=True, related_name='scenarios', to='organization.organization'),
        ),
        migrations.AddField(
            model_name='scenario',
            name='visibility_status',
            field=models.CharField(choices=[('private', 'Private (In-Progress)'), ('org', 'Organization Users Only'), ('public', 'Public')], default='private', max_length=20),
        ),
    ]
