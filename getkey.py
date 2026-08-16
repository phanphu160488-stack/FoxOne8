"""
getkey.py — KEY FREE / LINK4M / BYPASS
======================================
Blueprint `getkey_bp` chứa toàn bộ luồng lấy key miễn phí:
  - /api/gen_free_task — tạo key free theo IP (dùng cho app/tool)
  - /api/getkey — tạo link4m rút gọn cho người dùng
  - /api/confirm_bypass — cấp key sau khi vượt link rút gọn
  - /api/free_config — cấu hình key free (public)
"""

import os
import random
import string
import time

from flask import Blueprint, jsonify, request

from config import DEFAULT_FREE_CONFIG, FREE_KEYS_PER_IP_PER_DAY
from database import (
    load_db, save_db,
    sql_get_key, sql_save_key,
    sql_get_meta, sql_save_meta,
)
from utils import get_real_ip, check_rate_limit, shorten_with_link4m
from telegrambot import tg_notify

getkey_bp = Blueprint('getkey', __name__)


@getkey_bp.route('/api/free_config', methods=['GET'])
def api_public_free_config():
    """Public endpoint — trả cấu hình key free cho trang getkey."""
    cfg = sql_get_meta("___FREE_CONFIG___", DEFAULT_FREE_CONFIG)
    return jsonify({"val": str(cfg.get('val', '12')), "unit": cfg.get('unit', 'tiếng'), "dev": str(cfg.get('dev', '1'))})


@getkey_bp.route('/api/gen_free_task', methods=['POST'])
def gen_free_task():
    cfg = sql_get_meta("___FREE_CONFIG___", {"val": 12, "unit": "tiếng", "dev": 9999})
    client_ip_info = request.form.get('ip_info', 'Không quét được Client')
    server_ip = get_real_ip()
    final_info = f"SV IP: {server_ip} | {client_ip_info}"

    # Per-IP key: trả key cũ nếu còn dùng được
    ip_map = sql_get_meta("___IP_KEY_MAP___", {})
    existing_key = ip_map.get(server_ip)
    if existing_key:
        existing_info = sql_get_key(existing_key)
        if existing_info:
            created_at = existing_info.get('created_at', 0)
            age_hours = (time.time() - created_at) / 3600
            if age_hours < 12 or existing_info.get('status') == 'Đã kích hoạt':
                return jsonify({"status": "success", "key": existing_key, "reused": True})

    k = f"FREE-{''.join(random.choices(string.ascii_uppercase + string.digits, k=5))}"
    sql_save_key(k, {
        "duration_val": int(cfg['val']), "duration_unit": cfg['unit'],
        "max_devices": int(cfg['dev']),
        "status": "Chưa kích hoạt", "activated_time": None,
        "created_at": time.time(), "used_devices": {},
        "creator_info": final_info,
        "client_ip": server_ip
    })
    ip_map[server_ip] = k
    sql_save_meta("___IP_KEY_MAP___", ip_map)
    return jsonify({"status": "success", "key": k, "reused": False})


@getkey_bp.route('/api/getkey', methods=['GET', 'POST', 'OPTIONS'])
def api_getkey():
    """Public API cho tool/bot ngoài — tự tạo link4m rút gọn."""
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
    ip = get_real_ip()
    if not check_rate_limit(ip, max_req=5, window=30):
        return jsonify({"status": "error", "message": "Quá nhiều yêu cầu. Thử lại sau 30 giây!"}), 429
    db = load_db()
    now = time.time()
    ip_free_history = db.get("___FREE_IP_HISTORY___", {})
    ip_records = [t for t in ip_free_history.get(ip, []) if now - t < 86400]
    if len(ip_records) >= FREE_KEYS_PER_IP_PER_DAY:
        return jsonify({"status": "error", "message": f"IP {ip} đã lấy đủ {FREE_KEYS_PER_IP_PER_DAY} key hôm nay. Thử lại sau 24 giờ!"}), 429
    token = ''.join(random.choices(string.ascii_uppercase + string.digits, k=20))
    host = os.environ.get('RENDER_EXTERNAL_URL', request.host_url.rstrip('/'))
    dest_url = f"{host}/nhan-key-free?token={token}"
    short_url, err = shorten_with_link4m(dest_url)
    if not short_url:
        return jsonify({"status": "error", "message": f"Không tạo được link Link4m. {err}"}), 503
    tokens = db.get("___GETKEY_TOKENS___", {})
    tokens = {k: v for k, v in tokens.items() if now - v.get('created_at', 0) < 3600}
    tokens[token] = {"ip": ip, "created_at": now, "status": "pending", "is_admin": False}
    db["___GETKEY_TOKENS___"] = tokens
    stats = db.get("___FREE_KEY_STATS___", {"total_bypasses": 0})
    stats["total_bypasses"] = stats.get("total_bypasses", 0) + 1
    db["___FREE_KEY_STATS___"] = stats
    save_db(db)
    tg_notify(f"🔗 <b>LINK4M MỚI (API/getkey)</b>\n📍 IP: <code>{ip}</code>\n🔑 Token: <code>{token[:8]}...</code>\n🌐 Link: {short_url}")
    return jsonify({"status": "success", "shortenedUrl": short_url, "link": short_url, "token": token})


