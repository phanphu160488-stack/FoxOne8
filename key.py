"""
key.py — API XÁC THỰC KEY + CHECK DEVICE
=========================================
Blueprint `key_bp` chứa toàn bộ endpoint xác thực / tra cứu key:
  - /api/verify, /api/Fox, /api/check_expiry, /api/check-device
  - /api/checkkey, /api/checkkey/<key>, /api/check_key
  - /api/get_key_ip_info, /api/check_free_key_status
  - Đăng ký / duyệt thiết bị (device requests) — phía người dùng
"""

import hashlib
import random
import string
import time

from flask import Blueprint, jsonify, request

from database import (
    load_db, save_db,
    sql_get_key, sql_save_key,
    sql_get_meta, sql_find_key_by_device,
)
from utils import (
    get_real_ip, get_time_left_str, format_ts,
    check_rate_limit,
)
from telegrambot import tg_notify

key_bp = Blueprint('key', __name__)


# ============================================================
# API XÁC THỰC KEY (/api/verify, /api/Fox, ...)
# ============================================================
@key_bp.route('/api/verify', methods=['POST'])
def api_verify():
    """
    ENDPOINT CHÍNH — Tool/script bên ngoài gọi để xác thực key + hwid.
    1. Gửi POST { "key": "...", "hwid": "DEVICE_ID_CỐ_ĐỊNH" }
    2. Lần đầu → key kích hoạt, hwid đăng ký, timer chạy từ LÚC NÀY.
    3. Lần sau → kiểm tra expiry của riêng hwid đó.
    4. Key permanent → expiry_timestamp = -1, time_left = "∞".
    5. Key hết hạn / đầy thiết bị → không cho đăng ký mới.
    """
    data = request.get_json(silent=True) or {}
    key = (data.get('key', '') or request.form.get('key', '')).strip()
    hwid = (data.get('hwid', '') or data.get('device_id', '') or request.form.get('hwid', '') or request.form.get('device_id', '')).strip()
    if not key or not hwid:
        return jsonify({"status": "error", "message": "Missing key or hwid. Gửi: {key, hwid}"})
    info = sql_get_key(key)
    if info is None or key.startswith("___"):
        return jsonify({"status": "invalid", "message": "Key does not exist"})
    now = time.time()
    if isinstance(info.get('used_devices', []), list):
        new_devs = {}
        for d in info.get('used_devices', []):
            new_devs[d] = info.get('expiry_time', 0)
        info['used_devices'] = new_devs
    val, unit = info['duration_val'], info['duration_unit']
    sec = -1
    if unit == "phút":
        sec = val * 60
    elif unit == "tiếng":
        sec = val * 3600
    elif unit == "ngày":
        sec = val * 86400
    elif unit == "tháng":
        sec = val * 30 * 86400
    elif unit == "năm":
        sec = val * 365 * 86400
    is_permanent = (sec == -1)
    if info['status'] == "Hết hạn":
        return jsonify({
            "status": "expired",
            "message": "Key này đã hết hạn và không còn dùng được",
            "key_status": "Hết hạn",
            "expiry_timestamp": None,
            "is_permanent": False,
            "time_left": "Hết hạn"
        })
    is_first_activation = (info['status'] == "Chưa kích hoạt")
    if is_first_activation:
        info['status'] = "Đã kích hoạt"
        info['activated_time'] = now
    if hwid in info['used_devices']:
        dev_exp = info['used_devices'][hwid]
        if dev_exp != -1 and now > dev_exp:
            _non_perm = [e for e in info['used_devices'].values() if e != -1]
            is_full = len(info['used_devices']) >= info['max_devices']
            all_exp = len(_non_perm) > 0 and all(now > e for e in _non_perm)
            if is_full and all_exp:
                info['status'] = "Hết hạn"
            sql_save_key(key, info)
            return jsonify({
                "status": "expired",
                "message": "Key đã hết hạn trên thiết bị này",
                "expiry_timestamp": dev_exp,
                "expiry_str": format_ts(dev_exp),
                "is_permanent": False
            })
        sql_save_key(key, info)
        return jsonify({
            "status": "success",
            "message": "Key hợp lệ",
            "time_left": get_time_left_str(dev_exp),
            "expiry_timestamp": dev_exp,
            "expiry_str": format_ts(dev_exp) if dev_exp != -1 else "Vĩnh Viễn",
            "is_permanent": (dev_exp == -1),
            "is_new_device": False
        })
    else:
        if len(info['used_devices']) >= info['max_devices']:
            sql_save_key(key, info)
            return jsonify({
                "status": "device_limit",
                "message": f"Đã đạt giới hạn thiết bị ({info['max_devices']} thiết bị)",
                "max_devices": info['max_devices'],
                "used_devices": len(info['used_devices'])
            })
        dev_exp = -1 if is_permanent else (now + sec)
        info['used_devices'][hwid] = dev_exp
        sql_save_key(key, info)
        return jsonify({
            "status": "success",
            "message": "Thiết bị đã được đăng ký thành công",
            "time_left": get_time_left_str(dev_exp),
            "expiry_timestamp": dev_exp,
            "expiry_str": format_ts(dev_exp) if dev_exp != -1 else "Vĩnh Viễn",
            "is_permanent": is_permanent,
            "is_new_device": True,
            "activated_now": is_first_activation
        })


