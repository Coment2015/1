from __future__ import annotations

import random
import secrets
from dataclasses import dataclass
from typing import Dict, List, Set, Any, Awaitable, Callable, Optional

from aiogram import Router, F, BaseMiddleware
from aiogram.filters import CommandStart, Command
from aiogram.types import (
	Message,
	CallbackQuery,
	ReplyKeyboardMarkup,
	KeyboardButton,
	InputMediaPhoto,
	InlineKeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from bot.storage import load_state as _storage_load_state, save_state as _storage_save_state


router = Router()


# Набор выраженных и непохожих эмодзи для капчи (12 вариантов)
EMOJI_POOL: List[str] = [
	"🗽",  # статуя свободы
	"🐍",  # змея
	"🚀",  # ракета
	"🍕",  # пицца
	"🧠",  # мозг
	"🌋",  # вулкан
	"🎸",  # гитара
	"🐳",  # кит
	"⚡",   # молния
	"🎯",  # мишень
	"🦄",  # единорог
	"🧩",  # пазл
]


# Доступные города (по умолчанию)
cities: List[str] = []


@dataclass
class CaptchaState:
	round_id: str
	target_idx: int  # индекс в EMOJI_POOL


@dataclass
class FirstMessageConfig:
	text: Optional[str]
	photo_file_id: Optional[str]

# Новая модель товара и фасовок
@dataclass
class ProductVariant:
	size_label: str  # до 5 символов
	price_rub: int   # цена в рублях


@dataclass
class Product:
	name: str
	variants: List[ProductVariant]


# Память в процессе
pending_captcha: Dict[int, CaptchaState] = {}  # пользователь -> состояние капчи
verified_users: Set[int] = set()               # прошедшие капчу
admins: Set[int] = set()                       # администраторы
first_message: FirstMessageConfig = FirstMessageConfig(
	text="Привет! Вы прошли проверку. Напишите /help, чтобы узнать что я умею.",
	photo_file_id=None,
)
# Товары и фото для экрана выбора товара
products: List[Product] = []
product_select_photo_file_id: Optional[str] = None
variant_select_photo_file_id: Optional[str] = None
order_summary_photo_file_id: Optional[str] = None
# Привязка товаров к городам: индекс города -> набор индексов товаров, доступных в городе
city_products_enabled: Dict[int, Set[int]] = {}
# Адреса на уровень города и на уровень пары (товар, фасовка)
city_addresses: Dict[int, List[str]] = {}
city_variant_addresses: Dict[int, Dict[str, List[str]]] = {}
# Контакт оператора (username вида @name или ссылка)
operator_contact: Optional[str] = None

# Загрузка состояния при импорте
_loaded = _storage_load_state()
admins: Set[int] = set(_loaded.get("admins", []))
cities[:] = list(_loaded.get("cities", []))
fm = _loaded.get("first_message", {})
first_message.text = fm.get("text")
first_message.photo_file_id = fm.get("photo_file_id")
product_select_photo_file_id = _loaded.get("product_select_photo_file_id")
variant_select_photo_file_id = _loaded.get("variant_select_photo_file_id")
order_summary_photo_file_id = _loaded.get("order_summary_photo_file_id")
products_data = _loaded.get("products", [])
products[:] = [Product(name=p.get("name",""), variants=[ProductVariant(size_label=v.get("size_label",""), price_rub=int(v.get("price_rub",0))) for v in p.get("variants",[])]) for p in products_data]
city_products_enabled = {int(k): set(v) for k, v in _loaded.get("city_products_enabled", {}).items()}
city_addresses = {int(k): list(v) for k, v in (_loaded.get("city_addresses") or {}).items()}
city_variant_addresses = {int(k): {kv: list(av) for kv, av in v.items()} for k, v in (_loaded.get("city_variant_addresses") or {}).items()}
operator_contact = _loaded.get("operator_contact")


class AdminStates(StatesGroup):
	waiting_first_message = State()
	waiting_new_city_name = State()
	waiting_edit_city_name = State()
	waiting_new_product_name = State()
	waiting_variant_size = State()
	waiting_variant_price = State()
	waiting_product_photo = State()
	waiting_variant_photo = State()
	waiting_address_name = State()
	waiting_operator_contact = State()
	waiting_order_summary_photo = State()


# ========================= Утилиты клавиатур =========================

def kb_admin_menu() -> ReplyKeyboardMarkup:
	return ReplyKeyboardMarkup(
		keyboard=[[KeyboardButton(text="Админ меню")]],
		resize_keyboard=True,
	)


def kb_interface_menu() -> ReplyKeyboardMarkup:
	return ReplyKeyboardMarkup(
		keyboard=[[KeyboardButton(text="Интерфейс бота")]],
		resize_keyboard=True,
	)


def kb_first_message_menu() -> ReplyKeyboardMarkup:
	return ReplyKeyboardMarkup(
		keyboard=[
			[KeyboardButton(text="Первое сообщение бота")],
			[KeyboardButton(text="Фотка при выборе фасовки"), KeyboardButton(text="Фотка при выборе товара")],
			[KeyboardButton(text="Фото номера заказа")],
			[KeyboardButton(text="Города")],
			[KeyboardButton(text="Добавить товар")],
		],
		resize_keyboard=True,
	)


def build_user_cities_keyboard() -> InlineKeyboardBuilder:
	kb = InlineKeyboardBuilder()
	# Городские кнопки по 2 в ряд
	for idx, name in enumerate(cities):
		kb.button(text=name, callback_data=f"city:{idx}")
	kb.adjust(2)
	# Нижние 4 кнопки вертикально (по одной в ряд)
	kb.row(InlineKeyboardButton(text="🧜‍♀️🧜‍♂️Вакансии", callback_data="action:vacancies"))
	kb.row(InlineKeyboardButton(text="История покупок", callback_data="action:history"))
	kb.row(InlineKeyboardButton(text="Написать оператору", callback_data="action:operator"))
	kb.row(InlineKeyboardButton(text="Активировать промо", callback_data="action:promo"))
	return kb


async def send_city_picker(message: Message) -> None:
	await message.answer(
		"👇 ВЫБЕРИТЕ СВОЙ ГОРОД👇",
		reply_markup=build_user_cities_keyboard().as_markup(),
	)


# ========================= Капча =========================

def _build_captcha_keyboard(target_idx: int, round_id: str) -> InlineKeyboardBuilder:
	# Выбираем 5 случайных неверных вариантов
	all_indices = list(range(len(EMOJI_POOL)))
	distractors = [i for i in all_indices if i != target_idx]
	picked_distractors = random.sample(distractors, k=5)

	# Собираем и перемешиваем
	button_indices = picked_distractors + [target_idx]
	random.shuffle(button_indices)

	kb = InlineKeyboardBuilder()
	for idx in button_indices:
		emoji = EMOJI_POOL[idx]
		kb.button(text=emoji, callback_data=f"captcha:{round_id}:{idx}")
	kb.adjust(3, 3)
	return kb


async def _send_captcha(message: Message) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	target_idx = random.randrange(len(EMOJI_POOL))
	round_id = secrets.token_hex(4)

	pending_captcha[user_id] = CaptchaState(round_id=round_id, target_idx=target_idx)

	target_emoji = EMOJI_POOL[target_idx]
	text = (
		"Для начала работы пройдите проверку.\n"
		f"Нажмите на кнопку с изображением {target_emoji}"
	)
	kb = _build_captcha_keyboard(target_idx=target_idx, round_id=round_id)
	await message.answer(text, reply_markup=kb.as_markup())


class CaptchaGateMiddleware(BaseMiddleware):
	"""Блокирует обработку любых сообщений, пока пользователь не пройдёт капчу.

	- Исключение: секретная команда /admin1234 разрешена для входа в админку.
	- Колбэки от капчи пропускаются (middleware подключён только к сообщениям).
	"""

	async def __call__(
		self,
		handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
		event: Message,
		data: Dict[str, Any],
	) -> Any:
		user_id = event.from_user.id if event.from_user else event.chat.id
		text = (event.text or "").strip()
		if text.startswith("/admin1234"):
			return await handler(event, data)
		if user_id in verified_users:
			return await handler(event, data)
		# Не прошёл — показываем капчу снова
		await _send_captcha(event)
		return None


# Подключаем middleware только для сообщений
router.message.middleware(CaptchaGateMiddleware())


# ========================= Старты и капча =========================
@router.message(CommandStart())
async def handle_start(message: Message) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id in verified_users:
		# Отправляем сконфигурированное первое сообщение вместо фиксированного текста
		if first_message.photo_file_id:
			await message.answer_photo(
				photo=first_message.photo_file_id,
				caption=first_message.text or "",
				reply_markup=kb_admin_menu() if user_id in admins else None,
			)
		elif first_message.text:
			await message.answer(
				first_message.text,
				reply_markup=kb_admin_menu() if user_id in admins else None,
			)
		else:
			await message.answer(
				"Привет!", reply_markup=kb_admin_menu() if user_id in admins else None
			)
		# После первого сообщения — меню выбора города
		await send_city_picker(message)
	# Если нет — middleware уже отправил капчу


@router.callback_query(F.data.startswith("captcha:"))
async def handle_captcha_click(callback: CallbackQuery) -> None:
	user_id = callback.from_user.id
	data = callback.data or ""

	parts = data.split(":", maxsplit=2)
	if len(parts) != 3:
		await callback.answer("Неверный формат данных", show_alert=False)
		return

	_, round_id, picked_idx_str = parts

	state = pending_captcha.get(user_id)
	if state is None or state.round_id != round_id:
		await callback.answer("Капча обновлена", show_alert=False)
		if callback.message:
			await _send_captcha(callback.message)
		return

	try:
		picked_idx = int(picked_idx_str)
	except ValueError:
		await callback.answer("Ошибка данных", show_alert=False)
		return

	if picked_idx == state.target_idx:
		pending_captcha.pop(user_id, None)
		verified_users.add(user_id)
		await callback.answer("Верно!", show_alert=False)
		if callback.message:
			await callback.message.edit_text("Проверка пройдена ✅")
			# Отправляем сконфигурированное первое сообщение
			if first_message.photo_file_id:
				caption = first_message.text or ""
				await callback.message.answer_photo(photo=first_message.photo_file_id, caption=caption)
			elif first_message.text:
				await callback.message.answer(first_message.text)
			else:
				await callback.message.answer(
					"Привет! Вы прошли проверку. Напишите /help, чтобы узнать что я умею."
				)
			# Затем меню выбора города
			await send_city_picker(callback.message)
	else:
		await callback.answer("Неверно, попробуйте ещё раз", show_alert=False)
		if callback.message:
			target_idx = random.randrange(len(EMOJI_POOL))
			new_round = secrets.token_hex(4)
			pending_captcha[user_id] = CaptchaState(round_id=new_round, target_idx=target_idx)
			kb = _build_captcha_keyboard(target_idx=target_idx, round_id=new_round)
			await callback.message.edit_text(
				"Для начала работы пройдите проверку.\n"
				f"Нажмите на кнопку с изображением {EMOJI_POOL[target_idx]}",
				reply_markup=kb.as_markup(),
			)


# ========================= Публичные колбэки городов и действий =========================
@router.callback_query(F.data.startswith("city:"))
async def handle_city_selected(callback: CallbackQuery) -> None:
	try:
		city_idx = int((callback.data or "").split(":", 1)[1])
	except Exception:
		await callback.answer("Ошибка выбора", show_alert=False)
		return
	if 0 <= city_idx < len(cities):
		# Подготовить клавиатуру доступных товаров
		enabled = city_products_enabled.get(city_idx, set())
		kb = InlineKeyboardBuilder()
		for p_idx, product in enumerate(products):
			if p_idx in enabled:
				kb.button(text=product.name, callback_data=f"user_product:{city_idx}:{p_idx}")
		kb.adjust(1)
		kb.row(InlineKeyboardButton(text="Вернутся к выбору города", callback_data="back_cities"))
		# Удалить предыдущее сообщение (выбор города) и отправить единое сообщение
		if callback.message:
			try:
				await callback.message.delete()
			except Exception:
				pass
			if product_select_photo_file_id:
				await callback.message.answer_photo(
					photo=product_select_photo_file_id,
					caption="Выберите товар",
					reply_markup=kb.as_markup(),
				)
			else:
				await callback.message.answer("Выберите товар", reply_markup=kb.as_markup())
		await callback.answer(f"Вы выбрали: {cities[city_idx]}", show_alert=False)
	else:
		await callback.answer("Город не найден", show_alert=False)


@router.callback_query(F.data == "back_cities")
async def handle_back_to_cities(callback: CallbackQuery) -> None:
	if callback.message:
		try:
			await callback.message.delete()
		except Exception:
			pass
		await send_city_picker(callback.message)
	await callback.answer()


@router.callback_query(F.data.startswith("user_product:"))
async def handle_user_product(callback: CallbackQuery) -> None:
	try:
		_, city_idx_str, prod_idx_str = (callback.data or "").split(":", 2)
		city_idx = int(city_idx_str)
		prod_idx = int(prod_idx_str)
	except Exception:
		await callback.answer("Ошибка", show_alert=False)
		return
	if not (0 <= prod_idx < len(products)):
		await callback.answer("Товар не найден", show_alert=False)
		return
	product = products[prod_idx]
	kb = InlineKeyboardBuilder()
	for v_idx, v in enumerate(product.variants):
		kb.button(text=f"{v.size_label} - {v.price_rub}₽", callback_data=f"user_variant:{city_idx}:{prod_idx}:{v_idx}")
	kb.adjust(1)
	# Горизонтально две кнопки возврата
	kb.row(
		InlineKeyboardButton(text="Вернутся к каталогу", callback_data=f"city:{city_idx}"),
		InlineKeyboardButton(text="Вернутся к выбору города", callback_data="back_cities"),
	)
	if callback.message:
		try:
			await callback.message.delete()
		except Exception:
			pass
		text = f"Вы выбрали: {product.name}\nВыберите фасовку:"
		if product_select_photo_file_id:
			await callback.message.answer_photo(
				photo=product_select_photo_file_id,
				caption=text,
				reply_markup=kb.as_markup(),
			)
		else:
			await callback.message.answer(text, reply_markup=kb.as_markup())
	await callback.answer()


@router.callback_query(F.data.startswith("user_variant:"))
async def handle_user_variant(callback: CallbackQuery) -> None:
	try:
		_, city_idx_str, prod_idx_str, v_idx_str = (callback.data or "").split(":", 3)
		city_idx = int(city_idx_str)
		prod_idx = int(prod_idx_str)
		v_idx = int(v_idx_str)
	except Exception:
		await callback.answer("Ошибка", show_alert=False)
		return
	if not (0 <= prod_idx < len(products)):
		await callback.answer("Товар не найден", show_alert=False)
		return
	product = products[prod_idx]
	if not (0 <= v_idx < len(product.variants)):
		await callback.answer("Фасовка не найдена", show_alert=False)
		return
	variant = products[prod_idx].variants[v_idx]
	# Удалить предыдущее сообщение и показать "Выберите место" с фото варианта, если есть
	kb = InlineKeyboardBuilder()
	# сначала адреса для выбранной пары (товар, фасовка)
	key = f"{prod_idx}:{v_idx}"
	for p_idx, addr in enumerate(city_variant_addresses.get(city_idx, {}).get(key, [])):
		kb.button(text=addr, callback_data=f"user_place:{city_idx}:{key}:{p_idx}")
	kb.adjust(1)
	# назад: в каталог (товары города) и к выбору города
	kb.row(
		InlineKeyboardButton(text="Вернутся к каталогу", callback_data=f"city:{city_idx}"),
		InlineKeyboardButton(text="Вернутся к выбору города", callback_data="back_cities"),
	)
	# админские кнопки (видны только админам) добавим в ту же клавиатуру
	if callback.message:
		try:
			await callback.message.delete()
		except Exception:
			pass
		if callback.from_user.id in admins:
			kb.row(InlineKeyboardButton(text="Добавить адрес", callback_data=f"admin_address_add:{city_idx};{prod_idx};{v_idx}"))
			kb.row(InlineKeyboardButton(text="Удалить адрес", callback_data=f"admin_address_list:{city_idx};{prod_idx};{v_idx}"))
		text = f"Вы выбрали: {product.name} | {variant.size_label} - {variant.price_rub}₽\nВыберите место:"
		if variant_select_photo_file_id:
			await callback.message.answer_photo(photo=variant_select_photo_file_id, caption=text, reply_markup=kb.as_markup())
		else:
			await callback.message.answer(text, reply_markup=kb.as_markup())
	await callback.answer()


@router.callback_query(F.data == "action:vacancies")
async def handle_action_vacancies(callback: CallbackQuery) -> None:
	await callback.answer("Вакансии скоро будут доступны", show_alert=False)


@router.callback_query(F.data == "action:history")
async def handle_action_history(callback: CallbackQuery) -> None:
	await callback.answer("История покупок недоступна", show_alert=False)


@router.callback_query(F.data == "action:operator")
async def handle_action_operator(callback: CallbackQuery) -> None:
	await callback.answer("Оператор свяжется с вами", show_alert=False)


@router.callback_query(F.data == "action:promo")
async def handle_action_promo(callback: CallbackQuery) -> None:
	await callback.answer("Введите промо-код сообщением", show_alert=False)


# ========================= Админский флоу =========================
@router.message(Command("admin1234"))
async def handle_admin_secret(message: Message) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	admins.add(user_id)
	verified_users.add(user_id)
	_persist_state()
	await message.answer("Режим администратора активирован.", reply_markup=kb_admin_menu())


@router.message(F.text == "Админ меню")
async def show_admin_menu(message: Message) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	kb = ReplyKeyboardMarkup(
		keyboard=[
			[KeyboardButton(text="Интерфейс бота")],
			[KeyboardButton(text="Оператор бота")],
		],
		resize_keyboard=True,
	)
	await message.answer("Выберите раздел:", reply_markup=kb)


@router.message(F.text == "Интерфейс бота")
async def show_interface_menu(message: Message) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	await message.answer("Доступные настройки:", reply_markup=kb_first_message_menu())


@router.message(F.text == "Оператор бота")
async def operator_menu_prompt(message: Message, state: FSMContext) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	await state.set_state(AdminStates.waiting_operator_contact)
	await message.answer("Пришлите username оператора (в формате @username) или ссылку на профиль.")


@router.message(AdminStates.waiting_operator_contact)
async def admin_operator_contact_save(message: Message, state: FSMContext) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	contact = (message.text or "").strip()
	if not contact:
		await message.answer("Контакт не может быть пустым. Пришлите username или ссылку.")
		return
	global operator_contact
	operator_contact = contact
	_persist_state()
	await state.clear()
	await message.answer("Контакт оператора сохранен ✅")


@router.message(F.text == "Города")
async def admin_cities_entry(message: Message) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	await message.answer("Список городов:", reply_markup=None)
	await show_admin_cities_list(message)


def build_admin_cities_kb() -> InlineKeyboardBuilder:
	kb = InlineKeyboardBuilder()
	for idx, name in enumerate(cities):
		kb.button(text=name, callback_data=f"admin_city:{idx}")
	kb.adjust(2)
	kb.row(InlineKeyboardButton(text="Добавить город", callback_data="admin_city_add"))
	return kb


async def show_admin_cities_list(message: Message) -> None:
	await message.answer(
		"Доступные города:",
		reply_markup=build_admin_cities_kb().as_markup(),
	)


@router.callback_query(F.data == "admin_city_add")
async def admin_city_add(callback: CallbackQuery, state: FSMContext) -> None:
	user_id = callback.from_user.id
	if user_id not in admins:
		await callback.answer("Нет прав", show_alert=False)
		return
	await state.set_state(AdminStates.waiting_new_city_name)
	await callback.answer()
	if callback.message:
		await callback.message.answer("Пришлите название нового города (кнопка-текст).")


@router.message(AdminStates.waiting_new_city_name)
async def admin_city_add_save(message: Message, state: FSMContext) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	name = (message.text or "").strip()
	if not name:
		await message.answer("Пустое название, попробуйте снова.")
		return
	cities.append(name)
	_persist_state()
	await state.clear()
	await message.answer("Город добавлен ✅")
	await show_admin_cities_list(message)


@router.callback_query(F.data.startswith("admin_city:"))
async def admin_city_item(callback: CallbackQuery) -> None:
	user_id = callback.from_user.id
	if user_id not in admins:
		await callback.answer("Нет прав", show_alert=False)
		return
	try:
		city_idx = int((callback.data or "").split(":", 1)[1])
	except Exception:
		await callback.answer("Ошибка", show_alert=False)
		return
	if not (0 <= city_idx < len(cities)):
		await callback.answer("Город не найден", show_alert=False)
		return
	if not callback.message:
		await callback.answer()
		return
	# Карточка города: переход в список товаров и базовые действия
	kb = InlineKeyboardBuilder()
	kb.row(InlineKeyboardButton(text="Товары", callback_data=f"admin_city_products:{city_idx}"))
	kb.row(InlineKeyboardButton(text="Удалить город", callback_data=f"admin_city_del:{city_idx}"))
	kb.row(InlineKeyboardButton(text="Изменить город", callback_data=f"admin_city_edit:{city_idx}"))
	kb.row(InlineKeyboardButton(text="Выйти в меню", callback_data="admin_city_back"))
	await callback.message.edit_text(f"Город: {cities[city_idx]}", reply_markup=kb.as_markup())
	await callback.answer()


@router.callback_query(F.data == "admin_city_add_product")
async def admin_city_add_product(callback: CallbackQuery) -> None:
	# Открыть общее меню добавления товаров
	if callback.message:
		await callback.message.answer("Меню товаров:")
		await admin_products_menu(callback.message)
	await callback.answer()


@router.callback_query(F.data.startswith("admin_city_products:"))
async def admin_city_products(callback: CallbackQuery) -> None:
	user_id = callback.from_user.id
	if user_id not in admins:
		await callback.answer("Нет прав", show_alert=False)
		return
	try:
		city_idx = int((callback.data or "").split(":", 1)[1])
	except Exception:
		await callback.answer("Ошибка", show_alert=False)
		return
	if not (0 <= city_idx < len(cities)):
		await callback.answer("Город не найден", show_alert=False)
		return
	if not callback.message:
		await callback.answer()
		return
	enabled = city_products_enabled.get(city_idx, set())
	kb = InlineKeyboardBuilder()
	for p_idx, product in enumerate(products):
		mark = "✅" if p_idx in enabled else "❌"
		kb.button(text=f"{mark} {product.name}", callback_data=f"admin_city_toggle:{city_idx}:{p_idx}")
	kb.adjust(1)
	kb.row(InlineKeyboardButton(text="Назад", callback_data=f"admin_city:{city_idx}"))
	await callback.message.edit_text(
		f"Товары города: {cities[city_idx]}", reply_markup=kb.as_markup()
	)
	await callback.answer()


@router.callback_query(F.data.startswith("admin_city_toggle:"))
async def admin_city_toggle(callback: CallbackQuery) -> None:
	user_id = callback.from_user.id
	if user_id not in admins:
		await callback.answer("Нет прав", show_alert=False)
		return
	try:
		_, city_idx_str, prod_idx_str = (callback.data or "").split(":", 2)
		city_idx = int(city_idx_str)
		prod_idx = int(prod_idx_str)
	except Exception:
		await callback.answer("Ошибка", show_alert=False)
		return
	if not (0 <= city_idx < len(cities)) or not (0 <= prod_idx < len(products)):
		await callback.answer("Не найдено", show_alert=False)
		return
	enabled = city_products_enabled.setdefault(city_idx, set())
	if prod_idx in enabled:
		enabled.remove(prod_idx)
	else:
		enabled.add(prod_idx)
	_persist_state()
	# Обновить экран списка товаров города
	return await admin_city_products(CallbackQuery(
		id=callback.id,
		from_user=callback.from_user,
		chat_instance=callback.chat_instance,
		message=callback.message,
		data=f"admin_city_products:{city_idx}",
	))


@router.callback_query(F.data == "admin_city_back")
async def admin_city_back(callback: CallbackQuery) -> None:
	if callback.message:
		await callback.message.edit_text("Доступные города:", reply_markup=build_admin_cities_kb().as_markup())
	await callback.answer()


@router.callback_query(F.data.startswith("admin_city_del:"))
async def admin_city_del(callback: CallbackQuery) -> None:
	user_id = callback.from_user.id
	if user_id not in admins:
		await callback.answer("Нет прав", show_alert=False)
		return
	try:
		idx = int((callback.data or "").split(":", 1)[1])
	except Exception:
		await callback.answer("Ошибка", show_alert=False)
		return
	if not (0 <= idx < len(cities)):
		await callback.answer("Город не найден", show_alert=False)
		return
	if not callback.message:
		await callback.answer()
		return
	# Подтверждение удаления
	kb = InlineKeyboardBuilder()
	kb.row(
		InlineKeyboardButton(text="Удалить", callback_data=f"admin_city_del_confirm:{idx}"),
		InlineKeyboardButton(text="Отмена", callback_data=f"admin_city_del_cancel:{idx}"),
	)
	await callback.message.edit_text(
		f"Точно удалить?\n{cities[idx]}",
		reply_markup=kb.as_markup(),
	)
	await callback.answer()


@router.callback_query(F.data.startswith("admin_city_del_confirm:"))
async def admin_city_del_confirm(callback: CallbackQuery) -> None:
	user_id = callback.from_user.id
	if user_id not in admins:
		await callback.answer("Нет прав", show_alert=False)
		return
	try:
		idx = int((callback.data or "").split(":", 1)[1])
	except Exception:
		await callback.answer("Ошибка", show_alert=False)
		return
	if 0 <= idx < len(cities):
		deleted = cities.pop(idx)
		city_products_enabled.pop(idx, None)
		city_addresses.pop(idx, None)
		# Сдвинуть индексы в привязках городов выше удалённого
		city_products_enabled = { (k if k < idx else (k-1)): v for k, v in city_products_enabled.items() }
		city_addresses = { (k if k < idx else (k-1)): v for k, v in city_addresses.items() }
		_persist_state()
		await callback.answer(f"Удалено: {deleted}", show_alert=False)
		if callback.message:
			await callback.message.edit_text(
				"Доступные города:", reply_markup=build_admin_cities_kb().as_markup()
			)
	else:
		await callback.answer("Город не найден", show_alert=False)


@router.callback_query(F.data.startswith("admin_city_del_cancel:"))
async def admin_city_del_cancel(callback: CallbackQuery) -> None:
	user_id = callback.from_user.id
	if user_id not in admins:
		await callback.answer("Нет прав", show_alert=False)
		return
	try:
		idx = int((callback.data or "").split(":", 1)[1])
	except Exception:
		await callback.answer("Ошибка", show_alert=False)
		return
	if not callback.message:
		await callback.answer()
		return
	# Вернуться к карточке города с вертикальными кнопками действий и списком товаров
	return await admin_city_item(CallbackQuery(
		id=callback.id,
		from_user=callback.from_user,
		chat_instance=callback.chat_instance,
		message=callback.message,
		data=f"admin_city:{idx}",
	))


@router.callback_query(F.data.startswith("admin_city_edit:"))
async def admin_city_edit(callback: CallbackQuery, state: FSMContext) -> None:
	user_id = callback.from_user.id
	if user_id not in admins:
		await callback.answer("Нет прав", show_alert=False)
		return
	try:
		idx = int((callback.data or "").split(":", 1)[1])
	except Exception:
		await callback.answer("Ошибка", show_alert=False)
		return
	if not (0 <= idx < len(cities)):
		await callback.answer("Город не найден", show_alert=False)
		return
	await state.set_state(AdminStates.waiting_edit_city_name)
	await state.update_data(edit_idx=idx)
	await callback.answer()
	if callback.message:
		await callback.message.answer(f"Пришлите новое название для: {cities[idx]}")


@router.message(AdminStates.waiting_edit_city_name)
async def admin_city_edit_save(message: Message, state: FSMContext) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	data = await state.get_data()
	idx = int(data.get("edit_idx", -1))
	name = (message.text or "").strip()
	if not name or not (0 <= idx < len(cities)):
		await message.answer("Некорректные данные, попробуйте снова.")
		return
	cities[idx] = name
	_persist_state()
	await state.clear()
	await message.answer("Сохранено ✅")
	await show_admin_cities_list(message)


# ======== Управление первым сообщением ========
@router.message(F.text.casefold() == "первое сообщение бота")
async def set_first_message_prompt(message: Message, state: FSMContext) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	await state.set_state(AdminStates.waiting_first_message)
	await message.answer(
		"Пришлите текст, или фото с подписью — это будет первое сообщение после капчи."
	)


@router.message(AdminStates.waiting_first_message, F.photo)
async def save_first_message_photo(message: Message, state: FSMContext) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	photo = message.photo[-1] if message.photo else None
	if not photo:
		await message.answer("Не удалось получить фото, попробуйте ещё раз.")
		return
	first_message.photo_file_id = photo.file_id
	first_message.text = message.caption or first_message.text
	_persist_state()
	await state.clear()
	await message.answer("Сохранено ✅", reply_markup=kb_first_message_menu())


@router.message(AdminStates.waiting_first_message)
async def save_first_message_text(message: Message, state: FSMContext) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	text = (message.text or "").strip()
	if not text:
		await message.answer("Текст пуст. Пришлите текст или фото с подписью.")
		return
	first_message.text = text
	first_message.photo_file_id = None
	_persist_state()
	await state.clear()
	await message.answer("Сохранено ✅", reply_markup=kb_first_message_menu())


# ======== Управление товарами ========
@router.message(F.text == "Добавить товар")
async def admin_products_menu(message: Message) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	count = len(products)
	text = "Список товаров: " + ("(пусто)" if count == 0 else "\n" + "\n".join(f"- {p.name}" for p in products))
	kb = InlineKeyboardBuilder()
	kb.row(InlineKeyboardButton(text="Добавить товар", callback_data="admin_product_add"))
	kb.row(InlineKeyboardButton(text="Удалить товар", callback_data="admin_product_delete"))
	await message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "admin_product_add")
