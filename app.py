"""
app.py — FLASK APPLICATION (TOÀN BỘ ROUTES / API)
=================================================
Khởi tạo Flask app và đăng ký tất cả endpoint:
trang chủ, admin panel, key API, device, link4m/getkey, web log, SoundCloud.
Chạy bằng:  gunicorn main:app   (hoặc python main.py)
"""

import hashlib
import json
import os
import random
import re
import string
import threading
import time
import urllib.parse
import urllib.request as _ureq
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, redirect, render_template, request, session, send_file

from config import (
    SECRET_KEY, SESSION_LIFETIME_DAYS, BASE_DIR, DB_FILE, VN_TZ,
    DEFAULT_FREE_CONFIG, FREE_KEYS_PER_IP_PER_DAY,
)
from database import load_db, save_db, get_admin_config, get_cached_admin_cfg, invalidate_admin_cache, USE_MYSQL
from utils import (
    _TG_OK, _req_tg,
    get_real_ip, get_time_left_str, format_ts, format_full_ts,
    check_rate_limit, shorten_with_link4m,
)
from telegrambot import tg_notify

# ============================================================
# APP SETUP
# ============================================================
app = Flask(__name__)
app.secret_key = SECRET_KEY

app.permanent_session_lifetime = timedelta(days=SESSION_LIFETIME_DAYS)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = False


# ============================================================
# ERROR HANDLERS + CORS + SECURITY HEADERS
# ============================================================
@app.errorhandler(404)
def not_found_error(e):
    if request.path.startswith('/api/') or request.is_json or request.headers.get('X-Requested-With'):
        return jsonify({"status": "error", "message": "Endpoint không tồn tại: " + request.path}), 404
    return jsonify({"status": "error", "message": "Not found"}), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({"status": "error", "message": "Lỗi máy chủ nội bộ: " + str(e)}), 500


@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
    # --- Security headers ---
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response


@app.before_request
def handle_options():
    # Xử lý CORS preflight (OPTIONS) cho tất cả routes
    if request.method == 'OPTIONS':
        resp = jsonify({'status': 'ok'})
        resp.status_code = 204
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
        return resp


@app.before_request
def check_admin_changed():
    # Skip OPTIONS (CORS preflight) và các path tĩnh
    if request.method == 'OPTIONS':
        return
    path = request.path
    if path in ('/nhac.mp3', '/nhac2.mp3', '/nhac3.mp3', '/healthz'):
        return
    if session.get('is_admin'):
        try:
            admin_cfg = get_cached_admin_cfg()
            stored_pass = session.get('admin_pass', '')
            stored_user = session.get('admin_user', '')
            if (stored_pass and stored_user and
                    stored_pass != admin_cfg.get('pass', '') and
                    stored_user != admin_cfg.get('user', '')):
                session.clear()
        except Exception:
            pass


# ============================================================
# KEEP-ALIVE: tự ping mỗi ~14 phút để giảm cold start
# ============================================================
def _keep_alive_worker(offset_seconds=60):
    import time as _t
    _t.sleep(offset_seconds)  # chờ server start đầy đủ
    while True:
        _t.sleep(14 * 60)
        try:
            host = os.environ.get('RENDER_EXTERNAL_URL', '')
            if host:
                _ureq.urlopen(host.rstrip('/') + '/healthz', timeout=10)
        except Exception:
            pass


# 3 pinger với offset khác nhau (0s, 7min, 4.5min) → ping ~mỗi 4.5 phút,
# nằm trong ngưỡng 15 phút sleep của Render.
threading.Thread(target=_keep_alive_worker, args=(60,), daemon=True).start()
threading.Thread(target=_keep_alive_worker, args=(420,), daemon=True).start()
threading.Thread(target=_keep_alive_worker, args=(270,), daemon=True).start()


# ============================================================
# HEALTH CHECK
# ============================================================
@app.route('/healthz')
def healthz():
    db_ok = USE_MYSQL or os.path.exists(DB_FILE)
    return jsonify({"status": "ok", "db": db_ok}), 200


# ============================================================
# MEDIA (avatar + nhạc)
# ============================================================
@app.route('/img.png')
def play_avatar():
    f = os.path.join(BASE_DIR, 'img.png')
    if os.path.exists(f):
        return send_file(f, mimetype='image/png', conditional=True)
    return jsonify({"status": "missing"}), 404


@app.route('/nhac.mp3')
def play_music():
    f = os.path.join(BASE_DIR, 'nhac.mp3')
    if os.path.exists(f):
        return send_file(f, mimetype='audio/mpeg', conditional=True)
    return jsonify({"status": "missing"}), 404


@app.route('/nhac2.mp3')
def play_music2():
    f = os.path.join(BASE_DIR, 'nhac2.mp3')
    if os.path.exists(f):
        return send_file(f, mimetype='audio/mpeg', conditional=True)
    return jsonify({"status": "missing"}), 404


@app.route('/nhac3.mp3')
def play_music3():
    f = os.path.join(BASE_DIR, 'nhac3.mp3')
    if os.path.exists(f):
        return send_file(f, mimetype='audio/mpeg', conditional=True)
    return jsonify({"status": "missing"}), 404


