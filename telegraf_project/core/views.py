from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.utils import timezone
from datetime import datetime, timedelta
from django.http import HttpResponseRedirect, JsonResponse, HttpResponse
from django.core.paginator import Paginator
from django.db.models import Count
from django.db.models import Q, Exists, OuterRef
from django.db import transaction
from openpyxl import Workbook
from docx import Document
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache
from django.contrib.auth.views import LoginView
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
import json
from django.urls import reverse
from django.conf import settings
import hashlib
from django.utils.timezone import make_aware

from .models import (
    Telegram, User, Log, TelegramRecipient, Department, Position,
    OperatorZone, TelegramSignature, TelegramTemplate
)
from .forms import (
    TelegramForm, TelegramFilterForm, DepartmentForm, PositionForm,
    OperatorZoneForm, OperatorFilterForm, UserProfileForm
)

# ---------- Вспомогательные функции ----------
def admin_required(view_func):
    decorated = user_passes_test(lambda u: u.is_authenticated and u.role == 'ADMIN')
    return decorated(view_func)

def is_operator(user):
    return user.is_authenticated and user.role == 'OPERATOR'

def generate_telegram_number():
    today = datetime.now().strftime('%Y%m%d')
    last = Telegram.objects.filter(number__startswith=f'ТГ-{today}').count()
    return f"ТГ-{today}-{last+1:03d}"

@never_cache
def home(request):
    context = {}
    # Для неавторизованных подгружаем контакты администраторов
    if not request.user.is_authenticated:
        context['admins'] = User.objects.filter(role='ADMIN', is_active=True)
    return render(request, 'core/home.html', context)

# ---------- Управление телеграммами ----------
@login_required
def create_telegram_page(request):
    if request.method == 'POST':
        text = request.POST.get('text')
        priority = request.POST.get('priority', 'NORMAL')
        requires_approval = request.POST.get('requires_approval') == 'on'
        requires_signature = request.POST.get('requires_signature') == 'on'
        is_draft = request.POST.get('is_draft') == 'on'   # новый чекбокс

        recipients_raw = request.POST.get('recipients', '')
        recipients_ids = [int(x) for x in recipients_raw.split(',') if x.strip()]
        approvers_raw = request.POST.get('approvers', '')
        approvers_ids = [int(x) for x in approvers_raw.split(',') if x.strip()]
        signer_id = request.POST.get('signer')
        if signer_id:
            signer_id = int(signer_id)

        if not recipients_ids:
            messages.error(request, 'Выберите хотя бы одного получателя.')
            return redirect('create_telegram_page')
        if requires_approval and not approvers_ids:
            messages.error(request, 'Выберите согласующих.')
            return redirect('create_telegram_page')
        if requires_signature and not signer_id:
            messages.error(request, 'Выберите подписанта.')
            return redirect('create_telegram_page')

        with transaction.atomic():
            telegram = Telegram.objects.create(
                author=request.user,
                text=text,
                priority=priority,
                requires_approval=requires_approval,
                requires_signature=requires_signature,
                status=Telegram.Status.DRAFT,   # по умолчанию черновик
                author_department=request.user.department.name if request.user.department else ''
            )
            for uid in recipients_ids:
                TelegramRecipient.objects.create(telegram=telegram, user_id=uid, status='PENDING')
            if requires_approval:
                telegram.approvers.set(approvers_ids)
                for aid in approvers_ids:
                    TelegramRecipient.objects.get_or_create(telegram=telegram, user_id=aid, defaults={'status': 'PENDING'})
            if requires_signature:
                telegram.signer_id = signer_id
                telegram.save()
                TelegramRecipient.objects.get_or_create(telegram=telegram, user_id=signer_id, defaults={'status': 'PENDING'})

            # Если не черновик – отправляем в нужный статус
            if not is_draft:
                if requires_approval:
                    telegram.transition_to(Telegram.Status.ON_APPROVAL)
                elif requires_signature:
                    telegram.transition_to(Telegram.Status.ON_SIGNATURE)
                else:
                    telegram.transition_to(Telegram.Status.DELIVERY)

        if is_draft:
            messages.success(request, 'Телеграмма сохранена как черновик.')
        else:
            messages.success(request, 'Телеграмма отправлена по маршруту.')
        return redirect('telegram_journal')
    else:
        departments = Department.objects.all()
        templates = TelegramTemplate.objects.all()
        return render(request, 'core/create_telegram.html', {
            'departments': departments,
            'templates': templates,
        })

@login_required
def view_telegram(request, telegram_id):
    telegram = get_object_or_404(
        Telegram.objects.prefetch_related('recipients__user', 'approvers', 'signatures'),
        id=telegram_id
    )
    user = request.user
    user_recipient = telegram.recipients.filter(user=user).first()
    context = {
        'telegram': telegram,
        'user_is_recipient': user_recipient is not None,
        'user_recipient_id': user_recipient.id if user_recipient else None,
        'user_recipient_status': user_recipient.status if user_recipient else None,  # Добавлено
        'user_is_approver': user in telegram.approvers.all(),
        'user_is_signer': user == telegram.signer,
        'user_is_author': user == telegram.author,
    }
    return render(request, 'core/view_telegram.html', context)

