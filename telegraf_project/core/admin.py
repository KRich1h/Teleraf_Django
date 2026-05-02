from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.contrib.admin import SimpleListFilter
from .models import User, Department, Position, OperatorZone, Telegram, Log

admin.site.unregister(Group)

admin.site.site_header = "АС Телеграф"
admin.site.site_title = "АС Телеграф"
admin.site.index_title = "Управление системой"

class UserByDepartmentFilter(SimpleListFilter):
    title = 'Пользователь'
    parameter_name = 'user'

    def lookups(self, request, model_admin):
        department_id = request.GET.get('user__department__id__exact')
        if department_id:
            users = User.objects.filter(department_id=department_id)
        else:
            users = User.objects.all()
        return [(u.id, f"{u.username} ({u.full_name})") for u in users]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(user_id=self.value())
        return queryset

@admin.register(Telegram)
class TelegramAdmin(admin.ModelAdmin):
    list_display = ('id', 'number', 'author', 'text_preview', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('text', 'author__username', 'number')

    def text_preview(self, obj):
        return obj.text[:50] + '…' if len(obj.text) > 50 else obj.text
    text_preview.short_description = 'Текст'

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'full_name', 'department', 'position', 'role', 'is_active')
    list_filter = ('department', 'position', 'role', 'is_active')
    search_fields = ('username', 'full_name', 'email')
    autocomplete_fields = ('department', 'position')

    fieldsets = UserAdmin.fieldsets + (
        ('Дополнительная информация', {'fields': ('role', 'full_name', 'phone', 'department', 'position')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Дополнительная информация', {'fields': ('role', 'full_name', 'phone', 'department', 'position')}),
    )

@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'employee_count')
    search_fields = ('code', 'name')
    def employee_count(self, obj):
        return obj.user_set.count()
    employee_count.short_description = 'Кол-во сотрудников'

@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = ('name', 'department')
    list_filter = ('department',)
    search_fields = ('name',)

@admin.register(OperatorZone)
class OperatorZoneAdmin(admin.ModelAdmin):
    list_display = ('operator',)
    filter_horizontal = ('departments',)

@admin.register(Log)
class LogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'user', 'action', 'details')  
    list_filter = (
        ('user__department', admin.RelatedOnlyFieldListFilter),
        UserByDepartmentFilter,
        'action',
        'timestamp',
    )
    search_fields = ('user__username', 'user__full_name', 'details')
    readonly_fields = ('timestamp', 'user', 'action', 'details', 'ip_address')
    date_hierarchy = 'timestamp'