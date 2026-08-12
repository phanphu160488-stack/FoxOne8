"""
database.py — DATABASE ENGINE (MySQL + fallback JSON)
=====================================================
Lưu dữ liệu hệ thống vào MySQL (ưu tiên). Nếu chưa cấu hình MySQL
(hoặc driver/cổng không khả dụng), tự động quay lại dùng file JSON
(database_keys.json) để app vẫn chạy bình thường.

CẤU TRÚC MySQL (tự tạo bảng khi khởi động):
  - bảng `server_keys` : mỗi row = 1 KEY (key_name + các cột + used_devices JSON)
  - bảng `server_meta`  : mỗi row = 1 mục nội bộ (___ADMIN_CONFIG___, ___FREE_CONFIG___, ...)

API giữ nguyên:  db = load_db()   → sửa db   →   save_db(db)
"""

import json
import os
import shutil
import threading
import time

from config import (
    DB_FILE, ADMIN_DEFAULT_USER, ADMIN_DEFAULT_PASS, INTERNAL_PREFIX,
    MYSQL_ENABLED, MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DB,
    MYSQL_CHARSET, MYSQL_TABLE_KEYS, MYSQL_TABLE_META,
)

# ------------------------------------------------------------------
# MySQL driver (ưu tiên pymysql, fallback MySQLdb)
# ------------------------------------------------------------------
try:
    import pymysql
    _MYSQL_DRIVER = pymysql
    _MYSQL_DRIVER_OK = True
except ImportError:
    try:
        import MySQLdb
        _MYSQL_DRIVER = MySQLdb
        _MYSQL_DRIVER_OK = True
    except ImportError:
        _MYSQL_DRIVER = None
        _MYSQL_DRIVER_OK = False

_MYSQL_LOCK = threading.Lock()
USE_MYSQL = False


# ------------------------------------------------------------------
# CẤU HÌNH MẶC ĐỊNH
# ------------------------------------------------------------------
def default_admin_cfg():
    """Cấu hình admin mặc định (dùng khi DB chưa tồn tại)."""
    return {"user": ADMIN_DEFAULT_USER, "pass": ADMIN_DEFAULT_PASS}


# ==================================================================
# JSON ENGINE (fallback cũ)
# ==================================================================
def _json_load_db():
    if not os.path.exists(DB_FILE):
        return {"___ADMIN_CONFIG___": default_admin_cfg()}
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
            if "___ADMIN_CONFIG___" not in data:
                data["___ADMIN_CONFIG___"] = default_admin_cfg()
            return data
        except Exception:
            return {"___ADMIN_CONFIG___": default_admin_cfg()}


def _json_save_db(data):
    tmp = DB_FILE + '.tmp'
    bak = DB_FILE + '.bak'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        if os.path.exists(DB_FILE):
            shutil.copy2(DB_FILE, bak)
        os.replace(tmp, DB_FILE)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


# ==================================================================
# MYSQL ENGINE
# ==================================================================
def _mysql_connect():
    """Mở 1 kết nối mới (mỗi lệnh 1 kết nối — an toàn với đa thread)."""
    return _MYSQL_DRIVER.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        charset=MYSQL_CHARSET,
        connect_timeout=8,
        cursorclass=_MYSQL_DRIVER.cursors.DictCursor,
    )


def _mysql_escape(ident):
    """Bọc tên bảng bằng backtick (chỉ dùng cho tên bảng cấu hình, không dùng cho user input)."""
    return '`' + ident.replace('`', '``') + '`'


def _mysql_init():
    """Tạo bảng nếu chưa có + tự migrate dữ liệu từ file JSON cũ (nếu bảng trống)."""
    conn = _mysql_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {_mysql_escape(MYSQL_TABLE_KEYS)} ("
                "  key_name VARCHAR(255) NOT NULL PRIMARY KEY,"
                "  duration_val INT NULL,"
                "  duration_unit VARCHAR(20) NULL,"
                "  max_devices INT NULL,"
                "  status VARCHAR(40) NULL,"
                "  activated_time DOUBLE NULL,"
                "  created_at DOUBLE NULL,"
                "  used_devices LONGTEXT NULL,"
                "  creator_info TEXT NULL,"
                "  client_ip VARCHAR(64) NULL,"
                "  is_locked TINYINT(1) NOT NULL DEFAULT 0"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
            )
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {_mysql_escape(MYSQL_TABLE_META)} ("
                "  meta_key VARCHAR(255) NOT NULL PRIMARY KEY,"
                "  meta_value LONGTEXT NULL"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
            )
            cur.execute(f"SELECT COUNT(*) AS c FROM {_mysql_escape(MYSQL_TABLE_KEYS)}")
            k_cnt = cur.fetchone()['c']
            cur.execute(f"SELECT COUNT(*) AS c FROM {_mysql_escape(MYSQL_TABLE_META)}")
            m_cnt = cur.fetchone()['c']
        conn.commit()
    finally:
        conn.close()

    # Migrate dữ liệu JSON cũ sang MySQL nếu MySQL đang trống
    if k_cnt == 0 and m_cnt == 0 and os.path.exists(DB_FILE):
        old = _json_load_db()
        if old:
            _mysql_save_db(old)
            try:
                shutil.copy2(DB_FILE, DB_FILE + '.pre_mysql.bak')
            except Exception:
                pass
            print('[database] Đã migrate dữ liệu cũ từ JSON sang MySQL.')