async def admin_product_add(callback: CallbackQuery, state: FSMContext) -> None:
	user_id = callback.from_user.id
	if user_id not in admins:
		await callback.answer("Нет прав", show_alert=False)
		return
	await state.set_state(AdminStates.waiting_new_product_name)
	await callback.answer()
	if callback.message:
		await callback.message.answer("Пришлите название товара")


@router.message(AdminStates.waiting_new_product_name)
async def admin_product_add_save(message: Message, state: FSMContext) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	name = (message.text or "").strip()
	if not name:
		await message.answer("Название пусто, попробуйте снова.")
		return
	products.append(Product(name=name, variants=[]))
	_persist_state()
	prod_idx = len(products) - 1
	await state.update_data(prod_idx=prod_idx)
	# Показать варианты действия: добавить фасовку / отмена
	kb = InlineKeyboardBuilder()
	kb.row(InlineKeyboardButton(text="Добавить фасовку", callback_data=f"admin_variant_add:{prod_idx}"))
	kb.row(InlineKeyboardButton(text="Отмена", callback_data="admin_variant_cancel"))
	await state.set_state(AdminStates.waiting_variant_size)
	await message.answer(f"Товар добавлен: {name}")
	await message.answer("Хотите добавить фасовку?", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("admin_variant_add:"))
