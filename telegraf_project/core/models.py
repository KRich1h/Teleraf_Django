from django.contrib.auth.models import AbstractUser
from django.db import models

class Department(models.Model):
    code = models.CharField(max_length=20, unique=True, verbose_name='Код подразделения')
    name = models.CharField(max_length=255, verbose_name='Наименование')
    class Meta:
        verbose_name = 'Подразделение'
        verbose_name_plural = 'Подразделения'
    def __str__(self):
        return f"{self.code} - {self.name}"

class Position(models.Model):
    name = models.CharField(max_length=255, verbose_name='Название должности')
    department = models.ForeignKey(Department, on_delete=models.CASCADE, verbose_name='Подразделение')
    class Meta:
        verbose_name = 'Должность'
        verbose_name_plural = 'Должности'
    def __str__(self):
        return f"{self.name} ({self.department.code})"

class Role(models.TextChoices):
    ADMIN = 'ADMIN', 'Администратор'
    OPERATOR = 'OPERATOR', 'Оператор'
    USER = 'USER', 'Абонент'

class User(AbstractUser):
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.USER, verbose_name='Роль')
    full_name = models.CharField(max_length=255, verbose_name='Полное имя')
    phone = models.CharField(max_length=20, blank=True, null=True, verbose_name='Телефон')
    email = models.EmailField(blank=True, null=True, verbose_name='Email')
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Подразделение')
    position = models.ForeignKey(Position, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Должность')
    def __str__(self):
        return self.username

class Telegram(models.Model):
    class Priority(models.TextChoices):
        NORMAL = 'NORMAL', 'Обычная'
        URGENT = 'URGENT', 'Срочная'

    class Status(models.TextChoices):
        SENT = 'SENT', 'Отправлена'
        READ = 'READ', 'Прочитана'
        SIGNED = 'SIGNED', 'Подписана'

    number = models.CharField(max_length=50, blank=True, null=True, verbose_name='Номер телеграммы')
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='authored_telegrams', verbose_name='Автор')
    text = models.TextField(verbose_name='Текст телеграммы')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SENT, verbose_name='Общий статус')
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL, verbose_name='Приоритет')
    
    requires_approval = models.BooleanField(default=False, verbose_name='Требуется подпись')
    approvers = models.ManyToManyField(User, related_name='telegrams_to_approve', blank=True, verbose_name='Подписанты')
    is_signed = models.BooleanField(default=False, verbose_name='Подписана')
    signed_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата подписи')
    signature = models.CharField(max_length=128, blank=True, null=True, verbose_name='Электронная подпись')   # <-- добавлено

    def all_recipients_read(self):
        return self.recipients.exclude(status='READ').count() == 0

    def all_approvers_signed(self):
        if not self.requires_approval:
            return True
        return self.signatures.count() == self.approvers.count()

    def __str__(self):
        return f"Телеграмма №{self.number} от {self.author.username} от {self.created_at}"
      
class TelegramRecipient(models.Model):
    class RecipientStatus(models.TextChoices):
        PENDING = 'PENDING', 'Ожидает'
        READ = 'READ', 'Прочитано'
        DELIVERED = 'DELIVERED', 'Доставлено оператором'  # опционально, если нужно

    DELIVERY_METHOD_CHOICES = [
        ('PHONE', 'По телефону'),
        ('FAX', 'По факсу'),
        ('COURIER', 'Экспедиция'),
    ]

    telegram = models.ForeignKey(Telegram, on_delete=models.CASCADE, related_name='recipients')
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='Получатель')
    status = models.CharField(max_length=10, choices=RecipientStatus.choices, default=RecipientStatus.PENDING)
    read_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivery_method = models.CharField(max_length=10, choices=DELIVERY_METHOD_CHOICES, null=True, blank=True, verbose_name='Способ доставки')
    
    # Новые поля для уведомлений оператором
    is_notified = models.BooleanField(default=False, verbose_name='Уведомлён оператором')
    last_notified_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата последнего уведомления')

    class Meta:
        unique_together = ('telegram', 'user')
        verbose_name = 'Получатель телеграммы'
        verbose_name_plural = 'Получатели телеграмм'

    def __str__(self):
        return f"{self.user.username} - {self.telegram.number} - {self.get_status_display()}"

class TelegramSignature(models.Model):
    telegram = models.ForeignKey(Telegram, on_delete=models.CASCADE, related_name='signatures')
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='Подписант')
    signed_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ('telegram', 'user')

class Log(models.Model):
    class ActionType(models.TextChoices):
        LOGIN = 'LOGIN', 'Вход в систему'
        LOGOUT = 'LOGOUT', 'Выход из системы'
        CREATE_TELEGRAM = 'CREATE_TELEGRAM', 'Создание телеграммы'
        VIEW_TELEGRAM = 'VIEW_TELEGRAM', 'Просмотр телеграммы'
        DELIVER_TELEGRAM = 'DELIVER_TELEGRAM', 'Доставка телеграммы оператором'
        NOTIFY = 'NOTIFY', 'Уведомление оператором'

    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='Пользователь')
    action = models.CharField(max_length=20, choices=ActionType.choices, verbose_name='Действие')
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name='Дата и время')
    details = models.TextField(blank=True, null=True, verbose_name='Дополнительная информация')
    ip_address = models.GenericIPAddressField(blank=True, null=True, verbose_name='IP-адрес')
    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Журнал действия'
        verbose_name_plural = 'Журнал действий'
    def __str__(self):
        return f"{self.timestamp} - {self.user.username} - {self.get_action_display()}"

class OperatorZone(models.Model):
    operator = models.OneToOneField(User, on_delete=models.CASCADE, limit_choices_to={'role': 'OPERATOR'}, verbose_name='Оператор')
    departments = models.ManyToManyField(Department, blank=True, verbose_name='Обслуживаемые подразделения')
    class Meta:
        verbose_name = 'Зона оператора'
        verbose_name_plural = 'Зоны операторов'
    def __str__(self):
        return f"Зона оператора {self.operator.username}"

class TelegramTemplate(models.Model):
    name = models.CharField(max_length=100, verbose_name='Название шаблона')
    text = models.TextField(verbose_name='Текст шаблона')
    is_default = models.BooleanField(default=False, verbose_name='По умолчанию')
    class Meta:
        verbose_name = 'Шаблон телеграммы'
        verbose_name_plural = 'Шаблоны телеграмм'
    def __str__(self):
        return self.name