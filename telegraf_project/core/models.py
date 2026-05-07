from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

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

# ========== TelegramRecipient (определён до Telegram) ==========
class TelegramRecipient(models.Model):
    class RecipientStatus(models.TextChoices):
        PENDING = 'PENDING', 'Ожидает'
        READ = 'READ', 'Прочитано'
        DELIVERED = 'DELIVERED', 'Доставлено оператором'

    DELIVERY_METHOD_CHOICES = [
        ('PHONE', 'По телефону'),
        ('FAX', 'По факсу'),
        ('COURIER', 'Экспедиция'),
    ]

    telegram = models.ForeignKey('Telegram', on_delete=models.CASCADE, related_name='recipients')
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='Получатель')
    status = models.CharField(max_length=10, choices=RecipientStatus.choices, default=RecipientStatus.PENDING)
    read_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivery_method = models.CharField(max_length=10, choices=DELIVERY_METHOD_CHOICES, null=True, blank=True, verbose_name='Способ доставки')
    is_notified = models.BooleanField(default=False, verbose_name='Уведомлён оператором')
    last_notified_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата последнего уведомления')

    class Meta:
        unique_together = ('telegram', 'user')
        verbose_name = 'Получатель телеграммы'
        verbose_name_plural = 'Получатели телеграмм'

    def __str__(self):
        return f"{self.user.username} - {self.telegram.number} - {self.get_status_display()}"