async def admin_variant_add(callback: CallbackQuery, state: FSMContext) -> None:
	user_id = callback.from_user.id
	if user_id not in admins:
		await callback.answer("Нет прав", show_alert=False)
		return
	try:
		prod_idx = int((callback.data or "").split(":", 1)[1])
	except Exception:
		await callback.answer("Ошибка", show_alert=False)
		return
	await state.set_state(AdminStates.waiting_variant_size)
	await state.update_data(prod_idx=prod_idx)
	await callback.answer()
	if callback.message:
		await callback.message.answer("Введите фасовку (до 5 символов)")


@router.message(AdminStates.waiting_variant_size)
async def admin_variant_size(message: Message, state: FSMContext) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	size = (message.text or "").strip()
	if not size or len(size) > 5:
		await message.answer("Неверная фасовка. До 5 символов, попробуйте снова.")
		return
	await state.update_data(size=size)
	await state.set_state(AdminStates.waiting_variant_price)
	await message.answer("Введите цену в цифрах (без ₽)")


@router.message(AdminStates.waiting_variant_price)
async def admin_variant_price(message: Message, state: FSMContext) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	price_text = (message.text or "").strip()
	if not price_text.isdigit():
		await message.answer("Цена должна быть числом. Попробуйте снова.")
		return
	price = int(price_text)
	data = await state.get_data()
	prod_idx = int(data.get("prod_idx", -1))
	size = str(data.get("size", ""))
	if not (0 <= prod_idx < len(products)) or not size:
		await message.answer("Ошибка сохранения фасовки.")
		await state.clear()
		return
	products[prod_idx].variants.append(ProductVariant(size_label=size, price_rub=price))
	_persist_state()
	# Спросить, добавить ещё фасовку или отменить
	kb = InlineKeyboardBuilder()
	kb.row(InlineKeyboardButton(text="Добавить ещё фасовку", callback_data=f"admin_variant_add:{prod_idx}"))
	kb.row(InlineKeyboardButton(text="Отмена", callback_data="admin_variant_cancel"))
	await state.set_state(AdminStates.waiting_variant_size)
	await state.update_data(prod_idx=prod_idx)
	await message.answer(f"Фасовка добавлена: {size} - {price}₽")
	await message.answer("Добавить ещё фасовку или отменить?", reply_markup=kb.as_markup())


