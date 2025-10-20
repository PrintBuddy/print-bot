import requests
from requests.exceptions import RequestException
from .config import API_BASE_URL, API_TIMEOUT


def get_users(chat_id: int):
    url = f"{API_BASE_URL}/telegram/users"
    try:
        res = requests.get(url, json={"chat_id": str(chat_id)}, timeout=API_TIMEOUT)
        return res.status_code, res.json()
    except RequestException:
        return 503, {"detail": "Could not reach the server. Please try again later."}


def get_me(chat_id: int):
    url = f"{API_BASE_URL}/telegram/me"
    try:
        res = requests.get(url, json={"chat_id": str(chat_id)}, timeout=API_TIMEOUT)
        return res.status_code, res.json()
    except RequestException:
        return 503, {"detail": "Could not reach the server. Please try again later."}

def generate_voucher(chat_id: int, amount: float):
    url = f"{API_BASE_URL}/telegram/generate-voucher"
    try:
        res = requests.post(url, json={"chat_id": str(chat_id), "amount": amount}, timeout=API_TIMEOUT)
        return res.status_code, res.json()
    except RequestException:
        return 503, {"detail": "Could not reach the server. Please try again later."}


def get_user(chat_id: int, username: str):
    url = f"{API_BASE_URL}/telegram/user/{username}"
    try:
        res = requests.get(url, json={"chat_id": str(chat_id)}, timeout=API_TIMEOUT)
        return res.status_code, res.json()
    except RequestException:
        return 503, {"detail": "Could not reach the server. Please try again later."}


def recharge_user(chat_id: int, username: str, amount: float):
    url = f"{API_BASE_URL}/telegram/recharge"
    payload = {"chat_id": str(chat_id), "username": username, "amount": amount}
    try:
        res = requests.patch(url, json=payload, timeout=API_TIMEOUT)
        return res.status_code, res.json() if res.content else {}
    except RequestException:
        return 503, {"detail": "Could not reach the server. Please try again later."}


def adjust_balance(chat_id: int, username: str, amount: float):
    url = f"{API_BASE_URL}/telegram/balance-adjust"
    payload = {"chat_id": str(chat_id), "username": username, "amount": amount}
    try:
        res = requests.patch(url, json=payload, timeout=API_TIMEOUT)
        return res.status_code, res.json()
    except RequestException:
        return 503, {"detail": "Could not reach the server. Please try again later."}