@key_bp.route('/api/Fox', methods=['GET', 'POST', 'OPTIONS'])
@key_bp.route('/api/Fox=<fox_key>', methods=['GET', 'POST', 'OPTIONS'])
def api_fox(fox_key=None):
    """
    ENDPOINT /api/Fox — APP ĐĂNG KÝ KEY (MỖI KEY CHỈ DÙNG ĐƯỢC 1 APP)
    GET /api/Fox=KEY | GET /api/Fox?key=KEY&device_id=.. | POST form/JSON
    Phản hồi giống /api/verify (app cũ parse được).
    """
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200

    data = request.get_json(silent=True) or {}
    key = (fox_key or data.get('key') or request.values.get('key') or '').strip()
    if not key:
        return jsonify({"status": "error", "message": "Thiếu key. Gửi: /api/Fox=KEY hoặc ?key=KEY"}), 400

    device_id = (data.get('device_id') or data.get('hwid') or data.get('deviceId')
                 or request.values.get('device_id') or request.values.get('hwid')
                 or request.values.get('deviceId') or '').strip()
    caller_ip = get_real_ip()
    if not device_id:
        device_id = 'app_' + hashlib.sha256(caller_ip.encode('utf-8')).hexdigest()[:20]

    if not check_rate_limit('fox:' + caller_ip, max_req=10, window=60):
        return jsonify({"status": "error", "message": "Quá nhiều yêu cầu. Thử lại sau!"}), 429

    info = sql_get_key(key)
    if info is None or key.startswith("___"):
        return jsonify({"status": "invalid", "message": "Key không tồn tại trên hệ thống!"})

    now = time.time()

    if info.get('is_locked', False):
        return jsonify({"status": "locked", "message": "Key đã bị khóa bởi Admin!"})

    if isinstance(info.get('used_devices', []), list):
        new_devs = {}
        for d in info.get('used_devices', []):
            new_devs[d] = info.get('expiry_time', 0)
        info['used_devices'] = new_devs

    info['max_devices'] = 1

    val, unit = info['duration_val'], info['duration_unit']
    sec = -1
    if unit == "phút":
        sec = val * 60
    elif unit == "tiếng":
        sec = val * 3600
    elif unit == "ngày":
        sec = val * 86400
    elif unit == "tháng":
        sec = val * 30 * 86400
    elif unit == "năm":
        sec = val * 365 * 86400
    is_permanent = (sec == -1)

    if info['status'] == "Hết hạn":
        return jsonify({
            "status": "expired",
            "message": "Key này đã hết hạn và không còn dùng được",
            "key_status": "Hết hạn",
            "expiry_timestamp": None,
            "is_permanent": False,
            "time_left": "Hết hạn"
        })

    is_first_activation = (info['status'] == "Chưa kích hoạt")
    if is_first_activation:
        info['status'] = "Đã kích hoạt"
        info['activated_time'] = now

    if device_id in info['used_devices']:
        dev_exp = info['used_devices'][device_id]
        if dev_exp != -1 and now > dev_exp:
            _np = [e for e in info['used_devices'].values() if e != -1]
            if len(info['used_devices']) >= 1 and len(_np) > 0 and all(now > e for e in _np):
                info['status'] = "Hết hạn"
            sql_save_key(key, info)
            return jsonify({
                "status": "expired",
                "message": "Key đã hết hạn trên thiết bị này",
                "expiry_timestamp": dev_exp,
                "expiry_str": format_ts(dev_exp),
                "is_permanent": False
            })
        sql_save_key(key, info)
        return jsonify({
            "status": "success",
            "message": "Key hợp lệ",
            "time_left": get_time_left_str(dev_exp),
            "expiry_timestamp": dev_exp,
            "expiry_str": format_ts(dev_exp) if dev_exp != -1 else "Vĩnh Viễn",
            "is_permanent": (dev_exp == -1),
            "is_new_device": False
        })

    if len(info['used_devices']) >= 1:
        sql_save_key(key, info)
        return jsonify({
            "status": "device_limit",
            "message": "Key này chỉ được dùng bởi 1 app duy nhất!",
            "max_devices": 1,
            "used_devices": len(info['used_devices'])
        })

    dev_exp = -1 if is_permanent else (now + sec)
    info['used_devices'][device_id] = dev_exp
    sql_save_key(key, info)
    return jsonify({
        "status": "success",
        "message": "App đã được đăng ký thành công",
        "time_left": get_time_left_str(dev_exp),
        "expiry_timestamp": dev_exp,
        "expiry_str": format_ts(dev_exp) if dev_exp != -1 else "Vĩnh Viễn",
        "is_permanent": is_permanent,
        "is_new_device": True,
        "activated_now": is_first_activation
    })


