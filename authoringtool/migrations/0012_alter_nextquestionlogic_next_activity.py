# Generated by Django 4.0.10 on 2024-04-05 09:32

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('authoringtool', '0011_rename_activitybunch_questionbunch'),
    ]

    operations = [
        migrations.AlterField(
            model_name='nextquestionlogic',
            name='next_activity',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='previous_logic', to='authoringtool.activity'),
        ),
    ]
