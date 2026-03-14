import asyncio
import logging
import json
import os
from datetime import datetime, timedelta
from typing import Dict, Optional
from pathlib import Path

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest

# --- НАСТРОЙКИ ---
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
CHANNEL_ID = -1001234567890  # ID канала
CHANNEL_LINK = "https://t.me/your_channel"  # Запасная ссылка (если не получится сгенерировать)
MASTER_ID = 123456789  # ТВОЙ TELEGRAM ID (узнать у @userinfobot)
ADMIN_PASSWORD = "12345"

# --- ФАЙЛ ДЛЯ ХРАНЕНИЯ ТИПА КАНАЛА ---
DATA_DIR = "bot_data"
CHANNEL_TYPE_FILE = os.path.join(DATA_DIR, "channel_type.json")
Path(DATA_DIR).mkdir(exist_ok=True)

# --- КЛАСС ДЛЯ РАБОТЫ С КАНАЛОМ ---
class ChannelManager:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.current_type = self._load_channel_type()
    
    def _load_channel_type(self) -> Optional[str]:
        """Загружает сохраненный тип канала"""
        if os.path.exists(CHANNEL_TYPE_FILE):
            with open(CHANNEL_TYPE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('type')
        return None
    
    def _save_channel_type(self, channel_type: str):
        """Сохраняет тип канала"""
        with open(CHANNEL_TYPE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'type': channel_type, 'updated': datetime.now().isoformat()}, f)
    
    async def get_channel_info(self) -> Dict:
        """Получает актуальную информацию о канале"""
        try:
            chat = await self.bot.get_chat(CHANNEL_ID)
            
            # Определяем тип
            is_private = chat.username is None
            channel_type = 'private' if is_private else 'public'
            
            # Получаем ссылку
            if not is_private:
                # Публичный канал
                link = f"https://t.me/{chat.username}"
            else:
                # Приватный канал - пытаемся создать invite link
                try:
                    invite_link = await self.bot.create_chat_invite_link(
                        chat.id, 
                        member_limit=1,
                        name=f"temp_link_{datetime.now().timestamp()}"
                    )
                    link = invite_link.invite_link
                except:
                    link = CHANNEL_LINK  # запасная ссылка из конфига
            
            return {
                'id': chat.id,
                'title': chat.title,
                'type': channel_type,
                'username': chat.username,
                'link': link,
                'description': chat.description
            }
        except Exception as e:
            logging.error(f"Ошибка получения инфо о канале: {e}")
            return None
    
    async def check_and_notify_if_changed(self, master_id: int) -> bool:
        """
        Проверяет, изменился ли тип канала.
        Если да - уведомляет мастера и возвращает True.
        """
        current_info = await self.get_channel_info()
        if not current_info:
            return False
        
        new_type = current_info['type']
        
        # Если тип изменился
        if self.current_type and self.current_type != new_type:
            # Отправляем уведомление мастеру
            await self._notify_master(master_id, self.current_type, new_type, current_info)
            
            # Обновляем сохраненный тип
            self._save_channel_type(new_type)
            self.current_type = new_type
            return True
        
        # Если первый запуск или тип не менялся
        if not self.current_type:
            self._save_channel_type(new_type)
            self.current_type = new_type
        
        return False
    
    async def _notify_master(self, master_id: int, old_type: str, new_type: str, channel_info: Dict):
        """Отправляет уведомление мастеру о смене типа канала"""
        
        # Перевод типов на русский
        type_names = {'public': 'публичный', 'private': 'приватный'}
        
        # Базовое сообщение
        message = (
            f"🔔 <b>Изменение типа канала!</b>\n\n"
            f"Канал <b>{channel_info['title']}</b>\n"
            f"сменил тип с <b>{type_names[old_type]}</b> на <b>{type_names[new_type]}</b>.\n\n"
        )
        
        # Дополнение в зависимости от нового типа
        if new_type == 'private':
            message += (
                f"⚠️ <b>Важно!</b> Теперь канал приватный.\n"
                f"Чтобы бот мог приглашать пользователей, нужно установить ссылку-приглашение.\n\n"
                f"🔐 Используйте команду <b>/admin</b> и выберите пункт "
                f"<b>'🔗 Установить ссылку на канал'</b>, чтобы задать действующую ссылку."
            )
        else:
            message += (
                f"✅ Теперь канал публичный.\n"
                f"Бот будет автоматически использовать ссылку: "
                f"<code>{channel_info['link']}</code>"
            )
        
        # Создаем клавиатуру с действиями
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Установить ссылку", callback_data="admin_set_link")],
            [InlineKeyboardButton(text="🔄 Проверить сейчас", callback_data="admin_check_channel")],
            [InlineKeyboardButton(text="🚪 Закрыть", callback_data="delete_message")]
        ])
        
        try:
            await self.bot.send_message(
                master_id, 
                message, 
                parse_mode="HTML",
                reply_markup=keyboard
            )
        except Exception as e:
            logging.error(f"Не удалось уведомить мастера: {e}")