# ========== Основная модель Telegram ==========
class Telegram(models.Model):
    class Priority(models.TextChoices):
        NORMAL = 'NORMAL', 'Обычная'
        URGENT = 'URGENT', 'Срочная'

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Черновик'
        ON_APPROVAL = 'ON_APPROVAL', 'На согласовании'
        APPROVED = 'APPROVED', 'Согласовано'
        ON_SIGNATURE = 'ON_SIGNATURE', 'На подписании'
        SIGNED = 'SIGNED', 'Подписано'
        DELIVERY = 'DELIVERY', 'Доставка'
        DELIVERED = 'DELIVERED', 'Доставлено'
        REJECTED = 'REJECTED', 'Отказано'

    number = models.CharField(max_length=50, blank=True, null=True, verbose_name='Номер телеграммы')
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='authored_telegrams', verbose_name='Автор')
    text = models.TextField(verbose_name='Текст телеграммы')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, verbose_name='Статус')
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL, verbose_name='Приоритет')

    requires_approval = models.BooleanField(default=False, verbose_name='Требуется согласование')
    approvers = models.ManyToManyField(User, related_name='telegrams_to_approve', blank=True, verbose_name='Согласующие')
    approvals_received = models.PositiveIntegerField(default=0, verbose_name='Количество согласовавших')
    approval_sent_at = models.DateTimeField(null=True, blank=True, verbose_name='Отправлено на согласование')
    approval_completed_at = models.DateTimeField(null=True, blank=True, verbose_name='Согласование завершено')

    requires_signature = models.BooleanField(default=False, verbose_name='Требуется подпись')
    signer = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='telegrams_to_sign', verbose_name='Подписант')
    signed_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата подписи')
    signature = models.CharField(max_length=128, blank=True, null=True, verbose_name='Электронная подпись')
    signer_department = models.CharField(max_length=255, blank=True, verbose_name='Подразделение подписанта')

    delivered_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата доставки всем')
    executor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='executed_telegrams', verbose_name='Исполнитель')
    author_department = models.CharField(max_length=255, blank=True, verbose_name='Подразделение автора')

    def save(self, *args, **kwargs):
        if not self.number and self.pk:
            self.number = f"ТГ-{self.pk}"
        if not self.author_department and self.author.department:
            self.author_department = self.author.department.name
        super().save(*args, **kwargs)

    # ----- Методы переходов -----
    def can_transition_to(self, target_status):
        allowed = {
            self.Status.DRAFT: [self.Status.ON_APPROVAL, self.Status.ON_SIGNATURE, self.Status.DELIVERY, self.Status.REJECTED],
            self.Status.ON_APPROVAL: [self.Status.APPROVED, self.Status.REJECTED],
            self.Status.APPROVED: [self.Status.ON_SIGNATURE, self.Status.REJECTED],
            self.Status.ON_SIGNATURE: [self.Status.SIGNED, self.Status.REJECTED],
            self.Status.SIGNED: [self.Status.DELIVERY],
            self.Status.DELIVERY: [self.Status.DELIVERED],
            self.Status.DELIVERED: [],
            self.Status.REJECTED: [self.Status.DRAFT],
        }
        return target_status in allowed.get(self.status, [])

    def transition_to(self, target_status, user=None):
        if not self.can_transition_to(target_status):
            raise ValueError(f"Невозможно перейти из {self.status} в {target_status}")
        self.status = target_status
        if target_status == self.Status.ON_APPROVAL and not self.approval_sent_at:
            self.approval_sent_at = timezone.now()
        if target_status == self.Status.APPROVED:
            self.approval_completed_at = timezone.now()
        if target_status == self.Status.SIGNED and not self.signed_at:
            self.signed_at = timezone.now()
            if self.signer:
                self.signer_department = self.signer.department.name if self.signer.department else ''
        if target_status == self.Status.DELIVERED and not self.delivered_at:
            self.delivered_at = timezone.now()
        self.save()

    def approve(self, user):
        if not self.requires_approval:
            raise PermissionError("Согласование не требуется")
        if user not in self.approvers.all():
            raise PermissionError("Вы не в списке согласующих")
        # Добавляем запись о согласовании
        TelegramSignature.objects.get_or_create(telegram=self, user=user)
        self.approvals_received += 1
        self.save()
        if self.approvals_received >= self.approvers.count():
            self.transition_to(self.Status.APPROVED)

    def sign(self, user, signature_value=None):
        if not self.requires_signature:
            raise PermissionError("Подписание не требуется")
        if user != self.signer:
            raise PermissionError("Вы не назначены подписантом")
        self.transition_to(self.Status.SIGNED)
        if signature_value:
            self.signature = signature_value
            self.save()

    def send_to_delivery(self):
        self.transition_to(self.Status.DELIVERY)

    def all_recipients_read(self):
        return self.recipients.filter(status='PENDING').count() == 0

    def mark_as_read(self, user):
        recipient = self.recipients.get(user=user)
        recipient.status = 'READ'
        recipient.read_at = timezone.now()
        recipient.save()
        if self.all_recipients_read():
            self.transition_to(self.Status.DELIVERED)

    def reject(self, user):
        if self.status in [self.Status.ON_APPROVAL, self.Status.ON_SIGNATURE]:
            self.transition_to(self.Status.REJECTED)

    def get_approval_progress(self):
        total = self.approvers.count()
        return (self.approvals_received, total) if total else (0, 0)

    def get_recipient_departments(self):
        return [r.user.department.name for r in self.recipients.select_related('user__department') if r.user.department]
    
    def get_read_progress(self):
        total = self.recipients.count()
        if total == 0:
            return (0, 0)
        read_count = self.recipients.filter(status='READ').count()
        return (read_count, total)
    
    def get_recipients_status_list(self):
        data = []
        for r in self.recipients.select_related('user__department'):
            data.append({
                'full_name': r.user.full_name or r.user.username,
                'department': r.user.department.code if r.user.department else '—',
                'status': r.status,
                'read_at': r.read_at.strftime('%d.%m.%Y %H:%M') if r.read_at else None,
        })
        return data
    
    def get_approvers_status_list(self):
        data = []
        for approver in self.approvers.select_related('department'):
            signature = self.signatures.filter(user=approver).first()
            data.append({
                'full_name': approver.full_name or approver.username,
                'department': approver.department.code if approver.department else '—',
                'status': 'APPROVED' if signature else 'PENDING',
                'signed_at': signature.signed_at.strftime('%d.%m.%Y %H:%M') if signature else None,
            })
        return data

    def get_signer_info(self):
        if not self.signer:
            return None
        return {
            'full_name': self.signer.full_name or self.signer.username,
            'department': self.signer.department.code if self.signer.department else '—',
            'status': 'SIGNED' if self.signed_at else 'PENDING',
            'signed_at': self.signed_at.strftime('%d.%m.%Y %H:%M') if self.signed_at else None,
        }

    def __str__(self):
        return f"Телеграмма №{self.number} от {self.author.username} от {self.created_at}"

# ========== Остальные модели (зависят от Telegram) ==========
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