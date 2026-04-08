import asyncio
import logging
import sys

# Настройка логирования для вывода в консоль хостинга
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout
)
import os
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

# ================= КОНФИГУРАЦИЯ =================
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = 358741967

# Константы для премии (остаются в коде, так как формулы не менялись)
HOUR_RATE = 152
PREMIUM_CHECK_BASE = 10220
PREMIUM_CPH_BASE = 15330

# Начальные значения планов
DEFAULT_PLANS = {
    'conversion': 10.0,
    'upt': 2.0,
    'check': 3500.0,
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
    waiting_for_bonus = State()

# ================= БАЗА ДАННЫХ =================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица пользователей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                status TEXT DEFAULT 'pending'
            )
        """)
        
        # Таблица планов
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
        
        # Проверка наличия записи планов
        cursor = await db.execute("SELECT count(*) FROM plans")
        count = await cursor.fetchone()
        if count[0] == 0:
            await db.execute(
                "INSERT INTO plans (id, conversion, upt, check_avg, personal_cph, personal_check) VALUES (1, ?, ?, ?, ?, ?)",
                (DEFAULT_PLANS['conversion'], DEFAULT_PLANS['upt'], DEFAULT_PLANS['check'], 
                 DEFAULT_PLANS['personal_cph'], DEFAULT_PLANS['personal_check'])
            )
        else:
            # Поэтапная миграция базы, если столбцов не хватает
            columns = ["personal_cph", "personal_check"]
            for col in columns:
                try:
                    await db.execute(f"ALTER TABLE plans ADD COLUMN {col} REAL DEFAULT 1.0")
                except aiosqlite.OperationalError:
                    pass # Столбец уже есть
                    
        await db.commit()

async def get_plans():
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT conversion, upt, check_avg, personal_cph, personal_check FROM plans WHERE id=1")
        return await cursor.fetchone()

# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================
def get_coeff_check(percent):
    if percent >= 135: return 2.0
    if percent >= 130: return 1.6
    if percent >= 120: return 1.5
    if percent >= 115: return 1.3
    if percent >= 110: return 1.2
    if percent >= 105: return 1.1
    if percent >= 95: return 1.0
    if percent >= 80: return 0.8
    if percent >= 70: return 0.6
    return 0.5

def get_coeff_cph(percent):
    if percent >= 135: return 2.0
    if percent >= 130: return 1.6
    if percent >= 120: return 1.5
    if percent >= 115: return 1.3
    if percent >= 110: return 1.2
    if percent >= 105: return 1.1
    if percent >= 100: return 1.0
    return 0.5

def after_tax(amount):
    return int(amount * 0.87)

# ================= КЛАВИАТУРЫ =================
def get_main_keyboard(is_admin: bool):
    if is_admin:
        kb = [
            [KeyboardButton(text="Изменить планы"), KeyboardButton(text="Команда")],
            [KeyboardButton(text="Премия"), KeyboardButton(text="Инструкция")],
            [KeyboardButton(text="Перезагрузить бота")]
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
        [InlineKeyboardButton(text="Личные планы (Премия)", callback_data="plans_personal")]
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
            # Уведомление админу
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Одобрить", callback_data=f"approve_{user_id}"),
                InlineKeyboardButton(text="Отклонить", callback_data=f"reject_{user_id}")
            ]])
            await bot.send_message(ADMIN_ID, f"Новая заявка: {message.from_user.full_name}", reply_markup=kb)
            await message.answer("Заявка отправлена. Ожидайте одобрения.")
        elif row[0] == 'approved':
            await message.answer("Добро пожаловать.", reply_markup=get_main_keyboard(False))

# ================= ИЗМЕНЕНИЕ ПЛАНОВ (АДМИН) =================

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
        await message.answer("Ошибка! Введите три числа через запятую.")

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
        await message.answer("Ошибка! Введите два числа через запятую.")

# ================= ПРЕМИЯ (ДЛЯ ВСЕХ) =================

@dp.message(F.text == "Премия")
async def bonus_start(message: types.Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT status FROM users WHERE user_id = ?", (message.from_user.id,))
        row = await cursor.fetchone()
        if not row or row[0] != 'approved': return

    await state.set_state(BotStates.waiting_for_bonus)
    await message.answer("Введите через запятую:\nЧасы, Выручка, Корзины", reply_markup=get_cancel_keyboard())

@dp.message(BotStates.waiting_for_bonus)
async def calculate_bonus(message: types.Message, state: FSMContext):
    try:
        hours, to_sum, baskets = map(float, message.text.replace(' ', '').split(','))
        plans = await get_plans()
        p_cph, p_check = plans[3], plans[4]

        f_check = to_sum / baskets if baskets > 0 else 0
        f_cph = baskets / hours if hours > 0 else 0
        
        perc_check = (f_check / p_check) * 100 if p_check > 0 else 0
        perc_cph = (f_cph / p_cph) * 100 if p_cph > 0 else 0
        
        c_check, c_cph = get_coeff_check(perc_check), get_coeff_cph(perc_cph)
        
        sal = hours * HOUR_RATE
        total = sal + (PREMIUM_CHECK_BASE * c_check) + (PREMIUM_CPH_BASE * c_cph)
        
        is_admin = (message.from_user.id == ADMIN_ID)
        await message.answer(
            f"Оклад: {int(sal)} ({after_tax(sal)})\n"
            f"Чек: {int(f_check)} ({int(perc_check)}%) -> К {c_check}\n"
            f"CPH: {f_cph:.2f} ({int(perc_cph)}%) -> К {c_cph}\n\n"
            f"ИТОГО: {int(total)} ({after_tax(total)})",
            reply_markup=get_main_keyboard(is_admin)
        )
        await state.clear()
    except:
        await message.answer("Ошибка! Введите: Часы, Выручка, Корзины")

# ================= КОМАНДА (АДМИН) =================

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
    text = (
        "📖 **Инструкция**\n\n"
        "1️⃣ **Отчет по магазину:**\n"
        "Пришли цифры через запятую: `Выручка, Чеки, Штуки, Заходы`.\n"
        "Пример: `140000, 42, 84, 400`.\n\n"
        "2️⃣ **Расчет премии:**\n"
        "Нажми кнопку «Премия» и введи: `Часы, Выручка, Корзины`.\n"
        "Пример: `9, 45000, 15`."
    )
    await message.answer(text, parse_mode="Markdown")

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

@dp.message(F.text == "Перезагрузить бота")
async def cmd_reboot(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("Перезагрузка...")
        os.execl(sys.executable, sys.executable, *sys.argv)

@dp.message()
async def auto_report(message: types.Message):
    # Хендлер для автоматического парсинга отчета (Выручка, Чеки, Штуки, Заходы)
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
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