@key_bp.route('/api/check_expiry', methods=['GET', 'POST', 'OPTIONS'])
def api_check_expiry():
    """CHECK HẠN SỬ DỤNG KEY TỪ BÊN NGOÀI (READ-ONLY — không kích hoạt, không đăng ký device)."""
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
    data = request.get_json(silent=True) or {}
    key = (data.get('key', '') or request.values.get('key', '')).strip()
    hwid = (data.get('hwid', '') or data.get('device_id', '') or request.values.get('hwid', '') or request.values.get('device_id', '')).strip()
    if not key:
        return jsonify({"status": "error", "message": "Thiếu tham số 'key'"}), 400
    if not hwid:
        return jsonify({"status": "error", "message": "Thiếu tham số 'hwid' hoặc 'device_id'"}), 400
    info = sql_get_key(key)
    if info is None or key.startswith("___"):
        return jsonify({"status": "invalid", "message": "Key không tồn tại trên hệ thống"})
    now = time.time()
    devices = info.get('used_devices', {})
    if isinstance(devices, list):
        devices = {d: info.get('expiry_time', 0) for d in devices}
    key_status = info.get('status', 'Chưa kích hoạt')
    if key_status == "Hết hạn":
        return jsonify({
            "status": "expired",
            "message": "Key đã bị đánh dấu hết hạn",
            "key_status": "Hết hạn"
        })
    if key_status == "Chưa kích hoạt":
        return jsonify({
            "status": "not_activated",
            "message": "Key chưa được kích hoạt. Dùng /api/verify để kích hoạt lần đầu.",
            "key_status": "Chưa kích hoạt"
        })
    if hwid not in devices:
        return jsonify({
            "status": "device_not_found",
            "message": f"Device '{hwid}' chưa đăng ký trên key này. Dùng /api/verify để đăng ký.",
            "registered_count": len(devices),
            "max_devices": info.get('max_devices', 1)
        })
    dev_exp = devices[hwid]
    if dev_exp == -1:
        return jsonify({
            "status": "valid",
            "time_left": "∞",
            "expiry_timestamp": -1,
            "expiry_str": "Vĩnh Viễn",
            "is_permanent": True,
            "key_type": info.get('duration_unit', 'permanent')
        })
    if now > dev_exp:
        return jsonify({
            "status": "expired",
            "message": "Key đã hết hạn trên thiết bị này",
            "expiry_timestamp": dev_exp,
            "expiry_str": format_ts(dev_exp),
            "is_permanent": False,
            "expired_ago": get_time_left_str(now - dev_exp) + " trước"
        })
    return jsonify({
        "status": "valid",
        "time_left": get_time_left_str(dev_exp),
        "expiry_timestamp": dev_exp,
        "expiry_str": format_ts(dev_exp),
        "is_permanent": False,
        "key_type": f"{info.get('duration_val', 0)} {info.get('duration_unit', '?')}"
    })


