# Generated by Django 4.0.10 on 2024-04-01 07:13

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('authoringtool', '0003_activitytype_activity'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='activitytype',
            options={'ordering': ['created_on'], 'verbose_name': 'Activity Type', 'verbose_name_plural': 'Activity Types'},
        ),
        migrations.RenameField(
            model_name='activity',
            old_name='question_type',
            new_name='activity_type',
        ),
    ]
