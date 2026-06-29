import asyncio
import logging
import re
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    CallbackQuery
)
import aiosqlite

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ================= КОНФИГУРАЦИЯ =================
BOT_TOKEN = '7988135474:AAGgeT1tOlPR-DXhZmpeimukr1uBvL6eAvY'
ADMIN_ID = 358741967

# Константы для новой премии
PLAN_CONVERSION = 8.0
PLAN_UPT = 1.9
BASE_CONV_RATE = 7665
HOURS_NORM = 168

# Начальные значения старых планов магазина
DEFAULT_PLANS = {
    'conversion': 8.0,
    'upt': 1.9,
    'check': 5799.0,
    'personal_cph': 1.5,
    'personal_check': 5247.0
}

STORE_CODE = "8420"
DB_NAME = "reports.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= СОСТОЯНИЯ =================
class BotStates(StatesGroup):
    waiting_for_shop_plans = State()
    waiting_for_personal_plans = State()
    waiting_for_emp_plan = State()
    waiting_for_bonus = State()
    waiting_for_report_date = State()

# ================= БАЗА ДАННЫХ =================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                status TEXT DEFAULT 'pending',
                is_notify INTEGER DEFAULT 1
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY,
                conversion REAL,
                upt REAL,
                check_avg REAL,
                personal_cph REAL,
                personal_check REAL
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                time TEXT,
                to_sum REAL,
                baskets INTEGER,
                items INTEGER,
                visitors INTEGER,
                conversion REAL,
                upt REAL,
                avg_check REAL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS employee_plans (
                name TEXT PRIMARY KEY,
                personal_plan REAL
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS store_daily_history (
                date TEXT PRIMARY KEY,
                revenue REAL,
                traffic INTEGER,
                calculated_baskets REAL,
                calculated_items REAL
            )
        """)
        
        cursor = await db.execute("SELECT count(*) FROM plans")
        count = await cursor.fetchone()
        if count[0] == 0:
            await db.execute(
                "INSERT INTO plans (id, conversion, upt, check_avg, personal_cph, personal_check) VALUES (1, ?, ?, ?, ?, ?)",
                (DEFAULT_PLANS['conversion'], DEFAULT_PLANS['upt'], DEFAULT_PLANS['check'], 
                 DEFAULT_PLANS['personal_cph'], DEFAULT_PLANS['personal_check'])
            )
        else:
            columns = ["personal_cph", "personal_check"]
            for col in columns:
                try:
                    await db.execute(f"ALTER TABLE plans ADD COLUMN {col} REAL DEFAULT 1.0")
                except aiosqlite.OperationalError:
                    pass
                    
        cursor = await db.execute("SELECT count(*) FROM employee_plans")
        if (await cursor.fetchone())[0] == 0:
            default_emps = [
                ('Лилия', 698796), ('Илья', 720786), 
                ('Андрей', 903672), ('Лиза', 252813), 
                ('Улдуз', 575553), ('Леня', 903672)
            ]
            await db.executemany("INSERT INTO employee_plans (name, personal_plan) VALUES (?, ?)", default_emps)

        await db.commit()

async def get_plans():
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT conversion, upt, check_avg, personal_cph, personal_check FROM plans WHERE id=1")
        return await cursor.fetchone()

def after_tax(amount):
    return int(amount * 0.87)

# ================= КЛАВИАТУРЫ =================
def get_main_keyboard(is_admin: bool):
    if is_admin:
        kb = [
            [KeyboardButton(text="Изменить планы"), KeyboardButton(text="Команда")],
            [KeyboardButton(text="Премия"), KeyboardButton(text="Инструкция")]
        ]
    else:
        kb = [
            [KeyboardButton(text="Премия"), KeyboardButton(text="Инструкция")]
        ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_cancel_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Назад")]], resize_keyboard=True)

def get_plans_type_keyboard():
    kb = [
        [InlineKeyboardButton(text="Планы магазина", callback_data="plans_shop")],
        [InlineKeyboardButton(text="Личные планы (Старые)", callback_data="plans_personal")],
        [InlineKeyboardButton(text="Планы сотрудников", callback_data="plans_employees")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ================= ОБРАБОТЧИКИ НАВИГАЦИИ =================
@dp.message(StateFilter('*'), F.text == "Назад")
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    is_admin = (message.from_user.id == ADMIN_ID)
    await message.answer("Главное меню.", reply_markup=get_main_keyboard(is_admin))

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT status FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()

        if user_id == ADMIN_ID:
            if not row:
                await db.execute("INSERT INTO users (user_id, username, full_name, status) VALUES (?, ?, ?, 'approved')", 
                                 (user_id, message.from_user.username, message.from_user.full_name))
                await db.commit()
            await message.answer("Режим администратора.", reply_markup=get_main_keyboard(True))
            return

        if not row:
            await db.execute("INSERT INTO users (user_id, username, full_name, status) VALUES (?, ?, ?, 'pending')", 
                             (user_id, message.from_user.username, message.from_user.full_name))
            await db.commit()
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Одобрить", callback_data=f"approve_{user_id}"),
                InlineKeyboardButton(text="Отклонить", callback_data=f"reject_{user_id}")
            ]])
            await bot.send_message(ADMIN_ID, f"Новая заявка: {message.from_user.full_name}", reply_markup=kb)
            await message.answer("Заявка отправлена. Ожидайте одобрения.")
        elif row[0] == 'approved':
            await message.answer("Добро пожаловать.", reply_markup=get_main_keyboard(False))

# ================= ЗАГРУЗКА АРХИВА (АДМИН) =================
@dp.message(Command("load_archive"))
async def cmd_load_archive(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        data = message.text.replace('/load_archive', '').replace(' ', '').split(',')
        if len(data) == 4:
            revenue, traffic, conversion, upt = map(float, data)
            current_month = datetime.now().strftime('%Y-%m')
            
            async with aiosqlite.connect(DB_NAME) as db:
                if revenue == 0 and traffic == 0 and conversion == 0 and upt == 0:
                    await db.execute('DELETE FROM store_daily_history WHERE date LIKE ?', (f"{current_month}%",))
                    await db.commit()
                    await message.answer("База данных за текущий месяц полностью очищена (все тесты удалены).")
                    return
                
                calc_baskets = traffic * (conversion / 100)
                calc_items = calc_baskets * upt
                archive_date = f"{current_month}-00"
                
                await db.execute('''
                    INSERT OR REPLACE INTO store_daily_history 
                    (date, revenue, traffic, calculated_baskets, calculated_items) 
                    VALUES (?, ?, ?, ?, ?)
                ''', (archive_date, revenue, int(traffic), calc_baskets, calc_items))
                await db.commit()
                
            await message.answer("Архивные точные данные за прошедшие дни успешно загружены в базу.")
        else:
            await message.answer("Ошибка формата. Введите: /load_archive Выручка, Трафик, Конверсия, UPT")
    except Exception as e:
        await message.answer(f"Произошла ошибка при записи: {e}")

# ================= ИЗМЕНЕНИЕ ПЛАНОВ =================
@dp.message(F.text == "Изменить планы")
async def cmd_plans_choice(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("Какие планы изменим?", reply_markup=get_plans_type_keyboard())

@dp.callback_query(F.data == "plans_shop")
async def process_shop_plans_btn(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.waiting_for_shop_plans)
    await callback.message.answer("Введите через запятую:\nКонверсия, UPT, Средний чек", reply_markup=get_cancel_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "plans_personal")
async def process_personal_plans_btn(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.waiting_for_personal_plans)
    await callback.message.answer("Введите через запятую:\nЛичный чек, Личный CPH", reply_markup=get_cancel_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "plans_employees")
async def process_employees_plans_btn(callback: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT name FROM employee_plans")
        employees = await cursor.fetchall()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=emp[0], callback_data=f"setplan_{emp[0]}")] for emp in employees
    ])
    await callback.message.edit_text("Выберите сотрудника для изменения плана:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("setplan_"))
async def select_employee_for_plan(callback: CallbackQuery, state: FSMContext):
    emp_name = callback.data.split("_")[1]
    await state.update_data(emp_name=emp_name)
    await state.set_state(BotStates.waiting_for_emp_plan)
    await callback.message.edit_text(f"Сотрудник: {emp_name}\nВведите новый план на месяц (сумма):")
    await callback.answer()

@dp.message(BotStates.waiting_for_emp_plan)
async def save_employee_plan(message: types.Message, state: FSMContext):
    try:
        new_plan = float(message.text.replace(' ', ''))
        data = await state.get_data()
        emp_name = data['emp_name']
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE employee_plans SET personal_plan = ? WHERE name = ?", (new_plan, emp_name))
            await db.commit()
            
        await message.answer(f"План для {emp_name} успешно обновлен: {new_plan:,.0f} р.", reply_markup=get_main_keyboard(True))
        await state.clear()
    except ValueError:
        await message.answer("Ошибка ввода. Введите число.")

@dp.message(BotStates.waiting_for_shop_plans)
async def save_shop_plans(message: types.Message, state: FSMContext):
    try:
        c, u, ch = map(float, message.text.replace(' ', '').split(','))
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE plans SET conversion=?, upt=?, check_avg=? WHERE id=1", (c, u, ch))
            await db.commit()
        await message.answer(f"Планы магазина сохранены:\nКонв: {c}%\nUPT: {u}\nЧек: {ch}", reply_markup=get_main_keyboard(True))
        await state.clear()
    except:
        await message.answer("Ошибка формата. Введите три числа через запятую.")

@dp.message(BotStates.waiting_for_personal_plans)
async def save_personal_plans(message: types.Message, state: FSMContext):
    try:
        ch, cph = map(float, message.text.replace(' ', '').split(','))
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE plans SET personal_check=?, personal_cph=? WHERE id=1", (ch, cph))
            await db.commit()
        await message.answer(f"Личные планы сохранены:\nЧек: {ch}\nCPH: {cph}", reply_markup=get_main_keyboard(True))
        await state.clear()
    except:
        await message.answer("Ошибка формата. Введите два числа через запятую.")

# ================= РАСЧЕТ ПРЕМИИ =================
@dp.message(F.text == "Премия")
async def bonus_start(message: types.Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT status FROM users WHERE user_id = ?", (message.from_user.id,))
        row = await cursor.fetchone()
        if not row or row[0] != 'approved': return
        
        cursor = await db.execute("SELECT name FROM employee_plans")
        employees = await cursor.fetchall()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=emp[0], callback_data=f"bonus_{emp[0]}")] for emp in employees
    ])
    await message.answer("Выберите сотрудника для расчета премии:", reply_markup=kb)

@dp.callback_query(F.data.startswith("bonus_"))
async def select_employee_for_bonus(callback: CallbackQuery, state: FSMContext):
    emp_name = callback.data.split("_")[1]
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT personal_plan FROM employee_plans WHERE name = ?", (emp_name,))
        plan_row = await cursor.fetchone()
        plan = plan_row[0] if plan_row else 0

    await state.update_data(emp_name=emp_name, personal_plan=plan)
    await callback.message.edit_text(
        f"Выбран: {emp_name} (План: {plan:,.0f} р.)\n\n"
        f"Введите через запятую: Личный Факт, Отработанные часы\n"
        f"Пример: 950000, 160"
    )
    await state.set_state(BotStates.waiting_for_bonus)
    await callback.answer()

@dp.message(BotStates.waiting_for_bonus)
async def calculate_bonus(message: types.Message, state: FSMContext):
    try:
        fact_str, hours_str = map(str.strip, message.text.split(','))
        personal_fact = float(fact_str)
        worked_hours = float(hours_str)
    except ValueError:
        await message.answer("Ошибка формата. Введите два числа через запятую.")
        return

    data = await state.get_data()
    emp_name = data['emp_name']
    personal_plan = data['personal_plan']
    
    if personal_plan <= 0:
        await message.answer("План сотрудника равен нулю. Настройте план в меню.")
        await state.clear()
        return

    personal_fulfillment = (personal_fact / personal_plan) * 100
    if personal_fulfillment < 120:
        k_personal = 1.0
    elif personal_fulfillment < 135:
        k_personal = 1.3
    else:
        k_personal = 1.6
        
    bonus_personal = personal_fact * 0.0198 * k_personal
    
    current_month = datetime.now().strftime('%Y-%m')
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''
            SELECT SUM(traffic), SUM(calculated_baskets), SUM(calculated_items)
            FROM store_daily_history
            WHERE date LIKE ?
        ''', (f"{current_month}%",))
        totals = await cursor.fetchone()
    
    if not totals or not totals[0]:
        await message.answer("В базе нет данных о трафике за этот месяц. Загрузите отчеты 8420.")
        await state.clear()
        return
        
    total_traffic, total_baskets, total_items = totals
    store_conversion = (total_baskets / total_traffic) * 100 if total_traffic > 0 else 0
    store_upt = total_items / total_baskets if total_baskets > 0 else 0
    
    store_upt = round(store_upt, 2)
    
    conv_fulfillment = (store_conversion / PLAN_CONVERSION) * 100
    
    if conv_fulfillment >= 110:
        k_conv = 2.0
    elif conv_fulfillment >= 105:
        k_conv = 1.5 + (int(conv_fulfillment) - 105) * 0.1 
    elif conv_fulfillment >= 95:
        k_conv = conv_fulfillment / 100
    elif conv_fulfillment >= 90:
        k_conv = 0.7
    elif conv_fulfillment >= 80:
        k_conv = 0.5
    else:
        k_conv = 0.3

    upt_warning = ""
    if store_upt < PLAN_UPT and k_conv > 1.0:
        k_conv = 1.0
        upt_warning = "\n(Коэффициент урезан до 1.0 из-за невыполнения нормы UPT)"

    bonus_conversion = (BASE_CONV_RATE / HOURS_NORM) * worked_hours * k_conv
    
    total_bonus_dirty = bonus_personal + bonus_conversion
    total_bonus_clean = after_tax(total_bonus_dirty)
    
    is_admin = (message.from_user.id == ADMIN_ID)
    
    response = (
        f"Результаты: {emp_name}\n\n"
        f"Личные продажи:\n"
        f"Выручка: {personal_fact:,.0f} р. ({personal_fulfillment:.1f}%)\n"
        f"Коэффициент: {k_personal}\n"
        f"Премия за ТО: {bonus_personal:,.0f} р. ({after_tax(bonus_personal):,.0f} р. чистыми)\n\n"
        f"Показатели магазина:\n"
        f"Ср. Конверсия: {store_conversion:.1f}% (План: {conv_fulfillment:.1f}%)\n"
        f"Ср. UPT: {store_upt:.2f}{upt_warning}\n"
        f"Коэф. конверсии: {k_conv:.2f}\n"
        f"Премия за конверсию: {bonus_conversion:,.0f} р. ({after_tax(bonus_conversion):,.0f} р. чистыми)\n\n"
        f"ИТОГ: {total_bonus_dirty:,.0f} р.\n"
        f"НА РУКИ: {total_bonus_clean:,.0f} р."
    )
    
    await message.answer(response, reply_markup=get_main_keyboard(is_admin))
    await state.clear()

# ================= АВТОМАТИЧЕСКИЙ ПАРСИНГ ОТЧЕТА 8420 =================
@dp.message(F.text.startswith("8420"))
async def parse_evening_report(message: types.Message, state: FSMContext):
    text = message.text
    raw_numbers = []
    
    for line in text.split('\n'):
        match = re.match(r'^([\d,\.]+)', line.strip())
        if match:
            raw_numbers.append(float(match.group(1).replace(',', '.')))
            
    if len(raw_numbers) >= 5:
        revenue = raw_numbers[1]
        traffic = int(raw_numbers[2])
        conversion_pct = raw_numbers[3]
        upt = raw_numbers[4]
        
        await state.update_data(pending_report={
            'revenue': revenue,
            'traffic': traffic,
            'conversion_pct': conversion_pct,
            'upt': upt
        })
        
        await state.set_state(BotStates.waiting_for_report_date)
        await message.reply(
            "Отчет успешно распознан!\n\n"
            "Укажи дату этого отчета в формате ДД.ММ (например: 18.06 или 04.07):",
            reply_markup=get_cancel_keyboard()
        )

@dp.message(BotStates.waiting_for_report_date)
async def process_report_date(message: types.Message, state: FSMContext):
    text = message.text.strip()
    match = re.match(r'^(\d{1,2})[\.,](\d{1,2})$', text)
    
    if not match:
        await message.answer("Неверный формат. Введи день и месяц через точку, например: 18.06\n\nИли нажми «Назад» для отмены.")
        return
        
    day = int(match.group(1))
    month = int(match.group(2))
    
    now = datetime.now()
    year = now.year
    
    if now.month == 1 and month == 12:
        year -= 1
    
    try:
        target_date_obj = datetime(year, month, day)
        target_date = target_date_obj.strftime('%Y-%m-%d')
    except ValueError:
        await message.answer("Такой даты не существует в календаре. Перепроверь числа.")
        return
        
    user_data = await state.get_data()
    report = user_data.get('pending_report')
    
    if not report:
        await message.answer("Данные отчета потерялись. Скинь текст 8420 заново.")
        await state.clear()
        return
        
    revenue = report['revenue']
    traffic = report['traffic']
    conversion_pct = report['conversion_pct']
    upt = report['upt']
    
    calc_baskets = traffic * (conversion_pct / 100)
    calc_items = calc_baskets * upt
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            INSERT OR REPLACE INTO store_daily_history 
            (date, revenue, traffic, calculated_baskets, calculated_items) 
            VALUES (?, ?, ?, ?, ?)
        ''', (target_date, revenue, traffic, calc_baskets, calc_items))
        await db.commit()
        
    is_admin = (message.from_user.id == ADMIN_ID)
    await message.reply(
        f"Данные успешно записаны за {target_date_obj.strftime('%d.%m.%Y')}!\n\n"
        f"Расчетные чеки: {calc_baskets:.2f}\n"
        f"Расчетные товары: {calc_items:.2f}",
        reply_markup=get_main_keyboard(is_admin)
    )
    await state.clear()

# ================= КОМАНДА =================
@dp.message(F.text == "Команда")
async def cmd_team_list(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT user_id, full_name FROM users WHERE status = 'approved' AND user_id != ?", (ADMIN_ID,))
        users = await cursor.fetchall()
        
    if not users:
        await message.answer("В команде пока никого нет.")
        return

    for uid, name in users:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Удалить", callback_data=f"delete_{uid}")]])
        await message.answer(f"Сотрудник: {name}", reply_markup=kb)

# ================= ПРОЧЕЕ =================
@dp.message(F.text == "Инструкция")
async def cmd_help(message: types.Message):
    is_admin = (message.from_user.id == ADMIN_ID)
    
    text = (
        "Инструкция\n\n"
        "Отчет по магазину 8420:\n"
        "Просто перешли вечерний отчет, начинающийся с 8420.\n"
        "Бот сам достанет Выручку, Трафик, Конверсию и UPT, а затем попросит указать дату отчета.\n\n"
        "Отчет через запятую (старый):\n"
        "Пришли цифры: Выручка, Чеки, Штуки, Заходы.\n"
        "Пример: 140000, 42, 84, 400.\n\n"
        "Расчет премии:\n"
        "Нажми «Премия», выбери сотрудника и введи: Личный Факт, Отработанные часы.\n"
        "Пример: 950000, 160."
    )
    
    if is_admin:
        text += (
            "\n\nЗагрузка архива:\n"
            "Для загрузки старых данных введи:\n"
            "/load_archive Выручка, Трафик, Конверсия, UPT\n"
            "Сброс текущего месяца: /load_archive 0,0,0,0"
        )
        
    await message.answer(text)

@dp.callback_query(F.data.startswith(("approve_", "reject_", "delete_")))
async def callbacks_handler(callback: CallbackQuery):
    action, uid = callback.data.split("_")
    uid = int(uid)
    async with aiosqlite.connect(DB_NAME) as db:
        if action == "approve":
            await db.execute("UPDATE users SET status = 'approved' WHERE user_id = ?", (uid,))
            await bot.send_message(uid, "Доступ открыт!", reply_markup=get_main_keyboard(False))
            await callback.message.edit_text("Пользователь одобрен.")
        elif action == "reject" or action == "delete":
            await db.execute("DELETE FROM users WHERE user_id = ?", (uid,))
            await callback.message.edit_text("Доступ закрыт/удален.")
        await db.commit()
    await callback.answer()

@dp.message()
async def auto_report(message: types.Message):
    try:
        data = message.text.replace(' ', '').split(',')
        if len(data) == 4:
            to_sum, baskets, items, visitors = map(float, data)
            plans = await get_plans()
            p_conv, p_upt, p_check = plans[0], plans[1], plans[2]

            f_conv = (baskets / visitors) * 100 if visitors > 0 else 0
            f_upt = items / baskets if baskets > 0 else 0
            f_check = to_sum / baskets if baskets > 0 else 0

            res = (
                f"{STORE_CODE}\n\n"
                f"{int(to_sum)}\n"
                f"{int(visitors)}\n"
                f"{f_conv:.1f}% ({int(f_conv/p_conv*100)}%)\n"
                f"{f_upt:.2f} ({int(f_upt/p_upt*100)}%)\n"
                f"{f_check:.0f} ({int(f_check/p_check*100)}%)"
            )
            await message.answer(res)
    except:
        pass

async def main():
    await init_db()
    # Принудительно сбрасываем вебхук и старую очередь сообщений перед стартом
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