@key_bp.route('/api/check-device', methods=['GET', 'POST', 'OPTIONS'])
def api_check_device():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
    data = request.get_json(silent=True) or {}
    device_id = (
        data.get('device_id', '') or data.get('deviceId', '') or
        request.form.get('device_id', '') or request.args.get('device_id', '')
    ).strip()
    key = (data.get('key', '') or request.form.get('key', '') or request.args.get('key', '')).strip()
    note = (data.get('note', '') or request.form.get('note', '') or request.args.get('note', '')).strip()
    caller_ip = get_real_ip()
    if not check_rate_limit(caller_ip, max_req=10, window=30):
        return jsonify({"status": "error", "message": "Quá nhiều yêu cầu. Thử lại sau!"}), 429
    if not device_id:
        return jsonify({"status": "error", "message": "Missing device_id"}), 400
    now = time.time()
    approved = sql_get_meta("___APPROVED_DEVICES___", {})
    if device_id in approved:
        dinfo = approved[device_id]
        exp = dinfo.get('expiry', -1)
        if exp == 0:
            exp = -1
        if exp != -1 and now > exp:
            tg_notify(f"📱 <b>CHECK-DEVICE: HẾT HẠN</b>\n🔧 Device: <code>{device_id}</code>\n📍 IP: <code>{caller_ip}</code>\n⏰ {format_ts(now)}")
            return jsonify({"status": "expired", "message": "Device approval expired", "expiry_timestamp": exp, "expiry_str": format_ts(exp), "is_permanent": False, "time_left": "Hết hạn"})
        tg_notify(f"✅ <b>CHECK-DEVICE: HỢP LỆ</b>\n🔧 Device: <code>{device_id}</code>\n📍 IP: <code>{caller_ip}</code>\n⏰ Hết hạn: {format_ts(exp) if exp != -1 else 'Vĩnh viễn'}")
        return jsonify({
            "status": "approved",
            "expiry": exp,
            "expiry_timestamp": exp,
            "is_permanent": (exp == -1),
            "time_left": get_time_left_str(exp),
            "expiry_str": format_ts(exp) if exp != -1 else "Vĩnh Viễn"
        })
    found_key = None
    found_exp = None
    if key:
        kinfo = sql_get_key(key)
        if kinfo and not key.startswith("___"):
            if device_id in kinfo.get('used_devices', {}):
                found_key = key
                found_exp = kinfo['used_devices'][device_id]
    if not found_key:
        found_key, found_exp = sql_find_key_by_device(device_id)
    if found_key:
        if found_exp != -1 and now > found_exp:
            tg_notify(f"⚠️ <b>CHECK-DEVICE: KEY HẾT HẠN</b>\n🔧 Device: <code>{device_id}</code>\n🔑 Key: <code>{found_key}</code>\n📍 IP: <code>{caller_ip}</code>")
            return jsonify({"status": "expired", "message": "Key on this device has expired", "key": found_key, "expiry": found_exp, "expiry_timestamp": found_exp, "expiry_str": format_ts(found_exp), "is_permanent": False, "time_left": "Hết hạn"})
        tg_notify(f"✅ <b>CHECK-DEVICE: KEY HỢP LỆ</b>\n🔧 Device: <code>{device_id}</code>\n🔑 Key: <code>{found_key}</code>\n📍 IP: <code>{caller_ip}</code>\n⏳ Còn: {get_time_left_str(found_exp)}")
        return jsonify({"status": "approved", "key": found_key, "expiry": found_exp, "expiry_timestamp": found_exp, "is_permanent": (found_exp == -1), "time_left": get_time_left_str(found_exp), "expiry_str": format_ts(found_exp) if found_exp != -1 else "Vĩnh Viễn"})
    tg_notify(f"❓ <b>CHECK-DEVICE: KHÔNG TÌM THẤY</b>\n🔧 Device: <code>{device_id}</code>\n📍 IP: <code>{caller_ip}</code>\n📝 Note: {note or '—'}")
    return jsonify({"status": "not_found", "message": "Device not found in system"})


