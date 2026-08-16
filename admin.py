"""
admin.py — ADMIN PANEL (QUẢN LÝ KEY / THIẾT BỊ / CẤU HÌNH)
==========================================================
Blueprint `admin_bp` chứa toàn bộ endpoint dành cho Admin:
  - Tạo / xoá / reset / gia hạn / khóa / sao chép key
  - Danh sách key, key free, thống kê
  - Cấu hình key free + thông báo
  - Duyệt / thu hồi thiết bị (device requests)
  - Tạo link getkey, web log
"""

import os
import random
import string
import time
from datetime import datetime

from flask import Blueprint, jsonify, redirect, request, session

from config import DEFAULT_FREE_CONFIG
from database import (
    load_db, save_db,
    sql_get_key, sql_save_key, sql_delete_key,
    sql_get_meta, sql_save_meta, sql_list_keys,
)
from utils import get_real_ip, format_ts, format_full_ts, get_time_left_str, shorten_with_link4m
from telegrambot import tg_notify

admin_bp = Blueprint('admin', __name__)


# ============================================================
# ADMIN PANEL — QUẢN LÝ KEY
# ============================================================
@admin_bp.route('/admin', methods=['GET', 'POST'])
def admin_add_key():
    if request.method == 'GET':
        return redirect('/')
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    mode = request.form.get('mode', 'random')
    time_val = request.form.get('v', '1').strip()
    time_unit = request.form.get('u')
    max_dev = int(request.form.get('d', 1))
    if mode == 'custom' and request.form.get('c_key', '').strip():
        key_name = request.form.get('c_key').strip()
    else:
        p1 = "".join(random.choices(string.ascii_uppercase + string.digits, k=3))
        p2 = "".join(random.choices(string.ascii_uppercase + string.digits, k=3))
        pref_map = {"permanent": "VIP", "ngày": f"{time_val}DAY", "phút": f"{time_val}P", "tiếng": f"{time_val}H", "tháng": f"{time_val}M", "năm": f"{time_val}Y"}
        key_name = f"{pref_map.get(time_unit, 'KEY')}-{p1}-{p2}"
    sql_save_key(key_name, {
        "duration_val": int(time_val) if time_unit != "permanent" else 0,
        "duration_unit": time_unit, "max_devices": max_dev, "status": "Chưa kích hoạt",
        "activated_time": None, "created_at": time.time(), "used_devices": {}
    })
    return jsonify({"status": "success", "key": key_name})


@admin_bp.route('/api/list_keys', methods=['GET'])
def list_keys():
    if not session.get('is_admin'):
        return jsonify([]), 401
    db = sql_list_keys()
    now = time.time()
    res = []
    for k, v in db.items():
        if k.startswith("___"):
            continue
        if isinstance(v.get('used_devices', []), list):
            new_devs = {}
            for d in v.get('used_devices', []):
                new_devs[d] = v.get('expiry_time', 0)
            v['used_devices'] = new_devs
            sql_save_key(k, v)
        if v['status'] == "Đã kích hoạt":
            is_full = len(v['used_devices']) >= v['max_devices']
            _non_perm2 = [e for e in v['used_devices'].values() if e != -1]
            all_exp = len(_non_perm2) > 0 and all(now > e for e in _non_perm2)
            if is_full and all_exp:
                v['status'] = "Hết hạn"
                sql_save_key(k, v)
        dev_list = [{"device_id": did, "expiry": exp} for did, exp in v['used_devices'].items()]
        age_hours = (now - v.get('created_at', now)) / 3600
        res.append({
            "key": k, "status": v['status'],
            "han_dung": f"{v['duration_val']} {v['duration_unit']}" if v['duration_unit'] != 'permanent' else "Vĩnh viễn",
            "thiet_bi": f"{len(v['used_devices'])}/{v['max_devices']}",
            "activated_time_str": format_full_ts(v.get('activated_time')),
            "created_at_str": format_ts(v.get('created_at')),
            "creator_info": v.get('creator_info', 'Admin Gốc'),
            "devices": dev_list, "is_free": k.startswith("FREE-"),
            "created_at_ts": v.get('created_at', 0),
            "age_hours": round(age_hours, 1),
            "is_locked": v.get('is_locked', False)
        })
    return jsonify(res)


