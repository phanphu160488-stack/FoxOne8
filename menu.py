"""
menu.py — TRANG CHỦ / ĐĂNG NHẬP / CÁC TRANG PUBLIC
==================================================
Blueprint `menu_bp` chứa:
  - Trang chủ (/) + tra cứu key nhanh
  - Login / Logout / Đổi mật khẩu admin
  - Các trang tĩnh: tra cứu IP key, nhận key free, đăng ký thiết bị
  - Endpoint public: thông báo (announcement)
"""

import time

from flask import Blueprint, jsonify, redirect, render_template, request, session

from database import (
    load_db, save_db, get_admin_config, invalidate_admin_cache,
    sql_get_key, sql_save_key, sql_get_meta,
)
from utils import get_real_ip, format_ts, check_rate_limit

menu_bp = Blueprint('menu', __name__)


# ============================================================
# TRANG CHỦ + LOGIN + ĐỔI ADMIN
# ============================================================
@menu_bp.route('/', methods=['GET', 'POST'])
def home():
    if request.method == 'POST':
        k = request.form.get('key', '').strip()
        info = sql_get_key(k)
        if info and not k.startswith("___"):
            now = time.time()
            if isinstance(info.get('used_devices', []), list):
                new_devs = {}
                for d in info.get('used_devices', []):
                    new_devs[d] = info.get('expiry_time', 0)
                info['used_devices'] = new_devs
                sql_save_key(k, info)
            if info['status'] == 'Đã kích hoạt':
                is_full = len(info['used_devices']) >= info['max_devices']
                _non_perm = [e for e in info['used_devices'].values() if e != -1]
                all_exp = len(_non_perm) > 0 and all(now > e for e in _non_perm)
                if is_full and all_exp:
                    info['status'] = "Hết hạn"
                    sql_save_key(k, info)
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


@menu_bp.route('/login', methods=['POST'])
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


@menu_bp.route('/api/change_admin', methods=['POST'])
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


@menu_bp.route('/logout')
def logout():
    session.clear()
    return redirect('/')


# ============================================================
# CÁC TRANG PUBLIC
# ============================================================
@menu_bp.route('/check-ip-key')
def check_ip_key_page():
    return render_template('check_ip_key.html')


@menu_bp.route('/nhan-key-free')
def nhan_key_free_page():
    token = request.args.get('token', '')
    return render_template('free_key.html', token=token)


@menu_bp.route('/dang-ky-thiet-bi')
def device_registration_page():
    return render_template('device_reg.html')


@menu_bp.route('/api/announcement', methods=['GET'])
def api_get_announcement():
    """Public endpoint — trả nội dung thông báo hiện tại."""
    ann = sql_get_meta("___ANNOUNCEMENT___", {"text": ""})
    return jsonify({"text": ann.get("text", "")})