# ============================================================
# TRA CỨU KEY (nhiều alias)
# ============================================================
def _key_status_payload(info, k):
    """Payload tra cứu key dùng chung cho các endpoint checkkey."""
    now = time.time()
    if isinstance(info.get('used_devices', []), list):
        new_devs = {}
        for d in info.get('used_devices', []):
            new_devs[d] = info.get('expiry_time', 0)
        info['used_devices'] = new_devs
    if info['status'] == 'Đã kích hoạt':
        is_full = len(info['used_devices']) >= info['max_devices']
        _non_perm = [e for e in info['used_devices'].values() if e != -1]
        all_exp = len(_non_perm) > 0 and all(now > e for e in _non_perm)
        if is_full and all_exp:
            info['status'] = "Hết hạn"
    if info.get('is_locked', False):
        return {"status": "locked", "message": "Key đã bị khóa bởi Admin.", "exists": True}
    act_t = info.get('activated_time')
    dur_val = info.get('duration_val', 0)
    dur_unit = info.get('duration_unit', 'permanent')
    _sec_map = {'phút': 60, 'tiếng': 3600, 'ngày': 86400, 'tháng': 30 * 86400, 'năm': 365 * 86400}
    if act_t and dur_unit in _sec_map:
        expiry_ts = act_t + dur_val * _sec_map[dur_unit]
        expiry_date = format_ts(expiry_ts)
    elif dur_unit == 'permanent':
        expiry_date = "Vĩnh viễn"
    else:
        expiry_date = "Chưa kích hoạt"
    return {
        "status": "valid" if info['status'] != "Hết hạn" else "expired",
        "exists": True, "key": k, "key_status": info['status'],
        "duration": f"{dur_val} {dur_unit}" if dur_unit != 'permanent' else "Vĩnh viễn",
        "expiry_date": expiry_date,
        "max_devices": info['max_devices'], "used_devices": len(info['used_devices']),
        "activated_time": format_ts(act_t) if act_t else "Chưa kích hoạt",
    }


@key_bp.route('/api/checkkey/<key>', methods=['GET', 'POST'])
def api_checkkey_path(key):
    """Check key status — hỗ trợ GET và POST, không cần device_id."""
    k = (key or '').strip()
    if not k:
        return jsonify({"status": "error", "message": "Thiếu key"})
    info = sql_get_key(k)
    if info is None or k.startswith("___"):
        return jsonify({"status": "invalid", "message": "Key không tồn tại", "exists": False})
    payload = _key_status_payload(info, k)
    if payload.get('status') == 'locked':
        sql_save_key(k, info)
    elif payload['status'] in ('valid', 'expired') and info.get('status') == 'Hết hạn':
        sql_save_key(k, info)
    return jsonify(payload)


@key_bp.route('/api/checkkey', methods=['GET', 'POST'])
def api_checkkey_query():
    """Alias: /api/checkkey?key=XXXX — hỗ trợ GET và POST form/JSON."""
    data = request.get_json(silent=True) or {}
    k = (data.get('key', '') or request.values.get('key', '')).strip()
    if not k:
        return jsonify({"status": "error", "message": "Thiếu key"})
    info = sql_get_key(k)
    if info is None or k.startswith("___"):
        return jsonify({"status": "invalid", "message": "Key không tồn tại", "exists": False})
    payload = _key_status_payload(info, k)
    if payload.get('status') == 'locked':
        sql_save_key(k, info)
    elif payload['status'] in ('valid', 'expired') and info.get('status') == 'Hết hạn':
        sql_save_key(k, info)
    return jsonify(payload)