# --- СОСТОЯНИЯ ДЛЯ FSM ---
class AdminStates(StatesGroup):
    waiting_for_password = State()
    admin_menu = State()
    waiting_for_channel_link = State()  # Новое состояние для ввода ссылки
    # ... остальные состояния как были

# --- ИНИЦИАЛИЗАЦИЯ ---
logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# Создаем менеджер канала
channel_manager = ChannelManager(bot)

# --- ФУНКЦИЯ ПРОВЕРКИ ПОДПИСКИ (обновленная) ---
async def check_subscription(user_id: int) -> bool:
    """Проверяет подписку пользователя"""
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

# --- ФУНКЦИЯ ПОЛУЧЕНИЯ КЛАВИАТУРЫ ПОДПИСКИ (обновленная) ---
async def get_subscription_keyboard():
    """Создает клавиатуру подписки с актуальной ссылкой"""
    channel_info = await channel_manager.get_channel_info()
    
    if channel_info and channel_info['type'] == 'private':
        button_text = "🔐 Вступить в приватный канал"
        link = channel_info['link']
    else:
        button_text = "📢 Подписаться на канал"
        link = channel_info['link'] if channel_info else CHANNEL_LINK
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=button_text, url=link)],
        [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subscription")]
    ])
    
    return keyboard

# --- ЗАПУСК БОТА С ПРОВЕРКОЙ КАНАЛА ---
async def on_startup():
    """Действия при запуске бота"""
    # Проверяем тип канала и уведомляем мастера при изменении
    await channel_manager.check_and_notify_if_changed(MASTER_ID)
    
    # Запускаем фоновую задачу для периодической проверки (раз в час)
    asyncio.create_task(periodic_channel_check())

async def periodic_channel_check():
    """Периодическая проверка типа канала (каждый час)"""
    while True:
        await asyncio.sleep(3600)  # 1 час
        await channel_manager.check_and_notify_if_changed(MASTER_ID)

# --- ОБРАБОТЧИК /start ---
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    
    # Проверяем подписку
    if not await check_subscription(user_id):
        welcome_text = load_welcome_text()  # твоя функция загрузки
        channel_info = await channel_manager.get_channel_info()
        
        if channel_info and channel_info['type'] == 'private':
            text = (
                f"{welcome_text}\n\n"
                f"❗️ Для доступа к боту нужно вступить в **приватный канал** мастера.\n\n"
                f"1. Нажми кнопку ниже\n"
                f"2. Отправь заявку на вступление\n"
                f"3. Дождись одобрения\n"
                f"4. Нажми 'Я подписался'\n"
            )
        else:
            text = f"{welcome_text}\n\n❗️ Для доступа к боту нужно подписаться на канал:"
        
        keyboard = await get_subscription_keyboard()
        await message.answer(text, reply_markup=keyboard)
        return
    
    welcome_text = load_welcome_text()  # твоя функция
    await message.answer(welcome_text, reply_markup=get_main_keyboard())

# --- ОБРАБОТЧИК /admin (обновленный) ---
@dp.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    """Вход в админ-панель"""
    await state.set_state(AdminStates.waiting_for_password)
    await message.answer(
        "🔐 Введите пароль для доступа к админ-панели:",
        reply_markup=get_cancel_keyboard()
    )

# --- УСПЕШНЫЙ ВХОД В АДМИНКУ (обновленное меню) ---
@dp.message(AdminStates.waiting_for_password, F.text)
async def process_admin_password(message: Message, state: FSMContext):
    """Проверка пароля и показ админ-меню"""
    if message.text == "❌ Отменить":
        await state.clear()
        await message.answer("Вход отменён", reply_markup=get_main_keyboard())
        return
    
    if message.text == ADMIN_PASSWORD:
        await state.set_state(AdminStates.admin_menu)
        await message.answer(
            "✅ Пароль верный! Добро пожаловать в админ-панель.",
            reply_markup=ReplyKeyboardRemove()
        )
        
        # Получаем актуальную инфу о канале
        channel_info = await channel_manager.get_channel_info()
        
        # Создаем сообщение с информацией о канале
        if channel_info:
            status = "🔐 Приватный" if channel_info['type'] == 'private' else "🌐 Публичный"
            channel_text = (
                f"📊 <b>Информация о канале</b>\n"
                f"• Статус: {status}\n"
                f"• Название: {channel_info['title']}\n"
                f"• Ссылка: <code>{channel_info['link']}</code>\n\n"
                f"<i>Тип канала определяется автоматически</i>"
            )
        else:
            channel_text = "❌ Не удалось получить информацию о канале"
        
        # Создаем клавиатуру с кнопкой для смены ссылки
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💰 Управление ценами", callback_data="admin_prices")],
            [InlineKeyboardButton(text="📅 Управление расписанием", callback_data="admin_schedule")],
            [InlineKeyboardButton(text="📝 Изменить приветствие", callback_data="admin_welcome")],
            [InlineKeyboardButton(text="🔗 Установить ссылку на канал", callback_data="admin_set_link")],
            [InlineKeyboardButton(text="🔄 Проверить тип канала", callback_data="admin_check_channel")],
            [InlineKeyboardButton(text="🚪 Выйти из админки", callback_data="admin_exit")]
        ])
        
        await message.answer(channel_text, parse_mode="HTML")
        await message.answer("⚙️ Выберите действие:", reply_markup=keyboard)
    else:
        await message.answer(
            "❌ Неверный пароль. Попробуйте снова:",
            reply_markup=get_cancel_keyboard()
        )

