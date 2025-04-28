import re
import secrets
import sqlite3
import string
from contextlib import contextmanager
from hashlib import sha256
import aiohttp
from uuid import uuid4
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import StatesGroup, State
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import asyncio
import sys
import os
import warnings


warnings.filterwarnings("ignore", category=FutureWarning, module="huggingface_hub")
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

API_URL = "http://127.0.0.1:81/generate"
token = ""
DB_PATH = "testdatabase.db"
ADMINS = []
dir_path = "cache/"

bot = Bot(token)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

class request(StatesGroup):
    photo = State()
    emote = State()

def generate_key(length: int = 24) -> str:
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))

def hash_key(key: str) -> str:
    return sha256(key.encode()).hexdigest()

def is_valid_key(key: str) -> bool:
    return re.fullmatch(r"^[A-Za-z0-9]{24}$", key) is not None


class Database:
    def __init__(self, path: str):
        self.path = path
    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    def setup(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS keys ("
                "key TEXT PRIMARY KEY, "
                "user_id TEXT, "
                "username TEXT, "
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.commit()
    def add_key(self, key: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO keys (key) VALUES (?)",
                (hash_key(key),)
            )
            conn.commit()
    def activate_key(self, key: str, user_id: int, username: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE keys SET user_id = ?, username = ? "
                "WHERE key = ? AND user_id IS NULL",
                (str(user_id), username, hash_key(key))
            )
            conn.commit()
            return cursor.rowcount > 0
    def has_key(self, user_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM keys WHERE user_id = ?",
                (str(user_id),)
            )
            return bool(cursor.fetchone())
    def get_all_keys(self) -> list:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT key, user_id, username, created_at FROM keys ORDER BY created_at DESC"
            )
            return cursor.fetchall()
    def revoke_access(self, key_or_user: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                "UPDATE keys SET user_id = NULL, username = NULL "
                "WHERE user_id = ?",
                (key_or_user,)
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    "UPDATE keys SET user_id = NULL, username = NULL "
                    "WHERE key = ?",
                    (hash_key(key_or_user),)
                )
            conn.commit()
            return cursor.rowcount > 0
    def deactivate_key(self, key_hash: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE keys SET user_id = NULL, username = NULL "
                "WHERE key = ? AND user_id IS NOT NULL",
                (key_hash,)
            )
            conn.commit()
            return cursor.rowcount > 0
    def delete_key(self, key_hash: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM keys WHERE key = ? AND user_id IS NULL",
                (key_hash,)
            )
            conn.commit()
            return cursor.rowcount > 0
    def get_key_by_short_hash(self, short_hash: str) -> dict:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM keys WHERE key LIKE ? || '%'",
                (short_hash,)
            )
            result = cursor.fetchone()
            return dict(result) if result else None
db = Database(DB_PATH)

async def process_activation(user: types.User, key: str) -> bool:
    if not is_valid_key(key):
        await bot.send_message(user.id, "❌ Некорректный формат ключа")
        return False
    if db.has_key(user.id):
        await bot.send_message(user.id, "⚠️ У вас уже есть активный ключ!")
        return False
    if db.activate_key(key, user.id, user.username):
        await bot.send_message(user.id, "✅ Аккаунт успешно активирован!")
        return True
    await bot.send_message(user.id, "❌ Неверный или уже использованный ключ")
    return False

async def main_menu(user: types.User, state: FSMContext):
    await state.finish()
    keyboard = InlineKeyboardMarkup()
    if user.id in ADMINS:
        keyboard.row(InlineKeyboardButton("⚙️ Панель управления", callback_data="admin"),InlineKeyboardButton("✒️ Сгенерировать", callback_data="start"))
    else:
        keyboard.add(InlineKeyboardButton("✒️ Сгенерировать", callback_data="start"))
    await bot.send_message(user.id,"😃 <b>Face Poke</b>\n\n""✨ <b>Возможности:</b>\n""• Перенос выражений лиц между фотографиями\n""🔒 <b>Для доступа требуется <a href='t.me/wxqryy'>ключ активации</a></b>\n",reply_markup=keyboard,parse_mode="HTML",disable_web_page_preview=True)

@dp.callback_query_handler(text="cancel", state="*")
async def cancel_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await admin_panel(callback=callback, state=state)

@dp.callback_query_handler(text="back", state="*")
async def back_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await callback.message.delete()
    await main_menu(callback.message.chat, state=state)

@dp.message_handler(commands=['start'], state="*")
async def start_handler(message: types.Message, state: FSMContext):
    await state.finish()
    user = message.from_user
    args = message.get_args().strip()
    if args:
        if not await process_activation(user, args):
            return
    await main_menu(user, state=state)

@dp.callback_query_handler(text="admin")
async def admin_panel(callback: types.CallbackQuery, state: FSMContext):
    await state.finish()
    if callback.from_user.id not in ADMINS:
        return await callback.answer("⛔ Доступ запрещен")
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.row(InlineKeyboardButton("🔐 Создать ключ", callback_data="gen_key"),InlineKeyboardButton("❌ Удалить доступ", callback_data="remove_access"))
    keyboard.add(InlineKeyboardButton("📋 Список ключей", callback_data="key_list"))
    keyboard.add(InlineKeyboardButton("🔼 Назад", callback_data="back"))
    await callback.message.edit_text("⚙️ <b>Панель управления:</b>\n\n⚠️ <b>По вопросам писать <a href='t.me/wxqryy'>разработчику</a></b>",reply_markup=keyboard,parse_mode="HTML",disable_web_page_preview=True)

