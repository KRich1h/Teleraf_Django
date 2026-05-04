from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.decorators import user_passes_test
from django.contrib import messages
from django.utils import timezone
from datetime import timedelta, datetime
from django.urls import reverse
from django.http import HttpResponseRedirect
from .models import Telegram, User
from .forms import TelegramForm
from .models import Log, TelegramRecipient
from django.core.paginator import Paginator
from django.db.models import Count, Q, Prefetch
from django.db import transaction
from django.http import HttpResponse
from openpyxl import Workbook
from .forms import TelegramFilterForm
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from .models import Department, Position, OperatorZone, TelegramSignature, TelegramTemplate
from .forms import DepartmentForm, PositionForm, OperatorZoneForm, OperatorFilterForm
from django.http import JsonResponse
from docx import Document
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache
from django.contrib.auth.views import LoginView
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from .forms import UserProfileForm
import json

def admin_required(view_func):
    decorated = user_passes_test(lambda u: u.is_authenticated and u.role == 'ADMIN')
    return decorated(view_func)

# Проверка роли оператора
def is_operator(user):
    return user.is_authenticated and user.role == 'OPERATOR'

# Главная страница (для всех ролей)
@login_required
def home_authenticated(request):
    user = request.user
    # Входящие непрочитанные (получатель = user, статус PENDING, телеграмма не черновик)
    incoming_unread = TelegramRecipient.objects.filter(
        user=user,
        status='PENDING',
        telegram__status__in=[Telegram.Status.SENT, Telegram.Status.SIGNED]
    ).select_related('telegram').order_by('-telegram__created_at')
    
    # Телеграммы, требующие подписи (если пользователь входит в список approvers и ещё не подписал)
    pending_signature = Telegram.objects.filter(
        approvers=user,
        requires_approval=True,
        is_signed=False
    ).order_by('-created_at')
    
    return render(request, 'core/home_authenticated.html', {
        'incoming_unread': incoming_unread,
        'pending_signature': pending_signature,
    })

@never_cache
def home(request):
    if request.user.is_authenticated:
        return home_authenticated(request)
    else:
        admins = User.objects.filter(role='ADMIN', is_active=True)
        return render(request, 'core/home_unauthenticated.html', {'admins': admins})

@login_required  # только для администраторов (is_staff)
def department_list(request):
    departments = Department.objects.all()
    return render(request, 'core/department_list.html', {'departments': departments})

@admin_required
def department_create(request):
    if request.method == 'POST':
        form = DepartmentForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('department_list')
    else:
        form = DepartmentForm()
    return render(request, 'core/department_form.html', {'form': form})

@admin_required
def department_edit(request, pk):
    department = get_object_or_404(Department, pk=pk)
    if request.method == 'POST':
        form = DepartmentForm(request.POST, instance=department)
        if form.is_valid():
            form.save()
            return redirect('department_list')
    else:
        form = DepartmentForm(instance=department)
    return render(request, 'core/department_form.html', {'form': form})

@admin_required
def department_delete(request, pk):
    department = get_object_or_404(Department, pk=pk)
    department.delete()
    return redirect('department_list')

@login_required
def position_list(request):
    positions = Position.objects.all()
    return render(request, 'core/position_list.html', {'positions': positions})

@admin_required
def position_create(request):
    if request.method == 'POST':
        form = PositionForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('position_list')
    else:
        form = PositionForm()
    return render(request, 'core/position_form.html', {'form': form})

@admin_required
def position_edit(request, pk):
    pos = get_object_or_404(Position, pk=pk)
    if request.method == 'POST':
        form = PositionForm(request.POST, instance=pos)
        if form.is_valid():
            form.save()
            return redirect('position_list')
    else:
        form = PositionForm(instance=pos)
    return render(request, 'core/position_form.html', {'form': form})

@admin_required
def position_delete(request, pk):
    pos = get_object_or_404(Position, pk=pk)
    pos.delete()
    return redirect('position_list')

# ----- Зоны операторов -----
@login_required
def operatorzone_list(request):
    zones = OperatorZone.objects.all()
    return render(request, 'core/operatorzone_list.html', {'zones': zones})

@admin_required
def operatorzone_create(request):
    if request.method == 'POST':
        form = OperatorZoneForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('operatorzone_list')
    else:
        form = OperatorZoneForm()
    return render(request, 'core/operatorzone_form.html', {'form': form})

@admin_required
def operatorzone_edit(request, pk):
    zone = get_object_or_404(OperatorZone, pk=pk)
    if request.method == 'POST':
        form = OperatorZoneForm(request.POST, instance=zone)
        if form.is_valid():
            form.save()
            return redirect('operatorzone_list')
    else:
        form = OperatorZoneForm(instance=zone)
    return render(request, 'core/operatorzone_form.html', {'form': form})

