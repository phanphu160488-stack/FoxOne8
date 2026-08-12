package com.foxone.server;

import android.annotation.SuppressLint;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.util.Base64;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.UUID;

import javax.crypto.Cipher;
import javax.crypto.spec.SecretKeySpec;

/**
 * LoginActivity — Đăng nhập / xác thực key qua API của Fox One.
 *
 * Endpoint: POST {BASE_URL}/api/verify
 * Body    : {"key":"...", "hwid":"..."}  (JSON, bắt buộc)
 *
 * Phản hồi:
 *   status = "success"      → key hợp lệ, thiết bị đã được đăng ký / kiểm tra
 *   status = "invalid"      → key không tồn tại
 *   status = "expired"      → key hết hạn
 *   status = "device_limit" → hết slot thiết bị
 *   status = "locked"       → key bị khóa
 *   status = "error"        → thiếu key/hwid
 *
 * Kèm theo: message, time_left, expiry_timestamp, expiry_str,
 *           is_permanent, is_new_device
 */
public class LoginActivity extends AppCompatActivity {

    // ── CẤU HÌNH ──
    private static final String BASE_URL = "https://foxoneserver.com"; // Đổi thành domain thật của bạn
    private static final String HWID_SEED = "FOXONE_SERVER_2026";

    private EditText etKey, etEmail;
    private Button btnLogin;
    private ProgressBar progress;
    private TextView tvStatus;
    private SharedPreferences prefs;