# ============================================================
# TRANG CHỦ + LOGIN + ĐỔI ADMIN
# ============================================================
@app.route('/', methods=['GET', 'POST'])
def home():
    if request.method == 'POST':
        k = request.form.get('key', '').strip()
        db = load_db()
        if k in db and not k.startswith("___"):
            info = db[k]
            now = time.time()
            if isinstance(info.get('used_devices', []), list):
                new_devs = {}
                for d in info.get('used_devices', []):
                    new_devs[d] = info.get('expiry_time', 0)
                info['used_devices'] = new_devs
                save_db(db)
            if info['status'] == 'Đã kích hoạt':
                is_full = len(info['used_devices']) >= info['max_devices']
                _non_perm = [e for e in info['used_devices'].values() if e != -1]
                all_exp = len(_non_perm) > 0 and all(now > e for e in _non_perm)
                if is_full and all_exp:
                    info['status'] = "Hết hạn"
                    save_db(db)
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
            return jsonify({
                "exists": True, "key": k, "key_status": info['status'],
                "duration": f"{dur_val} {dur_unit}" if dur_unit != 'permanent' else "Vĩnh viễn",
                "expiry_date": expiry_date,
                "max_devices": info['max_devices'], "used_devices": len(info['used_devices']),
                "created_at": format_ts(info.get('created_at', 0)),
                "created_at_str": format_ts(info.get('created_at', 0)),
                "activated_time": format_ts(act_t) if act_t else "Chưa kích hoạt",
                "activated_time_str": format_ts(act_t) if act_t else "Chưa kích hoạt",
                "han_dung": f"{dur_val} {dur_unit}" if dur_unit != 'permanent' else "Vĩnh viễn",
                "dev_dict": info['used_devices']
            })
        return jsonify({"exists": False, "msg": "Mã Key không tồn tại trên hệ thống máy chủ!"})
    return render_template('index.html')


@app.route('/login', methods=['POST'])
def login():
    db = load_db()
    admin_cfg = get_admin_config(db)
    if request.form.get('user') == admin_cfg['user'] and request.form.get('pass') == admin_cfg['pass']:
        session.clear()
        session.permanent = True
        session['is_admin'] = True
        session['admin_user'] = admin_cfg['user']
        session['admin_pass'] = admin_cfg['pass']
        session.modified = True
        # Auto-whitelist admin's IP on first successful login
        real_ip = get_real_ip()
        if real_ip:
            saved_owners = db.get('___OWNER_IPS___', [])
            if real_ip not in saved_owners:
                saved_owners.append(real_ip)
                db['___OWNER_IPS___'] = saved_owners
                save_db(db)
        return jsonify({"status": "success"})
    # Chống brute-force: bucket RIÊNG cho login (max 5 lần / 60 giây mỗi IP)
    if not check_rate_limit('login:' + get_real_ip(), max_req=5, window=60):
        return jsonify({"status": "error", "message": "Quá nhiều lần thử đăng nhập! Vui lòng thử lại sau 60 giây."}), 429
    time.sleep(1)  # chống dò mật khẩu tự động (brute-force)
    return jsonify({"status": "error", "message": "Sai thông tin tài khoản hoặc mật khẩu quản trị!"})


@app.route('/api/change_admin', methods=['POST'])
def change_admin():
    db = load_db()
    admin_cfg = get_admin_config(db)
    if not session.get('is_admin') or session.get('admin_pass') != admin_cfg['pass']:
        return jsonify({"status": "error"}), 401
    new_u = request.form.get('u', '').strip()
    new_p = request.form.get('p', '').strip()
    if new_u and new_p:
        db["___ADMIN_CONFIG___"] = {"user": new_u, "pass": new_p}
        save_db(db)
        invalidate_admin_cache()
        session['admin_user'] = new_u
        session['admin_pass'] = new_p
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Tài khoản và mật khẩu không được để trống!"})


# ============================================================
# ADMIN PANEL — QUẢN LÝ KEY
# ============================================================
@app.route('/admin', methods=['GET', 'POST'])
def admin_add_key():
    if request.method == 'GET':
        return redirect('/')
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    db = load_db()
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
    db[key_name] = {
        "duration_val": int(time_val) if time_unit != "permanent" else 0,
        "duration_unit": time_unit, "max_devices": max_dev, "status": "Chưa kích hoạt",
        "activated_time": None, "created_at": time.time(), "used_devices": {}
    }
    save_db(db)
    return jsonify({"status": "success", "key": key_name})


@app.route('/api/list_keys', methods=['GET'])
def list_keys():
    if not session.get('is_admin'):
        return jsonify([]), 401
    db = load_db()
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
            save_db(db)
        if v['status'] == "Đã kích hoạt":
            is_full = len(v['used_devices']) >= v['max_devices']
            _non_perm2 = [e for e in v['used_devices'].values() if e != -1]
            all_exp = len(_non_perm2) > 0 and all(now > e for e in _non_perm2)
            if is_full and all_exp:
                v['status'] = "Hết hạn"
                save_db(db)
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


@app.route('/delete/<key>')
def delete(key):
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    db = load_db()
    if key in db:
        del db[key]
        ip_map = db.get("___IP_KEY_MAP___", {})
        to_remove = [ip for ip, k in ip_map.items() if k == key]
        for ip in to_remove:
            del ip_map[ip]
        db["___IP_KEY_MAP___"] = ip_map
        save_db(db)
    return jsonify({"status": "success"})


@app.route('/reset/<key>')
def reset_key(key):
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    db = load_db()
    if key in db:
        db[key]['status'] = "Chưa kích hoạt"
        db[key]['activated_time'] = None
        db[key]['used_devices'] = {}
        save_db(db)
    return jsonify({"status": "success"})