@admin_required
def operatorzone_delete(request, pk):
    zone = get_object_or_404(OperatorZone, pk=pk)
    zone.delete()
    return redirect('operatorzone_list')

# Просмотр телеграммы получателем 
@login_required
def view_telegram(request, telegram_id):
    telegram = get_object_or_404(Telegram.objects.prefetch_related('recipients__user', 'approvers', 'signatures'), id=telegram_id)
    signed_approver_ids = set(telegram.signatures.values_list('user_id', flat=True))
    recipient_entry = telegram.recipients.filter(user=request.user).first()
    user_is_recipient_pending = recipient_entry is not None and recipient_entry.status == 'PENDING'
    user_recipient_id = recipient_entry.id if recipient_entry else None
    user_is_approver_pending = request.user in telegram.approvers.all() and request.user.id not in signed_approver_ids

    # Проверка действительности подписи (только если телеграмма подписана)
    signature_valid = False
    if telegram.is_signed and telegram.signature:
        from django.conf import settings
        import hashlib
        data = f"{telegram.id}{telegram.text}{telegram.author.id}{telegram.created_at.isoformat()}{settings.SECRET_KEY}"
        computed = hashlib.sha256(data.encode()).hexdigest()
        signature_valid = (computed == telegram.signature)

    return render(request, 'core/view_telegram.html', {
        'telegram': telegram,
        'signed_approver_ids': signed_approver_ids,
        'user_is_recipient_pending': user_is_recipient_pending,
        'user_recipient_id': user_recipient_id,
        'user_is_approver_pending': user_is_approver_pending,
        'signature_valid': signature_valid,  # передаём в шаблон
    })

