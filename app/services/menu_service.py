import csv
from pathlib import Path
from typing import Any


MENU_FILE_PATH = Path("data/menu.csv")


def normalize_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def load_menu() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    with open(MENU_FILE_PATH, mode="r", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        for row in reader:
            items.append(
                {
                    "category": row.get("category", "").strip(),
                    "item_id": row.get("item_id", "").strip(),
                    "name": row.get("name", "").strip(),
                    "description": row.get("description", "").strip(),
                    "price_m": int(row.get("price_m", 0) or 0),
                    "price_l": int(row.get("price_l", 0) or 0),
                    "available": str(row.get("available", "")).strip().lower() == "true",
                }
            )

    return items


MENU_DATA = load_menu()


def get_all_items() -> list[dict[str, Any]]:
    return MENU_DATA


def get_available_items() -> list[dict[str, Any]]:
    return [item for item in MENU_DATA if item["available"]]


def find_item_by_name(query: str) -> dict[str, Any] | None:
    normalized_query = normalize_text(query)

    for item in MENU_DATA:
        if normalize_text(item["name"]) == normalized_query:
            return item

    for item in MENU_DATA:
        if normalized_query in normalize_text(item["name"]):
            return item

    return None

def find_item_by_id(item_id: str) -> dict | None:
    normalized_item_id = item_id.strip().upper()

    for item in MENU_DATA:
        if item["item_id"].strip().upper() == normalized_item_id:
            return item

    return None


def get_price_by_size(item: dict[str, Any], size: str) -> int:
    normalized_size = size.strip().upper()

    if normalized_size == "M":
        return item["price_m"]
    if normalized_size == "L":
        return item["price_l"]

    raise ValueError("Size must be M or L")


def is_item_available(item: dict[str, Any]) -> bool:
    return bool(item["available"])