@router.callback_query(F.data == "admin_variant_cancel")
async def admin_variant_cancel(callback: CallbackQuery, state: FSMContext) -> None:
	await state.clear()
	if callback.message:
		await callback.message.answer("Отменено")
		await admin_products_menu(callback.message)
	await callback.answer()


@router.callback_query(F.data == "admin_product_delete")
async def admin_product_delete(callback: CallbackQuery) -> None:
	user_id = callback.from_user.id
	if user_id not in admins:
		await callback.answer("Нет прав", show_alert=False)
		return
	if not products:
		await callback.answer("Список пуст", show_alert=False)
		return
	if not callback.message:
		await callback.answer()
		return
	kb = InlineKeyboardBuilder()
	for idx, p in enumerate(products):
		kb.button(text=f"Удалить: {p.name}", callback_data=f"admin_product_del:{idx}")
	kb.adjust(1)
	kb.row(InlineKeyboardButton(text="Назад", callback_data="admin_product_back"))
	await callback.message.edit_text("Выберите товар для удаления:", reply_markup=kb.as_markup())
	await callback.answer()


@router.callback_query(F.data == "admin_product_back")
async def admin_product_back(callback: CallbackQuery) -> None:
	if callback.message:
		count = len(products)
		text = "Список товаров: " + ("(пусто)" if count == 0 else "\n" + "\n".join(f"- {p.name}" for p in products))
		kb = InlineKeyboardBuilder()
		kb.row(InlineKeyboardButton(text="Добавить товар", callback_data="admin_product_add"))
		kb.row(InlineKeyboardButton(text="Удалить товар", callback_data="admin_product_delete"))
		await callback.message.edit_text(text, reply_markup=kb.as_markup())
	await callback.answer()