@login_required
def edit_telegram(request, pk):
    telegram = get_object_or_404(Telegram, pk=pk)
    if request.user != telegram.author and request.user.role != 'ADMIN':
        messages.error(request, 'Нет прав на редактирование.')
        return redirect('telegram_journal')
    
    if request.method == 'POST':
        # Получаем данные из формы
        text = request.POST.get('text')
        requires_approval = request.POST.get('requires_approval') == 'on'
        requires_signature = request.POST.get('requires_signature') == 'on'
        is_draft = request.POST.get('is_draft') == 'on'   # чекбокс черновика

        recipients_raw = request.POST.get('recipients', '')
        recipients_ids = [int(x) for x in recipients_raw.split(',') if x.strip()]
        approvers_raw = request.POST.get('approvers', '')
        approvers_ids = [int(x) for x in approvers_raw.split(',') if x.strip()]
        signer_id = request.POST.get('signer')
        if signer_id:
            signer_id = int(signer_id)

        # Валидация
        if not recipients_ids:
            messages.error(request, 'Выберите хотя бы одного получателя.')
            return redirect('edit_telegram', pk=pk)
        if requires_approval and not approvers_ids:
            messages.error(request, 'Выберите согласующих.')
            return redirect('edit_telegram', pk=pk)
        if requires_signature and not signer_id:
            messages.error(request, 'Выберите подписанта.')
            return redirect('edit_telegram', pk=pk)

        # Обновляем телеграмму
        telegram.text = text
        telegram.requires_approval = requires_approval
        telegram.requires_signature = requires_signature
        telegram.author_department = request.user.department.name if request.user.department else ''

        # Пересоздаём получателей
        telegram.recipients.all().delete()
        for uid in recipients_ids:
            TelegramRecipient.objects.create(telegram=telegram, user_id=uid, status='PENDING')

        # Обновляем согласующих
        if requires_approval:
            telegram.approvers.set(approvers_ids)
            for aid in approvers_ids:
                TelegramRecipient.objects.get_or_create(telegram=telegram, user_id=aid, defaults={'status': 'PENDING'})
        else:
            telegram.approvers.clear()

        # Обновляем подписанта
        if requires_signature and signer_id:
            telegram.signer_id = signer_id
            telegram.save()
            TelegramRecipient.objects.get_or_create(telegram=telegram, user_id=signer_id, defaults={'status': 'PENDING'})
        else:
            telegram.signer = None

        # Сбрасываем старые подписи, согласования, отказы
        telegram.signatures.all().delete()
        telegram.approvals_received = 0
        telegram.rejection_reason = None
        telegram.rejected_by = None
        telegram.rejected_at = None

        # Если не черновик – отправляем по маршруту
        if not is_draft:
            if requires_approval:
                telegram.transition_to(Telegram.Status.ON_APPROVAL)
            elif requires_signature:
                telegram.transition_to(Telegram.Status.ON_SIGNATURE)
            else:
                telegram.transition_to(Telegram.Status.DELIVERY)
        else:
            # Сохраняем как черновик
            telegram.status = Telegram.Status.DRAFT
            telegram.save()

        messages.success(request, 'Телеграмма обновлена.')
        return redirect('telegram_journal')
    
    # GET – подготовка данных для шаблона
    recipients_data = []
    for recipient in telegram.recipients.select_related('user__department', 'user__position').all():
        user = recipient.user
        recipients_data.append({
            'id': user.id,
            'deptId': user.department.id if user.department else None,
            'deptName': user.department.name if user.department else '',
            'displayName': f"{user.position.name if user.position else 'Без должности'} ({user.full_name or user.username})"
        })
    
    approvers_data = []
    for approver in telegram.approvers.select_related('department', 'position').all():
        approvers_data.append({
            'id': approver.id,
            'deptId': approver.department.id if approver.department else None,
            'deptName': approver.department.name if approver.department else '',
            'displayName': f"{approver.position.name if approver.position else 'Без должности'} ({approver.full_name or approver.username})"
        })
    
    signer_info = None
    if telegram.signer:
        signer_info = {
            'id': telegram.signer.id,
            'displayName': telegram.signer.full_name or telegram.signer.username,
            'deptId': telegram.signer.department.id if telegram.signer.department else None,
            'deptName': telegram.signer.department.name if telegram.signer.department else ''
        }
    
    return render(request, 'core/edit_telegram.html', {
        'telegram': telegram,
        'recipients_data_json': recipients_data,
        'approvers_data_json': approvers_data,
        'signer_info_json': signer_info,
    })

@login_required
def copy_telegram(request, pk):
    original = get_object_or_404(Telegram, pk=pk)
    new = Telegram.objects.create(
        author=request.user,
        text=original.text,
        priority=original.priority,
        requires_approval=original.requires_approval,
        requires_signature=original.requires_signature,
        status=Telegram.Status.DRAFT,
        author_department=request.user.department.name if request.user.department else ''
    )
    for recipient in original.recipients.all():
        TelegramRecipient.objects.create(telegram=new, user=recipient.user, status='PENDING')
    if original.requires_approval:
        new.approvers.set(original.approvers.all())
        for approver in original.approvers.all():
            TelegramRecipient.objects.get_or_create(telegram=new, user=approver, defaults={'status': 'PENDING'})
    if original.requires_signature and original.signer:
        new.signer = original.signer
        new.save()
        TelegramRecipient.objects.get_or_create(telegram=new, user=original.signer, defaults={'status': 'PENDING'})
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
    user = request.user
    
    # Проверка доступа: автор, получатель, согласующий, подписант или админ
    has_access = (
        user == telegram.author or
        user.role == 'ADMIN' or
        telegram.recipients.filter(user=user).exists() or
        telegram.approvers.filter(id=user.id).exists() or
        telegram.signer == user
    )
    if not has_access:
        messages.error(request, 'У вас нет доступа к этой телеграмме.')
        return redirect('telegram_journal')
    
    # Переменные для шаблона (аналогично view_telegram)
    user_recipient = telegram.recipients.filter(user=user).first()
    context = {
        'telegram': telegram,
        'user_recipient_id': user_recipient.id if user_recipient else None,
        'user_recipient_status': user_recipient.status if user_recipient else None,
        'user_is_approver': user in telegram.approvers.all(),
        'user_is_signer': user == telegram.signer,
        'user_is_author': user == telegram.author,
    }
    return render(request, 'core/print_telegram.html', context)