@admin_bp.route('/delete/<key>')
def delete(key):
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    sql_delete_key(key)
    ip_map = sql_get_meta("___IP_KEY_MAP___", {})
    to_remove = [ip for ip, k in ip_map.items() if k == key]
    for ip in to_remove:
        del ip_map[ip]
    sql_save_meta("___IP_KEY_MAP___", ip_map)
    return jsonify({"status": "success"})


@admin_bp.route('/reset/<key>')
def reset_key(key):
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    info = sql_get_key(key)
    if info is not None:
        info['status'] = "Chưa kích hoạt"
        info['activated_time'] = None
        info['used_devices'] = {}
        sql_save_key(key, info)
    return jsonify({"status": "success"})


@admin_bp.route('/admin/free_setup', methods=['POST'])
def admin_free_setup():
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    sql_save_meta("___FREE_CONFIG___", {"val": request.form.get('v'), "unit": request.form.get('u'), "dev": request.form.get('d')})
    return jsonify({"status": "success"})


@admin_bp.route('/admin/get_free_config', methods=['GET'])
def admin_get_free_config():
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    cfg = sql_get_meta("___FREE_CONFIG___", DEFAULT_FREE_CONFIG)
    return jsonify({"status": "success", "val": str(cfg.get('val', '12')), "unit": str(cfg.get('unit', 'tiếng')), "dev": str(cfg.get('dev', '1'))})


@admin_bp.route('/api/admin/update_announcement', methods=['POST'])
def api_update_announcement():
    """Admin endpoint — cập nhật banner thông báo."""
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    data = request.get_json(force=True, silent=True) or {}
    text = str(data.get('text', '')).strip()
    sql_save_meta("___ANNOUNCEMENT___", {"text": text})
    return jsonify({"ok": True, "text": text})


