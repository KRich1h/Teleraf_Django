from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from .models import Department, Position, OperatorZone, TelegramTemplate, User

class TelegramForm(forms.Form):
    priority = forms.ChoiceField(
        choices=[('NORMAL', 'Обычная'), ('URGENT', 'Срочная')],
        widget=forms.RadioSelect,
        initial='NORMAL',
        label='Приоритет'
    )
    text = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
        label='Текст телеграммы'
    )
    requires_approval = forms.BooleanField(
        required=False,
        label='Требуется подпись'
    )

class TelegramFilterForm(forms.Form):
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        label='Дата от'
    )
    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        label='Дата до'
    )
    number = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '№'}),
        label='№'
    )
    department = forms.ModelChoiceField(
        queryset=Department.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Подразделение'
    )

class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ['code', 'name']
        widgets = {
            'code': forms.TextInput(attrs={'class': 'form-control'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
        }

class PositionForm(forms.ModelForm):
    class Meta:
        model = Position
        fields = ['name', 'department']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'department': forms.Select(attrs={'class': 'form-select'}),
        }

class OperatorZoneForm(forms.ModelForm):
    class Meta:
        model = OperatorZone
        fields = ['operator', 'departments']
        widgets = {
            'operator': forms.Select(attrs={'class': 'form-select'}),
            'departments': forms.SelectMultiple(attrs={'class': 'form-select', 'size': 8}),
        }

class OperatorFilterForm(forms.Form):
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        label='Дата от'
    )
    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        label='Дата до'
    )
    number = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '№'}),
        label='Номер'
    )
    department = forms.ModelChoiceField(
        queryset=Department.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Подразделение получателя'
    )
    status = forms.ChoiceField(
        choices=[('', 'Все'), ('PENDING', 'Не прочитана'), ('READ', 'Прочитана')],
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Статус'
    )
    
class UserProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['full_name', 'phone', 'email']
        widgets = {
            'full_name': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
        }