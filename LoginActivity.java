package com.zew.fakelag;

import androidx.appcompat.app.AppCompatActivity;

import android.animation.ObjectAnimator;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.util.Log;
import android.view.View;
import android.view.animation.AccelerateInterpolator;
import android.view.animation.DecelerateInterpolator;
import android.view.animation.LinearInterpolator;
import android.view.animation.OvershootInterpolator;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONObject;

import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;

public class LoginActivity extends AppCompatActivity {

    private static final String BASE_URL       = "https://fox-one-prenium.onrender.com";
    private static final String VERIFY_URL     = BASE_URL + "/api/verify";
    private static final String GETKEY_URL     = BASE_URL + "/api/getkey";
    private static final String FREE_CONFIG_URL = BASE_URL + "/api/free_config";
    private static final int    NET_TIMEOUT    = 15_000;
    private static final String PREFS          = "booster_prefs";
    private static final String TAG            = "LoginActivity";

    // UA giả trình duyệt thật: host miễn phí (kesug) hay chặn/đổi phản hồi
    // thành trang HTML (vẫn trả HTTP 200) nếu thấy UA lạ → app báo lỗi "HTTP 200".
    private static final String BROWSER_UA     =
            "Mozilla/5.0 (Linux; Android 13; SM-G991B Build/TP1A.220624.014) " +
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36";

    // Giữ cookie giữa các request để qua được bước interstitial của host miễn phí
    static {
        try {
            java.net.CookieManager cm = new java.net.CookieManager();
            java.net.CookieHandler.setDefault(cm);
        } catch (Exception ignored) {}
    }

    // Thời gian tối thiểu vòng tròn loading quay khi đăng nhập thành công (ms)
    private static final long   MIN_LOADING_MS = 3_000;

    // ─── Views ────────────────────────────────────────────────
    private FrameLayout logoCircle;
    private TextView    tvBrand, tvBrandSub;
    private LinearLayout loginCard, statusPill, featureStrip;
    private EditText    etKey;
    private Button      btnLogin, btnGetKey;
    private TextView    tvStatus, loadingLabel, waitingLabel;
    private ImageView   eyeIcon, loadingCenter;
    private View        dotPill, loadingRing, orbitWrap;
    private FrameLayout loadingOverlay;
    private View        loadingCard;
    private LinearLayout notifBar;
    private TextView     notifText;
    private ImageView    notifIcon;
    private boolean keyVisible = false;
    // Chống bấm Get Key nhiều lần khi đang tạo link.
    // volatile: onGetKeyError có thể ghi trên worker thread, còn đọc trên UI thread.
    private volatile boolean fetchingKey = false;

    private final android.os.Handler uiHandler =
            new android.os.Handler(android.os.Looper.getMainLooper());

    // ─── Dot animation ────────────────────────────────────────
    private Runnable dotRunnable;
    private int      dotCount = 0;

    // ─── Chớp chấm trạng thái máy chủ ─────────────────────────
    private Runnable serverBlinkRunnable;

    // ─── Loading spinner ──────────────────────────────────────
    private ObjectAnimator ringRotator, orbitRotator, centerPulse, centerPulseYAnim;
    private long loginStartTime = 0;