    private String hardwareId;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_login);

        etKey = findViewById(R.id.etKey);
        etEmail = findViewById(R.id.etEmail);
        btnLogin = findViewById(R.id.btnLogin);
        progress = findViewById(R.id.progress);
        tvStatus = findViewById(R.id.tvStatus);

        prefs = getSharedPreferences("foxone_login", Context.MODE_PRIVATE);
        hardwareId = getStableHardwareId(this);

        btnLogin.setOnClickListener(v -> doLogin());

        // Nhớ key đã đăng nhập
        String savedKey = prefs.getString("key", "");
        if (!savedKey.isEmpty()) {
            etKey.setText(savedKey);
            doLogin();
        }
    }

    private void doLogin() {
        final String key = etKey.getText().toString().trim();
        if (key.isEmpty()) {
            toast("Vui lòng nhập key");
            return;
        }

        setLoading(true);
        tvStatus.setText("Đang xác thực key...");

        new Thread(() -> verifyWithServer(key)).start();
    }

    private void verifyWithServer(String key) {
        try {
            JSONObject body = new JSONObject();
            body.put("key", key);
            body.put("hwid", hardwareId);

            String resp = postJson(BASE_URL + "/api/verify", body.toString());
            JSONObject j = new JSONObject(resp);

            runOnUiThread(() -> handleResponse(j));
        } catch (Exception e) {
            runOnUiThread(() -> {
                setLoading(false);
                tvStatus.setText("Lỗi kết nối máy chủ");
                toast("Không thể kết nối server: " + e.getMessage());
            });
        }
    }

    @SuppressLint("ApplySharedPref")
    private void handleResponse(JSONObject j) {
        setLoading(false);
        try {
            String status = j.optString("status", "");

            switch (status) {
                case "success":
                    prefs.edit()
                            .putString("key", etKey.getText().toString().trim())
                            .putString("time_left", j.optString("time_left", "∞"))
                            .putLong("expiry_timestamp", j.optLong("expiry_timestamp", -1))
                            .putString("expiry_str", j.optString("expiry_str", "Vĩnh Viễn"))
                            .putBoolean("is_permanent", j.optBoolean("is_permanent", false))
                            .putBoolean("is_new_device", j.optBoolean("is_new_device", false))
                            .apply();

                    tvStatus.setText("Key hợp lệ ✔");
                    toast(j.optString("message", "Đăng nhập thành công"));

                    startActivity(new Intent(LoginActivity.this, MainActivity.class));
                    finish();
                    break;

                case "invalid":
                    tvStatus.setText("✘ Key không tồn tại");
                    toast(j.optString("message", "Key không tồn tại trên hệ thống"));
                    break;

                case "expired":
                    tvStatus.setText("✘ Key đã hết hạn");
                    toast(j.optString("message", "Key đã hết hạn"));
                    break;

                case "device_limit":
                    tvStatus.setText("✘ Hết thiết bị");
                    toast(j.optString("message", "Đã đạt giới hạn thiết bị"));
                    break;

                case "locked":
                    tvStatus.setText("✘ Key bị khóa");
                    toast(j.optString("message", "Key đã bị khóa"));
                    break;

                default:
                    tvStatus.setText("✘ " + j.optString("message", "Lỗi không xác định"));
                    toast(j.optString("message", "Lỗi không xác định"));
                    break;
            }
        } catch (JSONException e) {
            tvStatus.setText("Lỗi đọc dữ liệu server");
        }
    }

    // ── HTTP POST JSON ──
    private String postJson(String urlStr, String body) throws Exception {
        URL url = new URL(urlStr);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setRequestProperty("Accept", "application/json");
        conn.setDoOutput(true);
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(15000);

        OutputStream os = conn.getOutputStream();
        os.write(body.getBytes(StandardCharsets.UTF_8));
        os.flush();
        os.close();

        int code = conn.getResponseCode();
        InputStream is = (code >= 200 && code < 400) ? conn.getInputStream() : conn.getErrorStream();
        BufferedReader r = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8));
        StringBuilder sb = new StringBuilder();
        String line;
        while ((line = r.readLine()) != null) sb.append(line);
        r.close();
        conn.disconnect();
        return sb.toString();
    }

    // ── HWID cố định thiết bị ──
    @SuppressWarnings("deprecation")
    private String getStableHardwareId(Context ctx) {
        try {
            String androidId = Settings.Secure.getString(ctx.getContentResolver(), Settings.Secure.ANDROID_ID);
            String raw = (androidId == null ? "FOX" : androidId) + "|" + Build.MODEL + "|" + Build.BOARD + "|" + HWID_SEED;

            byte[] rawBytes = raw.getBytes(StandardCharsets.UTF_8);
            byte[] seedBytes = sha256(HWID_SEED).getBytes(StandardCharsets.UTF_8);
            SecretKeySpec spec = new SecretKeySpec(seedBytes, 0, 16, "AES");
            Cipher cipher = Cipher.getInstance("AES");
            cipher.init(Cipher.ENCRYPT_MODE, spec);
            byte[] enc = cipher.doFinal(rawBytes);

            String b64 = Base64.encodeToString(enc, Base64.NO_WRAP).replaceAll("[^A-Za-z0-9]", "");
            return (b64.length() >= 32) ? b64.substring(0, 32) : sha256(raw).substring(0, 32);
        } catch (Exception e) {
            // Fallback ổn định
            return sha256(rawFallback(ctx)).substring(0, 32);
        }
    }

    private String rawFallback(Context ctx) {
        String androidId = Settings.Secure.getString(ctx.getContentResolver(), Settings.Secure.ANDROID_ID);
        return (androidId == null ? "FOX" : androidId) + "|" + Build.MODEL + "|" + Build.BOARD;
    }

    private static String sha256(String input) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] hash = md.digest(input.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (byte b : hash) {
                sb.append(String.format("%02x", b));
            }
            return sb.toString();
        } catch (Exception e) {
            return UUID.randomUUID().toString().replace("-", "");
        }
    }

    // ── UI helpers ──
    private void setLoading(boolean loading) {
        btnLogin.setEnabled(!loading);
        progress.setVisibility(loading ? View.VISIBLE : View.GONE);
        if (!loading) tvStatus.setText("");
    }

    private void toast(String msg) {
        Toast.makeText(this, msg, Toast.LENGTH_LONG).show();
    }
}