@app.route('/admin/free_setup', methods=['POST'])
def admin_free_setup():
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    db = load_db()
    db["___FREE_CONFIG___"] = {"val": request.form.get('v'), "unit": request.form.get('u'), "dev": request.form.get('d')}
    save_db(db)
    return jsonify({"status": "success"})


@app.route('/api/free_config', methods=['GET'])
def api_public_free_config():
    """Public endpoint — trả cấu hình key free cho trang getkey."""
    db = load_db()
    cfg = db.get("___FREE_CONFIG___", DEFAULT_FREE_CONFIG)
    return jsonify({"val": str(cfg.get('val', '12')), "unit": cfg.get('unit', 'tiếng'), "dev": str(cfg.get('dev', '1'))})


@app.route('/api/announcement', methods=['GET'])
def api_get_announcement():
    """Public endpoint — trả nội dung thông báo hiện tại."""
    db = load_db()
    ann = db.get("___ANNOUNCEMENT___", {"text": ""})
    return jsonify({"text": ann.get("text", "")})


@app.route('/api/admin/update_announcement', methods=['POST'])
def api_update_announcement():
    """Admin endpoint — cập nhật banner thông báo."""
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    data = request.get_json(force=True, silent=True) or {}
    text = str(data.get('text', '')).strip()
    db = load_db()
    db["___ANNOUNCEMENT___"] = {"text": text}
    save_db(db)
    return jsonify({"ok": True, "text": text})


@app.route('/admin/get_free_config', methods=['GET'])
def admin_get_free_config():
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    db = load_db()
    cfg = db.get("___FREE_CONFIG___", DEFAULT_FREE_CONFIG)
    return jsonify({"status": "success", "val": str(cfg.get('val', '12')), "unit": str(cfg.get('unit', 'tiếng')), "dev": str(cfg.get('dev', '1'))})


@app.route('/api/create_key', methods=['POST'])
def api_create_key():
    if not session.get('is_admin'):
        return jsonify({"status": "error", "message": "Không có quyền"}), 401
    db = load_db()
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
        db[key_name] = {
            "duration_val": val, "duration_unit": unit,
            "max_devices": max_devices, "status": "Chưa kích hoạt",
            "activated_time": None, "created_at": now, "used_devices": {},
            "creator_info": f"Admin Panel | {key_type}"
        }
        keys_created.append(key_name)
    save_db(db)
    return jsonify({"status": "success", "keys": keys_created, "key": keys_created[0] if keys_created else ""})


@app.route('/api/list_free_keys', methods=['GET'])
def api_list_free_keys():
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    db = load_db()
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


@app.route('/api/extend_key', methods=['POST'])
def api_extend_key():
    if not session.get('is_admin'):
        return jsonify({"status": "error", "message": "Không có quyền"}), 401
    db = load_db()
    key = request.form.get('key', '').strip()
    hours = request.form.get('hours', '24').strip()
    if not key or key not in db:
        return jsonify({"status": "error", "message": "Key không tồn tại"})
    try:
        hours = float(hours)
    except Exception:
        return jsonify({"status": "error", "message": "Số giờ không hợp lệ"})
    info = db[key]
    unit = info.get('duration_unit', 'tiếng').lower()
    unit_secs = {'tiếng': 3600, 'giờ': 3600, 'ngày': 86400, 'ngay': 86400, 'tháng': 2592000, 'thang': 2592000, 'năm': 31536000, 'nam': 31536000, 'phút': 60, 'phut': 60}
    secs_per = unit_secs.get(unit, 3600)
    old_val = int(info.get('duration_val', 0)) if info.get('duration_val') else 0
    add_secs = hours * 3600
    total_secs = old_val * secs_per + add_secs
    new_val = int(total_secs / secs_per) if secs_per else old_val
    db[key]['duration_val'] = new_val
    save_db(db)
    return jsonify({"status": "success", "message": f"Đã gia hạn thêm {hours:.0f} giờ cho {key}"})


@app.route('/api/lock_key', methods=['POST'])
def api_lock_key():
    if not session.get('is_admin'):
        return jsonify({"status": "error", "message": "Không có quyền"}), 401
    db = load_db()
    key = request.form.get('key', '').strip()
    if not key or key not in db or key.startswith("___"):
        return jsonify({"status": "error", "message": "Key không tồn tại"})
    current = db[key].get('is_locked', False)
    db[key]['is_locked'] = not current
    save_db(db)
    action = "khóa" if not current else "mở khóa"
    return jsonify({"status": "success", "is_locked": not current, "message": f"Đã {action} key thành công"})


@app.route('/api/copy_key', methods=['POST'])
def api_copy_key():
    if not session.get('is_admin'):
        return jsonify({"status": "error", "message": "Không có quyền"}), 401
    db = load_db()
    src_key = request.form.get('key', '').strip()
    if not src_key or src_key not in db or src_key.startswith("___"):
        return jsonify({"status": "error", "message": "Key không tồn tại"})
    src = db[src_key]
    prefix = src_key.split('-')[0] if '-' in src_key else 'KEY'
    p1 = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    p2 = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    p3 = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    new_key = f"{prefix}-{p1}-{p2}-{p3}"
    db[new_key] = {
        "duration_val": src.get('duration_val', 1),
        "duration_unit": src.get('duration_unit', 'ngày'),
        "max_devices": src.get('max_devices', 1),
        "status": "Chưa kích hoạt",
        "activated_time": None,
        "created_at": time.time(),
        "used_devices": {},
        "creator_info": f"Sao chép từ {src_key}"
    }
    save_db(db)
    return jsonify({"status": "success", "new_key": new_key, "message": f"Đã tạo bản sao: {new_key}"})