    // ══════════════════════════════════════════════════════════
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (getSupportActionBar() != null) getSupportActionBar().hide();
        setContentView(R.layout.activity_login);
        initViews();
        applyBrandGradient();
        playEntranceAnimations();
        startServerDotBlink();
        attachListeners();
        loadSavedKey();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        stopSpinner();
        if (serverBlinkRunnable != null) uiHandler.removeCallbacks(serverBlinkRunnable);
    }

    // ══════════════════════════════════════════════════════════
    //  INIT VIEWS
    // ══════════════════════════════════════════════════════════
    private void initViews() {
        logoCircle   = findViewById(R.id.logoCircle);
        tvBrand      = findViewById(R.id.tvBrand);
        tvBrandSub   = findViewById(R.id.tvBrandSub);
        loginCard    = findViewById(R.id.loginCard);
        statusPill   = findViewById(R.id.statusPill);
        featureStrip = findViewById(R.id.featureStrip);
        etKey        = findViewById(R.id.etKey);
        eyeIcon      = findViewById(R.id.eyeIcon);
        tvStatus     = findViewById(R.id.tvStatus);
        btnLogin     = findViewById(R.id.btnLogin);
        btnGetKey    = findViewById(R.id.btnGetKey);
        dotPill      = findViewById(R.id.dotPill);
        notifBar     = findViewById(R.id.notifBar);
        notifIcon    = findViewById(R.id.notifIcon);
        notifText    = findViewById(R.id.notifText);
        loadingOverlay = findViewById(R.id.loadingOverlay);
        loadingCard    = findViewById(R.id.loadingCard);
        loadingRing    = findViewById(R.id.loadingRing);
        orbitWrap      = findViewById(R.id.orbitWrap);
        loadingCenter  = findViewById(R.id.loadingCenter);
        loadingLabel   = findViewById(R.id.loadingLabel);
        waitingLabel   = findViewById(R.id.waitingLabel);
    }

    /** Gradient chữ thương hiệu: hồng → tím như menu.html */
    private void applyBrandGradient() {
        try {
            float w = tvBrand.getPaint().measureText(tvBrand.getText().toString()) + dp(24);
            android.graphics.Shader sh = new android.graphics.LinearGradient(0, 0, w, 0,
                    new int[]{0xFFE14FD0, 0xFFB48CF5, 0xFF7B3FF2, 0xFFE14FD0},
                    null, android.graphics.Shader.TileMode.CLAMP);
            tvBrand.getPaint().setShader(sh);
            tvBrand.invalidate();
        } catch (Exception ignored) {}
    }

    /** Hiệu ứng vào màn hình: logo nảy, chữ lên dần, card trượt lên */
    private void playEntranceAnimations() {
        logoCircle.setScaleX(0.4f);
        logoCircle.setScaleY(0.4f);
        logoCircle.setAlpha(0f);
        logoCircle.animate().scaleX(1f).scaleY(1f).alpha(1f)
                .setDuration(560).setInterpolator(new OvershootInterpolator(1.7f))
                .setStartDelay(80).start();

        tvBrand.setAlpha(0f);
        tvBrand.setTranslationY(dp(14));
        tvBrand.animate().alpha(1f).translationY(0).setDuration(480)
                .setStartDelay(240).start();

        tvBrandSub.setAlpha(0f);
        tvBrandSub.animate().alpha(1f).setDuration(450).setStartDelay(340).start();

        loginCard.setAlpha(0f);
        loginCard.setTranslationY(dp(26));
        loginCard.animate().alpha(1f).translationY(0).setDuration(520)
                .setInterpolator(new DecelerateInterpolator()).setStartDelay(280).start();

        statusPill.setAlpha(0f);
        statusPill.animate().alpha(1f).setDuration(420).setStartDelay(580).start();

        featureStrip.setAlpha(0f);
        featureStrip.animate().alpha(1f).setDuration(420).setStartDelay(660).start();
    }

    /** Nhấp nháy chấm xanh trạng thái máy chủ */
    private void startServerDotBlink() {
        if (dotPill == null) return;
        serverBlinkRunnable = new Runnable() {
            boolean bright = true;
            @Override public void run() {
                dotPill.animate().alpha(bright ? 0.25f : 1f).setDuration(700).start();
                bright = !bright;
                uiHandler.postDelayed(this, 900);
            }
        };
        uiHandler.post(serverBlinkRunnable);
    }

    // ══════════════════════════════════════════════════════════
    //  LISTENERS
    // ══════════════════════════════════════════════════════════
    private void attachListeners() {
        eyeIcon.setOnClickListener(v -> {
            keyVisible = !keyVisible;
            etKey.setInputType(keyVisible
                    ? InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
                    : InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
            eyeIcon.setImageResource(keyVisible
                    ? R.drawable.ic_visibility_on
                    : R.drawable.ic_visibility_off);
            eyeIcon.setColorFilter(keyVisible
                    ? Color.parseColor("#E14FD0")
                    : Color.parseColor("#4A4A70"));
            eyeIcon.animate().scaleX(0.8f).scaleY(0.8f).setDuration(80)
                    .withEndAction(() -> eyeIcon.animate().scaleX(1f).scaleY(1f)
                            .setDuration(120).setInterpolator(new OvershootInterpolator()).start())
                    .start();
            etKey.setSelection(etKey.getText().length());
        });

        btnLogin.setOnClickListener(v -> {
            v.animate().scaleX(0.95f).scaleY(0.95f).setDuration(80)
                    .withEndAction(() -> v.animate().scaleX(1f).scaleY(1f)
                            .setDuration(150).setInterpolator(new OvershootInterpolator()).start())
                    .start();
            doLogin();
        });

        btnGetKey.setOnClickListener(v -> {
            v.animate().alpha(0.6f).setDuration(80)
                    .withEndAction(() -> v.animate().alpha(1f).setDuration(200).start())
                    .start();
            if (!fetchingKey) requestBypass();
        });
    }

    // ══════════════════════════════════════════════════════════
    //  LOGIC
    // ══════════════════════════════════════════════════════════
    private void loadSavedKey() {
        String saved = getSharedPreferences(PREFS, MODE_PRIVATE).getString("saved_key", "");
        if (!saved.isEmpty()) {
            etKey.setText(saved);
            setStatus("Nhấn Đăng Nhập để tiếp tục", Color.parseColor("#E14FD0"));
        } else {
            animateHint("Nhập key của bạn…");
        }
    }

    private void animateHint(String text) {
        if (etKey == null) return;
        etKey.setHint("");
        for (int i = 0; i <= text.length(); i++) {
            final int idx = i;
            uiHandler.postDelayed(() -> {
                if (etKey != null) etKey.setHint(text.substring(0, idx));
            }, i * 80L);
        }
    }

    private void doLogin() {
        String key = etKey.getText().toString().trim();
        if (key.isEmpty()) {
            shake(etKey);
            setStatus("Vui lòng nhập key!", Color.parseColor("#F2545B"));
            return;
        }
        setStatus("", Color.TRANSPARENT);
        loginStartTime = System.currentTimeMillis();
        setLoadingText("ĐANG XÁC THỰC", "Vui lòng chờ…");
        showLoading();
        setBtn(false);

        final String hwid = getAndroidId();
        new Thread(() -> {
            try {
                JSONObject body = new JSONObject();
                body.put("key", key);
                body.put("hwid", hwid);
                HttpResult r = performPostJson(VERIFY_URL, body.toString());

                // HTTP lỗi (4xx/5xx…) → ưu tiên lấy message JSON server gửi về
                if (r.code < 200 || r.code >= 300) {
                    onAuthError(serverErrorMessage(r.code, r.body));
                    return;
                }

                JSONObject o;
                try {
                    o = new JSONObject(r.body);
                } catch (Exception e) {
                    // Host miễn phí hay chèn BOM / cảnh báo PHP / HTML vào phản hồi
                    o = tryExtractJson(r.body);
                    if (o == null) {
                        Log.e(TAG, "Login: response not JSON, HTTP " + r.code
                                + " → " + truncate(r.body, 500));
                        onAuthError("Server phản hồi không hợp lệ (HTTP " + r.code + "). Thử lại sau.");
                        return;
                    }
                }

                String status = o.optString("status", "");
                if ("success".equals(status)) {
                    getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                            .putString("saved_key", key)
                            .putBoolean("is_authenticated", true)
                            .putString("time_left", o.optString("time_left", "∞"))
                            .putLong("expiry_timestamp", o.optLong("expiry_timestamp", -1))
                            .putString("expiry_str", o.optString("expiry_str", "Vĩnh Viễn"))
                            .putBoolean("is_permanent", o.optBoolean("is_permanent", false))
                            .putBoolean("is_new_device", o.optBoolean("is_new_device", false))
                            // Auto bật Zeadx Ping Flow+ PREMIUM ngay sau khi nhập key thành công
                            .putBoolean("flow_manual_choice", false)
                            .putInt("vpn_mode", 1)
                            .apply();
                    runOnUiThread(() -> {
                        // Giữ vòng tròn loading quay đủ 3 giây rồi mới vào app
                        setLoadingText("ĐĂNG NHẬP THÀNH CÔNG", "Đang vào Zeadx Ping…");
                        long elapsed = System.currentTimeMillis() - loginStartTime;
                        long wait = Math.max(0, MIN_LOADING_MS - elapsed);
                        uiHandler.postDelayed(() -> {
                            hideLoading();
                            goMain();
                        }, wait);
                    });
                } else {
                    String msg = o.optString("message", "Key không hợp lệ.");
                    if ("invalid".equals(status)) msg = "Key không tồn tại trên hệ thống!";
                    else if ("expired".equals(status)) msg = "Key đã hết hạn, hãy mua key mới!";
                    else if ("device_limit".equals(status)) msg = "Key đã đạt giới hạn thiết bị!";
                    else if (msg.isEmpty()) msg = "Key không hợp lệ.";
                    onAuthError(msg);
                }

            } catch (Exception e) {
                onAuthError("Lỗi kết nối / timeout. Kiểm tra mạng.");
            }
        }).start();
    }

    private void onAuthError(String msg) {
        runOnUiThread(() -> {
            hideLoading();
            setStatus(msg, Color.parseColor("#F2545B"));
            showNotif("❌  " + msg, false);
            setBtn(true);
        });
    }

    // ── GET KEY (chỉ gọi /api/getkey — bắt buộc vượt link mới có key) ───
    /**
     * Flow LẤY KEY — GỌI /api/getkey (server tự chọn thời hạn theo FREE_CONFIG):
     *   1. GET /api/getkey → trả về { status, shortenedUrl, link, token }
     *   2. App mở link vượt (shortenedUrl) trong trình duyệt
     *   3. User VƯỢT LINK xong → trang KẾT QUẢ hiển thị key
     *   4. User copy key dán vào ô nhập → Đăng nhập
     * Bắt buộc phải vượt link mới thấy được key (app KHÔNG tự lấy key).
     */
    private void requestBypass() {
        if (fetchingKey) return;
        fetchingKey = true;
        showLoading(false);
        setLoadingText("Đang tạo link vượt…", "Vui lòng chờ…");
        setBtn(false);
        enableGetKey(false);

        new Thread(() -> {
            try {
                String url = GETKEY_URL;
                HttpResult r = performRequest(url);

                if (r.code < 200 || r.code >= 300) {
                    onGetKeyError(serverErrorMessage(r.code, r.body));
                    return;
                }

                JSONObject o;
                try {
                    o = new JSONObject(r.body);
                } catch (Exception e) {
                    o = tryExtractJson(r.body);
                    if (o == null) {
                        Log.e(TAG, "GetKey: response not JSON, HTTP " + r.code
                                + " → " + truncate(r.body, 500));
                        onGetKeyError("Server phản hồi không hợp lệ (HTTP " + r.code + "). Thử lại sau.");
                        return;
                    }
                }

                if ("success".equals(o.optString("status"))) {
                    String bypassUrl = o.optString("shortenedUrl", "");
                    if (bypassUrl.isEmpty()) bypassUrl = o.optString("link", "");
                    if (bypassUrl.isEmpty()) {
                        onGetKeyError("Phản hồi server thiếu link vượt, hãy thử lại!");
                        return;
                    }
                    final String urlToOpen = bypassUrl;
                    runOnUiThread(() -> {
                        hideLoading();
                        fetchingKey = false;
                        setBtn(true);
                        enableGetKey(true);
                        if (openUrl(urlToOpen)) {
                            // Key chỉ hiện trên trang KẾT QUẢ sau khi vượt link xong
                            setStatus("Vượt link xong → copy key trên trang KẾT QUẢ → dán vào ô nhập!",
                                    Color.parseColor("#33D69F"));
                            showNotif("🔗  Vượt link trong trình duyệt, copy key rồi dán vào ô nhập!", true);
                            etKey.setHint("Dán key từ trang kết quả vào đây…");
                            etKey.requestFocus();
                        } else {
                            setStatus("Không mở được trang vượt link, hãy thử lại!",
                                    Color.parseColor("#F2545B"));
                            showNotif("❌  Không mở được trang vượt link!", false);
                        }
                    });
                } else {
                    onGetKeyError(o.optString("message", "Không tạo được link vượt, hãy thử lại!"));
                }
            } catch (Exception e) {
                onGetKeyError("Lỗi kết nối / timeout. Kiểm tra mạng.");
            }
        }).start();
    }

    private void onGetKeyError(String msg) {
        fetchingKey = false;
        runOnUiThread(() -> {
            hideLoading();
            setStatus(msg, Color.parseColor("#F2545B"));
            showNotif("❌  " + msg, false);
            setBtn(true);
            enableGetKey(true);
        });
    }

    private void enableGetKey(boolean enabled) {
        if (btnGetKey == null) return;
        btnGetKey.setEnabled(enabled);
        btnGetKey.animate().alpha(enabled ? 1f : 0.55f).setDuration(200).start();
    }

    // ══════════════════════════════════════════════════════════
    //  HELPERS
    // ══════════════════════════════════════════════════════════
    private void setBtn(boolean enabled) {
        if (btnLogin == null) return;
        runOnUiThread(() -> {
            btnLogin.setEnabled(enabled);
            btnLogin.animate().alpha(enabled ? 1f : 0.55f).setDuration(200).start();
        });
    }

    private void setStatus(String msg, int color) {
        runOnUiThread(() -> {
            if (tvStatus == null) return;
            tvStatus.setText(msg);
            tvStatus.setTextColor(color);
        });
    }

    private void showLoading() { showLoading(true); }

    private void showLoading(boolean withDots) {
        runOnUiThread(() -> {
            if (loadingOverlay == null) return;
            loadingOverlay.setAlpha(0f);
            loadingOverlay.setVisibility(View.VISIBLE);
            loadingOverlay.animate().alpha(1f).setDuration(220).start();

            if (loadingCard != null) {
                loadingCard.setScaleX(0.85f);
                loadingCard.setScaleY(0.85f);
                loadingCard.setAlpha(0f);
                loadingCard.animate().scaleX(1f).scaleY(1f).alpha(1f).setDuration(320)
                        .setInterpolator(new OvershootInterpolator(1.3f)).start();
            }
            startSpinner();
            if (withDots) startDots(); else stopDots();
        });
    }

    private void setLoadingText(String label, String wait) {
        runOnUiThread(() -> {
            if (loadingLabel != null) loadingLabel.setText(label);
            if (waitingLabel != null) waitingLabel.setText(wait);
        });
    }

    private void hideLoading() {
        runOnUiThread(() -> {
            if (loadingOverlay == null) return;
            stopSpinner();
            loadingOverlay.animate().alpha(0f).setDuration(220)
                    .withEndAction(() -> loadingOverlay.setVisibility(View.GONE)).start();
            stopDots();
        });
    }

    /** Vòng tròn loading quay liên tục + chấm quỹ đạo + tâm phát sáng */
    private void startSpinner() {
        try {
            if (ringRotator == null && loadingRing != null) {
                ringRotator = ObjectAnimator.ofFloat(loadingRing, "rotation", 0f, 360f);
                ringRotator.setDuration(900);
                ringRotator.setInterpolator(new LinearInterpolator());
                ringRotator.setRepeatCount(ObjectAnimator.INFINITE);
            }
            if (orbitRotator == null && orbitWrap != null) {
                orbitRotator = ObjectAnimator.ofFloat(orbitWrap, "rotation", 360f, 0f);
                orbitRotator.setDuration(1500);
                orbitRotator.setInterpolator(new LinearInterpolator());
                orbitRotator.setRepeatCount(ObjectAnimator.INFINITE);
            }
            if (centerPulse == null && loadingCenter != null) {
                centerPulse = ObjectAnimator.ofFloat(loadingCenter, "scaleX", 0.65f, 1.3f);
                centerPulse.setDuration(650);
                centerPulse.setRepeatCount(ObjectAnimator.INFINITE);
                centerPulse.setRepeatMode(ObjectAnimator.REVERSE);
                centerPulseYAnim = ObjectAnimator.ofFloat(loadingCenter, "scaleY", 0.65f, 1.3f);
                centerPulseYAnim.setDuration(650);
                centerPulseYAnim.setRepeatCount(ObjectAnimator.INFINITE);
                centerPulseYAnim.setRepeatMode(ObjectAnimator.REVERSE);
            }
            if (ringRotator != null) ringRotator.start();
            if (orbitRotator != null) orbitRotator.start();
            if (centerPulse != null) centerPulse.start();
            if (centerPulseYAnim != null) centerPulseYAnim.start();
        } catch (Exception ignored) {}
    }

    private void stopSpinner() {
        if (ringRotator != null) ringRotator.cancel();
        if (orbitRotator != null) orbitRotator.cancel();
        if (centerPulse != null) centerPulse.cancel();
        if (centerPulseYAnim != null) centerPulseYAnim.cancel();
    }

    private void startDots() {
        stopDots();
        dotCount = 0;
        dotRunnable = new Runnable() {
            @Override public void run() {
                dotCount = (dotCount % 3) + 1;
                StringBuilder dots = new StringBuilder();
                for (int i = 0; i < dotCount; i++) dots.append(".");
                if (waitingLabel != null) waitingLabel.setText("Vui lòng chờ" + dots);
                uiHandler.postDelayed(this, 500);
            }
        };
        uiHandler.post(dotRunnable);
    }

    private void stopDots() {
        if (dotRunnable != null) { uiHandler.removeCallbacks(dotRunnable); dotRunnable = null; }
        if (waitingLabel != null) waitingLabel.setText("Vui lòng chờ…");
    }

    private void showNotif(String msg, boolean ok) {
        runOnUiThread(() -> {
            if (notifBar == null || notifText == null || notifIcon == null) return;
            notifText.setText(msg);

            GradientDrawable bg = new GradientDrawable();
            bg.setShape(GradientDrawable.RECTANGLE);
            bg.setCornerRadius(dp(14));
            if (ok) {
                bg.setColor(Color.parseColor("#0F2B21"));
                bg.setStroke(dp(1), Color.parseColor("#33D69F"));
                notifIcon.setImageResource(android.R.drawable.checkbox_on_background);
                notifIcon.setColorFilter(Color.parseColor("#33D69F"));
            } else {
                bg.setColor(Color.parseColor("#2B1214"));
                bg.setStroke(dp(1), Color.parseColor("#F2545B"));
                notifIcon.setImageResource(android.R.drawable.ic_dialog_alert);
                notifIcon.setColorFilter(Color.parseColor("#F2545B"));
            }
            notifBar.setBackground(bg);
            notifBar.setVisibility(View.VISIBLE);
            notifBar.setTranslationY(dp(100));
            notifBar.animate().translationY(0).setDuration(380)
                    .setInterpolator(new DecelerateInterpolator()).start();
            uiHandler.postDelayed(this::hideNotif, 4000);
        });
    }

    private void hideNotif() {
        runOnUiThread(() -> {
            if (notifBar == null) return;
            notifBar.animate().translationY(dp(100)).setDuration(300)
                    .setInterpolator(new AccelerateInterpolator())
                    .withEndAction(() -> notifBar.setVisibility(View.GONE)).start();
        });
    }

    /** Shake animation for empty input */
    private void shake(View v) {
        v.animate().translationX(-dp(8)).setDuration(60)
                .withEndAction(() -> v.animate().translationX(dp(8)).setDuration(60)
                        .withEndAction(() -> v.animate().translationX(-dp(5)).setDuration(50)
                                .withEndAction(() -> v.animate().translationX(0).setDuration(50).start())
                                .start()).start()).start();
    }

    private String getAndroidId() {
        return Settings.Secure.getString(getContentResolver(), Settings.Secure.ANDROID_ID).toUpperCase();
    }

    private String enc(String s) {
        try { return URLEncoder.encode(s, "UTF-8"); } catch (Exception e) { return s; }
    }

    /** Kết quả request: mã HTTP + thân phản hồi đã làm sạch. */
    private static class HttpResult {
        final int    code;
        final String body;
        HttpResult(int code, String body) {
            this.code = code;
            this.body = body;
        }
    }

    /**
     * GET HTTP an toàn cho host miễn phí:
     *  - UA trình duyệt thật (tránh interstitial HTML trả về HTTP 200)
     *  - Tự theo redirect + giữ cookie
     *  - Yêu cầu không nén + tự giải nén gzip nếu server vẫn nén
     *  - Cắt BOM / khoảng trắng để JSON parse được
     */
    private HttpResult performRequest(String url) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        try {
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(NET_TIMEOUT);
            conn.setReadTimeout(NET_TIMEOUT);
            conn.setInstanceFollowRedirects(true);
            conn.setRequestProperty("User-Agent", BROWSER_UA);
            conn.setRequestProperty("Accept", "application/json, text/plain, */*");
            conn.setRequestProperty("Accept-Language", "vi,en;q=0.8");
            conn.setRequestProperty("Accept-Encoding", "identity");
            conn.setRequestProperty("X-Requested-With", "XMLHttpRequest");
            conn.setRequestProperty("Connection", "close");

            int code = conn.getResponseCode();
            java.io.InputStream is = conn.getErrorStream() != null
                    ? conn.getErrorStream() : conn.getInputStream();
            byte[] raw = readAllBytes(is);
            is.close();

            // Phòng trường hợp server vẫn nén dù đã yêu cầu identity
            String enc = conn.getContentEncoding();
            boolean gzipMagic = raw.length >= 2
                    && (raw[0] & 0xFF) == 0x1F && (raw[1] & 0xFF) == 0x8B;
            if (gzipMagic || (enc != null && enc.toLowerCase().contains("gzip"))) {
                raw = gunzip(raw);
            }
            return new HttpResult(code, decodeBody(raw));
        } finally {
            conn.disconnect();
        }
    }

    private byte[] readAllBytes(java.io.InputStream is) throws Exception {
        java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = is.read(buf)) != -1) bos.write(buf, 0, n);
        return bos.toByteArray();
    }

    /**
     * POST JSON an toàn (dùng cho /api/verify):
     *  - UA trình duyệt thật + giữ cookie
     *  - Yêu cầu không nén + tự giải nén gzip nếu server vẫn nén
     *  - Cắt BOM / khoảng trắng để JSON parse được
     */
    private HttpResult performPostJson(String url, String jsonBody) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        try {
            conn.setRequestMethod("POST");
            conn.setConnectTimeout(NET_TIMEOUT);
            conn.setReadTimeout(NET_TIMEOUT);
            conn.setInstanceFollowRedirects(true);
            conn.setDoOutput(true);
            conn.setRequestProperty("User-Agent", BROWSER_UA);
            conn.setRequestProperty("Accept", "application/json, text/plain, */*");
            conn.setRequestProperty("Accept-Language", "vi,en;q=0.8");
            conn.setRequestProperty("Accept-Encoding", "identity");
            conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
            conn.setRequestProperty("X-Requested-With", "XMLHttpRequest");
            conn.setRequestProperty("Connection", "close");

            java.io.OutputStream os = conn.getOutputStream();
            os.write(jsonBody.getBytes("UTF-8"));
            os.flush();
            os.close();

            int code = conn.getResponseCode();
            java.io.InputStream is = conn.getErrorStream() != null
                    ? conn.getErrorStream() : conn.getInputStream();
            byte[] raw = readAllBytes(is);
            is.close();

            String enc = conn.getContentEncoding();
            boolean gzipMagic = raw.length >= 2
                    && (raw[0] & 0xFF) == 0x1F && (raw[1] & 0xFF) == 0x8B;
            if (gzipMagic || (enc != null && enc.toLowerCase().contains("gzip"))) {
                raw = gunzip(raw);
            }
            return new HttpResult(code, decodeBody(raw));
        } finally {
            conn.disconnect();
        }
    }

    private byte[] gunzip(byte[] data) throws Exception {
        java.util.zip.GZIPInputStream gz =
                new java.util.zip.GZIPInputStream(new java.io.ByteArrayInputStream(data));
        try {
            return readAllBytes(gz);
        } finally {
            gz.close();
        }
    }

    /** UTF-8 + bỏ BOM + trim — một số host chèn BOM làm JSONObject văng lỗi. */
    private String decodeBody(byte[] raw) throws Exception {
        String s = new String(raw, "UTF-8");
        if (!s.isEmpty() && s.charAt(0) == '\uFEFF') s = s.substring(1);
        return s.trim();
    }

    /** Message lỗi từ server (nếu body là JSON) hoặc thông báo HTTP chung. */
    private String serverErrorMessage(int code, String body) {
        JSONObject err = tryExtractJson(body);
        if (err != null && err.has("message")) {
            String m = err.optString("message");
            if (!m.isEmpty()) return m;
        }
        return "Lỗi server (HTTP " + code + ")";
    }

    private String truncate(String s, int max) {
        if (s == null) return "null";
        return s.length() <= max ? s : s.substring(0, max) + "…";
    }

    /**
     * Fallback bền: tách JSON ra khỏi HTML/warning host miễn phí chèn phía trước/sau.
     * Tìm `{` đầu tiên rồi dò ngoặc cân bằng có hiểu chuỗi (tránh lỗi khi HTML
     * sau JSON chứa thêm `{`/`}`). Nếu không cân bằng được thì quay về lấy
     * giữa `{` đầu và `}` cuối.
     */
    private JSONObject tryExtractJson(String raw) {
        if (raw == null) return null;
        int start = raw.indexOf('{');
        if (start < 0) {
            // Có thể gói trong mảng: [ {...} ]
            int arr = raw.indexOf('[');
            if (arr >= 0 && raw.lastIndexOf(']') > arr) {
                try {
                    return new JSONObject("{\"arr\":" + raw.substring(arr, raw.lastIndexOf(']') + 1) + "}");
                } catch (Exception ignored) {}
            }
            return null;
        }
        // Dò ngoặc cân bằng, hiểu dấu nháy và escape
        int depth = 0;
        boolean inStr = false, esc = false;
        for (int i = start; i < raw.length(); i++) {
            char c = raw.charAt(i);
            if (inStr) {
                if (esc) esc = false;
                else if (c == '\\') esc = true;
                else if (c == '"') inStr = false;
                continue;
            }
            if (c == '"') { inStr = true; continue; }
            if (c == '{') depth++;
            else if (c == '}') {
                depth--;
                if (depth == 0) {
                    try {
                        return new JSONObject(raw.substring(start, i + 1));
                    } catch (Exception ignored) { return null; }
                }
            }
        }
        // Legacy: cắt giữa { đầu và } cuối cùng
        int end = raw.lastIndexOf('}');
        if (end > start) {
            try {
                return new JSONObject(raw.substring(start, end + 1));
            } catch (Exception ignored) {}
        }
        return null;
    }

    /** Mở trang vượt link; trả về true nếu mở thành công */
    private boolean openUrl(String url) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    private void goMain() {
        startActivity(new Intent(this, MainActivity.class));
        finish();
    }

    // ── Layout param helpers ──────────────────────────────────
    private int dp(float v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
