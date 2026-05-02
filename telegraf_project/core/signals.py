from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Telegram, Log

@receiver(post_save, sender=Telegram)
def log_telegram_created(sender, instance, created, **kwargs):
    if created:
        Log.objects.create(
            user=instance.author,
            action=Log.ActionType.CREATE_TELEGRAM,
            details=f"Создана телеграмма ID={instance.id}, номер={instance.number}"
        )