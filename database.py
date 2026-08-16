"""
database.py — DATABASE ENGINE (SQLite)
======================================
Lưu dữ liệu hệ thống vào file SQLite (server.db). Không cần cài driver —
Python có sẵn thư viện sqlite3. Tự động tạo bảng khi khởi động và migrate
dữ liệu từ file JSON cũ (database_keys.json) nếu bảng đang trống.

CẤU TRÚC:
  - bảng `server_keys` : mỗi row = 1 KEY (key_name + các cột + used_devices JSON)
  - bảng `server_meta`  : mỗi row = 1 mục nội bộ (___ADMIN_CONFIG___, ___FREE_CONFIG___, ...)

API giữ nguyên:  db = load_db()   → sửa db   →   save_db(db)
hoặc dùng trực tiếp: sql_get_key / sql_save_key / sql_get_meta / ...
"""

import json
import os
import shutil
import sqlite3
import threading
import time

from config import (
    DB_FILE, SQLITE_DB, ADMIN_DEFAULT_USER, ADMIN_DEFAULT_PASS, INTERNAL_PREFIX,
)

# File JSON hiển thị/đồng bộ (dual-write): mọi thao tác ghi lên SQLite sẽ tự
# động đồng bộ sang file này để admin tiện xem/lưu key.
# Mặc định trỏ cùng chỗ với DB_FILE (DATA_DIR) để mirror + migrate dùng 1 file.
JSON_MIRROR_FILE = os.environ.get('JSON_MIRROR_FILE', DB_FILE)

# Luôn dùng SQLite
USE_SQLITE = True
USE_MYSQL = False  # giữ tên cũ cho tương thích nếu nơi khác còn import

_DB_LOCK = threading.Lock()


# ------------------------------------------------------------------
# CẤU HÌNH MẶC ĐỊNH
# ------------------------------------------------------------------
def default_admin_cfg():
    """Cấu hình admin mặc định (dùng khi DB chưa tồn tại)."""
    return {"user": ADMIN_DEFAULT_USER, "pass": ADMIN_DEFAULT_PASS}


