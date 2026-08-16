"""
telegrambot.py — TELEGRAM BOT QUẢN LÝ KEY
=========================================
Bot long-polling chạy trong thread riêng. Admin (theo config TELEGRAM_ADMIN_ID)
điều khiển qua các lệnh /start /stats /newkey /delkey ... và nhận thông báo
tự động khi có key mới, link4m, check-device, bypass phát hiện.
"""

import os
import random
import string
import threading
import time
from datetime import datetime

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_ID, TELEGRAM_NOTIFY_CHAT_ID, SQLITE_DB, VN_TZ
from database import load_db, save_db
from utils import _TG_OK, _req_tg, format_ts, get_time_left_str, shorten_with_link4m, _RATE_LIMITER, _RATE_LOCK

_TG_OFFSET = [0]


# ------------------------------------------------------------------
# GỬI TIN NHẮN
# ------------------------------------------------------------------
def tg_send(chat_id, text, parse_mode='HTML'):
    if not _TG_OK or _req_tg is None:
        return
    try:
        _req_tg.post(
            f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
            json={'chat_id': chat_id, 'text': text[:4000], 'parse_mode': parse_mode, 'disable_web_page_preview': True},
            timeout=10
        )
    except Exception:
        pass


def tg_notify(text):
    tg_send(TELEGRAM_NOTIFY_CHAT_ID, text)