# ============================================================
# KEY FREE — TẠO / CẤP / XOAY
# ============================================================
@app.route('/api/gen_free_task', methods=['POST'])
def gen_free_task():
    db = load_db()
    cfg = db.get("___FREE_CONFIG___", {"val": 12, "unit": "tiếng", "dev": 9999})
    client_ip_info = request.form.get('ip_info', 'Không quét được Client')
    server_ip = get_real_ip()
    final_info = f"SV IP: {server_ip} | {client_ip_info}"

    # Per-IP key: trả key cũ nếu còn dùng được
    ip_map = db.get("___IP_KEY_MAP___", {})
    existing_key = ip_map.get(server_ip)
    if existing_key and existing_key in db:
        existing_info = db[existing_key]
        created_at = existing_info.get('created_at', 0)
        age_hours = (time.time() - created_at) / 3600
        if age_hours < 12 or existing_info.get('status') == 'Đã kích hoạt':
            return jsonify({"status": "success", "key": existing_key, "reused": True})

    k = f"FREE-{''.join(random.choices(string.ascii_uppercase + string.digits, k=5))}"
    db[k] = {
        "duration_val": int(cfg['val']), "duration_unit": cfg['unit'],
        "max_devices": int(cfg['dev']),
        "status": "Chưa kích hoạt", "activated_time": None,
        "created_at": time.time(), "used_devices": {},
        "creator_info": final_info,
        "client_ip": server_ip
    }
    ip_map[server_ip] = k
    db["___IP_KEY_MAP___"] = ip_map
    save_db(db)
    return jsonify({"status": "success", "key": k, "reused": False})


@app.route('/api/regen_free_key', methods=['POST'])
def regen_free_key():
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    target_ip = request.form.get('ip', '').strip()
    db = load_db()
    cfg = db.get("___FREE_CONFIG___", {"val": 12, "unit": "tiếng", "dev": 9999})
    ip_map = db.get("___IP_KEY_MAP___", {})

    old_key = ip_map.get(target_ip)
    if old_key and old_key in db:
        del db[old_key]

    k = f"FREE-{''.join(random.choices(string.ascii_uppercase + string.digits, k=5))}"
    db[k] = {
        "duration_val": int(cfg['val']), "duration_unit": cfg['unit'],
        "max_devices": int(cfg['dev']),
        "status": "Chưa kích hoạt", "activated_time": None,
        "created_at": time.time(), "used_devices": {},
        "creator_info": f"Tái tạo bởi Admin | IP: {target_ip}",
        "client_ip": target_ip
    }
    ip_map[target_ip] = k
    db["___IP_KEY_MAP___"] = ip_map
    save_db(db)
    return jsonify({"status": "success", "key": k})


# ============================================================
# API XÁC THỰC KEY (/api/verify, /api/Fox, ...)
# ============================================================
@app.route('/api/verify', methods=['POST'])
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
    db = load_db()
    if key not in db or key.startswith("___"):
        return jsonify({"status": "invalid", "message": "Key does not exist"})
    info = db[key]
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
            save_db(db)
            return jsonify({
                "status": "expired",
                "message": "Key đã hết hạn trên thiết bị này",
                "expiry_timestamp": dev_exp,
                "expiry_str": format_ts(dev_exp),
                "is_permanent": False
            })
        save_db(db)
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
            save_db(db)
            return jsonify({
                "status": "device_limit",
                "message": f"Đã đạt giới hạn thiết bị ({info['max_devices']} thiết bị)",
                "max_devices": info['max_devices'],
                "used_devices": len(info['used_devices'])
            })
        dev_exp = -1 if is_permanent else (now + sec)
        info['used_devices'][hwid] = dev_exp
        save_db(db)
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


@app.route('/api/Fox', methods=['GET', 'POST', 'OPTIONS'])
@app.route('/api/Fox=<fox_key>', methods=['GET', 'POST', 'OPTIONS'])
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

    db = load_db()
    if key not in db or key.startswith("___"):
        return jsonify({"status": "invalid", "message": "Key không tồn tại trên hệ thống!"})

    info = db[key]
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
            save_db(db)
            return jsonify({
                "status": "expired",
                "message": "Key đã hết hạn trên thiết bị này",
                "expiry_timestamp": dev_exp,
                "expiry_str": format_ts(dev_exp),
                "is_permanent": False
            })
        save_db(db)
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
        save_db(db)
        return jsonify({
            "status": "device_limit",
            "message": "Key này chỉ được dùng bởi 1 app duy nhất!",
            "max_devices": 1,
            "used_devices": len(info['used_devices'])
        })

    dev_exp = -1 if is_permanent else (now + sec)
    info['used_devices'][device_id] = dev_exp
    save_db(db)
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


@app.route('/api/check_expiry', methods=['GET', 'POST', 'OPTIONS'])
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
    db = load_db()
    if key not in db or key.startswith("___"):
        return jsonify({"status": "invalid", "message": "Key không tồn tại trên hệ thống"})
    info = db[key]
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