# ---------- Управление статусами ----------
@login_required
def send_to_approval(request, pk):
    telegram = get_object_or_404(Telegram, pk=pk)
    if request.user != telegram.author:
        messages.error(request, 'Только автор может отправить на согласование.')
        return redirect('telegram_journal')
    try:
        telegram.transition_to(Telegram.Status.ON_APPROVAL)
        messages.success(request, 'Телеграмма отправлена на согласование.')
    except ValueError as e:
        messages.error(request, str(e))
    return redirect('telegram_journal')

@login_required
def approve_telegram(request, pk):
    telegram = get_object_or_404(Telegram, pk=pk)
    try:
        telegram.approve(request.user)
        messages.success(request, 'Вы согласовали телеграмму.')
    except (PermissionError, ValueError) as e:
        messages.error(request, str(e))
    return redirect('telegram_journal')

@login_required
def send_to_signature(request, pk):
    telegram = get_object_or_404(Telegram, pk=pk)
    if request.user != telegram.author:
        messages.error(request, 'Только автор может отправить на подпись.')
        return redirect('telegram_journal')
    try:
        telegram.transition_to(Telegram.Status.ON_SIGNATURE)
        messages.success(request, 'Телеграмма отправлена на подписание.')
    except ValueError as e:
        messages.error(request, str(e))
    return redirect('telegram_journal')

@login_required
def sign_telegram(request, pk):
    telegram = get_object_or_404(Telegram, pk=pk)
    try:
        data = f"{telegram.id}{telegram.text}{telegram.author.id}{telegram.created_at.isoformat()}{settings.SECRET_KEY}"
        signature = hashlib.sha256(data.encode()).hexdigest()
        telegram.sign(request.user, signature)
        messages.success(request, 'Телеграмма подписана.')
    except (PermissionError, ValueError) as e:
        messages.error(request, str(e))
    return redirect('telegram_journal')

@login_required
def send_telegram_to_delivery(request, pk):
    telegram = get_object_or_404(Telegram, pk=pk)
    if request.user != telegram.author:
        messages.error(request, 'Не автор')
        return redirect('telegram_journal')
    try:
        telegram.send_to_delivery()
        messages.success(request, 'Телеграмма отправлена в доставку.')
    except ValueError as e:
        messages.error(request, str(e))
    return redirect('telegram_journal')

@login_required
def reject_telegram(request, pk):
    telegram = get_object_or_404(Telegram, pk=pk)
    if request.user != telegram.author and request.user not in telegram.approvers.all() and request.user != telegram.signer:
        messages.error(request, 'У вас нет прав на отклонение.')
        return redirect('telegram_journal')
    
    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()
        if not reason:
            messages.error(request, 'Укажите причину отказа.')
            return redirect('telegram_journal')
        try:
            telegram.reject(request.user, reason)
            messages.success(request, 'Телеграмма отклонена.')
        except ValueError as e:
            messages.error(request, str(e))
    else:
        # GET-запрос – просто показываем страницу (лучше использовать модальное окно)
        messages.error(request, 'Используйте форму для отказа.')
    return redirect('telegram_journal')

@login_required
def reject_telegram_with_reason(request, pk):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Метод не разрешён'}, status=405)
    
    try:
        data = json.loads(request.body)
        reason = data.get('reason', '').strip()
        if not reason:
            return JsonResponse({'success': False, 'message': 'Укажите причину отказа'})
        
        telegram = get_object_or_404(Telegram, pk=pk)
        if not (request.user == telegram.author or request.user in telegram.approvers.all() or request.user == telegram.signer):
            return JsonResponse({'success': False, 'message': 'Нет прав на отклонение'})
        
        telegram.reject(request.user, reason)
        return JsonResponse({'success': True, 'message': 'Телеграмма отклонена'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})

@login_required
def mark_as_read(request, recipient_id):
    recipient = get_object_or_404(TelegramRecipient, id=recipient_id, user=request.user)
    if recipient.status == 'PENDING':
        recipient.status = 'READ'
        recipient.read_at = timezone.now()
        recipient.save()
        telegram = recipient.telegram
        if telegram.recipients.filter(status='PENDING').count() == 0:
            try:
                telegram.transition_to(Telegram.Status.DELIVERED)
            except ValueError:
                pass
        messages.success(request, 'Телеграмма отмечена как прочитанная.')
    else:
        messages.warning(request, 'Телеграмма уже прочитана.')
    return redirect('telegram_journal')