# Модуль оператора (только для роли OPERATOR)
@login_required
@user_passes_test(is_operator)
def operator_dashboard(request):
    try:
        zone = OperatorZone.objects.get(operator=request.user)
        departments_in_zone = zone.departments.all()
    except OperatorZone.DoesNotExist:
        departments_in_zone = []

    # Показываем: НЕПРОЧИТАННЫЕ (PENDING) + ПРОЧИТАННЫЕ, НО УВЕДОМЛЁННЫЕ (is_notified=True)
    recipients = TelegramRecipient.objects.filter(
        user__department__in=departments_in_zone
    ).filter(
        Q(status='PENDING') | Q(is_notified=True)
    ).select_related('telegram', 'user__department', 'telegram__author').order_by('-telegram__created_at')

    # Фильтры
    form = OperatorFilterForm(request.GET)
    if form.is_valid():
        data = form.cleaned_data
        if data.get('start_date'):
            recipients = recipients.filter(telegram__created_at__date__gte=data['start_date'])
        if data.get('end_date'):
            recipients = recipients.filter(telegram__created_at__date__lte=data['end_date'])
        if data.get('number'):
            recipients = recipients.filter(telegram__number__icontains=data['number'])
        if data.get('department'):
            recipients = recipients.filter(user__department=data['department'])
        if data.get('status'):
            recipients = recipients.filter(status=data['status'])

    form.fields['department'].queryset = departments_in_zone

    # Время ожидания (только для непрочитанных)
    for item in recipients:
        if item.status == 'PENDING':
            delta = timezone.now() - item.telegram.created_at
            seconds = delta.total_seconds()
            if seconds > 300:
                minutes = int(seconds // 60)
                hours = minutes // 60
                days = hours // 24
                minutes = minutes % 60
                hours = hours % 24
                parts = []
                if days > 0:
                    parts.append(f"{days} дн.")
                if hours > 0:
                    parts.append(f"{hours} ч.")
                if minutes > 0:
                    parts.append(f"{minutes} мин.")
                item.waiting_display = ' '.join(parts) if parts else "менее 5 минут"
            else:
                item.waiting_display = "менее 5 минут"
        else:
            item.waiting_display = "—"

    # Обработка POST (уведомление)
    if request.method == 'POST':
        recipient_id = request.POST.get('recipient_id')
        if recipient_id:
            recipient = get_object_or_404(TelegramRecipient, id=recipient_id)
            if recipient.user.department not in departments_in_zone:
                messages.error(request, 'Нет прав на это действие.')
                return redirect('operator_dashboard')
            recipient.is_notified = True
            recipient.last_notified_at = timezone.now()
            recipient.save()
            Log.objects.create(
                user=request.user,
                action=Log.ActionType.NOTIFY,
                details=f"Уведомлён получатель {recipient.user.username} (ID={recipient.user.id}) о телеграмме ID={recipient.telegram.id}"
            )
            messages.info(request, 'Уведомление отправлено (логирование).')
        return redirect('operator_dashboard')

    return render(request, 'core/operator_dashboard.html', {
        'recipients': recipients,
        'form': form,
    })

@login_required
def telegram_journal(request):
    user = request.user

    # Базовый queryset в зависимости от роли
    if user.role == 'ADMIN':
        telegrams = Telegram.objects.all()
    elif user.role == 'OPERATOR':
        try:
            zone = OperatorZone.objects.get(operator=user)
            departments_in_zone = zone.departments.all()
        except OperatorZone.DoesNotExist:
            departments_in_zone = []
        telegrams = Telegram.objects.filter(
            Q(author__department__in=departments_in_zone) |
            Q(recipients__user__department__in=departments_in_zone)
        ).distinct()
    else:  # USER
        telegrams = Telegram.objects.filter(Q(author=user) | Q(recipients__user=user)).distinct()

    # ----- НОВАЯ ФИЛЬТРАЦИЯ (вместо старых вкладок) -----
    type_filter = request.GET.get('type')
    incoming_status = request.GET.get('status')    # 'new' или 'read'
    outgoing_stage = request.GET.get('stage')      # 'create', 'approval', 'signature', 'delivery', 'delivered'

    if type_filter == 'incoming':
        # только телеграммы, где пользователь является получателем
        telegrams = telegrams.filter(recipients__user=user).distinct()
        if incoming_status == 'new':
            telegrams = telegrams.filter(recipients__status='PENDING')
        elif incoming_status == 'read':
            telegrams = telegrams.filter(recipients__status='READ')
    elif type_filter == 'outgoing':
        # только телеграммы, где пользователь — автор
        telegrams = telegrams.filter(author=user)
        if outgoing_stage == 'create':
            # если у вас есть черновики (статус DRAFT)
            telegrams = telegrams.filter(status='DRAFT')
        elif outgoing_stage == 'approval':
            # ожидает подписи (требуется подпись, но ещё не подписана)
            telegrams = telegrams.filter(requires_approval=True, is_signed=False)
        elif outgoing_stage == 'signature':
            # подписана (статус SIGNED)
            telegrams = telegrams.filter(status='SIGNED')
        elif outgoing_stage == 'delivery':
            # отправлена, но не все получатели прочитали
            telegrams = telegrams.filter(status='SENT')
        elif outgoing_stage == 'delivered':
            # прочитана всеми получателями
            telegrams = telegrams.filter(status='READ')
    # Если type не задан — показываем все доступные телеграммы (например, входящие новые по умолчанию)

    # ---- Остальные фильтры (дата, номер, подразделение) ----
    form = TelegramFilterForm(request.GET)
    if form.is_valid():
        data = form.cleaned_data
        if data.get('start_date'):
            telegrams = telegrams.filter(created_at__date__gte=data['start_date'])
        if data.get('end_date'):
            telegrams = telegrams.filter(created_at__date__lte=data['end_date'])
        if data.get('number'):
            telegrams = telegrams.filter(number__icontains=data['number'])
        if data.get('department'):
            telegrams = telegrams.filter(
                Q(author__department=data['department']) |
                Q(recipients__user__department=data['department'])
            ).distinct()
        if data.get('search'):
            telegrams = telegrams.filter(text__icontains=data['search'])

    telegrams = telegrams.order_by('-created_at')
    paginator = Paginator(telegrams, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    # Дополнительные поля для отображения в таблице
    for t in page_obj:
        t.author_department = t.author.department.name if t.author.department else '—'
        t.recipient_departments = list(set(r.user.department.name for r in t.recipients.all() if r.user.department))
        t.approver_departments = list(set(a.department.name for a in t.approvers.all() if a.department)) if t.requires_approval else []

    # Счётчики для входящих
    new_count = TelegramRecipient.objects.filter(user=user, status='PENDING').count()
    read_count = TelegramRecipient.objects.filter(user=user, status='READ').count()

    departments = Department.objects.all()
    return render(request, 'core/journal.html', {
        'page_obj': page_obj,
        'form': form,
        'departments': departments,
        'user_role': user.role,
        'new_count': new_count,
        'read_count': read_count,
    })

def export_telegrams_excel(request):
    # Получаем отфильтрованные телеграммы (аналогично journal, но без пагинации)
    user = request.user
    if user.role == 'ADMIN':
        telegrams = Telegram.objects.all()
    elif user.role == 'OPERATOR':
        telegrams = Telegram.objects.filter(status=Telegram.Status.SENT, read_at__isnull=True)
    else:
        telegrams = Telegram.objects.filter(Q(author=user) | Q(recipient=user))

    # Применим фильтры из GET-параметров
    form = TelegramFilterForm(request.GET)
    if form.is_valid():
        data = form.cleaned_data
        if data.get('status_filter'):
            if data['status_filter'] == 'inbox':
                telegrams = telegrams.filter(recipient=user)
            elif data['status_filter'] == 'outbox':
                telegrams = telegrams.filter(author=user)
            elif data['status_filter'] == 'pending':
                telegrams = telegrams.filter(status=Telegram.Status.DRAFT, author=user)
        if data.get('start_date'):
            telegrams = telegrams.filter(created_at__date__gte=data['start_date'])
        if data.get('end_date'):
            telegrams = telegrams.filter(created_at__date__lte=data['end_date'])
        if data.get('number'):
            telegrams = telegrams.filter(number__icontains=data['number'])
        if data.get('search'):
            telegrams = telegrams.filter(text__icontains=data['search'])

    wb = Workbook()
    ws = wb.active
    ws.title = "Телеграммы"
    ws.append(['Номер', 'Дата создания', 'Отправитель', 'Получатель', 'Текст', 'Статус'])
    for t in telegrams:
        ws.append([
            t.number or '',
            t.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            t.author.full_name or t.author.username,
            t.recipient.full_name or t.recipient.username if t.recipient else '',
            t.text,
            t.get_status_display()
        ])
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=telegrams.xlsx'
    wb.save(response)
    return response

@login_required
def edit_telegram(request, pk):
    telegram = get_object_or_404(Telegram, pk=pk)
    if request.user != telegram.author and request.user.role != 'ADMIN':
        messages.error(request, 'Нет прав на редактирование.')
        return redirect('telegram_journal')
    
    if request.method == 'POST':
        telegram.text = request.POST.get('text')
        # Получатели: строка с ID через запятую
        recipients_str = request.POST.get('recipients', '')
        recipient_ids = [int(id) for id in recipients_str.split(',') if id] if recipients_str else []
        # Подписанты
        approvers_str = request.POST.get('approvers', '')
        approver_ids = [int(id) for id in approvers_str.split(',') if id] if approvers_str else []
        
        # Обновляем получателей
        telegram.recipients.all().delete()
        for uid in recipient_ids:
            TelegramRecipient.objects.create(telegram=telegram, user_id=uid, status='PENDING')
        
        # Обновляем подписантов
        if telegram.requires_approval:
            telegram.approvers.set(approver_ids)
            # Также добавим подписантов в получатели, если требуется (логика как при создании)
            for aid in approver_ids:
                TelegramRecipient.objects.get_or_create(telegram=telegram, user_id=aid, defaults={'status': 'PENDING'})
        else:
            telegram.approvers.clear()
        
        # Сбрасываем подпись, так как текст или состав подписантов изменился
        telegram.is_signed = False
        telegram.signature = None
        telegram.signed_at = None
        telegram.save()
        
        messages.success(request, 'Телеграмма обновлена.')
        return redirect('telegram_journal')
    
    # Подготовка данных для JSON-скриптов (получатели)
    recipients_data = []
    for recipient in telegram.recipients.select_related('user__department', 'user__position').all():
        user = recipient.user
        recipients_data.append({
            'id': user.id,
            'deptId': user.department.id if user.department else None,
            'deptName': user.department.name if user.department else '',
            'displayName': f"{user.position.name if user.position else 'Без должности'} ({user.full_name or user.username})"
        })
    
    # Подготовка данных для подписантов
    approvers_data = []
    for approver in telegram.approvers.select_related('department', 'position').all():
        approvers_data.append({
            'id': approver.id,
            'deptId': approver.department.id if approver.department else None,
            'deptName': approver.department.name if approver.department else '',
            'displayName': f"{approver.position.name if approver.position else 'Без должности'} ({approver.full_name or approver.username})"
        })
    
    return render(request, 'core/edit_telegram.html', {
        'telegram': telegram,
        'recipients_data_json': recipients_data,
        'approvers_data_json': approvers_data,
    })

@login_required
def copy_telegram(request, pk):
    original = get_object_or_404(Telegram, pk=pk)
    new = Telegram.objects.create(
        author=request.user,
        text=original.text,
        recipient=original.recipient,
        number='',
        status=Telegram.Status.DRAFT
    )
    messages.success(request, 'Телеграмма скопирована (черновик).')
    return redirect('telegram_journal')

@login_required
def delete_telegram(request, pk):
    telegram = get_object_or_404(Telegram, pk=pk)
    if request.user == telegram.author or request.user.role == 'ADMIN':
        telegram.delete()
        messages.success(request, 'Телеграмма удалена.')
    else:
        messages.error(request, 'Нет прав на удаление.')
    return redirect('telegram_journal')

@login_required
def print_telegram(request, pk):
    telegram = get_object_or_404(Telegram, pk=pk)
    return render(request, 'core/print_telegram.html', {'telegram': telegram})

@login_required
def mark_as_read(request, recipient_id):
    recipient = get_object_or_404(TelegramRecipient, id=recipient_id, user=request.user)
    if recipient.status == 'PENDING':
        recipient.status = 'READ'
        recipient.read_at = timezone.now()
        recipient.save()
        telegram = recipient.telegram
        if telegram.all_recipients_read():
            telegram.status = Telegram.Status.READ
            telegram.save()
        messages.success(request, 'Телеграмма отмечена как прочитанная.')
    else:
        messages.warning(request, 'Телеграмма уже прочитана.')
    return redirect('telegram_journal')

@login_required
def sign_telegram(request, telegram_id):
    telegram = get_object_or_404(Telegram, id=telegram_id, requires_approval=True, is_signed=False)
    if request.user not in telegram.approvers.all():
        messages.error(request, 'Вы не назначены подписантом этой телеграммы.')
        return redirect('home')
    signature, created = TelegramSignature.objects.get_or_create(telegram=telegram, user=request.user)
    if not created:
        messages.warning(request, 'Вы уже подписали эту телеграмму.')
        return redirect('home')
    if telegram.all_approvers_signed():
        telegram.is_signed = True
        telegram.signed_at = timezone.now()
        # ВАЖНО: статус должен остаться SIGNED, а не SENT
        telegram.status = Telegram.Status.SIGNED
        # Генерация ЭЦП
        from django.conf import settings
        import hashlib
        data = f"{telegram.id}{telegram.text}{telegram.author.id}{telegram.created_at.isoformat()}{settings.SECRET_KEY}"
        telegram.signature = hashlib.sha256(data.encode()).hexdigest()
        telegram.save()
        messages.success(request, 'Телеграмма полностью подписана и отправлена получателям.')
    else:
        telegram.save()  # сохраняем дополнительную подпись, но статус пока не меняем
        messages.success(request, 'Ваша подпись сохранена.')
    return redirect('home')

def get_positions(request, department_id):
    positions = Position.objects.filter(department_id=department_id).values('id', 'name')
    return JsonResponse(list(positions), safe=False)

def get_users(request, position_id):
    users = User.objects.filter(position_id=position_id, role='USER').values('id', 'full_name', 'username')
    return JsonResponse(list(users), safe=False)

def generate_telegram_number():
    today = datetime.now().strftime('%Y%m%d')
    last = Telegram.objects.filter(number__startswith=f'ТГ-{today}').count()
    return f"ТГ-{today}-{last+1:03d}"

@login_required
def create_telegram_page(request):
    if request.method == 'POST':
        # Получаем данные из POST
        text = request.POST.get('text')
        priority = request.POST.get('priority', 'NORMAL')
        requires_approval = request.POST.get('requires_approval') == 'on'

        # Обработка получателей: может быть строка с запятыми или список
        recipients_raw = request.POST.get('recipients', '')
        if recipients_raw:
            recipients_ids = [int(x) for x in recipients_raw.split(',') if x.strip()]
        else:
            recipients_ids = []

        # Обработка подписантов: аналогично
        approvers_raw = request.POST.get('approvers', '')
        if approvers_raw:
            approvers_ids = [int(x) for x in approvers_raw.split(',') if x.strip()]
        else:
            approvers_ids = []

        # Исключаем текущего пользователя из получателей и подписантов
        recipients_ids = [uid for uid in recipients_ids if uid != request.user.id]
        approvers_ids = [aid for aid in approvers_ids if aid != request.user.id]

        # Валидация
        if not recipients_ids:
            messages.error(request, 'Выберите хотя бы одного получателя (не считая себя).')
            return redirect('create_telegram_page')

        if requires_approval and not approvers_ids:
            messages.error(request, 'При включённой подписи выберите хотя бы одного подписанта.')
            return redirect('create_telegram_page')

        with transaction.atomic():
            # Генерация номера телеграммы
            number = generate_telegram_number()

            # Создаём телеграмму
            telegram = Telegram.objects.create(
                author=request.user,
                text=text,
                number=number,
                priority=priority,
                requires_approval=requires_approval,
                status=Telegram.Status.SENT  # или DRAFT, по вашему усмотрению
            )

            # Добавляем получателей
            for uid in recipients_ids:
                TelegramRecipient.objects.create(telegram=telegram, user_id=uid, status='PENDING')

            # Добавляем подписантов (если требуется)
            if requires_approval:
                telegram.approvers.set(approvers_ids)
                # Подписанты также автоматически становятся получателями (по желанию)
                for aid in approvers_ids:
                    TelegramRecipient.objects.get_or_create(
                        telegram=telegram,
                        user_id=aid,
                        defaults={'status': 'PENDING'}
                    )

        messages.success(request, f'Телеграмма №{number} успешно отправлена {len(recipients_ids)} получателям.')
        return redirect('telegram_journal')

    else:
        # GET-запрос — показываем форму
        departments = Department.objects.all()
        templates = TelegramTemplate.objects.all()
        return render(request, 'core/create_telegram.html', {
            'departments': departments,
            'templates': templates,
        })
    
@login_required
def department_users(request, department_id):
    department = get_object_or_404(Department, id=department_id)
    users = User.objects.filter(department=department, role='USER')
    return render(request, 'core/department_users.html', {
        'department': department,
        'users': users,
    })    

@login_required
def reports(request):
    if request.user.role not in ['ADMIN', 'OPERATOR']:
        messages.error(request, 'Доступ запрещён.')
        return redirect('home')
    
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    
    # Базовый queryset телеграмм (без фильтрации по получателям)
    telegrams = Telegram.objects.all()
    if start_date:
        telegrams = telegrams.filter(created_at__date__gte=start_date)
    if end_date:
        telegrams = telegrams.filter(created_at__date__lte=end_date)
    
    # Для оператора – только телеграммы, где получатель в его зоне
    if request.user.role == 'OPERATOR':
        try:
            zone = OperatorZone.objects.get(operator=request.user)
            departments_in_zone = zone.departments.all()
            telegrams = telegrams.filter(recipients__user__department__in=departments_in_zone).distinct()
        except OperatorZone.DoesNotExist:
            telegrams = Telegram.objects.none()
    
    total = telegrams.count()
    status_counts = telegrams.values('status').annotate(count=Count('status'))
    status_dict = {item['status']: item['count'] for item in status_counts}
    
    # Способы доставки (только для оператора) – через TelegramRecipient
    delivery_method_counts = {}
    if request.user.role == 'OPERATOR':
        recipients = TelegramRecipient.objects.filter(
            telegram__in=telegrams,
            delivery_method__isnull=False
        )
        if start_date:
            recipients = recipients.filter(telegram__created_at__date__gte=start_date)
        if end_date:
            recipients = recipients.filter(telegram__created_at__date__lte=end_date)
        method_counts = recipients.values('delivery_method').annotate(count=Count('delivery_method'))
        for item in method_counts:
            delivery_method_counts[item['delivery_method']] = item['count']
    
    context = {
        'total': total,
        'status_counts': status_dict,
        'method_counts': delivery_method_counts,
        'start_date': start_date,
        'end_date': end_date,
        'role': request.user.role,
    }
    return render(request, 'core/reports.html', context)

def export_report_excel(request):
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    telegrams = Telegram.objects.all()
    if start_date:
        telegrams = telegrams.filter(created_at__date__gte=start_date)
    if end_date:
        telegrams = telegrams.filter(created_at__date__lte=end_date)
    if request.user.role == 'OPERATOR':
        try:
            zone = OperatorZone.objects.get(operator=request.user)
            departments_in_zone = zone.departments.all()
            telegrams = telegrams.filter(recipients__user__department__in=departments_in_zone).distinct()
        except OperatorZone.DoesNotExist:
            telegrams = Telegram.objects.none()
    wb = Workbook()
    ws = wb.active
    ws.title = "Отчёт"
    ws.append(['ID', 'Дата', 'Отправитель', 'Получатели', 'Статус', 'Способ доставки'])
    for t in telegrams:
        recipients = t.recipients.all()
        recipient_names = ', '.join([r.user.full_name or r.user.username for r in recipients])
        # Для способа доставки – если есть запись с delivery_method
        delivery_method = ''
        for r in recipients:
            if r.delivery_method:
                delivery_method = r.get_delivery_method_display()
                break
        ws.append([
            t.id,
            t.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            t.author.full_name or t.author.username,
            recipient_names,
            t.get_status_display(),
            delivery_method
        ])
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=report.xlsx'
    wb.save(response)
    return response

def get_approvers_by_department(request, department_id):
    users = User.objects.filter(
        Q(role='ADMIN') | Q(position__department_id=department_id, position__name__icontains='начальник')
    ).distinct().values('id', 'full_name', 'username')
    return JsonResponse(list(users), safe=False)

@login_required
def telegram_detail(request, telegram_id):
    telegram = get_object_or_404(Telegram, id=telegram_id)
    # Проверяем, имеет ли пользователь право просмотра (автор, получатель, подписант или админ)
    if not (request.user == telegram.author or 
            request.user in telegram.recipients.values_list('user', flat=True) or
            request.user in telegram.approvers.all() or
            request.user.role == 'ADMIN'):
        messages.error(request, 'У вас нет доступа к этой телеграмме.')
        return redirect('telegram_journal')
    # Группируем получателей по подразделениям
    recipients_by_dept = {}
    for recipient in telegram.recipients.all():
        dept = recipient.user.department
        if dept:
            if dept not in recipients_by_dept:
                recipients_by_dept[dept] = []
            recipients_by_dept[dept].append(recipient.user)
    return render(request, 'core/telegram_detail.html', {
        'telegram': telegram,
        'recipients_by_dept': recipients_by_dept,
    })

def telegram_detail_json(request, telegram_id):
    telegram = get_object_or_404(Telegram, id=telegram_id)
    recipients = [{'name': r.user.full_name or r.user.username, 'department': r.user.department.name if r.user.department else '—'} for r in telegram.recipients.all()]
    approvers = [{'name': a.full_name or a.username, 'department': a.department.name if a.department else '—'} for a in telegram.approvers.all()]
    data = {
        'number': telegram.number,
        'created_at': telegram.created_at.strftime('%d.%m.%Y %H:%M'),
        'author': telegram.author.full_name or telegram.author.username,
        'author_department': telegram.author.department.name if telegram.author.department else '—',
        'text': telegram.text,
        'priority': telegram.priority,
        'recipient_departments': list(set(r.user.department.name for r in telegram.recipients.all() if r.user.department)),
        'recipients': recipients,
        'requires_approval': telegram.requires_approval,
        'approver_departments': list(set(a.department.name for a in telegram.approvers.all() if a.department)),
        'approvers': approvers,
    }
    return JsonResponse(data)

@login_required
def mass_delete(request):
    if request.method == 'POST':
        ids_str = request.POST.get('selected_ids', '')
        if ids_str:
            ids = ids_str.split(',')
            # Удаляем только те телеграммы, где автор – текущий пользователь
            deleted_count = Telegram.objects.filter(id__in=ids, author=request.user).delete()[0]
            if deleted_count:
                messages.success(request, f'Удалено {deleted_count} телеграмм.')
            else:
                messages.warning(request, 'Не удалось удалить: возможно, вы не автор.')
        else:
            messages.warning(request, 'Ничего не выбрано.')
    return redirect('telegram_journal')

@login_required
def mass_copy(request):
    if request.method == 'POST':
        telegram_id = request.POST.get('selected_id')
        if telegram_id:
            original = get_object_or_404(Telegram, id=telegram_id)
            with transaction.atomic():
                new = Telegram.objects.create(
                    author=request.user,
                    text=original.text,
                    number=generate_telegram_number(),
                    priority=original.priority,
                    requires_approval=original.requires_approval,
                    status=Telegram.Status.SENT
                )
                for recipient in original.recipients.all():
                    TelegramRecipient.objects.create(telegram=new, user=recipient.user, status='PENDING')
                if original.requires_approval:
                    new.approvers.set(original.approvers.all())
            messages.success(request, 'Телеграмма скопирована.')
        else:
            messages.warning(request, 'Ничего не выбрано.')
    return redirect('telegram_journal')

@login_required
def mark_as_read_and_view(request, recipient_id):
    recipient = get_object_or_404(TelegramRecipient, id=recipient_id, user=request.user)
    if recipient.status == 'PENDING':
        recipient.status = 'READ'
        recipient.read_at = timezone.now()
        recipient.save()
        telegram = recipient.telegram
        if telegram.all_recipients_read():
            telegram.status = Telegram.Status.READ
            telegram.save()
        messages.success(request, 'Телеграмма отмечена как прочитанная.')
    else:
        messages.warning(request, 'Телеграмма уже прочитана.')
    return redirect('home')

@csrf_exempt
@require_http_methods(['POST'])
def upload_word(request):
    if 'file' not in request.FILES:
        return JsonResponse({'error': 'Файл не загружен'}, status=400)
    file = request.FILES['file']
    if not file.name.endswith('.docx'):
        return JsonResponse({'error': 'Требуется файл .docx'}, status=400)
    try:
        doc = Document(file)
        text = '\n'.join([para.text for para in doc.paragraphs if para.text.strip()])
        return JsonResponse({'text': text})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
    
def get_recipients_by_departments(request):
    dept_ids = request.GET.get('departments', '').split(',')
    if dept_ids and dept_ids[0]:
        users = User.objects.filter(department_id__in=dept_ids, role='USER') \
                             .exclude(id=request.user.id) \
                             .values('id', 'full_name', 'username')
        return JsonResponse(list(users), safe=False)
    return JsonResponse([], safe=False)

def get_approvers_by_departments(request):
    dept_ids = request.GET.get('departments', '').split(',')
    if dept_ids and dept_ids[0]:
        keywords = ['начальник', 'заместитель', 'главный', 'руководитель', 'директор', 'зав.']
        q_filter = Q()
        for kw in keywords:
            q_filter |= Q(position__name__icontains=kw)
        users = User.objects.filter(q_filter, department_id__in=dept_ids, is_active=True) \
                             .exclude(id=request.user.id) \
                             .values('id', 'full_name', 'username', 'department__name')
        # Добавляем department_name для отображения в подписи (не обязательно)
        data = [{'id': u['id'], 'full_name': u['full_name'] or u['username'], 'department_name': u['department__name'] or '—'} for u in users]
        return JsonResponse(data, safe=False)
    return JsonResponse([], safe=False)

@never_cache
@login_required
def home_authenticated(request):
    user = request.user
    incoming_unread = TelegramRecipient.objects.filter(
        user=user,
        status='PENDING',
        telegram__status__in=[Telegram.Status.SENT, Telegram.Status.SIGNED]
    ).select_related('telegram').order_by('-telegram__created_at')
    
    pending_signature = Telegram.objects.filter(
        approvers=user,
        requires_approval=True,
        is_signed=False
    ).order_by('-created_at')
    
    return render(request, 'core/home_authenticated.html', {
        'incoming_unread': incoming_unread,
        'pending_signature': pending_signature,
    })

class CustomLoginView(LoginView):
    template_name = 'core/login.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['admins'] = User.objects.filter(role='ADMIN', is_active=True)
        return context
    
@login_required
def profile(request):
    user = request.user
    if request.method == 'POST':
        if 'update_profile' in request.POST:
            form = UserProfileForm(request.POST, instance=user)
            if form.is_valid():
                form.save()
                messages.success(request, 'Профиль обновлён.')
                return redirect('profile')
        elif 'change_password' in request.POST:
            password_form = PasswordChangeForm(user, request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)  # сохраняем сессию
                messages.success(request, 'Пароль изменён.')
                return redirect('profile')
            else:
                messages.error(request, 'Ошибка при смене пароля.')
    else:
        form = UserProfileForm(instance=user)
        password_form = PasswordChangeForm(user)

    # Статистика пользователя
    sent_count = Telegram.objects.filter(author=user).count()
    received_count = TelegramRecipient.objects.filter(user=user).count()
    unread_count = TelegramRecipient.objects.filter(user=user, status='PENDING').count()

    context = {
        'form': form,
        'password_form': password_form,
        'sent_count': sent_count,
        'received_count': received_count,
        'unread_count': unread_count,
    }
    return render(request, 'core/profile.html', context)

def departments_with_users(request):
    data = []
    departments = Department.objects.prefetch_related('user_set__position').all()
    for dept in departments:
        users = dept.user_set.filter(is_active=True).exclude(id=request.user.id)
        user_list = []
        for user in users:
            user_list.append({
                'id': user.id,
                'position_name': user.position.name if user.position else 'Без должности',
                'full_name': user.full_name or user.username,
                'username': user.username
            })
        data.append({
            'id': dept.id,
            'name': f"{dept.code} - {dept.name}",
            'users': user_list
        })
    return JsonResponse(data, safe=False)

def approvers_tree(request):
    # Отбираем активных пользователей с нужными должностями
    users = User.objects.filter(
        is_active=True,
        position__name__in=['Начальник депо', 'Начальник дистанции', 'Главный инженер']
    ).select_related('department').values(
        'id',
        'full_name',
        'username',
        'position__name',
        'department__id',
        'department__code',
        'department__name'
    )
    
    # Группировка по подразделениям
    dept_dict = {}
    for u in users:
        dept_id = u['department__id']
        if dept_id is None:
            continue
        dept_key = dept_id
        if dept_key not in dept_dict:
            dept_dict[dept_key] = {
                'name': f"{u['department__code']} - {u['department__name']}",
                'users': []
            }
        dept_dict[dept_key]['users'].append({
            'id': u['id'],
            'position_name': u['position__name'],
            'full_name': u['full_name'] or u['username'],
            'username': u['username']
        })
    
    # Преобразуем в список
    data = [
        {
            'id': dept_id,
            'name': info['name'],
            'users': info['users']
        }
        for dept_id, info in dept_dict.items()
    ]
    
    return JsonResponse(data, safe=False)

@login_required
def send_telegram(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Метод не разрешён'}, status=405)
    try:
        data = json.loads(request.body)
        ids = data.get('ids', [])
        if not ids:
            return JsonResponse({'success': False, 'message': 'Не выбраны телеграммы'})
        telegrams = Telegram.objects.filter(id__in=ids, author=request.user, status='DRAFT')
        count = telegrams.count()
        telegrams.update(status=Telegram.Status.SENT)
        return JsonResponse({'success': True, 'message': f'Отправлено {count} телеграмм'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})

@login_required
def reset_to_draft(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Метод не разрешён'}, status=405)
    try:
        data = json.loads(request.body)
        ids = data.get('ids', [])
        if not ids:
            return JsonResponse({'success': False, 'message': 'Не выбраны телеграммы'})
        telegrams = Telegram.objects.filter(id__in=ids, author=request.user)
        count = 0
        for tg in telegrams:
            tg.status = Telegram.Status.DRAFT
            tg.is_signed = False
            tg.signature = None
            tg.signed_at = None
            tg.signatures.all().delete()
            tg.save()
            count += 1
        return JsonResponse({'success': True, 'message': f'Сброшено {count} телеграмм'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})