def _mysql_load_db():
    conn = _mysql_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT key_name, duration_val, duration_unit, max_devices, status,"
                f" activated_time, created_at, used_devices, creator_info, client_ip, is_locked"
                f" FROM {_mysql_escape(MYSQL_TABLE_KEYS)}"
            )
            key_rows = cur.fetchall()
            cur.execute(
                f"SELECT meta_key, meta_value FROM {_mysql_escape(MYSQL_TABLE_META)}"
            )
            meta_rows = cur.fetchall()
    finally:
        conn.close()

    data = {}
    for r in key_rows:
        rec = {
            'duration_val': r['duration_val'],
            'duration_unit': r['duration_unit'],
            'max_devices': r['max_devices'],
            'status': r['status'],
            'activated_time': r['activated_time'],
            'created_at': r['created_at'],
            'used_devices': json.loads(r['used_devices'] or '{}'),
        }
        if r['creator_info'] is not None:
            rec['creator_info'] = r['creator_info']
        if r['client_ip'] is not None:
            rec['client_ip'] = r['client_ip']
        if r['is_locked']:
            rec['is_locked'] = True
        data[r['key_name']] = rec
    for r in meta_rows:
        data[r['meta_key']] = json.loads(r['meta_value'] or '{}')
    if "___ADMIN_CONFIG___" not in data:
        data["___ADMIN_CONFIG___"] = default_admin_cfg()
    return data


def _mysql_save_db(data):
    key_rows = []
    meta_rows = []
    for k, v in data.items():
        if k.startswith(INTERNAL_PREFIX) or not isinstance(v, dict):
            meta_rows.append((k, v))
        else:
            key_rows.append((k, v))

    with _MYSQL_LOCK:
        conn = _mysql_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {_mysql_escape(MYSQL_TABLE_KEYS)}")
                cur.execute(f"DELETE FROM {_mysql_escape(MYSQL_TABLE_META)}")
                if key_rows:
                    cur.executemany(
                        f"INSERT INTO {_mysql_escape(MYSQL_TABLE_KEYS)}"
                        " (key_name, duration_val, duration_unit, max_devices, status,"
                        "  activated_time, created_at, used_devices, creator_info, client_ip, is_locked)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [
                            (
                                k,
                                v.get('duration_val'),
                                v.get('duration_unit'),
                                v.get('max_devices'),
                                v.get('status'),
                                v.get('activated_time'),
                                v.get('created_at'),
                                json.dumps(v.get('used_devices', {}), ensure_ascii=False),
                                v.get('creator_info'),
                                v.get('client_ip'),
                                1 if v.get('is_locked') else 0,
                            )
                            for k, v in key_rows
                        ],
                    )
                if meta_rows:
                    cur.executemany(
                        f"INSERT INTO {_mysql_escape(MYSQL_TABLE_META)} (meta_key, meta_value)"
                        " VALUES (%s,%s)",
                        [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta_rows],
                    )
            conn.commit()
        finally:
            conn.close()


# ==================================================================
# PUBLIC API — tự chọn engine
# ==================================================================
def load_db():
    if USE_MYSQL:
        return _mysql_load_db()
    return _json_load_db()


def save_db(data):
    if USE_MYSQL:
        _mysql_save_db(data)
        return
    _json_save_db(data)


# ==================================================================
# KHỞI TẠO ENGINE (chạy lúc import)
# ==================================================================
def _try_init_mysql():
    global USE_MYSQL
    if not (MYSQL_ENABLED and _MYSQL_DRIVER_OK and MYSQL_HOST and MYSQL_DB):
        return
    try:
        _mysql_init()
        USE_MYSQL = True
        print(f'[database] MySQL đã kết nối: {MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}')
    except Exception as e:
        USE_MYSQL = False
        print(f'[database] Không kết nối được MySQL, dùng JSON fallback: {e}')


_try_init_mysql()


# ==================================================================
# ADMIN CONFIG CACHE — tránh đọc DB trên từng request
# ==================================================================
_admin_cfg_cache = {"data": None, "ts": 0}
_ADMIN_CACHE_TTL = 30  # seconds


def get_cached_admin_cfg():
    """Trả về admin config từ cache (tối đa 30s cũ), chỉ đọc DB khi cache hết hạn."""
    now_t = time.time()
    if _admin_cfg_cache["data"] is None or (now_t - _admin_cfg_cache["ts"]) > _ADMIN_CACHE_TTL:
        db = load_db()
        _admin_cfg_cache["data"] = db.get("___ADMIN_CONFIG___", default_admin_cfg())
        _admin_cfg_cache["ts"] = now_t
    return _admin_cfg_cache["data"]


def invalidate_admin_cache():
    """Force cache refresh ở request kế tiếp (gọi sau khi đổi admin config)."""
    _admin_cfg_cache["ts"] = 0


def get_admin_config(db):
    """Lấy admin config từ dict DB đã load (không cache)."""
    return db.get("___ADMIN_CONFIG___", default_admin_cfg())
