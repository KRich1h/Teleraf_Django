from django.utils.deprecation import MiddlewareMixin
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from .models import Log

@receiver(user_logged_in)
def log_login(sender, request, user, **kwargs):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    Log.objects.create(
        user=user,
        action=Log.ActionType.LOGIN,
        ip_address=ip
    )

@receiver(user_logged_out)
def log_logout(sender, request, user, **kwargs):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    if user:
        Log.objects.create(
            user=user,
            action=Log.ActionType.LOGOUT,
            ip_address=ip
        )