@login_required
def mark_as_read_and_view(request, recipient_id):
    recipient = get_object_or_404(TelegramRecipient, id=recipient_id, user=request.user)
    if recipient.status == 'PENDING':
        recipient.status = 'READ'
        recipient.read_at = timezone.now()
        recipient.save()
        telegram = recipient.telegram
        if telegram.recipients.filter(status='PENDING').count() == 0:
            try:
                telegram.transition_to(Telegram.Status.DELIVERED)
            except ValueError:
                pass
        messages.success(request, 'Телеграмма отмечена как прочитанная.')
        # Редирект на вкладку "Ознакомлен" в журнале
        return redirect(reverse('telegram_journal') + '?type=incoming&status=read')
    else:
        messages.warning(request, 'Телеграмма уже прочитана.')
        return redirect('telegram_journal')

# ---------- Массовые операции ----------
@login_required
def send_telegram(request):
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
            if tg.status == Telegram.Status.DRAFT:
                if tg.requires_approval:
                    tg.transition_to(Telegram.Status.ON_APPROVAL)
                elif tg.requires_signature:
                    tg.transition_to(Telegram.Status.ON_SIGNATURE)
                else:
                    tg.transition_to(Telegram.Status.DELIVERY)
                count += 1
            elif tg.status == Telegram.Status.SIGNED:
                tg.transition_to(Telegram.Status.DELIVERY)
                count += 1
            elif tg.status == Telegram.Status.APPROVED and tg.requires_signature:
                tg.transition_to(Telegram.Status.ON_SIGNATURE)
                count += 1
            # можно добавить другие статусы по необходимости
        return JsonResponse({'success': True, 'message': f'Обработано {count} телеграмм'})
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
        # Не сбрасываем телеграммы, которые уже в доставке или доставлены
        telegrams = Telegram.objects.filter(
            id__in=ids,
            author=request.user
        ).exclude(status__in=[Telegram.Status.DELIVERY, Telegram.Status.DELIVERED])
        
        count = 0
        for tg in telegrams:
            tg.status = Telegram.Status.DRAFT
            tg.signature = None
            tg.signed_at = None
            tg.signatures.all().delete()
            tg.approvals_received = 0
            tg.rejection_reason = None
            tg.rejected_by = None
            tg.rejected_at = None
            tg.save()
            count += 1
        if count < len(ids):
            return JsonResponse({'success': True, 'message': f'Сброшено {count} телеграмм (доставленные и в доставке пропущены)'})
        return JsonResponse({'success': True, 'message': f'Сброшено {count} телеграмм'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})

@login_required
def mass_delete(request):
    if request.method == 'POST':
        ids_str = request.POST.get('selected_ids', '')
        if ids_str:
            ids = ids_str.split(',')
            # Удаляем только черновики и отклонённые
            deleted_count = Telegram.objects.filter(
                id__in=ids,
                author=request.user,
                status__in=[Telegram.Status.DRAFT, Telegram.Status.REJECTED]
            ).delete()[0]
            if deleted_count:
                messages.success(request, f'Удалено {deleted_count} телеграмм.')
            else:
                messages.warning(request, 'Не удалось удалить: возможно, вы не автор или статус не позволяет.')
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
                    requires_signature=original.requires_signature,
                    status=Telegram.Status.DRAFT
                )
                for recipient in original.recipients.all():
                    TelegramRecipient.objects.create(telegram=new, user=recipient.user, status='PENDING')
                if original.requires_approval:
                    new.approvers.set(original.approvers.all())
                    for approver in original.approvers.all():
                        TelegramRecipient.objects.get_or_create(telegram=new, user=approver, defaults={'status': 'PENDING'})
                if original.requires_signature and original.signer:
                    new.signer = original.signer
                    new.save()
                    TelegramRecipient.objects.get_or_create(telegram=new, user=original.signer, defaults={'status': 'PENDING'})
            messages.success(request, 'Телеграмма скопирована.')
        else:
            messages.warning(request, 'Ничего не выбрано.')
    return redirect('telegram_journal')