@key_bp.route('/api/check_key', methods=['GET', 'POST'])
def api_check_key():
    """Alias của /api/verify — hỗ trợ GET và POST form-data."""
    data = request.get_json(silent=True) or {}
    k = (data.get('key', '') or request.values.get('key', '')).strip()
    device_id = (data.get('hwid', '') or data.get('device_id', '') or request.values.get('device_id', '') or request.values.get('hwid', '')).strip()
    if not k or not device_id:
        return jsonify({"status": "error", "message": "Thiếu key hoặc device_id/hwid"})
    info = sql_get_key(k)
    if info is None or k.startswith("___"):
        return jsonify({"status": "invalid", "message": "Key không tồn tại"})
    if info.get('is_locked', False):
        return jsonify({"status": "locked", "message": "Key đã bị khóa bởi Admin. Vui lòng liên hệ Admin để mở khóa."})
    now = time.time()
    if isinstance(info.get('used_devices', []), list):
        new_devs = {}
        for d in info.get('used_devices', []):
            new_devs[d] = info.get('expiry_time', 0)
        info['used_devices'] = new_devs
    val, unit = info['duration_val'], info['duration_unit']
    sec = -1
    if unit == "phút":
        sec = val * 60
    elif unit == "tiếng":
        sec = val * 3600
    elif unit == "ngày":
        sec = val * 86400
    elif unit == "tháng":
        sec = val * 30 * 86400
    elif unit == "năm":
        sec = val * 365 * 86400
    is_permanent = (sec == -1)
    if info['status'] == "Hết hạn":
        return jsonify({"status": "expired", "message": "Key đã hết hạn"})
    is_first_activation = (info['status'] == "Chưa kích hoạt")
    if is_first_activation:
        info['status'] = "Đã kích hoạt"
        info['activated_time'] = now
    if device_id in info['used_devices']:
        dev_exp = info['used_devices'][device_id]
        if dev_exp != -1 and now > dev_exp:
            _non_perm = [e for e in info['used_devices'].values() if e != -1]
            is_full = len(info['used_devices']) >= info['max_devices']
            all_exp = len(_non_perm) > 0 and all(now > e for e in _non_perm)
            if is_full and all_exp:
                info['status'] = "Hết hạn"
            sql_save_key(k, info)
            return jsonify({
                "status": "expired",
                "message": "Key đã hết hạn trên thiết bị này",
                "expiry_timestamp": dev_exp,
                "expiry_str": format_ts(dev_exp)
            })
        sql_save_key(k, info)
        return jsonify({
            "status": "success",
            "message": "Key hợp lệ",
            "time_left": get_time_left_str(dev_exp),
            "expiry_timestamp": dev_exp,
            "expiry_str": format_ts(dev_exp) if dev_exp != -1 else "Vĩnh Viễn",
            "is_permanent": (dev_exp == -1),
            "is_new_device": False
        })
    else:
        if len(info['used_devices']) < info['max_devices']:
            dev_exp = -1 if is_permanent else (now + sec)
            info['used_devices'][device_id] = dev_exp
            sql_save_key(k, info)
            return jsonify({
                "status": "success",
                "message": "Thiết bị đã được đăng ký",
                "time_left": get_time_left_str(dev_exp),
                "expiry_timestamp": dev_exp,
                "expiry_str": format_ts(dev_exp) if dev_exp != -1 else "Vĩnh Viễn",
                "is_permanent": is_permanent,
                "is_new_device": True
            })
        sql_save_key(k, info)
        return jsonify({
            "status": "device_limit",
            "message": f"Đã đạt giới hạn thiết bị ({info['max_devices']})"
        })


@key_bp.route('/api/get_key_ip_info', methods=['POST'])
def get_key_ip_info():
    k = request.form.get('key', '').strip()
    info = sql_get_key(k)
    if not k or info is None or k.startswith("___"):
        return jsonify({"exists": False, "msg": "Key không tồn tại trên hệ thống!"})
    devices = []
    for did, exp in info.get('used_devices', {}).items():
        devices.append({
            "device_id": did,
            "expiry": exp,
            "expiry_str": format_ts(exp) if (isinstance(exp, (int, float)) and exp != -1) else "Vĩnh viễn"
        })
    return jsonify({
        "exists": True,
        "key": k,
        "status": info.get('status', '—'),
        "client_ip": info.get('client_ip', ''),
        "creator_info": info.get('creator_info', 'Không có thông tin'),
        "activated_time": format_ts(info.get('activated_time')) if info.get('activated_time') else "Chưa kích hoạt",
        "created_at": format_ts(info.get('created_at', 0)),
        "devices": devices,
        "duration": f"{info['duration_val']} {info['duration_unit']}" if info.get('duration_unit') != 'permanent' else "Vĩnh viễn"
    })


