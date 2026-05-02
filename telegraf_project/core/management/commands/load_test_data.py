from django.core.management.base import BaseCommand
from django.contrib.auth.hashers import make_password
from core.models import Department, Position, User, Role

class Command(BaseCommand):
    help = 'Создаёт тестовые подразделения, должности (привязанные к подразделениям) и пользователей'

    def handle(self, *args, **options):
        # 1. Подразделения
        departments_data = [
            {'code': 'ВЧД-1', 'name': 'Минское вагонное депо'},
            {'code': 'ТЧ-1', 'name': 'Локомотивное депо Минск'},
            {'code': 'ПЧ-1', 'name': 'Оршанская дистанция пути'},
            {'code': 'ШЧ-1', 'name': 'Минская дистанция сигнализации и связи'},
        ]
        departments = {}
        for data in departments_data:
            dept, created = Department.objects.get_or_create(
                code=data['code'],
                defaults={'name': data['name']}
            )
            departments[data['code']] = dept
            self.stdout.write(f"{'Создано' if created else 'Существует'} подразделение: {dept.code} – {dept.name}")

        # 2. Для каждого подразделения создаём свои должности
        position_names = ['Начальник', 'Заместитель начальника', 'Инженер', 'Техник', 'Оператор']
        positions_by_dept = {}
        for dept in departments.values():
            dept_positions = {}
            for pos_name in position_names:
                pos, created = Position.objects.get_or_create(
                    name=pos_name,
                    department=dept
                )
                dept_positions[pos_name] = pos
                if created:
                    self.stdout.write(f"Создана должность: {pos_name} в {dept.code}")
            positions_by_dept[dept.id] = dept_positions

        # 3. Пользователи: для каждого подразделения по 5, логины user1_1..user4_5
        user_counter = 1
        users_created = 0
        password = 'password123'

        for dept in departments.values():
            dept_positions = positions_by_dept[dept.id]
            for i in range(1, 6):
                username = f"user{user_counter}_{i}"
                full_name = f"Сотрудник {user_counter}_{i}"
                pos_name = position_names[(i - 1) % len(position_names)]
                position = dept_positions[pos_name]
                email = f"{username}@example.com"
                phone = f"+3752912345{user_counter}{i}"

                user, created = User.objects.get_or_create(
                    username=username,
                    defaults={
                        'password': make_password(password),
                        'full_name': full_name,
                        'role': Role.USER,
                        'department': dept,
                        'position': position,
                        'email': email,
                        'phone': phone,
                    }
                )
                if created:
                    users_created += 1
                    self.stdout.write(f"Создан пользователь: {username} ({full_name}) – {dept.code}")
                else:
                    self.stdout.write(f"Пользователь {username} уже существует")
            user_counter += 1

        self.stdout.write(self.style.SUCCESS(
            f"Готово! Создано {len(departments)} подразделений и {users_created} новых пользователей. "
            f"Пароль для всех: {password}"
        ))