@app.route('/api/check-device', methods=['GET', 'POST', 'OPTIONS'])
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
    db = load_db()
    now = time.time()
    approved = db.get("___APPROVED_DEVICES___", {})
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
    if key and key in db and not key.startswith("___"):
        kinfo = db[key]
        if device_id in kinfo.get('used_devices', {}):
            found_key = key
            found_exp = kinfo['used_devices'][device_id]
    if not found_key:
        for k, v in db.items():
            if k.startswith("___") or not isinstance(v, dict):
                continue
            if device_id in v.get('used_devices', {}):
                found_key = k
                found_exp = v['used_devices'][device_id]
                break
    if found_key:
        if found_exp != -1 and now > found_exp:
            tg_notify(f"⚠️ <b>CHECK-DEVICE: KEY HẾT HẠN</b>\n🔧 Device: <code>{device_id}</code>\n🔑 Key: <code>{found_key}</code>\n📍 IP: <code>{caller_ip}</code>")
            return jsonify({"status": "expired", "message": "Key on this device has expired", "key": found_key, "expiry": found_exp, "expiry_timestamp": found_exp, "expiry_str": format_ts(found_exp), "is_permanent": False, "time_left": "Hết hạn"})
        tg_notify(f"✅ <b>CHECK-DEVICE: KEY HỢP LỆ</b>\n🔧 Device: <code>{device_id}</code>\n🔑 Key: <code>{found_key}</code>\n📍 IP: <code>{caller_ip}</code>\n⏳ Còn: {get_time_left_str(found_exp)}")
        return jsonify({"status": "approved", "key": found_key, "expiry": found_exp, "expiry_timestamp": found_exp, "is_permanent": (found_exp == -1), "time_left": get_time_left_str(found_exp), "expiry_str": format_ts(found_exp) if found_exp != -1 else "Vĩnh Viễn"})
    tg_notify(f"❓ <b>CHECK-DEVICE: KHÔNG TÌM THẤY</b>\n🔧 Device: <code>{device_id}</code>\n📍 IP: <code>{caller_ip}</code>\n📝 Note: {note or '—'}")
    return jsonify({"status": "not_found", "message": "Device not found in system"})


# ============================================================
# TRANG TRA CỨU IP KEY + NHẬN KEY FREE
# ============================================================
@app.route('/check-ip-key')
def check_ip_key_page():
    return render_template('check_ip_key.html')


@app.route('/api/get_key_ip_info', methods=['POST'])
def get_key_ip_info():
    k = request.form.get('key', '').strip()
    db = load_db()
    if not k or k not in db or k.startswith("___"):
        return jsonify({"exists": False, "msg": "Key không tồn tại trên hệ thống!"})
    info = db[k]
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


@app.route('/api/check_free_key_status', methods=['POST'])
def check_free_key_status():
    k = request.form.get('key', '')
    db = load_db()
    if k in db:
        info = db[k]
        now = time.time()
        if info['status'] == 'Đã kích hoạt':
            _non_perm3 = [e for e in info['used_devices'].values() if e != -1]
            all_expired = len(_non_perm3) > 0 and all(now > e for e in _non_perm3)
            if all_expired:
                return jsonify({"valid": False})
        return jsonify({"valid": True})
    return jsonify({"valid": False})


@app.route('/nhan-key-free')
def nhan_key_free_page():
    token = request.args.get('token', '')
    return render_template('free_key.html', token=token)


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')


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


@app.route('/api/checkkey/<key>', methods=['GET', 'POST'])
def api_checkkey_path(key):
    """Check key status — hỗ trợ GET và POST, không cần device_id."""
    k = (key or '').strip()
    if not k:
        return jsonify({"status": "error", "message": "Thiếu key"})
    db = load_db()
    if k not in db or k.startswith("___"):
        return jsonify({"status": "invalid", "message": "Key không tồn tại", "exists": False})
    payload = _key_status_payload(db[k], k)
    if payload.get('status') == 'locked':
        save_db(db)
    elif payload['status'] in ('valid', 'expired') and db[k].get('status') == 'Hết hạn':
        save_db(db)
    return jsonify(payload)


@app.route('/api/checkkey', methods=['GET', 'POST'])
def api_checkkey_query():
    """Alias: /api/checkkey?key=XXXX — hỗ trợ GET và POST form/JSON."""
    data = request.get_json(silent=True) or {}
    k = (data.get('key', '') or request.values.get('key', '')).strip()
    if not k:
        return jsonify({"status": "error", "message": "Thiếu key"})
    db = load_db()
    if k not in db or k.startswith("___"):
        return jsonify({"status": "invalid", "message": "Key không tồn tại", "exists": False})
    payload = _key_status_payload(db[k], k)
    if payload.get('status') == 'locked':
        save_db(db)
    elif payload['status'] in ('valid', 'expired') and db[k].get('status') == 'Hết hạn':
        save_db(db)
    return jsonify(payload)


@app.route('/api/check_key', methods=['GET', 'POST'])
def api_check_key():
    """Alias của /api/verify — hỗ trợ GET và POST form-data."""
    data = request.get_json(silent=True) or {}
    k = (data.get('key', '') or request.values.get('key', '')).strip()
    device_id = (data.get('hwid', '') or data.get('device_id', '') or request.values.get('device_id', '') or request.values.get('hwid', '')).strip()
    if not k or not device_id:
        return jsonify({"status": "error", "message": "Thiếu key hoặc device_id/hwid"})
    db = load_db()
    if k not in db or k.startswith("___"):
        return jsonify({"status": "invalid", "message": "Key không tồn tại"})
    info = db[k]
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
            save_db(db)
            return jsonify({
                "status": "expired",
                "message": "Key đã hết hạn trên thiết bị này",
                "expiry_timestamp": dev_exp,
                "expiry_str": format_ts(dev_exp)
            })
        save_db(db)
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
            save_db(db)
            return jsonify({
                "status": "success",
                "message": "Thiết bị đã được đăng ký",
                "time_left": get_time_left_str(dev_exp),
                "expiry_timestamp": dev_exp,
                "expiry_str": format_ts(dev_exp) if dev_exp != -1 else "Vĩnh Viễn",
                "is_permanent": is_permanent,
                "is_new_device": True
            })
        save_db(db)
        return jsonify({
            "status": "device_limit",
            "message": f"Đã đạt giới hạn thiết bị ({info['max_devices']})"
        })


