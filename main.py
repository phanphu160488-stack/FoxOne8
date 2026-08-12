"""
main.py — ENTRY POINT
=====================
Chạy server Flask.

Cách chạy:
  python main.py                          # dev
  gunicorn main:app --bind 0.0.0.0:$PORT  # production
"""

import os

# Import app để khởi tạo Flask app + bắt đầu các thread ngầm
# (keep-alive, telegram bot) ngay khi module được nạp.
from app import app

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(port=port, host='0.0.0.0')