# --- ОБРАБОТЧИК ДЛЯ УСТАНОВКИ ССЫЛКИ ---
@dp.callback_query(F.data == "admin_set_link", AdminStates.admin_menu)
async def admin_set_link(callback: CallbackQuery, state: FSMContext):
    """Запрос ссылки на канал"""
    await state.set_state(AdminStates.waiting_for_channel_link)
    await callback.message.edit_text(
        "🔗 Отправьте ссылку-приглашение на ваш канал.\n"
        "Для приватного канала это должна быть ссылка вида:\n"
        "<code>https://t.me/+ABCDefgh12345</code> или <code>https://t.me/joinchat/...</code>\n\n"
        "Для публичного канала можно оставить пустым (будет использован username).",
        parse_mode="HTML"
    )

@dp.message(AdminStates.waiting_for_channel_link, F.text)
async def process_channel_link(message: Message, state: FSMContext):
    """Сохранение новой ссылки на канал"""
    global CHANNEL_LINK
    new_link = message.text.strip()
    
    # Проверяем, что это похоже на ссылку Telegram
    if new_link and not (new_link.startswith('https://t.me/') or new_link.startswith('t.me/')):
        await message.answer(
            "❌ Это не похоже на ссылку Telegram. Ссылка должна начинаться с https://t.me/\n"
            "Попробуйте снова:"
        )
        return
    
    # Сохраняем новую ссылку (в реальном проекте сохраняй в конфиг)
    CHANNEL_LINK = new_link if new_link else CHANNEL_LINK
    
    await state.set_state(AdminStates.admin_menu)
    await message.answer(
        f"✅ Ссылка сохранена!\n"
        f"Теперь бот будет использовать: {CHANNEL_LINK or 'автоматическую ссылку'}",
        reply_markup=ReplyKeyboardRemove()
    )
    
    # Возвращаем в админ-меню
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Цены", callback_data="admin_prices")],
        [InlineKeyboardButton(text="📅 Расписание", callback_data="admin_schedule")],
        [InlineKeyboardButton(text="📝 Приветствие", callback_data="admin_welcome")],
        [InlineKeyboardButton(text="🔗 Сменить ссылку", callback_data="admin_set_link")],
        [InlineKeyboardButton(text="🚪 Выйти", callback_data="admin_exit")]
    ])
    await message.answer("⚙️ Админ-меню:", reply_markup=keyboard)

# --- ОБРАБОТЧИК ДЛЯ ПРОВЕРКИ ТИПА КАНАЛА ---
@dp.callback_query(F.data == "admin_check_channel", AdminStates.admin_menu)
async def admin_check_channel(callback: CallbackQuery, state: FSMContext):
    """Ручная проверка типа канала"""
    channel_info = await channel_manager.get_channel_info()
    
    if channel_info:
        status = "🔐 Приватный" if channel_info['type'] == 'private' else "🌐 Публичный"
        text = (
            f"📊 <b>Текущая информация о канале</b>\n\n"
            f"• Тип: {status}\n"
            f"• Название: {channel_info['title']}\n"
            f"• ID: {channel_info['id']}\n"
            f"• Username: {channel_info['username'] or 'отсутствует'}\n"
            f"• Используемая ссылка: <code>{channel_info['link']}</code>\n"
        )
        
        # Проверяем, изменился ли тип
        await channel_manager.check_and_notify_if_changed(MASTER_ID)
        
        text += "\n✅ Проверка выполнена. При изменении типа вы получите уведомление."
    else:
        text = "❌ Не удалось получить информацию о канале"
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
        ])
    )

# --- ОБРАБОТЧИК ДЛЯ УДАЛЕНИЯ СООБЩЕНИЯ ---
@dp.callback_query(F.data == "delete_message")
async def callback_delete_message(callback: CallbackQuery):
    """Удаляет сообщение"""
    await callback.message.delete()

# --- ЗАПУСК БОТА ---
async def main():
    # Вызываем при старте
    await on_startup()
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())