# ============================================================
# DEVICE REQUESTS / APPROVED DEVICES
# ============================================================
@app.route('/api/submit_device_request', methods=['POST', 'OPTIONS'])
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


@app.route('/api/list_device_requests', methods=['GET', 'OPTIONS'])
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


@app.route('/api/approve_device_request', methods=['POST', 'OPTIONS'])
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


@app.route('/api/reject_device_request', methods=['POST'])
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


@app.route('/api/list_approved_devices', methods=['GET'])
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


@app.route('/api/delete_approved_device', methods=['POST'])
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


@app.route('/api/extend_approved_device', methods=['POST'])
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


@app.route('/api/check_device_approval', methods=['POST', 'GET', 'OPTIONS'])
def check_device_approval():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
    data = request.get_json(silent=True) or {}
    device_id = (data.get('device_id', '') or request.form.get('device_id', '') or request.args.get('device_id', '')).strip()
    if not device_id:
        return jsonify({"status": "error", "msg": "Thiếu Device ID"})
    db = load_db()
    approved = db.get("___APPROVED_DEVICES___", {})
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


@app.route('/api/direct_activate_device', methods=['POST'])
def direct_activate_device():
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


@app.route('/dang-ky-thiet-bi')
def device_registration_page():
    return render_template('device_reg.html')


@app.route('/api/add_device_id', methods=['POST'])
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


# ============================================================
# LINK4M + GETKEY + BYPASS
# ============================================================
@app.route('/api/getkey', methods=['GET', 'POST', 'OPTIONS'])
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


@app.route('/admin/gen_key_link', methods=['POST'])
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


@app.route('/api/confirm_bypass', methods=['POST'])
def confirm_bypass():
    """Called khi user tới /nhan-key-free?token=XXX sau khi vượt link4m."""
    token = request.form.get('token', '').strip()
    client_ip_info = request.form.get('ip_info', '').strip()
    server_ip = get_real_ip()
    if not check_rate_limit(server_ip, max_req=8, window=60):
        return jsonify({"status": "error", "message": "Quá nhiều yêu cầu. Thử lại sau 1 phút!"})
    if not token:
        return jsonify({"status": "error", "message": "Token không hợp lệ! Bạn cần vượt link rút gọn trước."})
    db = load_db()
    now = time.time()
    tokens = db.get("___GETKEY_TOKENS___", {})
    if token not in tokens:
        return jsonify({"status": "error", "message": "Link đã hết hạn hoặc không hợp lệ! Vui lòng lấy link mới từ Admin."})
    token_info = tokens[token]
    if now - token_info.get('created_at', 0) > 3600:
        return jsonify({"status": "error", "message": "Link đã hết hạn (quá 1 giờ)! Vui lòng lấy link mới từ Admin."})
    if token_info.get('status') == 'used':
        existing_key = token_info.get('key', '')
        if existing_key and existing_key in db:
            return jsonify({"status": "success", "key": existing_key, "reused": True, "msg": "Bạn đã nhận key này rồi!"})
        return jsonify({"status": "error", "message": "Link này đã được sử dụng! Vui lòng lấy link mới."})
    is_admin_token = token_info.get('is_admin', False)
    if not is_admin_token:
        ip_free_history = db.get("___FREE_IP_HISTORY___", {})
        ip_records = [t for t in ip_free_history.get(server_ip, []) if now - t < 86400]
        if len(ip_records) >= FREE_KEYS_PER_IP_PER_DAY:
            return jsonify({"status": "error", "message": f"IP này đã đạt giới hạn {FREE_KEYS_PER_IP_PER_DAY} key/ngày. Thử lại sau 24 giờ!"})
    cfg = db.get("___FREE_CONFIG___", {"val": 12, "unit": "tiếng", "dev": 9999})
    key_name = f"FREE-{''.join(random.choices(string.ascii_uppercase + string.digits, k=12))}"
    final_info = f"SV IP: {server_ip} | Token: {token[:8]}... | {client_ip_info}"
    db[key_name] = {
        "duration_val": int(cfg.get('val', 12)),
        "duration_unit": cfg.get('unit', 'tiếng'),
        "max_devices": int(cfg.get('dev', 9999)),
        "status": "Chưa kích hoạt",
        "activated_time": None,
        "created_at": now,
        "used_devices": {},
        "creator_info": final_info,
        "client_ip": server_ip
    }
    tokens[token]['status'] = 'used'
    tokens[token]['key'] = key_name
    db["___GETKEY_TOKENS___"] = tokens
    if not is_admin_token:
        ip_free_history = db.get("___FREE_IP_HISTORY___", {})
        ip_records = [t for t in ip_free_history.get(server_ip, []) if now - t < 86400]
        ip_records.append(now)
        ip_free_history[server_ip] = ip_records
        db["___FREE_IP_HISTORY___"] = ip_free_history
    ip_map = db.get("___IP_KEY_MAP___", {})
    ip_map[server_ip] = key_name
    db["___IP_KEY_MAP___"] = ip_map
    save_db(db)
    tg_notify(f"🎉 <b>KEY FREE MỚI CẤP!</b>\n🔑 Key: <code>{key_name}</code>\n📍 IP: <code>{server_ip}</code>\n⏰ {cfg.get('val', 12)} {cfg.get('unit', 'tiếng')} | {cfg.get('dev', 1)} thiết bị\n📊 {client_ip_info[:100] if client_ip_info else '—'}")
    dur_val = int(cfg.get('val', 12))
    dur_unit = cfg.get('unit', 'tiếng')
    max_dev = int(cfg.get('dev', 1))
    duration_label = f"{dur_val} {dur_unit}"
    expiry_note = f"Hết hạn sau {duration_label} kể từ lần dùng đầu tiên"
    return jsonify({"status": "success", "key": key_name, "reused": False,
                    "expiry": expiry_note, "duration_label": duration_label,
                    "max_devices": max_dev})