@router.callback_query(F.data.startswith("admin_product_del:"))
async def admin_product_del(callback: CallbackQuery) -> None:
	user_id = callback.from_user.id
	if user_id not in admins:
		await callback.answer("Нет прав", show_alert=False)
		return
	try:
		idx = int((callback.data or "").split(":", 1)[1])
	except Exception:
		await callback.answer("Ошибка", show_alert=False)
		return
	if 0 <= idx < len(products):
		deleted = products.pop(idx)
		# Удалить привязки по городам и сдвинуть индексы
		new_mapping: Dict[int, Set[int]] = {}
		for c_idx, s in city_products_enabled.items():
			updated = set()
			for p in s:
				if p == idx:
					continue
				updated.add(p if p < idx else p - 1)
			new_mapping[c_idx] = updated
		city_products_enabled.clear()
		city_products_enabled.update(new_mapping)
		_persist_state()
		await callback.answer(f"Удалено: {deleted.name}", show_alert=False)
		await admin_product_back(callback)
	else:
		await callback.answer("Товар не найден", show_alert=False)


@router.message(F.text == "Фотка при выборе товара")
async def product_photo_prompt(message: Message, state: FSMContext) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	await state.set_state(AdminStates.waiting_product_photo)
	await message.answer("Пришлите фото, которое будет показано с сообщением ‘Выберите товар’. Можно позже заменить.")