@login_required
def telegram_journal(request):
    # Редирект на вкладку "Создание", если параметры не указаны
    if not request.GET.get('type') and not request.GET.get('stage'):
        return redirect(f"{request.path}?type=outgoing&stage=create")

    user = request.user

    # Базовый QuerySet в зависимости от роли
    if user.role == 'ADMIN':
        telegrams = Telegram.objects.all()
    elif user.role == 'OPERATOR':
        try:
            zone = OperatorZone.objects.get(operator=user)
            depts = zone.departments.all()
            telegrams = Telegram.objects.filter(
                Q(author__department__in=depts) | Q(recipients__user__department__in=depts)
            ).distinct()
        except OperatorZone.DoesNotExist:
            telegrams = Telegram.objects.none()
    else:  # USER
        telegrams = Telegram.objects.filter(Q(author=user) | Q(recipients__user=user)).distinct()

    # Исключаем чужие черновики для не-USER
    if user.role != 'USER':
        telegrams = telegrams.filter(
            Q(author=user) | ~Q(status=Telegram.Status.DRAFT)
        )

    # Фильтрация по вкладкам
    type_filter = request.GET.get('type')
    incoming_status = request.GET.get('status')
    outgoing_stage = request.GET.get('stage')

    if type_filter == 'incoming':
        telegrams = telegrams.filter(recipients__user=user).distinct()
        if incoming_status == 'new':
            if user.role == 'USER':
                is_approver = Exists(Telegram.approvers.through.objects.filter(telegram=OuterRef('pk'), user=user))
                q = Q(status=Telegram.Status.DELIVERY)
                q |= (Q(status=Telegram.Status.ON_APPROVAL) & is_approver)
                q |= (Q(status=Telegram.Status.ON_SIGNATURE) & Q(signer=user))
                telegrams = telegrams.filter(q)
            else:
                telegrams = telegrams.filter(status__in=[Telegram.Status.ON_APPROVAL, Telegram.Status.ON_SIGNATURE, Telegram.Status.DELIVERY])
        elif incoming_status == 'read':
            recipient_read_exists = TelegramRecipient.objects.filter(
                telegram=OuterRef('pk'), user=user, status='READ'
            )
            telegrams = telegrams.filter(Exists(recipient_read_exists))
    elif type_filter == 'outgoing':
        if user.role == 'OPERATOR' and outgoing_stage in ('delivery', 'delivered'):
            telegrams = telegrams
        else:
            telegrams = telegrams.filter(author=user)
        if outgoing_stage == 'create':
            telegrams = telegrams.exclude(status__in=[Telegram.Status.DELIVERY, Telegram.Status.DELIVERED])
        elif outgoing_stage == 'approval':
            telegrams = telegrams.filter(status__in=[Telegram.Status.ON_APPROVAL, Telegram.Status.APPROVED])
        elif outgoing_stage == 'signature':
            telegrams = telegrams.filter(status__in=[Telegram.Status.ON_SIGNATURE, Telegram.Status.SIGNED])
        elif outgoing_stage == 'delivery':
            telegrams = telegrams.filter(status=Telegram.Status.DELIVERY)
        elif outgoing_stage == 'delivered':
            telegrams = telegrams.filter(status=Telegram.Status.DELIVERED)

    # Дополнительные фильтры (форма)
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

    telegrams = telegrams.order_by('-created_at')

    # Параметры фильтрации без page для пагинации
    filter_params = request.GET.copy()
    filter_params.pop('page', None)

    paginator = Paginator(telegrams, 15)   # количество на страницу
    page_obj = paginator.get_page(request.GET.get('page'))

    # Функция для добавления 3 часов к строке времени
    def add_three_hours(time_str):
        if not time_str or time_str == '—':
            return time_str
        try:
            # формат: "dd.mm.yyyy HH:MM"
            date_part, time_part = time_str.split(' ')
            hour = int(time_part.split(':')[0])
            minute = time_part.split(':')[1]
            new_hour = hour + 3
            if new_hour >= 24:
                new_hour -= 24
            return f"{date_part} {new_hour:02d}:{minute}"
        except:
            return time_str

    # Подготовка данных для отображения
    for t in page_obj:
        t.author_department = t.author.department.code if t.author.department else '—'
        t.recipient_departments = list(set(
            r.user.department.code for r in t.recipients.select_related('user__department') if r.user.department
        ))
        t.approver_departments = list(set(
            a.department.code for a in t.approvers.select_related('department') if a.department
        )) if t.requires_approval else []
        t.approval_count = t.approvals_received
        user_recipient = t.recipients.filter(user=user).first()
        t.user_recipient_id = user_recipient.id if user_recipient else None

        # --- recipients_status_json (с добавлением 3 часов) ---
        recipients_status = t.get_recipients_status_list()
        for item in recipients_status:
            if item.get('read_at'):
                item['read_at'] = add_three_hours(item['read_at'])
        t.recipients_status_json = json.dumps(recipients_status, ensure_ascii=False)

        # --- approvers_status_json (с добавлением 3 часов) ---
        approvers_status = t.get_approvers_status_list()
        for item in approvers_status:
            if item.get('signed_at'):
                item['signed_at'] = add_three_hours(item['signed_at'])
        t.approvers_status_json = json.dumps(approvers_status, ensure_ascii=False)

        # --- signer_info_json (с добавлением 3 часов) ---
        if t.signer:
            signer_info = t.get_signer_info()
            if signer_info.get('signed_at'):
                signer_info['signed_at'] = add_three_hours(signer_info['signed_at'])
            t.signer_info_json = json.dumps(signer_info, ensure_ascii=False)
        else:
            t.signer_info_json = 'null'

        # --- rejection_data_json (с добавлением 3 часов) ---
        rejection_data = {
            'reason': t.rejection_reason,
            'rejected_by': t.rejected_by.full_name or t.rejected_by.username if t.rejected_by else None,
            'rejected_at': add_three_hours(t.rejected_at.strftime('%d.%m.%Y %H:%M')) if t.rejected_at else None,
        }
        t.rejection_data_json = json.dumps(rejection_data, ensure_ascii=False)

        # --- для оператора: recipients_manage_json (с добавлением 3 часов) ---
        if user.role == 'OPERATOR':
            recipients_list = []
            for r in t.recipients.select_related('user__department'):
                read_at_str = r.read_at.strftime('%d.%m.%Y %H:%M') if r.read_at else None
                last_notified_str = r.last_notified_at.strftime('%d.%m.%Y %H:%M') if r.last_notified_at else None
                recipients_list.append({
                    'id': r.id,
                    'full_name': r.user.full_name or r.user.username,
                    'department': r.user.department.code if r.user.department else '—',
                    'status': r.status,
                    'read_at': add_three_hours(read_at_str),
                    'delivery_method': r.delivery_method,
                    'is_notified': r.is_notified,
                    'last_notified_at': add_three_hours(last_notified_str),
                })
            t.recipients_manage_json = json.dumps(recipients_list, ensure_ascii=False)

    # Счётчик новых
    if user.role == 'USER':
        is_approver = Exists(Telegram.approvers.through.objects.filter(telegram=OuterRef('telegram__pk'), user=user))
        q_new = Q(telegram__status=Telegram.Status.DELIVERY)
        q_new |= (Q(telegram__status=Telegram.Status.ON_APPROVAL) & is_approver)
        q_new |= (Q(telegram__status=Telegram.Status.ON_SIGNATURE) & Q(telegram__signer=user))
        new_count = TelegramRecipient.objects.filter(user=user, status='PENDING').filter(q_new).distinct().count()
    else:
        new_count = TelegramRecipient.objects.filter(user=user, status='PENDING').count()

    departments = Department.objects.all()
    return render(request, 'core/journal.html', {
        'page_obj': page_obj,
        'form': form,
        'departments': departments,
        'user_role': user.role,
        'new_count': new_count,
        'filter_params': filter_params,
    })