@app.route('/api/key_stats', methods=['GET'])
def api_key_stats():
    """Thống kê key cho tab stats trong admin panel."""
    if not session.get('is_admin'):
        return jsonify({"status": "error"}), 401
    db = load_db()
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
    stats = db.get("___FREE_KEY_STATS___", {"total_bypasses": 0})
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


# ============================================================
# WEB LOG — nhật ký truy cập (in-memory)
# ============================================================
_WEB_LOG = []
_WEB_LOG_LOCK = threading.Lock()


def web_log_add(entry):
    with _WEB_LOG_LOCK:
        _WEB_LOG.append(entry)
        if len(_WEB_LOG) > 300:
            _WEB_LOG.pop(0)


@app.after_request
def log_request(response):
    try:
        ip = get_real_ip() if request.endpoint not in ('healthz', 'static') else None
        if ip and not request.path.startswith('/healthz'):
            web_log_add({
                "time": datetime.now(VN_TZ).strftime('%H:%M:%S %d/%m'),
                "ip": ip,
                "method": request.method,
                "path": request.path,
                "status": response.status_code
            })
    except Exception:
        pass
    return response


@app.route('/api/web_log', methods=['GET'])
def api_web_log():
    if not session.get('is_admin'):
        return jsonify([]), 401
    with _WEB_LOG_LOCK:
        return jsonify(list(reversed(_WEB_LOG[-100:])))


# ============================================================
# SOUNDCLOUD SEARCH
# ============================================================
_sc_client_id_cache = [None]


def _sc_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://soundcloud.com/"
    }


def _sc_get_client_id(force_refresh=False):
    if _sc_client_id_cache[0] and not force_refresh:
        return _sc_client_id_cache[0]
    try:
        if not _TG_OK or _req_tg is None:
            return None
        res = _req_tg.get('https://soundcloud.com/', headers=_sc_headers(), timeout=12)
        if res.status_code != 200:
            return None
        try:
            from bs4 import BeautifulSoup as _BS
            soup = _BS(res.text, 'html.parser')
            scripts = [t.get('src') for t in soup.find_all('script', {'crossorigin': True}) if t.get('src', '').startswith('https')]
        except ImportError:
            scripts = re.findall(r'src="(https://[^"]+\.js[^"]*)"', res.text)
        if not scripts:
            return None
        for sc_url in reversed(scripts[-3:]):
            try:
                js = _req_tg.get(sc_url, headers=_sc_headers(), timeout=12)
                if js.status_code != 200:
                    continue
                m = re.search(r'client_id:"([a-zA-Z0-9]{20,})"', js.text)
                if m:
                    _sc_client_id_cache[0] = m.group(1)
                    return _sc_client_id_cache[0]
            except Exception:
                continue
    except Exception:
        pass
    return None