@key_bp.route('/api/check_free_key_status', methods=['POST'])
def check_free_key_status():
    k = request.form.get('key', '')
    info = sql_get_key(k)
    if info is not None:
        now = time.time()
        if info['status'] == 'Đã kích hoạt':
            _non_perm3 = [e for e in info['used_devices'].values() if e != -1]
            all_expired = len(_non_perm3) > 0 and all(now > e for e in _non_perm3)
            if all_expired:
                return jsonify({"valid": False})
        return jsonify({"valid": True})
    return jsonify({"valid": False})


# ============================================================
# DEVICE REQUESTS / APPROVED DEVICES (NGƯỜI DÙNG)
# ============================================================
@key_bp.route('/api/submit_device_request', methods=['POST', 'OPTIONS'])
def submit_device_request():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
    data = request.get_json(silent=True) or {}
    device_id = (data.get('device_id', '') or request.form.get('device_id', '')).strip()
    val = (data.get('val', '') or request.form.get('val', '1')).strip()
    unit = (data.get('unit', '') or request.form.get('unit', 'ngày')).strip()
    note = (data.get('note', '') or request.form.get('note', '')).strip()
    if not device_id:
        return jsonify({"status": "error", "msg": "Thiếu Device ID!"})
    db = load_db()
    requests_map = db.get("___DEVICE_REQUESTS___", {})
    for rid, rinfo in requests_map.items():
        if rinfo.get('device_id') == device_id and rinfo.get('status') == 'pending':
            return jsonify({"status": "exists", "msg": "Device ID này đang chờ duyệt rồi!"})
    approved = db.get("___APPROVED_DEVICES___", {})
    if device_id in approved:
        exp = approved[device_id].get('expiry', 0)
        if exp == -1 or time.time() < exp:
            return jsonify({"status": "already_approved", "msg": "Device ID này đã được duyệt và còn hạn!"})
    req_id = str(int(time.time() * 1000)) + "-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    requests_map[req_id] = {
        "device_id": device_id,
        "val": val,
        "unit": unit,
        "note": note,
        "status": "pending",
        "submitted_at": time.time(),
        "ip": get_real_ip()
    }
    db["___DEVICE_REQUESTS___"] = requests_map
    save_db(db)
    return jsonify({"status": "success", "req_id": req_id})


@key_bp.route('/api/check_device_approval', methods=['POST', 'GET', 'OPTIONS'])
def check_device_approval():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
    data = request.get_json(silent=True) or {}
    device_id = (data.get('device_id', '') or request.form.get('device_id', '') or request.args.get('device_id', '')).strip()
    if not device_id:
        return jsonify({"status": "error", "msg": "Thiếu Device ID"})
    approved = sql_get_meta("___APPROVED_DEVICES___", {})
    if device_id not in approved:
        return jsonify({"status": "not_found", "msg": "Device ID chưa được duyệt"})
    dinfo = approved[device_id]
    exp = dinfo.get('expiry', 0)
    now = time.time()
    if exp != -1 and now > exp:
        return jsonify({
            "status": "expired",
            "msg": "Device ID đã hết hạn",
            "expiry_timestamp": exp,
            "expiry_str": format_ts(exp),
            "is_permanent": False,
            "time_left": "Hết hạn"
        })
    return jsonify({
        "status": "approved",
        "expiry": exp,
        "expiry_timestamp": exp,
        "is_permanent": (exp == -1),
        "time_left": get_time_left_str(exp),
        "expiry_str": format_ts(exp) if exp != -1 else "Vĩnh viễn"
    })


@key_bp.route('/api/add_device_id', methods=['POST'])
def add_device_id():
    device_id = request.form.get('device_id', '').strip()
    val = request.form.get('val', '1').strip()
    unit = request.form.get('unit', 'ngày').strip()
    if not device_id:
        return jsonify({"status": "error", "msg": "Vui lòng nhập Device ID!"})
    db = load_db()
    approved = db.get("___APPROVED_DEVICES___", {})
    now = time.time()
    val_int = int(val) if val and val.isdigit() else 1
    sec = -1
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
    expiry = -1 if sec == -1 else (now + sec)
    approved[device_id] = {
        "expiry": expiry,
        "approved_at": now,
        "val": val_int,
        "unit": unit,
        "note": "Thêm ID trực tiếp từ trang đăng ký",
        "ip": get_real_ip()
    }
    db["___APPROVED_DEVICES___"] = approved
    save_db(db)
    return jsonify({"status": "success"})