# ---------- Экспорт ----------
def export_telegrams_excel(request):
    user = request.user
    if user.role == 'ADMIN':
        telegrams = Telegram.objects.all()
    elif user.role == 'OPERATOR':
        telegrams = Telegram.objects.filter(status=Telegram.Status.DELIVERY)
    else:
        telegrams = Telegram.objects.filter(Q(author=user) | Q(recipients__user=user)).distinct()

    form = TelegramFilterForm(request.GET)
    if form.is_valid():
        data = form.cleaned_data
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
    ws.append(['Номер', 'Дата создания', 'Отправитель', 'Получатели', 'Текст', 'Статус'])
    for t in telegrams:
        recipients_names = ', '.join([r.user.full_name or r.user.username for r in t.recipients.all()])
        ws.append([
            t.number or '',
            t.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            t.author.full_name or t.author.username,
            recipients_names,
            t.text,
            t.get_status_display()
        ])
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=telegrams.xlsx'
    wb.save(response)
    return response

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
    ws.append(['ID', 'Дата', 'Отправитель', 'Получатели', 'Статус'])
    for t in telegrams:
        recipients_names = ', '.join([r.user.full_name or r.user.username for r in t.recipients.all()])
        ws.append([
            t.id,
            t.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            t.author.full_name or t.author.username,
            recipients_names,
            t.get_status_display()
        ])
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=report.xlsx'
    wb.save(response)
    return response

# ---------- AJAX ----------
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
        data = [{'id': u['id'], 'full_name': u['full_name'] or u['username'], 'department_name': u['department__name'] or '—'} for u in users]
        return JsonResponse(data, safe=False)
    return JsonResponse([], safe=False)

# ---------- Управление подразделениями (НСИ) ----------
@login_required
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

# ---------- Управление должностями ----------
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

# ---------- Зоны операторов ----------
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

