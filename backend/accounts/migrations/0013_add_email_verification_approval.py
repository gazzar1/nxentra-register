# Generated migration for email verification and admin approval
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def set_existing_users_verified_approved(apps, schema_editor):
    """
    Backward compatibility: Set all existing users as verified and approved.

    This ensures existing users can continue to log in after this migration.
    """
    User = apps.get_model('accounts', 'User')
    from django.utils import timezone
    now = timezone.now()

    User.objects.filter(email_verified=False).update(
        email_verified=True,
        email_verified_at=now,
        is_approved=True,
        approved_at=now,
    )


def reverse_verification_approval(apps, schema_editor):
    """
    Reverse migration: nothing to do since fields will be removed.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_add_company_logo'),
    ]

    operations = [
        # Add email verification fields to User
        migrations.AddField(
            model_name='user',
            name='email_verified',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='user',
            name='email_verified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),

        # Add admin approval fields to User
        migrations.AddField(
            model_name='user',
            name='is_approved',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='user',
            name='approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='approved_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='approved_users',
                to=settings.AUTH_USER_MODEL,
            ),
        ),

        # Create EmailVerificationToken model
        migrations.CreateModel(
            name='EmailVerificationToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token_hash', models.CharField(
                    db_index=True,
                    help_text='SHA-256 hash of the verification token',
                    max_length=64,
                    unique=True,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='verification_tokens',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Email Verification Token',
                'verbose_name_plural': 'Email Verification Tokens',
            },
        ),

        # Add indexes for EmailVerificationToken
        migrations.AddIndex(
            model_name='emailverificationtoken',
            index=models.Index(fields=['user', 'created_at'], name='accounts_em_user_id_7a1b5e_idx'),
        ),
        migrations.AddIndex(
            model_name='emailverificationtoken',
            index=models.Index(fields=['expires_at'], name='accounts_em_expires_8e3c2d_idx'),
        ),

        # Set existing users as verified and approved (backward compatibility)
        migrations.RunPython(
            set_existing_users_verified_approved,
            reverse_verification_approval,
        ),
    ]
