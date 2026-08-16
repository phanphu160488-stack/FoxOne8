"""
config.py — CẤU HÌNH TÙY CHỈNH SERVER
====================================
Chỉnh sửa file này để thay đổi:
  - Bot Telegram (token, admin id)
  - Admin panel (tài khoản / mật khẩu mặc định)
  - Secret key, database, Link4m API
Tất cả giá trị có thể được override bằng biến môi trường (env var),
phù hợp khi deploy lên Render / Heroku.
"""

import os
from datetime import timezone, timedelta

# ============================================================
# FLASK / SESSION
# ============================================================
# Dùng env var SECRET_KEY cho deploy ổn định (Render), fallback fixed string
SECRET_KEY = os.environ.get('SECRET_KEY', 'server_vkhanh_2026_stable_secret_key_do_not_change')

SESSION_LIFETIME_DAYS = int(os.environ.get('SESSION_LIFETIME_DAYS', '365'))

# ============================================================
# ĐƯỜNG DẪN + DATABASE
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Dùng Render persistent disk nếu có (/data), nếu không fallback về thư mục script
DATA_DIR = '/data' if os.path.isdir('/data') else BASE_DIR
# File JSON hiển thị/đồng bộ (mirror) — để admin tiện xem/lưu key
DB_FILE = os.path.join(DATA_DIR, "database_keys.json")
# Database chính — SQLite
SQLITE_DB = os.environ.get('SQLITE_DB', os.path.join(DATA_DIR, "server.db"))

os.makedirs(DATA_DIR, exist_ok=True)

# ============================================================
# ADMIN PANEL — tài khoản/mật khẩu MẶC ĐỊNH (fallback khi chưa có DB)
# Lưu ý: sau khi đổi mật khẩu qua panel, giá trị mới sẽ được lưu
# vào database (MySQL nếu đã cấu hình, ngược lại là database_keys.json)
# và thay thế cấu hình này.
# ============================================================
ADMIN_DEFAULT_USER = os.environ.get('ADMIN_USER', 'phedevdzz')
ADMIN_DEFAULT_PASS = os.environ.get('ADMIN_PASS', 'x$f%^R4tGF4&nTyD5LW8Avlf')

# ============================================================
# TELEGRAM BOT — tùy chỉnh bot quản lý key
# ============================================================
# Token bot Telegram (lấy từ @BotFather)
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', 'AAEXvoXN8xCRzlkEfjun1ybjNtKpcdRrNc0')
# Admin ID Telegram — CHỈ user này được quyền điều khiển bot
TELEGRAM_ADMIN_ID = int(os.environ.get('TELEGRAM_ADMIN_ID', '6285624662'))
# ID chat nhận thông báo hệ thống (mặc định = admin id)
TELEGRAM_NOTIFY_CHAT_ID = int(os.environ.get('TELEGRAM_NOTIFY_CHAT_ID', TELEGRAM_ADMIN_ID))

# ============================================================
# LINK4M — API keys tạo link rút gọn (dùng cho lấy key free)
# ============================================================
LINK4M_API_KEYS = os.environ.get('LINK4M_API_KEYS', '6a7580c1d285ec09d8145cf4,69c76b755e6016383f32fdc9,6931d24fa35e7468b2604623').split(',')

# ============================================================
# CẤU HÌNH MẶC ĐỊNH KHÁC
# ============================================================
# Múi giờ Việt Nam
VN_TZ = timezone(timedelta(hours=7))

# Thời lượng key FREE mặc định khi chưa có cấu hình trong DB
DEFAULT_FREE_CONFIG = {"val": "12", "unit": "tiếng", "dev": "1"}

# Giới hạn key free mỗi IP mỗi ngày
FREE_KEYS_PER_IP_PER_DAY = int(os.environ.get('FREE_KEYS_PER_IP_PER_DAY', '3'))

# ============================================================
# MỤC "___" NỘI BỘ — không sửa trừ khi hiểu rõ
# ============================================================
INTERNAL_PREFIX = "___"