@app.route('/api/search_music', methods=['GET', 'POST', 'OPTIONS'])
def api_search_music():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
    query = (request.args.get('q') or request.form.get('q') or '').strip()
    if not query:
        return jsonify({"status": "error", "message": "Thiếu từ khóa tìm kiếm"})
    if not _TG_OK or _req_tg is None:
        return jsonify({"status": "error", "message": "requests không có sẵn"})
    try:
        songs = []

        # --- Approach 1: SoundCloud API v2 với client_id ---
        client_id = _sc_get_client_id()
        if client_id:
            try:
                api_url = f'https://api-v2.soundcloud.com/search/tracks?q={urllib.parse.quote(query)}&client_id={client_id}&limit=10&offset=0'
                res2 = _req_tg.get(api_url, headers=_sc_headers(), timeout=12)
                if res2.status_code == 200:
                    try:
                        data2 = res2.json()
                        for item in data2.get('collection', [])[:8]:
                            title = item.get('title', '').strip()
                            url_sc = item.get('permalink_url', '').strip()
                            cover = item.get('artwork_url') or item.get('user', {}).get('avatar_url', '')
                            if cover:
                                cover = cover.replace('-large', '-t200x200')
                            if title and url_sc:
                                songs.append({"title": title, "url": url_sc, "cover": cover})
                    except Exception:
                        pass
                elif res2.status_code in (401, 403):
                    _sc_client_id_cache[0] = None
                    new_cid = _sc_get_client_id(force_refresh=True)
                    if new_cid:
                        client_id = new_cid
                        try:
                            api_url_retry = f'https://api-v2.soundcloud.com/search/tracks?q={urllib.parse.quote(query)}&client_id={client_id}&limit=10&offset=0'
                            res3 = _req_tg.get(api_url_retry, headers=_sc_headers(), timeout=12)
                            if res3.status_code == 200:
                                data3 = res3.json()
                                for item in data3.get('collection', [])[:8]:
                                    title = item.get('title', '').strip()
                                    url_sc = item.get('permalink_url', '').strip()
                                    cover = item.get('artwork_url') or item.get('user', {}).get('avatar_url', '')
                                    if cover:
                                        cover = cover.replace('-large', '-t200x200')
                                    if title and url_sc:
                                        songs.append({"title": title, "url": url_sc, "cover": cover})
                        except Exception:
                            pass
            except Exception:
                pass

        # --- Approach 2: SoundCloud search page scraping (fallback) ---
        if not songs:
            encoded_q = urllib.parse.quote(query)
            search_url = f'https://soundcloud.com/search/tracks?q={encoded_q}'
            headers_sc = _sc_headers()
            headers_sc['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
            res = _req_tg.get(search_url, headers=headers_sc, timeout=12)
            url_pat = re.compile(r'^/[^/]+/[^/]+$')
            json_matches = re.findall(r'\{"id":\d+,"kind":"track","permalink_url":"([^"]+)","title":"([^"]+)".*?"artwork_url":("(?:[^"\\]|\\.)*"|null)', res.text)
            for m in json_matches[:8]:
                url_sc = m[0]
                title = m[1]
                cover_raw = m[2].strip('"') if m[2] != 'null' else ''
                if cover_raw:
                    cover_raw = cover_raw.replace('-large', '-t200x200')
                if title and url_sc:
                    songs.append({"title": title, "url": url_sc, "cover": cover_raw})

            if not songs:
                hrefs = re.findall(r'href="(/[^/"]+/[^/"#]+)"[^>]*aria-label="([^"]{3,})"', res.text)
                seen = set()
                for href, label in hrefs:
                    if url_pat.match(href) and href not in seen:
                        seen.add(href)
                        link = 'https://soundcloud.com' + href
                        imgs = re.findall(r'src="(https://i1\.sndcdn\.com/artworks-[^"]+)"', res.text)
                        cover = imgs[len(songs)].replace('-large', '-t200x200') if len(imgs) > len(songs) else ''
                        songs.append({"title": label.strip(), "url": link, "cover": cover})
                    if len(songs) >= 8:
                        break

        if songs:
            return jsonify({"status": "success", "songs": songs})
        return jsonify({"status": "error", "message": "Không tìm thấy bài hát! Thử lại với từ khóa tiếng Việt hoặc tiếng Anh khác."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Lỗi tìm kiếm: {str(e)}"})


@app.route('/api/get_stream_url', methods=['POST', 'OPTIONS'])
def api_get_stream_url():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
    url = request.form.get('url', '').strip()
    if not url:
        return jsonify({"status": "error", "message": "Thiếu URL"})
    if not _TG_OK or _req_tg is None:
        return jsonify({"status": "error", "message": "requests không có sẵn"})
    try:
        client_id = _sc_get_client_id()
        if not client_id:
            return jsonify({"status": "error", "message": "Không lấy được client_id từ SoundCloud"})
        api_url = f'https://api-v2.soundcloud.com/resolve?url={urllib.parse.quote(url, safe="")}&client_id={client_id}'
        res = _req_tg.get(api_url, headers=_sc_headers(), timeout=10)
        if res.status_code in (401, 403):
            _sc_client_id_cache[0] = None
            new_client_id = _sc_get_client_id(force_refresh=True)
            if not new_client_id:
                return jsonify({"status": "error", "message": "Client ID SoundCloud không hợp lệ, thử lại sau"})
            api_url2 = f'https://api-v2.soundcloud.com/resolve?url={urllib.parse.quote(url, safe="")}&client_id={new_client_id}'
            res = _req_tg.get(api_url2, headers=_sc_headers(), timeout=10)
            client_id = new_client_id
        if res.status_code != 200:
            return jsonify({"status": "error", "message": f"SoundCloud API lỗi: HTTP {res.status_code}"})
        try:
            data = res.json()
        except Exception:
            return jsonify({"status": "error", "message": "SoundCloud trả về dữ liệu không hợp lệ"})
        title = data.get('title', 'SoundCloud Track')
        cover = data.get('artwork_url') or data.get('user', {}).get('avatar_url', '')
        if cover:
            cover = cover.replace('-large', '-t300x300')
        for t in data.get('media', {}).get('transcodings', []):
            if t.get('format', {}).get('protocol') == 'progressive':
                try:
                    sr = _req_tg.get(f"{t['url']}?client_id={client_id}", headers=_sc_headers(), timeout=8)
                    if sr.status_code != 200:
                        continue
                    sr_data = sr.json()
                    stream_url = sr_data.get('url')
                    if stream_url:
                        return jsonify({"status": "success", "stream_url": stream_url, "title": title, "cover": cover})
                except Exception:
                    continue
        # Fallback: hls format
        for t in data.get('media', {}).get('transcodings', []):
            if t.get('format', {}).get('protocol') == 'hls':
                try:
                    sr = _req_tg.get(f"{t['url']}?client_id={client_id}", headers=_sc_headers(), timeout=8)
                    if sr.status_code != 200:
                        continue
                    sr_data = sr.json()
                    stream_url = sr_data.get('url')
                    if stream_url:
                        return jsonify({"status": "success", "stream_url": stream_url, "title": title, "cover": cover})
                except Exception:
                    continue
        return jsonify({"status": "error", "message": "Không tìm thấy stream URL cho bài hát này"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Lỗi kết nối: {str(e)}"})