@admin_bp.route('/api/create_key', methods=['POST'])
def api_create_key():
    if not session.get('is_admin'):
        return jsonify({"status": "error", "message": "Không có quyền"}), 401
    now = time.time()
    try:
        duration = int(request.form.get('duration', 86400))
        max_devices = max(1, int(request.form.get('max_devices', 1)))
        count = max(1, min(50, int(request.form.get('count', 1))))
        key_type = request.form.get('key_type', 'premium').lower()
    except Exception:
        return jsonify({"status": "error", "message": "Tham số không hợp lệ"})

    if duration >= 315360000:
        val, unit = 0, 'permanent'
    elif duration >= 31536000:
        val, unit = duration // 31536000, 'năm'
    elif duration >= 2592000:
        val, unit = duration // 2592000, 'tháng'
    elif duration >= 86400:
        val, unit = duration // 86400, 'ngày'
    elif duration >= 3600:
        val, unit = duration // 3600, 'tiếng'
    else:
        val, unit = max(1, duration // 60), 'phút'

    prefix = 'VIP' if key_type == 'vip' else ('FREE' if key_type == 'free' else 'KEY')
    keys_created = []
    for _ in range(count):
        p1 = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        p2 = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        p3 = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        key_name = f"{prefix}-{p1}-{p2}-{p3}"
        sql_save_key(key_name, {
            "duration_val": val, "duration_unit": unit,
            "max_devices": max_devices, "status": "Chưa kích hoạt",
            "activated_time": None, "created_at": now, "used_devices": {},
            "creator_info": f"Admin Panel | {key_type}"
        })
        keys_created.append(key_name)
    return jsonify({"status": "success", "keys": keys_created, "key": keys_created[0] if keys_created else ""})


@admin_bp.route('/api/list_free_keys', methods=['GET'])
def api_list_free_keys():
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    db = sql_list_keys()
    now = time.time()
    result = []
    for key, info in db.items():
        if key.startswith('___') or not isinstance(info, dict):
            continue
        if not key.upper().startswith('FREE'):
            continue
        duration_val = info.get('duration_val', 0)
        duration_unit = info.get('duration_unit', 'tiếng')
        activated_time = info.get('activated_time')
        expiry_str = '—'
        is_valid = False
        if activated_time:
            unit_secs = {'tiếng': 3600, 'giờ': 3600, 'ngày': 86400, 'ngay': 86400, 'tháng': 2592000, 'thang': 2592000, 'năm': 31536000, 'nam': 31536000, 'phút': 60, 'phut': 60}
            secs = unit_secs.get(duration_unit.lower(), 3600) * int(duration_val) if duration_val else 0
            exp_ts = activated_time + secs
            is_valid = now < exp_ts
            expiry_str = datetime.fromtimestamp(exp_ts).strftime('%d/%m/%Y %H:%M')
        created_at = info.get('created_at', 0)
        used_d = len(info.get('used_devices', {}))
        result.append({
            "key": key, "status": info.get('status', '—'), "expiry_date": expiry_str,
            "max_devices": info.get('max_devices', 1), "used_devices": used_d,
            "is_valid": is_valid, "key_type": "FREE",
            "created_at": time.strftime('%d/%m/%Y %H:%M', time.localtime(created_at)) if created_at else '—'
        })
    result.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return jsonify(result)


@admin_bp.route('/api/extend_key', methods=['POST'])
def api_extend_key():
    if not session.get('is_admin'):
        return jsonify({"status": "error", "message": "Không có quyền"}), 401
    key = request.form.get('key', '').strip()
    hours = request.form.get('hours', '24').strip()
    info = sql_get_key(key)
    if not key or info is None:
        return jsonify({"status": "error", "message": "Key không tồn tại"})
    if info is None or key.startswith("___"):
        return jsonify({"status": "error", "message": "Key không tồn tại"})
    try:
        hours = float(hours)
    except Exception:
        return jsonify({"status": "error", "message": "Số giờ không hợp lệ"})
    unit = info.get('duration_unit', 'tiếng').lower()
    unit_secs = {'tiếng': 3600, 'giờ': 3600, 'ngày': 86400, 'ngay': 86400, 'tháng': 2592000, 'thang': 2592000, 'năm': 31536000, 'nam': 31536000, 'phút': 60, 'phut': 60}
    secs_per = unit_secs.get(unit, 3600)
    old_val = int(info.get('duration_val', 0)) if info.get('duration_val') else 0
    add_secs = hours * 3600
    total_secs = old_val * secs_per + add_secs
    new_val = int(total_secs / secs_per) if secs_per else old_val
    info['duration_val'] = new_val
    sql_save_key(key, info)
    return jsonify({"status": "success", "message": f"Đã gia hạn thêm {hours:.0f} giờ cho {key}"})


@admin_bp.route('/api/lock_key', methods=['POST'])
def api_lock_key():
    if not session.get('is_admin'):
        return jsonify({"status": "error", "message": "Không có quyền"}), 401
    key = request.form.get('key', '').strip()
    info = sql_get_key(key)
    if not key or info is None or key.startswith("___"):
        return jsonify({"status": "error", "message": "Key không tồn tại"})
    current = info.get('is_locked', False)
    info['is_locked'] = not current
    sql_save_key(key, info)
    action = "khóa" if not current else "mở khóa"
    return jsonify({"status": "success", "is_locked": not current, "message": f"Đã {action} key thành công"})


@admin_bp.route('/api/copy_key', methods=['POST'])
def api_copy_key():
    if not session.get('is_admin'):
        return jsonify({"status": "error", "message": "Không có quyền"}), 401
    src_key = request.form.get('key', '').strip()
    src = sql_get_key(src_key)
    if not src_key or src is None or src_key.startswith("___"):
        return jsonify({"status": "error", "message": "Key không tồn tại"})
    prefix = src_key.split('-')[0] if '-' in src_key else 'KEY'
    p1 = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    p2 = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    p3 = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    new_key = f"{prefix}-{p1}-{p2}-{p3}"
    sql_save_key(new_key, {
        "duration_val": src.get('duration_val', 1),
        "duration_unit": src.get('duration_unit', 'ngày'),
        "max_devices": src.get('max_devices', 1),
        "status": "Chưa kích hoạt",
        "activated_time": None,
        "created_at": time.time(),
        "used_devices": {},
        "creator_info": f"Sao chép từ {src_key}"
    })
    return jsonify({"status": "success", "new_key": new_key, "message": f"Đã tạo bản sao: {new_key}"})


@admin_bp.route('/api/regen_free_key', methods=['POST'])
def regen_free_key():
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    target_ip = request.form.get('ip', '').strip()
    cfg = sql_get_meta("___FREE_CONFIG___", {"val": 12, "unit": "tiếng", "dev": 9999})
    ip_map = sql_get_meta("___IP_KEY_MAP___", {})

    old_key = ip_map.get(target_ip)
    if old_key:
        sql_delete_key(old_key)

    k = f"FREE-{''.join(random.choices(string.ascii_uppercase + string.digits, k=5))}"
    sql_save_key(k, {
        "duration_val": int(cfg['val']), "duration_unit": cfg['unit'],
        "max_devices": int(cfg['dev']),
        "status": "Chưa kích hoạt", "activated_time": None,
        "created_at": time.time(), "used_devices": {},
        "creator_info": f"Tái tạo bởi Admin | IP: {target_ip}",
        "client_ip": target_ip
    })
    ip_map[target_ip] = k
    sql_save_meta("___IP_KEY_MAP___", ip_map)
    return jsonify({"status": "success", "key": k})


@admin_bp.route('/api/key_stats', methods=['GET'])
def api_key_stats():
    """Thống kê key cho tab stats trong admin panel."""
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    db = sql_list_keys()
    now = time.time()
    total = 0
    activated = 0
    expired = 0
    not_activated = 0
    free_total = 0
    for k, v in db.items():
        if k.startswith("___"):
            continue
        if not isinstance(v, dict):
            continue
        total += 1
        if k.startswith("FREE-"):
            free_total += 1
        st = v.get('status', '')
        if st == "Đã kích hoạt":
            _non_perm = [e for e in v.get('used_devices', {}).values() if e != -1]
            is_full = len(v.get('used_devices', {})) >= v.get('max_devices', 1)
            all_exp = len(_non_perm) > 0 and all(now > e for e in _non_perm)
            if is_full and all_exp:
                expired += 1
            else:
                activated += 1
        elif st == "Hết hạn":
            expired += 1
        else:
            not_activated += 1
    stats = sql_get_meta("___FREE_KEY_STATS___", {"total_bypasses": 0})
    keys_list = []
    for k, v in db.items():
        if k.startswith("___") or not isinstance(v, dict):
            continue
        st = v.get('status', '')
        if st == 'Đã kích hoạt':
            _np = [e for e in v.get('used_devices', {}).values() if e != -1]
            _full = len(v.get('used_devices', {})) >= v.get('max_devices', 1)
            if _full and len(_np) > 0 and all(now > e for e in _np):
                st = 'Hết hạn'
        dur_unit = v.get('duration_unit', '')
        han = f"{v.get('duration_val', '?')} {dur_unit}" if dur_unit and dur_unit != 'permanent' else "Vĩnh viễn"
        keys_list.append({
            "key": k, "status": st,
            "han_dung": han,
            "created_at_str": format_ts(v.get('created_at'))
        })
    return jsonify({
        "total": total,
        "active": activated,
        "activated": activated,
        "expired": expired,
        "pending": not_activated,
        "not_activated": not_activated,
        "free_total": free_total,
        "total_devices": 0,
        "total_bypasses": stats.get("total_bypasses", 0),
        "keys": keys_list
    })


@admin_bp.route('/admin/gen_key_link', methods=['POST'])
def admin_gen_key_link():
    """Admin endpoint — tạo link4m cho panel key free."""
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    ip = get_real_ip()
    db = load_db()
    now = time.time()
    token = ''.join(random.choices(string.ascii_uppercase + string.digits, k=20))
    host = os.environ.get('RENDER_EXTERNAL_URL', request.host_url.rstrip('/'))
    dest_url = f"{host}/nhan-key-free?token={token}"
    short_url, err = shorten_with_link4m(dest_url)
    if not short_url:
        return jsonify({"status": "error", "message": f"Không tạo được link Link4m. {err}"}), 503
    tokens = db.get("___GETKEY_TOKENS___", {})
    tokens = {k: v for k, v in tokens.items() if now - v.get('created_at', 0) < 3600}
    tokens[token] = {"ip": ip, "created_at": now, "status": "pending", "is_admin": True}
    db["___GETKEY_TOKENS___"] = tokens
    stats = db.get("___FREE_KEY_STATS___", {"total_bypasses": 0})
    stats["total_bypasses"] = stats.get("total_bypasses", 0) + 1
    db["___FREE_KEY_STATS___"] = stats
    save_db(db)
    tg_notify(f"🔗 <b>LINK4M MỚI (Admin Panel)</b>\n📍 Admin IP: <code>{ip}</code>\n🔑 Token: <code>{token[:8]}...</code>\n🌐 Link: {short_url}")
    return jsonify({"status": "success", "shortenedUrl": short_url, "link": short_url, "token": token})


# ============================================================
# DEVICE REQUESTS / APPROVED DEVICES (ADMIN)
# ============================================================
@admin_bp.route('/api/list_device_requests', methods=['GET', 'OPTIONS'])
def list_device_requests():
    if request.method == 'OPTIONS':
        return jsonify([]), 200
    if not session.get('is_admin'):
        return jsonify([]), 401
    db = load_db()
    requests_map = db.get("___DEVICE_REQUESTS___", {})
    result = []
    for rid, rinfo in requests_map.items():
        if rinfo.get('status') == 'pending':
            result.append({
                "req_id": rid,
                "device_id": rinfo.get('device_id', ''),
                "val": rinfo.get('val', '1'),
                "unit": rinfo.get('unit', 'ngày'),
                "note": rinfo.get('note', ''),
                "submitted_at_str": format_ts(rinfo.get('submitted_at', 0)),
                "submitted_at_ts": rinfo.get('submitted_at', 0),
                "ip": rinfo.get('ip', '—')
            })
    result.sort(key=lambda x: x['submitted_at_ts'], reverse=True)
    return jsonify(result)


@admin_bp.route('/api/approve_device_request', methods=['POST', 'OPTIONS'])
def approve_device_request():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    req_id = request.form.get('req_id', '').strip()
    val = request.form.get('val', '').strip()
    unit = request.form.get('unit', '').strip()
    db = load_db()
    requests_map = db.get("___DEVICE_REQUESTS___", {})
    if req_id not in requests_map:
        return jsonify({"status": "error", "msg": "Yêu cầu không tồn tại!"})
    rinfo = requests_map[req_id]
    device_id = rinfo['device_id']
    now = time.time()
    val_int = int(val) if val and val.isdigit() else int(rinfo.get('val', 1))
    u = unit if unit else rinfo.get('unit', 'ngày')
    sec = -1
    if u == "phút":
        sec = val_int * 60
    elif u == "tiếng":
        sec = val_int * 3600
    elif u == "ngày":
        sec = val_int * 86400
    elif u == "tháng":
        sec = val_int * 30 * 86400
    elif u == "năm":
        sec = val_int * 365 * 86400
    expiry = -1 if sec == -1 else (now + sec)
    approved = db.get("___APPROVED_DEVICES___", {})
    approved[device_id] = {
        "expiry": expiry, "approved_at": now,
        "val": val_int, "unit": u,
        "note": rinfo.get('note', ''), "ip": rinfo.get('ip', '')
    }
    db["___APPROVED_DEVICES___"] = approved
    requests_map[req_id]['status'] = 'approved'
    db["___DEVICE_REQUESTS___"] = requests_map
    save_db(db)
    return jsonify({"status": "success"})


@admin_bp.route('/api/reject_device_request', methods=['POST'])
def reject_device_request():
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    req_id = request.form.get('req_id', '').strip()
    db = load_db()
    requests_map = db.get("___DEVICE_REQUESTS___", {})
    if req_id in requests_map:
        requests_map[req_id]['status'] = 'rejected'
        db["___DEVICE_REQUESTS___"] = requests_map
        save_db(db)
    return jsonify({"status": "success"})


@admin_bp.route('/api/list_approved_devices', methods=['GET'])
def list_approved_devices():
    if not session.get('is_admin'):
        return jsonify([]), 401
    db = load_db()
    approved = db.get("___APPROVED_DEVICES___", {})
    now = time.time()
    result = []
    for did, dinfo in approved.items():
        exp = dinfo.get('expiry', 0)
        if exp == -1:
            time_left = "Vĩnh viễn"
            is_expired = False
        else:
            time_left = get_time_left_str(exp)
            is_expired = now > exp
        result.append({
            "device_id": did,
            "expiry": exp,
            "expiry_str": format_ts(exp) if (exp != -1) else "Vĩnh viễn",
            "time_left": time_left,
            "is_expired": is_expired,
            "approved_at": format_ts(dinfo.get('approved_at', 0)),
            "val": dinfo.get('val', ''),
            "unit": dinfo.get('unit', ''),
            "note": dinfo.get('note', ''),
            "ip": dinfo.get('ip', '—')
        })
    return jsonify(result)


@admin_bp.route('/api/delete_approved_device', methods=['POST'])
def delete_approved_device():
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    device_id = request.form.get('device_id', '').strip()
    db = load_db()
    approved = db.get("___APPROVED_DEVICES___", {})
    if device_id in approved:
        del approved[device_id]
        db["___APPROVED_DEVICES___"] = approved
        save_db(db)
    return jsonify({"status": "success"})


@admin_bp.route('/api/extend_approved_device', methods=['POST'])
def extend_approved_device():
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    device_id = request.form.get('device_id', '').strip()
    val = request.form.get('val', '').strip()
    unit = request.form.get('unit', '').strip()
    db = load_db()
    approved = db.get("___APPROVED_DEVICES___", {})
    if device_id not in approved:
        return jsonify({"status": "error", "msg": "Device ID không tồn tại!"})
    dinfo = approved[device_id]
    now = time.time()
    val_int = int(val) if val and val.isdigit() else 1
    sec = 0
    if unit == "phút":
        sec = val_int * 60
    elif unit == "tiếng":
        sec = val_int * 3600
    elif unit == "ngày":
        sec = val_int * 86400
    elif unit == "tháng":
        sec = val_int * 30 * 86400
    elif unit == "năm":
        sec = val_int * 365 * 86400
    cur_exp = dinfo.get('expiry', now)
    if cur_exp == -1:
        new_exp = -1
    else:
        base = max(cur_exp, now)
        new_exp = base + sec
    dinfo['expiry'] = new_exp
    dinfo['val'] = val_int
    dinfo['unit'] = unit
    approved[device_id] = dinfo
    db["___APPROVED_DEVICES___"] = approved
    save_db(db)
    return jsonify({"status": "success"})


@admin_bp.route('/api/direct_activate_device', methods=['POST'])
def direct_activate_device():
    from datetime import timezone
    device_id = request.form.get('device_id', '').strip()
    expiry_date = request.form.get('expiry_date', '').strip()
    if not device_id:
        return jsonify({"status": "error", "msg": "Thiếu Device ID"})
    db = load_db()
    approved = db.get("___APPROVED_DEVICES___", {})
    now = time.time()
    expiry = -1
    if expiry_date:
        try:
            dt = datetime.strptime(expiry_date, '%Y-%m-%d')
            expiry = dt.replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            expiry = -1
    approved[device_id] = {
        "expiry": expiry,
        "approved_at": now,
        "val": 0,
        "unit": "permanent" if expiry == -1 else "ngày",
        "note": "Kích hoạt trực tiếp bởi Admin",
        "ip": get_real_ip()
    }
    db["___APPROVED_DEVICES___"] = approved
    save_db(db)
    return jsonify({"status": "success"})