# ==================================================================
# SQLITE ENGINE
# ==================================================================
def _sqlite_connect():
    """Mở 1 kết nối mới (mỗi lệnh 1 kết nối — an toàn với đa thread)."""
    conn = sqlite3.connect(SQLITE_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _sqlite_init():
    """Tạo bảng nếu chưa có + tự migrate dữ liệu từ file JSON cũ (nếu bảng trống)."""
    conn = _sqlite_connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS server_keys ("
            "  key_name TEXT PRIMARY KEY,"
            "  duration_val INTEGER,"
            "  duration_unit TEXT,"
            "  max_devices INTEGER,"
            "  status TEXT,"
            "  activated_time REAL,"
            "  created_at REAL,"
            "  used_devices TEXT,"
            "  creator_info TEXT,"
            "  client_ip TEXT,"
            "  is_locked INTEGER NOT NULL DEFAULT 0"
            ")"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS server_meta ("
            "  meta_key TEXT PRIMARY KEY,"
            "  meta_value TEXT"
            ")"
        )
        k_cnt = conn.execute("SELECT COUNT(*) AS c FROM server_keys").fetchone()['c']
        m_cnt = conn.execute("SELECT COUNT(*) AS c FROM server_meta").fetchone()['c']
        conn.commit()
    finally:
        conn.close()

    # Migrate dữ liệu JSON cũ sang SQLite nếu SQLite đang trống.
    # Thử DB_FILE trước (DATA_DIR), rồi fallback về file JSON trong thư mục dự án
    # (trường hợp trước đây chạy không có persistent disk /data).
    if k_cnt == 0 and m_cnt == 0:
        src = None
        for candidate in (DB_FILE, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database_keys.json')):
            if os.path.exists(candidate):
                src = candidate
                break
        if src:
            old = _json_load_db(src)
            if old:
                _sqlite_save_db(old)
                try:
                    shutil.copy2(src, src + '.pre_sqlite.bak')
                except Exception:
                    pass
                print(f'[database] Đã migrate dữ liệu cũ từ JSON ({src}) sang SQLite.')


def _row_to_rec(r):
    """Chuyển 1 row SQL thành dict record (định dạng giống JSON cũ)."""
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
    return rec


def _key_row(k, v):
    """Chuyển 1 record dict thành tuple để INSERT/UPSERT vào bảng server_keys."""
    return (
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


_UPSERT_KEY_SQL = (
    "INSERT INTO server_keys"
    " (key_name, duration_val, duration_unit, max_devices, status,"
    "  activated_time, created_at, used_devices, creator_info, client_ip, is_locked)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
    " ON CONFLICT(key_name) DO UPDATE SET"
    " duration_val=excluded.duration_val, duration_unit=excluded.duration_unit,"
    " max_devices=excluded.max_devices, status=excluded.status,"
    " activated_time=excluded.activated_time, created_at=excluded.created_at,"
    " used_devices=excluded.used_devices, creator_info=excluded.creator_info,"
    " client_ip=excluded.client_ip, is_locked=excluded.is_locked"
)

_UPSERT_META_SQL = (
    "INSERT INTO server_meta (meta_key, meta_value)"
    " VALUES (?,?)"
    " ON CONFLICT(meta_key) DO UPDATE SET meta_value=excluded.meta_value"
)


def _sqlite_load_db():
    conn = _sqlite_connect()
    try:
        key_rows = conn.execute(
            "SELECT key_name, duration_val, duration_unit, max_devices, status,"
            " activated_time, created_at, used_devices, creator_info, client_ip, is_locked"
            " FROM server_keys"
        ).fetchall()
        meta_rows = conn.execute("SELECT meta_key, meta_value FROM server_meta").fetchall()
    finally:
        conn.close()

    data = {}
    for r in key_rows:
        data[r['key_name']] = _row_to_rec(r)
    for r in meta_rows:
        try:
            data[r['meta_key']] = json.loads(r['meta_value'] or '{}')
        except Exception:
            data[r['meta_key']] = r['meta_value']
    if "___ADMIN_CONFIG___" not in data:
        data["___ADMIN_CONFIG___"] = default_admin_cfg()
    return data


def _sqlite_save_db(data):
    """Ghi toàn bộ DB xuống SQLite bằng UPSERT.
    Không xoá-sạch rồi chèn lại để tránh mất dữ liệu khi bị lỗi giữa chừng / đa thread.
    Chỉ xoá những key không còn tồn tại trong data.
    """
    key_rows = []
    meta_rows = []
    for k, v in data.items():
        if k.startswith(INTERNAL_PREFIX) or not isinstance(v, dict):
            meta_rows.append((k, v))
        else:
            key_rows.append((k, v))

    with _DB_LOCK:
        conn = _sqlite_connect()
        try:
            if key_rows:
                conn.executemany(_UPSERT_KEY_SQL, [_key_row(k, v) for k, v in key_rows])
            if meta_rows:
                conn.executemany(_UPSERT_META_SQL, [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta_rows])
            # Xoá key không còn nằm trong data (dữ liệu cũ không còn dùng)
            existing_keys = {row['key_name'] for row in conn.execute("SELECT key_name FROM server_keys").fetchall()}
            keep_keys = {k for k, _ in key_rows}
            for old_k in existing_keys - keep_keys:
                conn.execute("DELETE FROM server_keys WHERE key_name=?", (old_k,))
            existing_meta = {row['meta_key'] for row in conn.execute("SELECT meta_key FROM server_meta").fetchall()}
            keep_meta = {k for k, _ in meta_rows}
            for old_m in existing_meta - keep_meta:
                conn.execute("DELETE FROM server_meta WHERE meta_key=?", (old_m,))
            conn.commit()
        finally:
            conn.close()
    _json_mirror_sync()


# ==================================================================
# JSON MIRROR (đồng bộ sang database_keys.json để admin tiện xem)
# ==================================================================
def _json_load_db(path=None):
    path = path or DB_FILE
    if not os.path.exists(path):
        return {"___ADMIN_CONFIG___": default_admin_cfg()}
    with open(path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
            if "___ADMIN_CONFIG___" not in data:
                data["___ADMIN_CONFIG___"] = default_admin_cfg()
            return data
        except Exception:
            return {"___ADMIN_CONFIG___": default_admin_cfg()}


def _json_mirror_sync():
    """Đồng bộ toàn bộ dữ liệu từ SQLite sang file JSON (dual-write).
    File này để admin tiện xem/lưu key — dữ liệu chính vẫn nằm trong SQLite."""
    tmp = JSON_MIRROR_FILE + '.tmp'
    try:
        data = _sqlite_load_db()
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp, JSON_MIRROR_FILE)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


# ==================================================================
# PUBLIC API — đọc/ghi toàn bộ DB
# ==================================================================
def load_db():
    return _sqlite_load_db()


def save_db(data):
    _sqlite_save_db(data)


# ==================================================================
# API SQL TRỰC TIẾP — dùng cho các endpoint CHECK key
# (kiểm tra bằng SELECT WHERE key_name=?, lưu bằng UPSERT 1 dòng)
# ==================================================================
def sql_get_key(key_name):
    """SELECT 1 key trực tiếp từ SQLite theo key_name. Trả về dict record hoặc None."""
    if not key_name:
        return None
    try:
        conn = _sqlite_connect()
        try:
            r = conn.execute(
                "SELECT key_name, duration_val, duration_unit, max_devices, status,"
                " activated_time, created_at, used_devices, creator_info, client_ip, is_locked"
                " FROM server_keys WHERE key_name=?", (key_name,)
            ).fetchone()
        finally:
            conn.close()
        return _row_to_rec(r) if r else None
    except Exception as e:
        print(f'[database] sql_get_key lỗi ({key_name}): {e}')
        return None


def sql_save_key(key_name, rec):
    """UPSERT 1 key duy nhất xuống SQLite (không đụng các key khác)."""
    if not key_name or not isinstance(rec, dict):
        return
    with _DB_LOCK:
        conn = _sqlite_connect()
        try:
            conn.execute(_UPSERT_KEY_SQL, _key_row(key_name, rec))
            conn.commit()
        finally:
            conn.close()
    _json_mirror_sync()


def sql_delete_key(key_name):
    """Xoá 1 key trực tiếp khỏi SQLite."""
    if not key_name:
        return
    with _DB_LOCK:
        conn = _sqlite_connect()
        try:
            conn.execute("DELETE FROM server_keys WHERE key_name=?", (key_name,))
            conn.commit()
        finally:
            conn.close()
    _json_mirror_sync()


def sql_get_meta(meta_key, default=None):
    """SELECT 1 meta (mục ___xxx___) trực tiếp từ SQLite."""
    if not meta_key:
        return default
    try:
        conn = _sqlite_connect()
        try:
            r = conn.execute("SELECT meta_value FROM server_meta WHERE meta_key=?", (meta_key,)).fetchone()
        finally:
            conn.close()
        if r and r['meta_value']:
            try:
                return json.loads(r['meta_value'])
            except Exception:
                return r['meta_value']
        return default
    except Exception as e:
        print(f'[database] sql_get_meta lỗi ({meta_key}): {e}')
        return default


def sql_save_meta(meta_key, value):
    """UPSERT 1 meta (mục ___xxx___) duy nhất xuống SQLite."""
    if not meta_key:
        return
    with _DB_LOCK:
        conn = _sqlite_connect()
        try:
            conn.execute(_UPSERT_META_SQL, (meta_key, json.dumps(value, ensure_ascii=False)))
            conn.commit()
        finally:
            conn.close()
    _json_mirror_sync()


def sql_list_keys():
    """Lấy toàn bộ keys dạng dict (dùng cho admin panel / check-device quét toàn bộ)."""
    try:
        conn = _sqlite_connect()
        try:
            rows = conn.execute(
                "SELECT key_name, duration_val, duration_unit, max_devices, status,"
                " activated_time, created_at, used_devices, creator_info, client_ip, is_locked"
                " FROM server_keys"
            ).fetchall()
        finally:
            conn.close()
        return {r['key_name']: _row_to_rec(r) for r in rows}
    except Exception as e:
        print(f'[database] sql_list_keys lỗi: {e}')
        return {}


def sql_find_key_by_device(device_id):
    """Quét SQLite tìm key chứa device_id trong used_devices.
    Trả về (key_name, expiry) hoặc (None, None).
    """
    if not device_id:
        return None, None
    keys = sql_list_keys()
    for k, v in keys.items():
        devs = v.get('used_devices', {})
        if isinstance(devs, dict) and device_id in devs:
            return k, devs[device_id]
    return None, None


# ==================================================================
# KHỞI TẠO ENGINE (chạy lúc import)
# ==================================================================
try:
    _sqlite_init()
    print(f'[database] SQLite đã sẵn sàng: {SQLITE_DB}')
except Exception as e:
    print(f'[database] Lỗi khởi tạo SQLite: {e}')


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