@getkey_bp.route('/api/confirm_bypass', methods=['POST'])
def confirm_bypass():
    """Called khi user tới /nhan-key-free?token=XXX sau khi vượt link4m."""
    token = request.form.get('token', '').strip()
    client_ip_info = request.form.get('ip_info', '').strip()
    server_ip = get_real_ip()
    if not check_rate_limit(server_ip, max_req=8, window=60):
        return jsonify({"status": "error", "message": "Quá nhiều yêu cầu. Thử lại sau 1 phút!"})
    if not token:
        return jsonify({"status": "error", "message": "Token không hợp lệ! Bạn cần vượt link rút gọn trước."})
    now = time.time()
    tokens = sql_get_meta("___GETKEY_TOKENS___", {})
    if token not in tokens:
        return jsonify({"status": "error", "message": "Link đã hết hạn hoặc không hợp lệ! Vui lòng lấy link mới từ Admin."})
    token_info = tokens[token]
    if now - token_info.get('created_at', 0) > 3600:
        return jsonify({"status": "error", "message": "Link đã hết hạn (quá 1 giờ)! Vui lòng lấy link mới từ Admin."})
    if token_info.get('status') == 'used':
        existing_key = token_info.get('key', '')
        if existing_key and sql_get_key(existing_key):
            return jsonify({"status": "success", "key": existing_key, "reused": True, "msg": "Bạn đã nhận key này rồi!"})
        return jsonify({"status": "error", "message": "Link này đã được sử dụng! Vui lòng lấy link mới."})
    is_admin_token = token_info.get('is_admin', False)
    if not is_admin_token:
        ip_free_history = sql_get_meta("___FREE_IP_HISTORY___", {})
        ip_records = [t for t in ip_free_history.get(server_ip, []) if now - t < 86400]
        if len(ip_records) >= FREE_KEYS_PER_IP_PER_DAY:
            return jsonify({"status": "error", "message": f"IP này đã đạt giới hạn {FREE_KEYS_PER_IP_PER_DAY} key/ngày. Thử lại sau 24 giờ!"})
    cfg = sql_get_meta("___FREE_CONFIG___", {"val": 12, "unit": "tiếng", "dev": 9999})
    key_name = f"FREE-{''.join(random.choices(string.ascii_uppercase + string.digits, k=12))}"
    final_info = f"SV IP: {server_ip} | Token: {token[:8]}... | {client_ip_info}"
    sql_save_key(key_name, {
        "duration_val": int(cfg.get('val', 12)),
        "duration_unit": cfg.get('unit', 'tiếng'),
        "max_devices": int(cfg.get('dev', 9999)),
        "status": "Chưa kích hoạt",
        "activated_time": None,
        "created_at": now,
        "used_devices": {},
        "creator_info": final_info,
        "client_ip": server_ip
    })
    tokens[token]['status'] = 'used'
    tokens[token]['key'] = key_name
    sql_save_meta("___GETKEY_TOKENS___", tokens)
    if not is_admin_token:
        ip_free_history = sql_get_meta("___FREE_IP_HISTORY___", {})
        ip_records = [t for t in ip_free_history.get(server_ip, []) if now - t < 86400]
        ip_records.append(now)
        ip_free_history[server_ip] = ip_records
        sql_save_meta("___FREE_IP_HISTORY___", ip_free_history)
    ip_map = sql_get_meta("___IP_KEY_MAP___", {})
    ip_map[server_ip] = key_name
    sql_save_meta("___IP_KEY_MAP___", ip_map)
    tg_notify(f"🎉 <b>KEY FREE MỚI CẤP!</b>\n🔑 Key: <code>{key_name}</code>\n📍 IP: <code>{server_ip}</code>\n⏰ {cfg.get('val', 12)} {cfg.get('unit', 'tiếng')} | {cfg.get('dev', 1)} thiết bị\n📊 {client_ip_info[:100] if client_ip_info else '—'}")
    dur_val = int(cfg.get('val', 12))
    dur_unit = cfg.get('unit', 'tiếng')
    max_dev = int(cfg.get('dev', 1))
    duration_label = f"{dur_val} {dur_unit}"
    expiry_note = f"Hết hạn sau {duration_label} kể từ lần dùng đầu tiên"
    return jsonify({"status": "success", "key": key_name, "reused": False,
                    "expiry": expiry_note, "duration_label": duration_label,
                    "max_devices": max_dev})