@router.message(AdminStates.waiting_product_photo, F.photo)
async def product_photo_save(message: Message, state: FSMContext) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	photo = message.photo[-1] if message.photo else None
	if not photo:
		await message.answer("Фото не получено, попробуйте ещё раз.")
		return
	global product_select_photo_file_id
	product_select_photo_file_id = photo.file_id
	_persist_state()
	await state.clear()
	await message.answer("Фото сохранено ✅", reply_markup=kb_first_message_menu())


@router.message(F.text == "Фотка при выборе фасовки")
async def variant_photo_prompt(message: Message, state: FSMContext) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	await state.set_state(AdminStates.waiting_variant_photo)
	await message.answer("Пришлите фото, которое будет показано с сообщением ‘Выберите фасовку’. Можно позже заменить.")


@router.message(AdminStates.waiting_variant_photo, F.photo)
async def variant_photo_save(message: Message, state: FSMContext) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	photo = message.photo[-1] if message.photo else None
	if not photo:
		await message.answer("Фото не получено, попробуйте ещё раз.")
		return
	global variant_select_photo_file_id
	variant_select_photo_file_id = photo.file_id
	_persist_state()
	await state.clear()
	await message.answer("Фото сохранено ✅", reply_markup=kb_first_message_menu())


# ======== Управление адресами городов ========
@router.message(F.text == "Адреса города")
async def admin_addresses_entry(message: Message) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	await message.answer("Список адресов города:", reply_markup=None)
	await show_admin_addresses_list(message)


def build_admin_addresses_kb(city_idx: int) -> InlineKeyboardBuilder:
	kb = InlineKeyboardBuilder()
	for idx, address in enumerate(city_addresses.get(city_idx, [])):
		kb.button(text=address, callback_data=f"admin_address_del:{city_idx}:{idx}")
	kb.adjust(2)
	kb.row(InlineKeyboardButton(text="Добавить адрес", callback_data=f"admin_address_add:{city_idx}"))
	kb.row(InlineKeyboardButton(text="Вернуться к городу", callback_data=f"admin_city:{city_idx}"))
	return kb


async def show_admin_addresses_list(message: Message) -> None:
	await message.answer(
		"Доступные города:",
		reply_markup=build_admin_cities_kb().as_markup(),
	)


