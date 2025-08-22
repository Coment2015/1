import json
import os
from typing import Any, Dict

STATE_DIR = "/workspace/data"
STATE_FILE = os.path.join(STATE_DIR, "state.json")


def default_state() -> Dict[str, Any]:
	return {
		"admins": [],
		"cities": [
			"🦑 Москва 🦑",
			"🦑 Санкт-Петербург 🦑",
			"🦑 Екатеринбург 🦑",
			"🦑 Новосибирск 🦑",
			"🦑 Казань 🦑",
			"🦑 Краснодар 🦑",
			"🦑 Ростов 🦑",
			"🦑 Пермь 🦑",
			"🦑 Уфа 🦑",
			"🦑 Сочи 🦑",
			"🦑 Саратов 🦑",
			"🦑 Самара 🦑",
			"🦑 Воронеж 🦑",
		],
		"first_message": {"text": "Привет! Вы прошли проверку. Напишите /help, чтобы узнать что я умею.", "photo_file_id": None},
		"product_select_photo_file_id": None,
		"variant_select_photo_file_id": None,
		"products": [],  # [{"name": str, "variants": [{"size_label": str, "price_rub": int}]}]
		"city_products_enabled": {},  # {str(city_idx): [product_idx, ...]}
		"city_variant_addresses": {},  # {str(city_idx): {"prod:var": [address, ...]}}
		"operator_contact": None,
	}


def load_state() -> Dict[str, Any]:
	try:
		with open(STATE_FILE, "r", encoding="utf-8") as f:
			data = json.load(f)
			# заполняем отсутствующие ключи значениями по умолчанию (миграция)
			defaults = default_state()
			for k, v in defaults.items():
				data.setdefault(k, v)
			return data
	except FileNotFoundError:
		return default_state()
	except Exception:
		# В случае повреждения файла — начать с дефолта
		return default_state()


def save_state(state: Dict[str, Any]) -> None:
	os.makedirs(STATE_DIR, exist_ok=True)
	tmp_file = STATE_FILE + ".tmp"
	with open(tmp_file, "w", encoding="utf-8") as f:
		json.dump(state, f, ensure_ascii=False, indent=2)
	os.replace(tmp_file, STATE_FILE)