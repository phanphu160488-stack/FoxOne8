"""
server.py — FLASK APP FACTORY (KHỞI TẠO + MIDDLEWARE + DỊCH VỤ NỀN)
===================================================================
Tạo Flask app, đăng ký các blueprint (menu / admin / key / getkey)
và gắn middleware toàn cục:
  - Error handlers (404/500), CORS + security headers
  - Xử lý OPTIONS (preflight) + kiểm tra admin session
  - Keep-alive worker (giảm cold start trên Render)
  - Health check, media (avatar/nhạc)
  - Web log (nhật ký truy cập in-memory)
  - SoundCloud search + stream

Chạy bằng:  gunicorn server:app   (hoặc python server.py)
"""

import os
import re
import threading
import urllib.parse
import urllib.request as _ureq
from datetime import datetime, timedelta

from flask import Flask, jsonify, request, session, send_file

from config import SECRET_KEY, SESSION_LIFETIME_DAYS, BASE_DIR, DB_FILE, VN_TZ
from database import get_cached_admin_cfg, USE_SQLITE
from utils import get_real_ip, _TG_OK, _req_tg

# ============================================================
# APP FACTORY
# ============================================================
def create_app():
    app = Flask(__name__)
    app.secret_key = SECRET_KEY

    app.permanent_session_lifetime = timedelta(days=SESSION_LIFETIME_DAYS)
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SECURE'] = False

    # ---- Register blueprints ----
    from menu import menu_bp
    from admin import admin_bp
    from key import key_bp
    from getkey import getkey_bp
    app.register_blueprint(menu_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(key_bp)
    app.register_blueprint(getkey_bp)

    # ---- Error handlers + CORS + security headers ----
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

    # ---- Web log (in-memory) ----
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

    # ---- Keep-alive worker ----
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

    # ---- Health check ----
    @app.route('/healthz')
    def healthz():
        db_ok = USE_SQLITE or os.path.exists(DB_FILE)
        return jsonify({"status": "ok", "db": db_ok}), 200

    # ---- Media (avatar + nhạc) ----
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

    # ---- SoundCloud ----
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

    return app


# ============================================================
# KHỞI TẠO APP (cho gunicorn server:app)
# ============================================================
app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(port=port, host='0.0.0.0')