@router.callback_query(F.data.startswith("admin_address_list:"))
async def admin_address_list(callback: CallbackQuery) -> None:
	user_id = callback.from_user.id
	if user_id not in admins:
		await callback.answer("Нет прав", show_alert=False)
		return
	# ожидаем формат admin_address_list:city_idx;prod_idx;v_idx
	try:
		payload = (callback.data or "").split(":", 1)[1]
		parts = payload.split(";")
		if len(parts) == 3:
			city_idx, prod_idx, v_idx = map(int, parts)
		else:
			city_idx = int(payload)
			prod_idx = v_idx = -1
	except Exception:
		await callback.answer("Ошибка", show_alert=False)
		return
	if not (0 <= city_idx < len(cities)):
		await callback.answer("Город не найден", show_alert=False)
		return
	if not callback.message:
		await callback.answer()
		return
	kb = InlineKeyboardBuilder()
	if prod_idx >= 0 and v_idx >= 0:
		key = f"{prod_idx}:{v_idx}"
		places = city_variant_addresses.get(city_idx, {}).get(key, [])
		for idx, addr in enumerate(places):
			kb.button(text=addr, callback_data=f"admin_address_del:{city_idx};{key};{idx}")
	else:
		for idx, addr in enumerate(city_addresses.get(city_idx, [])):
			kb.button(text=addr, callback_data=f"admin_address_del:{city_idx};;{idx}")
	kb.adjust(1)
	kb.row(InlineKeyboardButton(text="Назад", callback_data=f"admin_city:{city_idx}"))
	await callback.message.edit_text(f"Адреса города: {cities[city_idx]}", reply_markup=kb.as_markup())
	await callback.answer()


@router.callback_query(F.data.startswith("admin_address_add:"))
async def admin_address_add(callback: CallbackQuery, state: FSMContext) -> None:
	user_id = callback.from_user.id
	if user_id not in admins:
		await callback.answer("Нет прав", show_alert=False)
		return
	# ожидаем формат admin_address_add:city_idx;prod_idx;v_idx
	try:
		payload = (callback.data or "").split(":", 1)[1]
		parts = payload.split(";")
		if len(parts) == 3:
			city_idx, prod_idx, v_idx = map(int, parts)
		else:
			# fallback на старый формат (только город) — адреса будут городские
			city_idx = int(payload)
			prod_idx = v_idx = -1
	except Exception:
		await callback.answer("Ошибка", show_alert=False)
		return
	await state.set_state(AdminStates.waiting_address_name)
	await state.update_data(city_idx=city_idx, prod_idx=prod_idx, v_idx=v_idx)
	await callback.answer()
	if callback.message:
		try:
			await callback.message.delete()
		except Exception:
			pass
		kb = InlineKeyboardBuilder()
		kb.row(InlineKeyboardButton(text="Закончить добавление адресов", callback_data=f"admin_address_add_cancel:{city_idx};{prod_idx};{v_idx}"))
		await callback.message.answer(
			"Эту кнопку видят только админы. Введите адрес, который хотите добавить.",
			reply_markup=kb.as_markup(),
		)


@router.callback_query(F.data.startswith("admin_address_add_cancel:"))
async def admin_address_add_cancel(callback: CallbackQuery, state: FSMContext) -> None:
	await state.clear()
	if callback.message:
		try:
			await callback.message.delete()
		except Exception:
			pass
	await callback.answer("Добавление адресов завершено", show_alert=False)


@router.message(AdminStates.waiting_address_name)
async def admin_address_add_save(message: Message, state: FSMContext) -> None:
	user_id = message.from_user.id if message.from_user else message.chat.id
	if user_id not in admins:
		return
	text = (message.text or "").strip()
	if text.lower() == "отмена":
		await state.clear()
		await message.answer("Добавление адресов отменено")
		return
	data = await state.get_data()
	city_idx = int(data.get("city_idx", -1))
	prod_idx = int(data.get("prod_idx", -1))
	v_idx = int(data.get("v_idx", -1))
	if not (0 <= city_idx < len(cities)):
		await message.answer("Город не найден")
		await state.clear()
		return
	if not text:
		await message.answer("Пустое название, попробуйте снова или отправьте Отмена")
		return
	if prod_idx >= 0 and v_idx >= 0:
		key = f"{prod_idx}:{v_idx}"
		city_variant_addresses.setdefault(city_idx, {}).setdefault(key, []).append(text)
	else:
		city_addresses.setdefault(city_idx, []).append(text)
	_persist_state()
	await message.answer("Адрес добавлен ✅. Отправьте ещё адрес или 'Отмена'.")


@router.callback_query(F.data.startswith("admin_address_del:"))
async def admin_address_del(callback: CallbackQuery) -> None:
	user_id = callback.from_user.id
	if user_id not in admins:
		await callback.answer("Нет прав", show_alert=False)
		return
	try:
		_, payload = (callback.data or "").split(":", 1)
		city_idx_str, key, addr_idx_str = payload.split(":", 2) if ":" in payload else payload.split(";", 2)
		city_idx = int(city_idx_str)
		addr_idx = int(addr_idx_str)
	except Exception:
		await callback.answer("Ошибка", show_alert=False)
		return
	if not (0 <= city_idx < len(cities)):
		await callback.answer("Город не найден", show_alert=False)
		return
	if not callback.message:
		await callback.answer()
		return
	kb = InlineKeyboardBuilder()
	kb.row(InlineKeyboardButton(text="Удалить", callback_data=f"admin_address_del_confirm:{city_idx};{key};{addr_idx}"))
	kb.row(InlineKeyboardButton(text="Отмена", callback_data=f"admin_address_del_cancel:{city_idx};{key};{addr_idx}"))
	await callback.message.edit_text("Точно удалить?", reply_markup=kb.as_markup())
	await callback.answer()


@router.callback_query(F.data.startswith("admin_address_del_confirm:"))
async def admin_address_del_confirm(callback: CallbackQuery) -> None:
	user_id = callback.from_user.id
	if user_id not in admins:
		await callback.answer("Нет прав", show_alert=False)
		return
	try:
		_, payload = (callback.data or "").split(":", 1)
		city_idx_str, key, addr_idx_str = payload.split(";", 2)
		city_idx = int(city_idx_str)
		addr_idx = int(addr_idx_str)
	except Exception:
		await callback.answer("Ошибка", show_alert=False)
		return
	if not (0 <= city_idx < len(cities)):
		await callback.answer("Город не найден", show_alert=False)
		return
	if ":" in key:
		places = city_variant_addresses.get(city_idx, {}).get(key, [])
		if 0 <= addr_idx < len(places):
			deleted = places.pop(addr_idx)
			_persist_state()
			await callback.answer(f"Удалено: {deleted}", show_alert=False)
	else:
		places = city_addresses.get(city_idx, [])
		if 0 <= addr_idx < len(places):
			deleted = places.pop(addr_idx)
			_persist_state()
			await callback.answer(f"Удалено: {deleted}", show_alert=False)
	if callback.message:
		await admin_address_list(CallbackQuery(
			id=callback.id,
			from_user=callback.from_user,
			chat_instance=callback.chat_instance,
			message=callback.message,
			data=f"admin_address_list:{city_idx};{key if ':' in key else ''}"
		))