# ------------------------------------------------------------------
# XỬ LÝ LỆNH TỪ ADMIN
# ------------------------------------------------------------------
def _tg_handle_cmd(chat_id, text):
    if chat_id != TELEGRAM_ADMIN_ID:
        tg_send(chat_id, '⛔ Bạn không có quyền sử dụng bot này.\nLiên hệ @vkhanh3010 để được hỗ trợ.')
        return
    parts = text.strip().split()
    cmd = parts[0].lower().split('@')[0] if parts else ''
    args = parts[1:]

    if cmd in ('/start', '/menu', '/help'):
        tg_send(chat_id, """🤖 <b>BOT QUẢN LÝ KEY SERVER — FOX ONE</b>

📊 <b>Thống kê:</b>
/stats — Thống kê tổng quan keys
/link4mstats — Số link4m &amp; key đã lấy
/status — Trạng thái server &amp; DB

🔑 <b>Quản lý Keys VIP:</b>
/keys — 10 VIP keys mới nhất
/newkey [time] [unit] [devices] — Tạo key VIP
  Ví dụ: /newkey 7 ngày 1
/delkey [KEY] — Xóa key
/resetkey [KEY] — Reset key về chưa kích hoạt

🎁 <b>Key Free:</b>
/freekeys — 10 Free keys mới nhất
/genlink — Tạo link Link4m mới (Admin bypass)

📱 <b>Quản lý Device ID:</b>
/approvedev [id] [val] [unit] — Duyệt thiết bị
  Ví dụ: /approvedev ABC123 7 ngày
  Vĩnh viễn: /approvedev ABC123 1 permanent
/revokedev [id] — Thu hồi thiết bị đã duyệt
/listdev — Xem danh sách thiết bị đã duyệt
/pendingdev — Xem yêu cầu duyệt đang chờ

🛡️ <b>Bảo mật &amp; DDoS:</b>
/iplog — Nhật ký IP lấy key free
/ddos — Kiểm tra IPs rate-limit bất thường
/resetip [IP] — Reset giới hạn IP

<i>✅ Bot tự thông báo: link4m mới, key cấp, check-device, bypass phát hiện.</i>""")

    elif cmd == '/stats':
        try:
            db = load_db()
            now = time.time()
            total = activated = expired = not_act = free_total = 0
            for k, v in db.items():
                if k.startswith('___') or not isinstance(v, dict):
                    continue
                total += 1
                if k.startswith('FREE-'):
                    free_total += 1
                st = v.get('status', '')
                if st == 'Đã kích hoạt':
                    activated += 1
                elif st == 'Hết hạn':
                    expired += 1
                else:
                    not_act += 1
            stats = db.get('___FREE_KEY_STATS___', {'total_bypasses': 0})
            tokens = db.get('___GETKEY_TOKENS___', {})
            used_tokens = sum(1 for v in tokens.values() if v.get('status') == 'used')
            tg_send(chat_id, f"""📊 <b>THỐNG KÊ HỆ THỐNG KEY</b>

🗄 Tổng keys: <b>{total}</b>
✅ Đã kích hoạt: <b>{activated}</b>
❌ Hết hạn: <b>{expired}</b>
⏳ Chưa kích hoạt: <b>{not_act}</b>
🎁 Keys Free: <b>{free_total}</b>
🔗 Lượt tạo link4m: <b>{stats.get('total_bypasses', 0)}</b>
🔑 Keys đã cấp qua link4m: <b>{used_tokens}</b>""")
        except Exception as e:
            tg_send(chat_id, f'❌ Lỗi: {e}')

    elif cmd == '/link4mstats':
        try:
            db = load_db()
            stats = db.get('___FREE_KEY_STATS___', {'total_bypasses': 0})
            tokens = db.get('___GETKEY_TOKENS___', {})
            used = sum(1 for v in tokens.values() if v.get('status') == 'used')
            pending = sum(1 for v in tokens.values() if v.get('status') == 'pending')
            total_bp = stats.get('total_bypasses', 0)
            tg_send(chat_id, f"""🔗 <b>LINK4M STATISTICS</b>

📨 Tổng lượt tạo link: <b>{total_bp}</b>
✅ Đã lấy key thành công: <b>{used}</b>
⏳ Đang chờ (pending): <b>{pending}</b>
❌ Không vượt (bỏ): <b>{max(0, total_bp - used - pending)}</b>""")
        except Exception as e:
            tg_send(chat_id, f'❌ Lỗi: {e}')

    elif cmd == '/status':
        try:
            db_size = os.path.getsize(SQLITE_DB) / 1024 if os.path.exists(SQLITE_DB) else 0
            host = os.environ.get('RENDER_EXTERNAL_URL', 'localhost')
            with _RATE_LOCK:
                rl_count = len(_RATE_LIMITER)
            now_vn = datetime.now(VN_TZ).strftime('%d/%m/%Y %H:%M:%S')
            tg_send(chat_id, f"""🟢 <b>SERVER STATUS</b>

🌐 Host: <code>{host}</code>
📦 DB Size: <b>{db_size:.1f} KB</b>
🛡 IPs rate-limit đang theo dõi: <b>{rl_count}</b>
⏰ Thời gian VN: <b>{now_vn}</b>""")
        except Exception as e:
            tg_send(chat_id, f'❌ Lỗi: {e}')

    elif cmd == '/keys':
        try:
            db = load_db()
            vip_keys = [(k, v) for k, v in db.items() if not k.startswith('___') and isinstance(v, dict) and not k.startswith('FREE-')]
            vip_keys.sort(key=lambda x: x[1].get('created_at', 0), reverse=True)
            if not vip_keys:
                tg_send(chat_id, '📭 Chưa có VIP key nào.')
                return
            lines = ['🔑 <b>10 VIP KEYS MỚI NHẤT:</b>\n']
            for k, v in vip_keys[:10]:
                st = v.get('status', '?')
                icon = '✅' if st == 'Đã kích hoạt' else ('❌' if st == 'Hết hạn' else '⏳')
                lines.append(f'{icon} <code>{k}</code>\n   ⏰ {v.get("duration_val", 0)} {v.get("duration_unit", "?")} | 📱 {v.get("max_devices", 1)} TB')
            tg_send(chat_id, '\n'.join(lines))
        except Exception as e:
            tg_send(chat_id, f'❌ Lỗi: {e}')

    elif cmd == '/freekeys':
        try:
            db = load_db()
            free_keys = [(k, v) for k, v in db.items() if not k.startswith('___') and isinstance(v, dict) and k.startswith('FREE-')]
            free_keys.sort(key=lambda x: x[1].get('created_at', 0), reverse=True)
            if not free_keys:
                tg_send(chat_id, '📭 Chưa có Free key nào.')
                return
            lines = ['🎁 <b>10 FREE KEYS MỚI NHẤT:</b>\n']
            for k, v in free_keys[:10]:
                st = v.get('status', '?')
                icon = '✅' if st == 'Đã kích hoạt' else ('❌' if st == 'Hết hạn' else '⏳')
                ip = v.get('client_ip', '?')
                ct = format_ts(v.get('created_at', 0))
                lines.append(f'{icon} <code>{k}</code>\n   📍 IP: <code>{ip}</code> | {ct}')
            tg_send(chat_id, '\n'.join(lines))
        except Exception as e:
            tg_send(chat_id, f'❌ Lỗi: {e}')

    elif cmd == '/newkey':
        try:
            if len(args) < 2:
                tg_send(chat_id, '❌ Cú pháp: /newkey [thời_gian] [đơn_vị] [thiết_bị]\nVí dụ: /newkey 7 ngày 1\nĐơn vị: phút, tiếng, ngày, tháng, năm')
                return
            time_val = args[0]
            time_unit = args[1]
            max_dev = int(args[2]) if len(args) > 2 else 1
            if time_unit not in ('phút', 'tiếng', 'ngày', 'tháng', 'năm', 'permanent'):
                tg_send(chat_id, '❌ Đơn vị không hợp lệ! Dùng: phút, tiếng, ngày, tháng, năm')
                return
            db = load_db()
            p1 = ''.join(random.choices(string.ascii_uppercase + string.digits, k=3))
            p2 = ''.join(random.choices(string.ascii_uppercase + string.digits, k=3))
            pfx = {'ngày': f'{time_val}D', 'tiếng': f'{time_val}H', 'phút': f'{time_val}P', 'tháng': f'{time_val}M', 'năm': f'{time_val}Y', 'permanent': 'VIP'}
            key_name = f"{pfx.get(time_unit, 'KEY')}-{p1}-{p2}"
            db[key_name] = {
                'duration_val': int(time_val) if time_unit != 'permanent' else 0,
                'duration_unit': time_unit, 'max_devices': max_dev, 'status': 'Chưa kích hoạt',
                'activated_time': None, 'created_at': time.time(), 'used_devices': {},
                'creator_info': 'Tạo bởi Admin Bot Telegram'
            }
            save_db(db)
            tg_send(chat_id, f'✅ <b>Tạo key thành công!</b>\n\n🔑 Key: <code>{key_name}</code>\n⏰ Hạn: {time_val} {time_unit}\n📱 Thiết bị: {max_dev}')
        except Exception as e:
            tg_send(chat_id, f'❌ Lỗi tạo key: {e}\n\nCú pháp: /newkey [thời_gian] [đơn_vị] [thiết_bị]')

    elif cmd == '/delkey':
        if not args:
            tg_send(chat_id, '❌ Cú pháp: /delkey [KEY]')
            return
        key = args[0]
        try:
            db = load_db()
            if key in db and not key.startswith('___'):
                del db[key]
                save_db(db)
                tg_send(chat_id, f'✅ Đã xóa key: <code>{key}</code>')
            else:
                tg_send(chat_id, f'❌ Key không tồn tại: <code>{key}</code>')
        except Exception as e:
            tg_send(chat_id, f'❌ Lỗi: {e}')

    elif cmd == '/resetkey':
        if not args:
            tg_send(chat_id, '❌ Cú pháp: /resetkey [KEY]')
            return
        key = args[0]
        try:
            db = load_db()
            if key in db and not key.startswith('___'):
                db[key]['status'] = 'Chưa kích hoạt'
                db[key]['activated_time'] = None
                db[key]['used_devices'] = {}
                save_db(db)
                tg_send(chat_id, f'✅ Đã reset key: <code>{key}</code>')
            else:
                tg_send(chat_id, f'❌ Key không tồn tại: <code>{key}</code>')
        except Exception as e:
            tg_send(chat_id, f'❌ Lỗi: {e}')

    elif cmd == '/genlink':
        try:
            now = time.time()
            token = ''.join(random.choices(string.ascii_uppercase + string.digits, k=20))
            host = os.environ.get('RENDER_EXTERNAL_URL', 'https://localhost')
            dest_url = f"{host}/nhan-key-free?token={token}"
            short_url, err = shorten_with_link4m(dest_url)
            if not short_url:
                tg_send(chat_id, f'❌ Không tạo được link Link4m: {err}')
                return
            db = load_db()
            tokens = db.get('___GETKEY_TOKENS___', {})
            tokens = {k: v for k, v in tokens.items() if now - v.get('created_at', 0) < 3600}
            tokens[token] = {'ip': 'ADMIN_BOT', 'created_at': now - 60, 'status': 'pending', 'is_admin': True}
            db['___GETKEY_TOKENS___'] = tokens
            stats = db.get('___FREE_KEY_STATS___', {'total_bypasses': 0})
            stats['total_bypasses'] = stats.get('total_bypasses', 0) + 1
            db['___FREE_KEY_STATS___'] = stats
            save_db(db)
            tg_send(chat_id, f'🔗 <b>Link Link4m mới (Admin bypass):</b>\n\n<code>{short_url}</code>\n\n⏰ Hết hạn sau 1 giờ\n✅ Admin bypass — không cần VPN check, không cần timing')
        except Exception as e:
            tg_send(chat_id, f'❌ Lỗi tạo link: {e}')

    elif cmd == '/iplog':
        try:
            db = load_db()
            ip_history = db.get('___FREE_IP_HISTORY___', {})
            now = time.time()
            if not ip_history:
                tg_send(chat_id, '📭 Chưa có nhật ký IP nào.')
                return
            lines = ['📋 <b>NHẬT KÝ IP LẤY KEY FREE (24h):</b>\n']
            recent = [(ip, times) for ip, times in ip_history.items() if any(now - t < 86400 for t in times)]
            recent.sort(key=lambda x: max(x[1]), reverse=True)
            for ip_addr, times in recent[:20]:
                count = len([t for t in times if now - t < 86400])
                last = format_ts(max(times))
                lines.append(f'📍 <code>{ip_addr}</code> — {count} key | {last}')
            tg_send(chat_id, '\n'.join(lines) if len(lines) > 1 else '📭 Không có dữ liệu trong 24h.')
        except Exception as e:
            tg_send(chat_id, f'❌ Lỗi: {e}')

    elif cmd == '/ddos':
        try:
            with _RATE_LOCK:
                rl_copy = dict(_RATE_LIMITER)
            now = time.time()
            high = [(ip_addr, len([t for t in times if now - t < 60])) for ip_addr, times in rl_copy.items()]
            high = [(ip_addr, cnt) for ip_addr, cnt in high if cnt >= 3]
            high.sort(key=lambda x: x[1], reverse=True)
            if not high:
                tg_send(chat_id, '✅ Không phát hiện hoạt động DDoS/Rate limit bất thường.')
                return
            lines = [f'⚠️ <b>RATE LIMIT ALERT ({len(high)} IPs):</b>\n']
            for ip_addr, cnt in high[:15]:
                lines.append(f'🔴 <code>{ip_addr}</code> — {cnt} req/60s')
            tg_send(chat_id, '\n'.join(lines))
        except Exception as e:
            tg_send(chat_id, f'❌ Lỗi: {e}')

    elif cmd == '/resetip':
        if not args:
            tg_send(chat_id, '❌ Cú pháp: /resetip [IP]\nVí dụ: /resetip 1.2.3.4')
            return
        target_ip = args[0]
        try:
            db = load_db()
            changed = []
            ip_history = db.get('___FREE_IP_HISTORY___', {})
            if target_ip in ip_history:
                del ip_history[target_ip]
                db['___FREE_IP_HISTORY___'] = ip_history
                changed.append('Nhật ký IP')
            ip_map = db.get('___IP_KEY_MAP___', {})
            if target_ip in ip_map:
                del ip_map[target_ip]
                db['___IP_KEY_MAP___'] = ip_map
                changed.append('IP-Key map')
            save_db(db)
            msg = f'✅ Đã reset giới hạn cho IP: <code>{target_ip}</code>'
            if changed:
                msg += f'\nĐã xóa: {", ".join(changed)}'
            tg_send(chat_id, msg)
        except Exception as e:
            tg_send(chat_id, f'❌ Lỗi: {e}')

    elif cmd == '/approvedev':
        if len(args) < 3:
            tg_send(chat_id, (
                '❌ Cú pháp: /approvedev [device_id] [val] [unit]\n'
                'Đơn vị: phút | tiếng | ngày | tháng | năm | permanent\n'
                'Ví dụ: /approvedev ABCD1234 7 ngày\n'
                'Hoặc vĩnh viễn: /approvedev ABCD1234 1 permanent'
            ))
            return
        did = args[0]
        val_str = args[1]
        unit_arg = args[2].lower()
        try:
            val_int = int(val_str)
        except Exception:
            tg_send(chat_id, '❌ Giá trị thời gian phải là số nguyên dương.')
            return
        db = load_db()
        approved = db.get("___APPROVED_DEVICES___", {})
        now_ts = time.time()
        if unit_arg == 'permanent':
            exp_ts = -1
        elif unit_arg == 'phút':
            exp_ts = now_ts + val_int * 60
        elif unit_arg == 'tiếng':
            exp_ts = now_ts + val_int * 3600
        elif unit_arg == 'ngày':
            exp_ts = now_ts + val_int * 86400
        elif unit_arg == 'tháng':
            exp_ts = now_ts + val_int * 30 * 86400
        elif unit_arg == 'năm':
            exp_ts = now_ts + val_int * 365 * 86400
        else:
            tg_send(chat_id, '❌ Đơn vị không hợp lệ. Dùng: phút | tiếng | ngày | tháng | năm | permanent')
            return
        approved[did] = {
            "expiry": exp_ts,
            "approved_at": now_ts,
            "val": val_int,
            "unit": unit_arg,
            "note": "Duyệt bởi Telegram Bot Admin",
            "ip": "telegram"
        }
        db["___APPROVED_DEVICES___"] = approved
        save_db(db)
        exp_display = 'Vĩnh viễn' if exp_ts == -1 else format_ts(exp_ts)
        tg_send(chat_id, (
            f'✅ <b>ĐÃ DUYỆT DEVICE ID</b>\n'
            f'🔧 Device: <code>{did}</code>\n'
            f'⏳ Thời gian: {val_int} {unit_arg}\n'
            f'⏰ Hết hạn: {exp_display}'
        ))

    elif cmd == '/revokedev':
        if not args:
            tg_send(chat_id, '❌ Cú pháp: /revokedev [device_id]\nVí dụ: /revokedev ABCD1234')
            return
        did = args[0]
        db = load_db()
        approved = db.get("___APPROVED_DEVICES___", {})
        if did in approved:
            del approved[did]
            db["___APPROVED_DEVICES___"] = approved
            save_db(db)
            tg_send(chat_id, f'✅ <b>ĐÃ THU HỒI DEVICE ID</b>\n🔧 Device: <code>{did}</code>\nThiết bị này sẽ không còn được duyệt nữa.')
        else:
            tg_send(chat_id, f'⚠️ Device ID <code>{did}</code> không tồn tại trong danh sách duyệt.')

    elif cmd == '/listdev':
        db = load_db()
        approved = db.get("___APPROVED_DEVICES___", {})
        now_ts = time.time()
        if not approved:
            tg_send(chat_id, '📋 <b>DANH SÁCH DEVICE ĐÃ DUYỆT</b>\n\n<i>Chưa có thiết bị nào được duyệt.</i>')
            return
        lines = ['📋 <b>DANH SÁCH DEVICE ĐÃ DUYỆT</b>\n']
        count = 0
        for did, dinfo in approved.items():
            exp = dinfo.get('expiry', -1)
            if exp == 0:
                exp = -1
            is_perm = (exp == -1)
            if not is_perm and exp < now_ts:
                status_icon = '❌'
                time_str = 'Hết hạn'
            else:
                status_icon = '✅'
                time_str = 'Vĩnh viễn' if is_perm else get_time_left_str(exp)
            short_id = did[:16] + '...' if len(did) > 16 else did
            lines.append(f'{status_icon} <code>{short_id}</code>\n   ⏳ {time_str}')
            count += 1
            if count >= 30:
                lines.append(f'\n<i>... và {len(approved) - 30} thiết bị khác</i>')
                break
        lines.append(f'\n<b>Tổng: {len(approved)} thiết bị</b>')
        tg_send(chat_id, '\n'.join(lines))

    elif cmd == '/pendingdev':
        db = load_db()
        pending = db.get("___PENDING_DEVICE_REQUESTS___", {})
        if not pending:
            tg_send(chat_id, '📥 <b>YÊU CẦU DUYỆT DEVICE</b>\n\n<i>Không có yêu cầu nào đang chờ duyệt.</i>')
            return
        lines = [f'📥 <b>YÊU CẦU DUYỆT DEVICE ({len(pending)} yêu cầu)</b>\n']
        count = 0
        for req_id, rinfo in pending.items():
            did = rinfo.get('device_id', '—')
            short_id = did[:16] + '...' if len(did) > 16 else did
            val = rinfo.get('val', 7)
            unit = rinfo.get('unit', 'ngày')
            ip = rinfo.get('ip', '—')
            note = rinfo.get('note', '')
            submitted = rinfo.get('submitted_at_str', '—')
            lines.append(
                f'🔧 <code>{short_id}</code>\n'
                f'   📅 {submitted} | ⏳ {val} {unit} | 🌐 {ip}'
                + (f'\n   📝 {note}' if note else '')
                + f'\n   /approvedev {did} {val} {unit}'
            )
            count += 1
            if count >= 10:
                lines.append(f'\n<i>... và {len(pending) - 10} yêu cầu khác</i>')
                break
        tg_send(chat_id, '\n'.join(lines))

    else:
        if text.startswith('/'):
            tg_send(chat_id, f'❓ Lệnh không hợp lệ: <code>{cmd}</code>\nGõ /start để xem menu đầy đủ.')


# ------------------------------------------------------------------
# LONG-POLLING WORKER
# ------------------------------------------------------------------
def _tg_poll_worker():
    import time as _tt
    _tt.sleep(10)
    while True:
        try:
            if not _TG_OK or _req_tg is None:
                _tt.sleep(30)
                continue
            resp = _req_tg.get(
                f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates',
                params={'offset': _TG_OFFSET[0], 'timeout': 25, 'allowed_updates': ['message']},
                timeout=32
            )
            data = resp.json()
            if data.get('ok'):
                for upd in data.get('result', []):
                    _TG_OFFSET[0] = upd['update_id'] + 1
                    try:
                        msg = upd.get('message', {})
                        if msg:
                            cid = msg.get('chat', {}).get('id', 0)
                            txt = msg.get('text', '').strip()
                            if txt:
                                _tg_handle_cmd(cid, txt)
                    except Exception:
                        pass
        except Exception:
            _tt.sleep(5)


# ------------------------------------------------------------------
# KHỞI ĐỘNG THREAD BOT (chạy ngầm cùng server)
# ------------------------------------------------------------------
_tg_thread = threading.Thread(target=_tg_poll_worker, daemon=True)
_tg_thread.start()