@dp.callback_query_handler(text="gen_key")
async def generate_key_handler(callback: types.CallbackQuery):
    try:
        key = generate_key()
        db.add_key(key)
        await callback.message.answer(f"🔑 Новый ключ: <a href='t.me/emcopy_bot?start={key}'>{key}</a>\n\n",parse_mode="HTML",disable_web_page_preview=True)
    except sqlite3.IntegrityError:
        await callback.message.answer("⚠️ Ключ уже существует, попробуйте снова")

@dp.callback_query_handler(text="start")
async def start(callback: types.CallbackQuery):
    uid = callback.message.chat.id
    if db.has_key(uid):
        await request.photo.set()
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="back"))
        await callback.message.edit_text("<b>📷 Отправьте исходную фотографию:</b>", reply_markup=keyboard, parse_mode="HTML")
    else:
        await callback.message.edit_text("⛔ Доступ запрещен\n\n<b><a href='t.me/wxqryy'>Получить ключ</a></b>", parse_mode="HTML", disable_web_page_preview=True)

@dp.message_handler(state=request.photo, content_types=['photo'])
async def get_photo(message: types.Message, state: FSMContext):
    uid = message.chat.id
    if db.has_key(uid):
        file_id = message.photo[-1].file_id
        file_info = await bot.get_file(file_id)
        file_path = file_info.file_path
        async with state.proxy() as data:
            data['photo'] =f"https://api.telegram.org/file/bot{token}/{file_path}"
        await request.emote.set()
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="back"))
        await bot.send_message(uid, "<b>📷 Отправьте фотографию с выражением лица:</b>", reply_markup=keyboard, parse_mode="HTML")

@dp.message_handler(state=request.emote, content_types=['photo'])
async def get_emote(message: types.Message, state: FSMContext):
    uid = message.chat.id
    if db.has_key(uid):
        file_id = message.photo[-1].file_id
        file_info = await bot.get_file(file_id)
        file_path = file_info.file_path
        emote = f"https://api.telegram.org/file/bot{token}/{file_path}"
        async with state.proxy() as data:
            photo = data['photo']
        await state.finish()
        request_id = str(uuid4())
        payload = {"id": request_id,"input": {"source_image_file": photo,"driving_image_file": emote}}
        await bot.send_message(uid, "<b>🚀 Запуск генерации...</b>", parse_mode="HTML")
        processed_image_path = await send_to_server(payload, request_id)
        if processed_image_path:
            with open(processed_image_path, "rb") as photo:
                await message.answer_photo(photo, caption="✅ <b>Успешно!</b>", parse_mode="HTML")
        else:
            await message.reply("❌ <b>Ошибка обработки. Попробуй снова!</b>", parse_mode="HTML")
        await state.finish()

async def send_to_server(payload, request_id):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(API_URL, json=payload) as response:
                if response.status == 200:
                    os.makedirs("results", exist_ok=True)
                    filename = f"results/{request_id}.png"
                    image_data = await response.read()
                    with open(filename, "wb") as f:
                        f.write(image_data)
                    return filename
                else:
                    return None
        except Exception as e:
            print(f"Ошибка при отправке на сервер: {str(e)}")
            return None

@dp.callback_query_handler(text="remove_access")
async def remove_access_handler(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMINS: return await callback.answer("⛔ Доступ запрещен")
    keys = db.get_all_keys()
    keyboard = InlineKeyboardMarkup(row_width=1)
    for key in keys:
        status = "✅ Активирован" if key['user_id'] else "🆓 Неактивен"
        short_hash = key['key'][:8]
        btn_text = (f"{status} | {short_hash}... | "f"{key['username'] or 'нет данных'}")
        keyboard.add(InlineKeyboardButton(text=btn_text,callback_data=f"revoke_{short_hash}"))
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel"))
    await callback.message.edit_text("🔐 Выберите ключ для отзыва:\n""✅ - активирован\n🆓 - неактивен", reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data.startswith('revoke_'))
async def revoke_key_handler(callback: types.CallbackQuery):
    short_hash = callback.data.split('_')[1]
    key = db.get_key_by_short_hash(short_hash)
    if not key: return await callback.answer("❌ Ключ не найден")
    if key['user_id']: success = db.deactivate_key(key['key'])
    else: success = db.delete_key(key['key'])
    keys = db.get_all_keys()
    keyboard = InlineKeyboardMarkup(row_width=1)
    for key in keys:
        status = "✅ Активирован" if key['user_id'] else "🆓 Неактивен"
        short_hash = key['key'][:8]
        btn_text = (f"{status} | {short_hash}... | "f"{key['username'] or 'нет данных'}")
        keyboard.add(InlineKeyboardButton(text=btn_text,callback_data=f"revoke_{short_hash}"))
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel"))
    await callback.message.edit_text("🔐 Выберите ключ для отзыва:\n""✅ - активирован\n🆓 - неактивен",reply_markup=keyboard)

@dp.callback_query_handler(text="key_list")
async def key_list_handler(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return await callback.answer("⛔ Доступ запрещен")
    keys = db.get_all_keys()
    if not keys:
        return await callback.message.answer("📭 База ключей пуста")
    message = "📋 Список всех ключей:\n\n"
    for key in keys:
        status = "✅ Активен" if key['user_id'] else "🆓 Свободен"
        user_info = f"👤 @{key['username']} ({key['user_id']})\n" if key['user_id'] else ""
        created = key['created_at'].split('.')[0]
        message += (f"🔑 {key['key'][:8]}...{key['key'][-4:]} - {status}\n"f"{user_info}"f"🕒 Создан: {created}\n""────────────────────\n")
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel"))
    await callback.message.answer(message,parse_mode="HTML",disable_web_page_preview=True,reply_markup=keyboard)

if __name__ == '__main__':
    db.setup()
    executor.start_polling(dp, skip_updates=True)