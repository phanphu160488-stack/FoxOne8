"""
utils.py — CÁC HÀM TIỆN ÍCH DÙNG CHUNG
=======================================
Định dạng thời gian, lấy IP thật, rate-limit chống DDoS,
tạo link Link4m, kiểm tra VPN/Proxy.
"""

import json
import os
import random
import threading
import time
import urllib.parse
import urllib.request as _ureq
from datetime import datetime

from flask import request

from config import VN_TZ, LINK4M_API_KEYS

# ------------------------------------------------------------------
# requests (chỉ dùng cho Telegram bot + SoundCloud) — tùy chọn
# ------------------------------------------------------------------
try:
    import requests as _req_tg
    _TG_OK = True
except ImportError:
    _req_tg = None
    _TG_OK = False


# ------------------------------------------------------------------
# REAL IP FROM SERVER
# ------------------------------------------------------------------
def get_real_ip():
    if request.headers.get('CF-Connecting-IP'):
        return request.headers.get('CF-Connecting-IP')
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr


# ------------------------------------------------------------------
# TIME FORMATTING (múi giờ VN)
# ------------------------------------------------------------------
def get_time_left_str(expiry_timestamp):
    if expiry_timestamp == -1:
        return "∞"
    now = time.time()
    diff = expiry_timestamp - now
    if diff <= 0:
        return "Hết hạn"
    days = int(diff // 86400)
    hours = int((diff % 86400) // 3600)
    minutes = int((diff % 3600) // 60)
    parts = []
    if days > 0:
        parts.append(f"{days} ngày")
    if hours > 0:
        parts.append(f"{hours} giờ")
    if minutes > 0:
        parts.append(f"{minutes} phút")
    return " ".join(parts) if parts else "Dưới 1 phút"


def format_ts(ts):
    if not ts:
        return "Chưa cập nhật"
    return datetime.fromtimestamp(ts, VN_TZ).strftime('%d/%m/%Y %H:%M:%S')


def format_full_ts(ts):
    if not ts:
        return "Chưa kích hoạt"
    dt = datetime.fromtimestamp(ts, VN_TZ)
    days = ["Chủ Nhật", "Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7"]
    day_str = days[int(dt.strftime('%w'))]
    return f"{day_str}, {dt.strftime('%d/%m/%Y %H:%M:%S')} (VN)"


def secs_from_unit(val, unit):
    """Đổi (giá trị, đơn vị) thành số giây. permanent → -1."""
    unit = (unit or '').lower()
    m = {
        'phút': 60, 'phut': 60, 'tiếng': 3600, 'tieng': 3600, 'giờ': 3600, 'gio': 3600,
        'ngày': 86400, 'ngay': 86400, 'tháng': 30 * 86400, 'thang': 2592000,
        'năm': 365 * 86400, 'nam': 31536000, 'year': 31536000, 'month': 2592000,
        'day': 86400, 'hour': 3600, 'minute': 60, 'permanent': -1, 'permanen': -1,
    }
    return m.get(unit, 3600) * int(val)


# ------------------------------------------------------------------
# ANTI-DDOS RATE LIMITER (in-memory, transparent)
# ------------------------------------------------------------------
_RATE_LIMITER = {}
_RATE_LOCK = threading.Lock()


def check_rate_limit(ip, max_req=20, window=60):
    now = time.time()
    with _RATE_LOCK:
        times = _RATE_LIMITER.get(ip, [])
        times = [t for t in times if now - t < window]
        if len(times) >= max_req:
            _RATE_LIMITER[ip] = times
            return False
        times.append(now)
        _RATE_LIMITER[ip] = times
        return True


def get_rate_limiter():
    return _RATE_LIMITER


# ------------------------------------------------------------------
# LINK4M SHORTENER
# ------------------------------------------------------------------
def shorten_with_link4m(long_url):
    """
    Gọi link4m API để tạo link rút gọn thật.
    Trả về (short_url, error_msg). Thành công → (url, None).
    """
    try:
        link4m_api_key = random.choice(LINK4M_API_KEYS)
        for encoded_url in [urllib.parse.quote(long_url, safe=''), long_url]:
            api_url = f"https://link4m.co/api-shorten/v2?api={link4m_api_key}&url={encoded_url}"
            try:
                req = _ureq.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
                resp = _ureq.urlopen(req, timeout=15)
                raw = resp.read().decode('utf-8', errors='replace')
                data = json.loads(raw)
                if data.get('status') == 'success':
                    su = data.get('shortenedUrl', '') or data.get('shorten_url', '')
                    if su and su.startswith('http'):
                        return su, None
                err_msg = data.get('message') or data.get('error') or str(data)
                return '', f"Link4m API lỗi: {err_msg}"
            except Exception:
                continue
        return '', "Không kết nối được Link4m API sau 2 lần thử"
    except Exception as e:
        return '', f"Lỗi hệ thống khi gọi Link4m: {str(e)}"


# ------------------------------------------------------------------
# VPN / PROXY CHECK
# ------------------------------------------------------------------
def check_vpn_or_proxy(ip):
    try:
        url = f"http://ip-api.com/json/{ip}?fields=status,proxy,hosting"
        resp = _ureq.urlopen(url, timeout=5)
        data = json.loads(resp.read().decode())
        if data.get('status') == 'success':
            return bool(data.get('proxy') or data.get('hosting'))
    except Exception:
        pass
    return False