@login_required
@user_passes_test(is_operator)
def operator_dashboard(request):
    try:
        zone = OperatorZone.objects.get(operator=request.user)
        departments_in_zone = zone.departments.all()
    except OperatorZone.DoesNotExist:
        departments_in_zone = []

    # Базовые фильтры (по зоне)
    base_filter = Q(author__department__in=departments_in_zone) | Q(recipients__user__department__in=departments_in_zone)
    if not departments_in_zone:
        base_filter = Q(pk__in=[])  # нет подразделений → ничего не показываем

    # 1. Телеграммы, ожидающие согласования (ON_APPROVAL)
    pending_approvals_qs = Telegram.objects.filter(
        base_filter,
        status=Telegram.Status.ON_APPROVAL
    ).distinct()
    # фильтры дат, номера (можно добавить из формы)
    form = OperatorFilterForm(request.GET)
    if form.is_valid():
        data = form.cleaned_data
        if data.get('start_date'):
            pending_approvals_qs = pending_approvals_qs.filter(created_at__date__gte=data['start_date'])
        if data.get('end_date'):
            pending_approvals_qs = pending_approvals_qs.filter(created_at__date__lte=data['end_date'])
        if data.get('number'):
            pending_approvals_qs = pending_approvals_qs.filter(number__icontains=data['number'])
        # если выбран department, но в форме он может быть ограничен зоной
        if data.get('department'):
            pending_approvals_qs = pending_approvals_qs.filter(
                Q(author__department=data['department']) | Q(recipients__user__department=data['department'])
            ).distinct()

    pending_approvals = []
    for tg in pending_approvals_qs.select_related('author').prefetch_related('approvers', 'signatures'):
        approvers = tg.approvers.all()
        signed_ids = set(tg.signatures.values_list('user_id', flat=True))
        pending = [a for a in approvers if a.id not in signed_ids]
        pending_approvals.append({
            'telegram': tg,
            'approvers_list': [a.full_name or a.username for a in approvers],
            'pending_approvers': [a.full_name or a.username for a in pending],
            'waiting_display': _calc_waiting(tg.created_at),
        })

    # 2. Телеграммы, ожидающие подписи (ON_SIGNATURE)
    pending_signatures_qs = Telegram.objects.filter(
        base_filter,
        status=Telegram.Status.ON_SIGNATURE,
        signer__isnull=False
    ).distinct()
    if form.is_valid():
        data = form.cleaned_data
        if data.get('start_date'):
            pending_signatures_qs = pending_signatures_qs.filter(created_at__date__gte=data['start_date'])
        if data.get('end_date'):
            pending_signatures_qs = pending_signatures_qs.filter(created_at__date__lte=data['end_date'])
        if data.get('number'):
            pending_signatures_qs = pending_signatures_qs.filter(number__icontains=data['number'])
        if data.get('department'):
            pending_signatures_qs = pending_signatures_qs.filter(
                Q(author__department=data['department']) | Q(recipients__user__department=data['department'])
            ).distinct()
    pending_signatures = []
    for tg in pending_signatures_qs.select_related('author', 'signer'):
        pending_signatures.append({
            'telegram': tg,
            'signer_name': tg.signer.full_name or tg.signer.username,
            'waiting_display': _calc_waiting(tg.created_at),
        })

    # 3. Доставка, непрочитанные получатели
    pending_reads_qs = TelegramRecipient.objects.filter(
        telegram__status=Telegram.Status.DELIVERY,
        status='PENDING',
        user__department__in=departments_in_zone
    ).select_related('telegram__author', 'user').order_by('-telegram__created_at')
    if form.is_valid():
        data = form.cleaned_data
        if data.get('start_date'):
            pending_reads_qs = pending_reads_qs.filter(telegram__created_at__date__gte=data['start_date'])
        if data.get('end_date'):
            pending_reads_qs = pending_reads_qs.filter(telegram__created_at__date__lte=data['end_date'])
        if data.get('number'):
            pending_reads_qs = pending_reads_qs.filter(telegram__number__icontains=data['number'])
        if data.get('department'):
            pending_reads_qs = pending_reads_qs.filter(user__department=data['department'])
    pending_reads = []
    for recip in pending_reads_qs:
        recip.waiting_display = _calc_waiting(recip.telegram.created_at)
        pending_reads.append(recip)

    # Фильтры для формы (отображать только подразделения из зоны)
    form.fields['department'].queryset = departments_in_zone

    # Обработка POST (уведомления)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'notify_recipient':
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
                messages.info(request, 'Уведомление отправлено.')
        elif action == 'notify_approvers' or action == 'notify_signer':
            telegram_id = request.POST.get('telegram_id')
            if telegram_id:
                tg = get_object_or_404(Telegram, id=telegram_id)
                # Можно отправить уведомление всем согласующим или подписанту
                # Здесь просто логируем, но можно расширить
                messages.info(request, f'Уведомление для телеграммы №{tg.number} зарегистрировано.')
        return redirect('operator_dashboard')

    return render(request, 'core/operator_dashboard.html', {
        'form': form,
        'pending_approvals': pending_approvals,
        'pending_signatures': pending_signatures,
        'pending_reads': pending_reads,
    })

def _calc_waiting(created_at):
    delta = timezone.now() - created_at
    seconds = delta.total_seconds()
    if seconds < 300:
        return "менее 5 минут"
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
    return ' '.join(parts) if parts else "менее 5 минут"

# ---------- Отчёты ----------
@login_required
def reports(request):
    if request.user.role not in ['ADMIN', 'OPERATOR']:
        messages.error(request, 'Доступ запрещён.')
        return redirect('home')
    
    # Словарь для отображения статусов
    STATUS_DISPLAY = {
        'DRAFT': 'Черновик',
        'ON_APPROVAL': 'На согласовании',
        'APPROVED': 'Согласовано',
        'ON_SIGNATURE': 'На подписании',
        'SIGNED': 'Подписано',
        'DELIVERY': 'Доставка',
        'DELIVERED': 'Доставлено',
        'REJECTED': 'Отказано',
    }
    
    # Получение фильтров из GET
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    status_filter = request.GET.get('status')
    department_id = request.GET.get('department')
    number_filter = request.GET.get('number')
    
    # Базовый QuerySet для телеграмм (с учётом прав)
    if request.user.role == 'ADMIN':
        telegrams = Telegram.objects.all()
    else:  # OPERATOR
        try:
            zone = OperatorZone.objects.get(operator=request.user)
            depts = zone.departments.all()
            telegrams = Telegram.objects.filter(Q(author__department__in=depts) | Q(recipients__user__department__in=depts)).distinct()
        except OperatorZone.DoesNotExist:
            telegrams = Telegram.objects.none()
    
    # Применяем фильтры
    if start_date:
        telegrams = telegrams.filter(created_at__date__gte=start_date)
    if end_date:
        telegrams = telegrams.filter(created_at__date__lte=end_date)
    if status_filter:
        telegrams = telegrams.filter(status=status_filter)
    if department_id:
        telegrams = telegrams.filter(author__department_id=department_id)
    if number_filter:
        telegrams = telegrams.filter(number__icontains=number_filter)
    
    # Общая статистика
    total = telegrams.count()
    delivered_count = telegrams.filter(status=Telegram.Status.DELIVERED).count()
    read_count = TelegramRecipient.objects.filter(telegram__in=telegrams, status='READ').values('telegram').distinct().count()
    in_progress_count = telegrams.filter(status__in=[Telegram.Status.ON_APPROVAL, Telegram.Status.ON_SIGNATURE, Telegram.Status.DELIVERY]).count()
    
    # Распределение по статусам (для круговой диаграммы)
    status_counts_qs = telegrams.values('status').annotate(count=Count('status'))
    status_labels = [STATUS_DISPLAY.get(item['status'], item['status']) for item in status_counts_qs]
    status_data = [item['count'] for item in status_counts_qs]
    
    # Словарь статусов для отображения под диаграммой (статус → количество)
    status_counts = {STATUS_DISPLAY.get(item['status'], item['status']): item['count'] for item in status_counts_qs}
    
    # Динамика по дням (последние 30 дней или за период)
    if start_date and end_date:
        date_start = datetime.strptime(start_date, '%Y-%m-%d').date()
        date_end = datetime.strptime(end_date, '%Y-%m-%d').date()
        delta = (date_end - date_start).days + 1
    else:
        date_end = timezone.now().date()
        date_start = date_end - timedelta(days=30)
        delta = 31
    
    from django.db.models.functions import TruncDate
    daily_stats = telegrams.filter(created_at__date__gte=date_start, created_at__date__lte=date_end) \
                           .annotate(date=TruncDate('created_at')) \
                           .values('date').annotate(cnt=Count('id')).order_by('date')
    daily_labels = [stat['date'].strftime('%d.%m') for stat in daily_stats]
    daily_counts = [stat['cnt'] for stat in daily_stats]
    
    # Пагинация для детального списка
    from django.core.paginator import Paginator
    paginator = Paginator(telegrams.order_by('-created_at'), 20)
    page_number = request.GET.get('page')
    telegrams_page = paginator.get_page(page_number)
    
    # Для фильтра "Подразделение" в форме
    departments = Department.objects.all()
    
    context = {
        'total': total,
        'delivered_count': delivered_count,
        'read_count': read_count,
        'in_progress_count': in_progress_count,
        'status_counts': status_counts,
        'status_labels': status_labels,
        'status_data': status_data,
        'daily_labels': daily_labels,
        'daily_counts': daily_counts,
        'telegrams_page': telegrams_page,
        'departments': departments,
        'start_date': start_date,
        'end_date': end_date,
    }
    return render(request, 'core/reports.html', context)