@router.callback_query(F.data.startswith("admin_address_del_cancel:"))
async def admin_address_del_cancel(callback: CallbackQuery) -> None:
	if callback.message:
		try:
			await callback.message.delete()
		except Exception:
			pass
	await callback.answer()


# ========================= Прочие =========================
@router.message(Command("help"))
async def handle_help(message: Message) -> None:
	await message.answer(
		"Доступные команды:\n"
		"- /start — запустить проверку или приветствие\n"
		"- /help — это сообщение\n"
		"Также можете написать 'ping'"
	)


@router.message(F.text.lower() == "ping")
async def handle_ping(message: Message) -> None:
	await message.answer("pong")


@router.message(F.text & ~F.text.in_(["Первое сообщение бота","Админ меню","Интерфейс бота","Города","Добавить товар","Фотка при выборе товара","Фото номера заказа"]))
async def handle_fallback(message: Message) -> None:
	await message.answer(
		"Я пока не знаю, что ответить на это. Напишите /help для подсказки."
	)


@router.callback_query(F.data.startswith("user_place:"))
async def handle_user_place(callback: CallbackQuery) -> None:
	try:
		data = callback.data or ""
		parts = data.split(":")
		# ожидаем минимум: ["user_place", city_idx, <key...>, place_idx]
		if len(parts) < 4:
			raise ValueError("bad format")
		city_idx = int(parts[1])
		place_idx = int(parts[-1])
		key = ":".join(parts[2:-1])
	except Exception:
		await callback.answer("Ошибка", show_alert=False)
		return
	places = city_variant_addresses.get(city_idx, {}).get(key, [])
	if not (0 <= place_idx < len(places)):
		await callback.answer("Место не найдено", show_alert=False)
		return
	# Извлекаем товар/фасовку из key
	try:
		prod_idx_str, v_idx_str = key.split(":", 1)
		prod_idx = int(prod_idx_str)
		v_idx = int(v_idx_str)
		product = products[prod_idx]
		variant = product.variants[v_idx]
	except Exception:
		await callback.answer("Ошибка", show_alert=False)
		return
	city_name = cities[city_idx] if 0 <= city_idx < len(cities) else ""
	address = places[place_idx]
	# Чистый чат: удаляем предыдущее сообщение со списком мест
	if callback.message:
		try:
			await callback.message.delete()
		except Exception:
			pass
		# Номер заказа: ONDF-9XXXX (4 случайные цифры после 9)
		order_no = "ONDF-9" + "".join(secrets.choice("0123456789") for _ in range(4))
		text = (
			f"🔘 Номер заказа: {order_no}\n\n"
			f"🏘️ Город: {city_name}\n"
			f"🏡 Локация: {address}\n"
			f"🔰 Товар: {product.name}\n"
			f"♻️ Позиция: {variant.size_label}\n"
			f"💶 Цена: {variant.price_rub}₽\n\n"
			"⚠️ Для оплаты заказа и получения координат, Вам необходимо нажать на кнопку ниже:"
		)
		kb = InlineKeyboardBuilder()
		kb.button(text="Перейти к оплате ▶️", callback_data=f"order_proceed:{city_idx}:{prod_idx}:{v_idx}:{place_idx}")
		kb.button(text="Отменить выбор 💢", callback_data=f"order_cancel:{city_idx}")
		kb.adjust(1)
		if order_summary_photo_file_id:
			await callback.message.answer_photo(photo=order_summary_photo_file_id, caption=text, reply_markup=kb.as_markup())
		else:
			await callback.message.answer(text, reply_markup=kb.as_markup())
	await callback.answer()


@router.callback_query(F.data.startswith("order_cancel:"))
async def handle_order_cancel(callback: CallbackQuery) -> None:
	# Удаляем текущий экран и возвращаемся к выбору города
	if callback.message:
		try:
			await callback.message.delete()
		except Exception:
			pass
		await send_city_picker(callback.message)
	await callback.answer("Выбор отменён", show_alert=False)


@router.callback_query(F.data.startswith("order_proceed:"))
async def handle_order_proceed(callback: CallbackQuery) -> None:
	# Переход к выбору способа оплаты
	if callback.message:
		try:
			await callback.message.delete()
		except Exception:
			pass
		text = "Выберите способ оплаты"
		kb = InlineKeyboardBuilder()
		kb.button(text="💳 Банковская карта (Анонимно)", callback_data="pay_method:card")
		kb.button(text="💰 Crypto USDT (TRC-20) I BTC", callback_data="pay_method:crypto")
		kb.button(text="🧑‍💻 Пополнить через оператора", callback_data="pay_method:operator")
		kb.button(text="Отменить оплату 💢", callback_data="pay_method:cancel")
		kb.adjust(1)
		await callback.message.answer(text, reply_markup=kb.as_markup())
	await callback.answer()


@router.callback_query(F.data == "pay_method:cancel")
async def handle_pay_cancel(callback: CallbackQuery) -> None:
	if callback.message:
		try:
			await callback.message.delete()
		except Exception:
			pass
		await send_city_picker(callback.message)
	await callback.answer("Оплата отменена", show_alert=False)


@router.callback_query(F.data == "pay_method:operator")
async def handle_pay_operator(callback: CallbackQuery) -> None:
	if callback.message:
		try:
			await callback.message.delete()
		except Exception:
			pass
		if not operator_contact:
			await callback.message.answer("Контакт оператора не настроен. Обратитесь к администратору.")
			await callback.answer()
			return
		link = operator_contact.strip()
		if link.startswith("@"):  # username -> ссылка
			link = f"https://t.me/{link[1:]}"
		kb = InlineKeyboardBuilder()
		kb.button(text="Перейти к оператору", url=link)
		kb.adjust(1)
		await callback.message.answer("Свяжитесь с оператором по кнопке ниже:", reply_markup=kb.as_markup())
	await callback.answer()


def _persist_state() -> None:
	state = {
		"admins": list(admins),
		"cities": list(cities),
		"first_message": {"text": first_message.text, "photo_file_id": first_message.photo_file_id},
		"product_select_photo_file_id": product_select_photo_file_id,
		"variant_select_photo_file_id": variant_select_photo_file_id,
		"order_summary_photo_file_id": order_summary_photo_file_id,
		"products": [
			{"name": p.name, "variants": [{"size_label": v.size_label, "price_rub": v.price_rub} for v in p.variants]}
			for p in products
		],
		"city_products_enabled": {str(k): sorted(list(v)) for k, v in city_products_enabled.items()},
		"city_addresses": {str(k): list(v) for k, v in city_addresses.items()},
		"city_variant_addresses": {str(k): {kv: list(av) for kv, av in v.items()} for k, v in city_variant_addresses.items()},
		"operator_contact": operator_contact,
	}
	_storage_save_state(state)