# ---------- Другие вспомогательные ----------
@login_required
def department_users(request, department_id):
    department = get_object_or_404(Department, id=department_id)
    users = User.objects.filter(department=department, role='USER')
    return render(request, 'core/department_users.html', {
        'department': department,
        'users': users,
    })

@login_required
def telegram_detail(request, telegram_id):
    telegram = get_object_or_404(Telegram, id=telegram_id)
    # Проверка прав
    if not (request.user == telegram.author or 
            request.user in telegram.recipients.values_list('user', flat=True) or
            request.user in telegram.approvers.all() or
            request.user.role == 'ADMIN'):
        messages.error(request, 'У вас нет доступа к этой телеграмме.')
        return redirect('telegram_journal')
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

def get_positions(request, department_id):
    positions = Position.objects.filter(department_id=department_id).values('id', 'name')
    return JsonResponse(list(positions), safe=False)

def get_users(request, position_id):
    users = User.objects.filter(position_id=position_id, role='USER').values('id', 'full_name', 'username')
    return JsonResponse(list(users), safe=False)

def get_approvers_by_department(request, department_id):
    users = User.objects.filter(
        Q(role='ADMIN') | Q(position__department_id=department_id, position__name__icontains='начальник')
    ).distinct().values('id', 'full_name', 'username')
    return JsonResponse(list(users), safe=False)

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
    data = [
        {
            'id': dept_id,
            'name': info['name'],
            'users': info['users']
        }
        for dept_id, info in dept_dict.items()
    ]
    return JsonResponse(data, safe=False)

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
                update_session_auth_hash(request, user)
                messages.success(request, 'Пароль изменён.')
                return redirect('profile')
            else:
                messages.error(request, 'Ошибка при смене пароля.')
    else:
        form = UserProfileForm(instance=user)
        password_form = PasswordChangeForm(user)

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

class CustomLoginView(LoginView):
    template_name = 'core/login.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['admins'] = User.objects.filter(role='ADMIN', is_active=True)
        return context
    
@login_required
@user_passes_test(is_operator)
@require_http_methods(['POST'])
def notify_and_mark_read(request):
    data = json.loads(request.body)
    recipient_id = data.get('recipient_id')
    method = data.get('method')
    if not recipient_id or method not in dict(TelegramRecipient.DELIVERY_METHOD_CHOICES):
        return JsonResponse({'success': False, 'message': 'Неверные данные'})
    recipient = get_object_or_404(TelegramRecipient, id=recipient_id)
    # Проверка прав
    try:
        zone = OperatorZone.objects.get(operator=request.user)
        if recipient.user.department not in zone.departments.all():
            return JsonResponse({'success': False, 'message': 'Нет прав'})
    except OperatorZone.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Нет прав'})
    
    recipient.delivery_method = method
    recipient.is_notified = True
    recipient.last_notified_at = timezone.now()
    if recipient.status != 'READ':
        recipient.status = 'READ'
        recipient.read_at = timezone.now()
    recipient.save()
    
    tg = recipient.telegram
    if tg.recipients.filter(status='PENDING').count() == 0:
        try:
            tg.transition_to(Telegram.Status.DELIVERED)
        except ValueError:
            pass
    return JsonResponse({'success': True, 'message': 'Уведомление отправлено, получатель отмечен прочитанным'})