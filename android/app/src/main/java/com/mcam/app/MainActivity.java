package com.mcam.app;

import android.Manifest;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.provider.Settings;
import android.graphics.ImageFormat;
import android.graphics.Rect;
import android.graphics.YuvImage;
import android.media.Image;
import android.os.Bundle;
import android.util.Size;
import android.view.WindowManager;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.ComponentActivity;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.camera.core.CameraSelector;
import androidx.camera.core.ImageAnalysis;
import androidx.camera.core.ImageProxy;
import androidx.camera.core.Preview;
import androidx.camera.core.resolutionselector.ResolutionSelector;
import androidx.camera.core.resolutionselector.ResolutionStrategy;
import androidx.camera.lifecycle.ProcessCameraProvider;
import androidx.camera.view.PreviewView;
import androidx.core.content.ContextCompat;

import com.google.common.util.concurrent.ListenableFuture;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

// ponytail: 前台 Activity + 亮屏保活就夠測 M1，ForegroundService 以後加
public class MainActivity extends ComponentActivity {
    private static final int PORT = 8080;
    private ActivityResultLauncher<String> reqCamera;
    private MjpegServer server;
    private ExecutorService cameraIo;
    private PreviewView previewView;
    private ImageView recvView;
    private TextView hint;
    private volatile Thread recvThread;
    private TextView usbStatus;
    private TextView wifiStatus;
    private Button previewBtn;
    private Button wifiModeBtn;
    private Button freezeBtn;
    private boolean frozen = false;
    private SharedPreferences prefs;
    private boolean wifiRecv = false;
    private ProcessCameraProvider provider;
    private Preview previewUseCase;
    private ImageAnalysis analysisUseCase;
    private boolean previewOn = true;
    // ponytail: 轉向 Activity 重建，同進程用 static 接住狀態；真離開(isFinishing)才清
    private static boolean keptPreviewOn = true;
    private static boolean keptFrozen = false;
    private static byte[] keptJpeg = null;
    private static int keptW, keptH;
    private String usbUrl;
    private String wifiUrl;
    private String wifiRecvUrl;
    private String wifiSendUrl;
    // ponytail: 轉向重建同進程，server 不停播不斷流；真離開才停
    private static MjpegServer keptServer;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        setContentView(R.layout.activity_main);
        previewView = findViewById(R.id.preview);
        recvView = findViewById(R.id.recvView);
        hint = findViewById(R.id.previewHint);
        usbStatus = findViewById(R.id.usbStatus);
        wifiStatus = findViewById(R.id.wifiStatus);
        prefs = getSharedPreferences("mCam", MODE_PRIVATE);
        wifiRecv = prefs.getBoolean("wifi_recv", false);
        wifiRecvUrl = prefs.getString("wifi_recv_url", null);
        wifiSendUrl = prefs.getString("wifi_send_url", null);
        usbStatus.setOnClickListener(v -> editSendCode(usbUrl));  // ponytail: USB 跟 WiFi 同一組碼，點哪行都是改碼+複製該行
        wifiStatus.setOnClickListener(v -> {
            if (wifiRecv) editRecvUrl();
            else editSendCode(wifiUrl);  // ponytail: 傳送模式點 WiFi 行=改自家碼，存了顯示即真相
        });
        wifiModeBtn = findViewById(R.id.wifiModeBtn);
        wifiModeBtn.setOnClickListener(v -> toggleWifiMode());
        previewBtn = findViewById(R.id.previewBtn);
        previewBtn.setOnClickListener(v -> togglePreview());
        freezeBtn = findViewById(R.id.freezeBtn);
        freezeBtn.setOnClickListener(v -> toggleFreeze());
        findViewById(R.id.tetherBtn).setOnClickListener(v -> openTetherSettings());

        cameraIo = Executors.newSingleThreadExecutor();
        // ponytail: 舊 onRequestPermissionsResult 已 deprecated，改 Result API
        reqCamera = registerForActivityResult(new ActivityResultContracts.RequestPermission(), ok -> {
            if (Boolean.TRUE.equals(ok)) startCamera();
            else wifiStatus.setText("沒相機權限就沒畫面");
        });
        if (server == null) server = keptServer;  // ponytail: 轉向接回舊 server，PC 不斷線
        previewOn = keptPreviewOn;  // ponytail: 轉向前關了預覽，重建不自動開
        refreshStatus();
        applyMode();  // ponytail: 傳送才開 server；接收不開，不留凍結幀害 PC 的 cap.read 卡死

        if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            startCamera();
        } else {
            reqCamera.launch(Manifest.permission.CAMERA);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (wifiStatus != null) refreshStatus();  // ponytail: 從系統設定頁回來順手更新，不用重開 App
    }

    // ponytail: 第三方 App 拿不到 TETHER_PRIVILEGED，直接開關系統不給；
    // 只能一鍵跳系統共享頁讓使用者撥， intent 逐級 fallback 保各牌子都跳得進去
    private void openTetherSettings() {
        String[] actions = {
                "android.settings.TETHER_SETTINGS",  // ponytail: 無 SDK 常數，只能寫死字串
                Settings.ACTION_WIRELESS_SETTINGS,
                Settings.ACTION_SETTINGS};
        for (String a : actions) {
            try {
                startActivity(new Intent(a));
                return;
            } catch (Exception ignored) {
            }
        }
    }

    private void refreshStatus() {
        String sc = prefs.getString("wifi_send_code", "");
        String suffix = (sc != null && sc.matches("\\d{1,6}")) ? "/" + sc : "";
        usbUrl = "http://192.168.42.129:8080/v" + suffix;  // ponytail: USB 跟 WiFi 同一組碼，碼不對 server 照 404
        usbStatus.setText("USB:"+usbUrl);
        wifiModeBtn.setText(wifiRecv ? "WiFi 接收" : "WiFi 傳送");
        if (wifiRecv) {
            wifiStatus.setText(wifiRecvUrl != null ? "WiFi:"+wifiRecvUrl : "WiFi:點此輸入對方 URL");
            return;
        }
        String w = wifiIp();
        if (w != null) {
            wifiUrl = "http://" + w + ":8080/v" + suffix;
            // ponytail: 記住最後一次抓到的傳送 ip，切去接收再回來沒 WiFi 時還能顯示原來的
            wifiSendUrl = wifiUrl;
            prefs.edit().putString("wifi_send_url", wifiSendUrl).apply();
        } else {
            // ponytail: 存的可能是別台殘留，只取 host 重組
            String hip = ipOf(wifiSendUrl);
            wifiUrl = hip != null ? "http://" + hip + ":8080/v" : null;
        }
        wifiStatus.setText(wifiUrl != null ? "WiFi:"+wifiUrl : "WiFi:行動網路不能直連");
    }

    private void toggleWifiMode() {
        wifiRecv = !wifiRecv;
        prefs.edit().putBoolean("wifi_recv", wifiRecv).apply();
        refreshStatus();
        applyMode();
    }

    // ponytail: 傳送=綁相機推流，接收=解綁相機看對方；各做各的不互卡
    // ponytail: 接收順手關 server，凍結連線不斷 PC 會卡死在 cap.read，連中斷都按不了
    private void applyMode() {
        if (wifiRecv) {
            stopServer();
            try {
                if (provider != null) provider.unbindAll();
            } catch (Exception ignored) {
            }
            frozen = false;
            if (freezeBtn != null) {
                freezeBtn.setText("凍結");
                freezeBtn.setEnabled(false);
            }
            keptFrozen = false;  // ponytail: 接收模式本來就不能凍，殘留一併清
            keptJpeg = null;
            previewView.setVisibility(View.INVISIBLE);
            recvView.setImageBitmap(null);
            recvView.setVisibility(View.VISIBLE);
            previewBtn.setEnabled(false);
            syncHint();
            startRecv();
        } else {
            stopRecv();
            frozen = false;
            if (freezeBtn != null) {
                freezeBtn.setText("凍結");
                freezeBtn.setEnabled(true);
            }
            recvView.setVisibility(View.GONE);
            recvView.setImageBitmap(null);
            previewBtn.setEnabled(true);
            previewView.setVisibility(previewOn ? View.VISIBLE : View.INVISIBLE);
            syncHint();
            if (!ensureServer()) {
                wifiStatus.setText("port 8080 開不起來");
            } else if (keptFrozen && keptJpeg != null) {
                // ponytail: 轉向前凍著，重建把舊幀灌回新 server 繼續凍，PC 不閃一下活的
                server.pushFrame(keptJpeg, keptW, keptH);
                server.setFrozen(true);
                frozen = true;
                freezeBtn.setText("解凍");
                if (!showFrozen(keptJpeg)) {
                    frozen = false;
                    keptFrozen = false;
                    keptJpeg = null;
                    server.setFrozen(false);
                    freezeBtn.setText("凍結");
                }
            }
            if (provider != null && previewUseCase != null && analysisUseCase != null) {
                try {
                    provider.unbindAll();
                    if (previewOn) {
                        provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, previewUseCase, analysisUseCase);
                    } else {
                        provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, analysisUseCase);
                    }
                } catch (Exception e) {
                    Toast.makeText(this, "相機重綁失敗: " + e.getMessage(), Toast.LENGTH_SHORT).show();
                }
            }
        }
    }

    private void stopServer() {
        if (server != null) {
            server.stop();
            server = null;
        }
        keptServer = null;
    }

    private boolean ensureServer() {
        if (wifiRecv) return false;
        if (server == null) server = new MjpegServer(PORT);
        server.setCode(prefs.getString("wifi_send_code", ""));  // ponytail: 碼是門禁，開播即按碼放行
        try {
            server.start();
            return true;
        } catch (java.io.IOException e) {
            server = null;
            Toast.makeText(this, "port 8080 開不起來: " + e.getMessage(), Toast.LENGTH_SHORT).show();
            return false;
        }
    }

    // ponytail: 黑屏中央灰字只用一個 TextView；接收來幀就藏，斷了不重叫（要看連線狀態點下面 URL）
    private void syncHint() {
        if (wifiRecv) {
            hint.setText("接收中...");
            hint.setVisibility(View.VISIBLE);
        } else if (!previewOn) {
            hint.setText("預覽已關閉");
            hint.setVisibility(View.VISIBLE);
        } else {
            hint.setVisibility(View.GONE);
        }
    }

    private void startRecv() {
        stopRecv();
        if (wifiRecvUrl == null) return;
        final String url = wifiRecvUrl;
        recvThread = new Thread(() -> recvLoop(url), "mjpeg-recv");
        recvThread.start();
    }

    private void stopRecv() {
        Thread t = recvThread;
        recvThread = null;
        if (t != null) t.interrupt();
    }

    private void recvLoop(String url) {
        Thread me = Thread.currentThread();
        boolean told = false;
        byte[] buf = new byte[8 << 20];
        byte[] chunk = new byte[8192];
        while (recvThread == me) {
            java.net.HttpURLConnection c = null;
            try {
                c = (java.net.HttpURLConnection) new java.net.URL(url).openConnection();
                c.setConnectTimeout(3000);
                c.setReadTimeout(5000);
                int code = c.getResponseCode();
                if (code != 200) {
                    if (!told) {
                        told = true;
                        final int fc = code;
                        runOnUiThread(() -> Toast.makeText(this,
                                "HTTP " + fc + "：路徑不對，IP/碼重打", Toast.LENGTH_SHORT).show());
                    }
                    try {
                        Thread.sleep(2000);
                    } catch (InterruptedException e) {
                        return;
                    }
                    continue;
                }
                java.io.InputStream in = c.getInputStream();
                int n = 0;
                while (recvThread == me) {
                    int r = in.read(chunk);
                    if (r < 0) break;
                    if (n + r > buf.length) n = 0;  // ponytail: 單幀不可能 8MB，爆了就丟掉重攢
                    System.arraycopy(chunk, 0, buf, n, r);
                    n += r;
                    // ponytail: 解碼必須從 SOI 起跳，multipart 檔頭餵進去 BitmapFactory 直接回 null 全黑
                    int s = findJpegStart(buf, n);
                    if (s < 0) continue;
                    if (s > 0) {
                        System.arraycopy(buf, s, buf, 0, n - s);
                        n -= s;
                        s = 0;
                    }
                    int e = findJpegEnd(buf, s, n);
                    if (e > 0) {
                        // ponytail: 緊鄰下一幀已完整才丟舊的、播最新；沒攢出整幀就照播，跟得上不跳、跟不上跳整幀
                        boolean newer = false;
                        for (int i = e; i + 1 < n; i++) {
                            if (buf[i] == (byte) 0xFF && buf[i + 1] == (byte) 0xD8) {
                                newer = findJpegEnd(buf, i, n) > 0;
                                break;
                            }
                        }
                        final android.graphics.Bitmap bm = newer ? null :
                                android.graphics.BitmapFactory.decodeByteArray(buf, s, e - s);
                        int rest = n - e;
                        System.arraycopy(buf, e, buf, 0, rest);
                        n = rest;
                        if (newer) continue;
                        if (bm != null) runOnUiThread(() -> {
                            recvView.setImageBitmap(bm);
                            hint.setVisibility(View.GONE);
                        });
                    }
                }
            } catch (java.net.SocketTimeoutException e) {
                if (!told) {
                    told = true;
                    runOnUiThread(() -> Toast.makeText(this, "連線逾時：IP 錯或防火牆擋了", Toast.LENGTH_SHORT).show());
                }
            } catch (Exception e) {
                if (!told) {
                    told = true;
                    runOnUiThread(() -> Toast.makeText(this, "連不上 " + url, Toast.LENGTH_SHORT).show());
                }
            } finally {
                if (c != null) c.disconnect();
            }
            if (recvThread != me) return;
            try {
                Thread.sleep(2000);  // ponytail: 斷線 2 秒重試一次，不狂刷
            } catch (InterruptedException e) {
                return;
            }
        }
    }

    // ponytail: 不解析 multipart，直接找 JPEG 頭尾，PC/手機 server 通吃
    static int findJpegStart(byte[] b, int n) {
        for (int i = 0; i + 1 < n; i++) {
            if (b[i] == (byte) 0xFF && b[i + 1] == (byte) 0xD8) return i;
        }
        return -1;
    }

    static int findJpegEnd(byte[] b, int s, int n) {
        for (int i = s + 2; i + 1 < n; i++) {
            if (b[i] == (byte) 0xFF && b[i + 1] == (byte) 0xD9) return i + 2;
        }
        return -1;
    }

    // ponytail: 傳送端點 WiFi/USB 行都是改自家碼(空=空碼)；顯示即真相，server 空碼/1~6碼按碼放行
    private void editSendCode(String copyUrl) {
        EditText et = new EditText(this);
        et.setSingleLine();
        et.setHint("留空=空碼，或 1~6 碼");
        String cur = prefs.getString("wifi_send_code", "");
        if (cur != null && !cur.isEmpty()) et.setText(cur);
        new AlertDialog.Builder(this)
                .setTitle("自家連線碼")
                .setView(et)
                .setPositiveButton("儲存", (d, w) -> {
                    String t = et.getText().toString().trim();
                    if (!t.isEmpty() && !t.matches("\\d{1,6}")) {
                        Toast.makeText(this, "只要 1~6 碼，留空=空碼", Toast.LENGTH_SHORT).show();
                        return;
                    }
                    prefs.edit().putString("wifi_send_code", t).apply();
                    if (server != null) server.setCode(t);  // ponytail: 改碼即時生效，不用重開播
                    refreshStatus();
                })
                .setNeutralButton("複製URL", (d, w) -> copyUrl(copyUrl))
                .setNegativeButton("取消", null)
                .show();
    }

    // ponytail: 三種收法：完整 URL 照用 / IP+空格+1~6碼組路徑 / 純 IP 組空碼 /v
    private void editRecvUrl() {
        EditText et = new EditText(this);
        et.setSingleLine();
        et.setHint("10.35.9.108 140023 或完整 URL");
        if (wifiRecvUrl != null) et.setText(shortRecv(wifiRecvUrl));
        new AlertDialog.Builder(this)
                .setTitle("對方連線")
                .setView(et)
                .setPositiveButton("儲存", (d, w) -> {
                    String url = recvUrlOf(et.getText().toString().trim());
                    if (url == null) {
                        Toast.makeText(this, "格式不對：IP+空格+1~6碼，如 10.35.9.108 140023", Toast.LENGTH_SHORT).show();
                        return;
                    }
                    wifiRecvUrl = url;
                    prefs.edit().putString("wifi_recv_url", wifiRecvUrl).apply();
                    refreshStatus();
                    applyMode();
                })
                .setNegativeButton("取消", null)
                .show();
    }

    // ponytail: 純函式，方便以後單測；回 null=格式不對
    static String recvUrlOf(String t) {
        if (t == null) return null;
        t = t.trim();
        if (t.startsWith("http://") || t.startsWith("https://")) return t;
        if (t.contains("/")) return "http://" + t;  // ponytail: 10.35.9.108/v/140023 這種省 scheme 的
        String[] parts = t.split("\\s+");
        String got = ipOf(parts[0]);
        if (got == null) return null;
        if (parts.length >= 2) {
            if (!parts[1].matches("\\d{1,6}")) return null;
            return "http://" + got + ":8080/v/" + parts[1];
        }
        return "http://" + got + ":8080/v";
    }

    // ponytail: 存的是完整 URL，顯示縮回短格式好抄好改
    static String shortRecv(String url) {
        if (url == null) return "";
        java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("http://([\\d.]+):8080/v/(\\d{1,6})").matcher(url.trim());
        if (m.find()) return m.group(1) + " " + m.group(2);
        return url;
    }

    static String ipOf(String s) {
        if (s == null) return null;
        java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("(\\d{1,3}(?:\\.\\d{1,3}){3})").matcher(s.trim());
        return m.find() ? m.group(1) : null;
    }

    // ponytail: 點一下複製整串 url，HINT 時無 url 就不動作
    private void copyUrl(String url) {
        if (url == null) return;
        ((ClipboardManager) getSystemService(CLIPBOARD_SERVICE))
                .setPrimaryClip(ClipData.newPlainText("url", url));
        Toast.makeText(this, "已複製 " + url, Toast.LENGTH_SHORT).show();
    }

    // ponytail: 讀不到系統 tethering state，就看 rndis/usb 網卡有沒有拿到 IPv4，夠判斷開關
    static boolean usbTetherOn() {
        try {
            for (java.util.Enumeration<java.net.NetworkInterface> e =
                 java.net.NetworkInterface.getNetworkInterfaces(); e.hasMoreElements(); ) {
                java.net.NetworkInterface ni = e.nextElement();
                String n = ni.getName().toLowerCase();
                if (!(n.contains("rndis") || n.contains("usb") || n.startsWith("eth"))) continue;
                if (!ni.isUp() || ni.isLoopback()) continue;
                for (java.util.Enumeration<java.net.InetAddress> a =
                     ni.getInetAddresses(); a.hasMoreElements(); ) {
                    java.net.InetAddress addr = a.nextElement();
                    if (!addr.isLoopbackAddress() && addr instanceof java.net.Inet4Address)
                        return true;
                }
            }
        } catch (Exception ignored) {
        }
        return false;
    }

    private void startCamera() {
        ListenableFuture<ProcessCameraProvider> f = ProcessCameraProvider.getInstance(this);
        f.addListener(() -> {
            try {
                provider = f.get();
                previewUseCase = new Preview.Builder().build();
                previewUseCase.setSurfaceProvider(previewView.getSurfaceProvider());
                analysisUseCase = new ImageAnalysis.Builder()
                        .setResolutionSelector(new ResolutionSelector.Builder()
                                .setResolutionStrategy(new ResolutionStrategy(new Size(1280, 720),
                                        ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER))
                                .build())
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .build();
                analysisUseCase.setAnalyzer(cameraIo, this::onFrame);
                provider.unbindAll();
                if (!wifiRecv) {
                    if (previewOn) {
                        provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, previewUseCase, analysisUseCase);
                    } else {
                        provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, analysisUseCase);
                    }
                }
            } catch (Exception e) {
                wifiStatus.setText("相機起不來: " + e.getMessage());
            }
        }, ContextCompat.getMainExecutor(this));
    }

    // ponytail: 只關本地 PreviewView 管線，ImageAnalysis 照推流，PC 不斷線；unbind 才真省電，INVISIBLE 留框不讓按鍵上移
    private void togglePreview() {
        if (wifiRecv) return;  // ponytail: 接收模式看的是對方，本地預覽鍵無效
        previewOn = !previewOn;
        keptPreviewOn = previewOn;
        previewBtn.setText(previewOn ? "關預覽" : "開預覽");
        previewView.setVisibility(previewOn ? View.VISIBLE : View.INVISIBLE);
        syncHint();
        if (provider == null || previewUseCase == null || analysisUseCase == null) return;
        try {
            if (previewOn) {
                provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, previewUseCase);
            } else {
                provider.unbind(previewUseCase);
            }
        } catch (Exception e) {
            Toast.makeText(this, "預覽切換失敗: " + e.getMessage(), Toast.LENGTH_SHORT).show();
        }
    }

    // ponytail: 凍結=!wifiRecv 才有效，USB/WiFi 同一 server 同一管線；overlay 重用 recvView，零新增 View
    private void toggleFreeze() {
        if (wifiRecv) return;
        frozen = !frozen;
        if (server != null) server.setFrozen(frozen);
        freezeBtn.setText(frozen ? "解凍" : "凍結");
        if (frozen) {
            byte[] fj = server != null ? server.getFrozenJpeg() : null;
            if (fj != null && showFrozen(fj)) {
                keptFrozen = true;
                keptJpeg = fj;
                keptW = server.getFrameW();
                keptH = server.getFrameH();
            } else {
                Toast.makeText(this, "還沒畫面，凍不了", Toast.LENGTH_SHORT).show();
                frozen = false;
                if (server != null) server.setFrozen(false);
                freezeBtn.setText("凍結");
            }
        } else {
            keptFrozen = false;
            keptJpeg = null;
            hideFrozen();
        }
    }

    // ponytail: 凍結圖顯示抽出來，轉向重建共用；回 false=解不出圖
    private boolean showFrozen(byte[] fj) {
        final android.graphics.Bitmap bm =
                android.graphics.BitmapFactory.decodeByteArray(fj, 0, fj.length);
        if (bm == null) return false;
        recvView.setImageBitmap(bm);
        recvView.setVisibility(View.VISIBLE);
        previewView.setVisibility(View.INVISIBLE);
        hint.setVisibility(View.GONE);
        return true;
    }

    private void hideFrozen() {
        recvView.setImageBitmap(null);
        recvView.setVisibility(View.GONE);
        previewView.setVisibility(previewOn ? View.VISIBLE : View.INVISIBLE);
        syncHint();
    }

    private void onFrame(ImageProxy proxy) {
        try {
            if (frozen) return;  // ponytail: 凍結中不壓 JPEG，server 重發快照就夠，省電
            Image img = proxy.getImage();
            if (img != null && img.getFormat() == ImageFormat.YUV_420_888) {
                int w = img.getWidth(), h = img.getHeight();
                byte[] nv21 = yuv420ToNv21(img);
                // ponytail: sensor 橫的，直屏時 rotationDegrees=90；NV21 先轉正再壓 JPEG，
                // /v /s /i 全都正，PC 不用動
                int deg = proxy.getImageInfo().getRotationDegrees();
                if (deg == 90 || deg == 270) {
                    nv21 = rotateNv21(nv21, w, h, deg);
                    int t = w;
                    w = h;
                    h = t;
                } else if (deg == 180) {
                    nv21 = rotateNv21(nv21, w, h, deg);
                }
                ByteArrayOutputStream out = new ByteArrayOutputStream(nv21.length);
                new YuvImage(nv21, ImageFormat.NV21, w, h, null)
                        .compressToJpeg(new Rect(0, 0, w, h), 90, out);
                if (server != null) server.pushFrame(out.toByteArray(), w, h);  // ponytail: 切接收在飛的幀，server 已關就丟
            }
        } finally {
            proxy.close();
        }
    }

    static byte[] rotateNv21(byte[] in, int w, int h, int deg) {
        int w2 = w / 2, h2 = h / 2;
        boolean swap = (deg == 90 || deg == 270);
        int ow = swap ? h : w, oh = swap ? w : h;
        int ow2 = ow / 2, oh2 = oh / 2;
        byte[] out = new byte[in.length];
        for (int y = 0; y < h; y++) {
            for (int x = 0; x < w; x++) {
                int ox, oy;
                if (deg == 90) {
                    ox = h - 1 - y;
                    oy = x;
                } else if (deg == 270) {
                    ox = y;
                    oy = w - 1 - x;
                } else {
                    ox = w - 1 - x;
                    oy = h - 1 - y;
                }
                out[oy * ow + ox] = in[y * w + x];
            }
        }
        int ySize = w * h, oySize = ow * oh;
        for (int y = 0; y < h2; y++) {
            for (int x = 0; x < w2; x++) {
                int ox, oy;
                if (deg == 90) {
                    ox = h2 - 1 - y;
                    oy = x;
                } else if (deg == 270) {
                    ox = y;
                    oy = w2 - 1 - x;
                } else {
                    ox = w2 - 1 - x;
                    oy = h2 - 1 - y;
                }
                out[oySize + (oy * ow2 + ox) * 2] = in[ySize + (y * w2 + x) * 2];
                out[oySize + (oy * ow2 + ox) * 2 + 1] = in[ySize + (y * w2 + x) * 2 + 1];
            }
        }
        return out;
    }

    static byte[] yuv420ToNv21(Image img) {
        int w = img.getWidth(), h = img.getHeight();
        byte[] nv21 = new byte[w * h * 3 / 2];
        ByteBuffer y = img.getPlanes()[0].getBuffer();
        int yRow = img.getPlanes()[0].getRowStride();
        for (int r = 0; r < h; r++) {
            y.position(r * yRow);
            y.get(nv21, r * w, w);
        }
        ByteBuffer u = img.getPlanes()[1].getBuffer();
        ByteBuffer v = img.getPlanes()[2].getBuffer();
        int uRow = img.getPlanes()[1].getRowStride();
        int vRow = img.getPlanes()[2].getRowStride();
        int uPix = img.getPlanes()[1].getPixelStride();
        int vPix = img.getPlanes()[2].getPixelStride();
        for (int r = 0; r < h / 2; r++) {
            for (int c = 0; c < w / 2; c++) {
                nv21[w * h + r * w + c * 2] = v.get(r * vRow + c * vPix);
                nv21[w * h + r * w + c * 2 + 1] = u.get(r * uRow + c * uPix);
            }
        }
        return nv21;
    }

    // ponytail: 只認 wlan/wifi，沒有就不顯示；行動 rmnet 是電信 NAT，PC 連不到，顯示反而誤導
    static String wifiIp() {
        try {
            java.util.Enumeration<java.net.NetworkInterface> nis =
                    java.net.NetworkInterface.getNetworkInterfaces();
            while (nis.hasMoreElements()) {
                java.net.NetworkInterface ni = nis.nextElement();
                if (!ni.isUp() || ni.isLoopback()) continue;
                String n = ni.getName().toLowerCase();
                if (!(n.contains("wlan") || n.contains("wifi") || n.contains("wlp"))) continue;
                for (java.util.Enumeration<java.net.InetAddress> a =
                     ni.getInetAddresses(); a.hasMoreElements(); ) {
                    java.net.InetAddress addr = a.nextElement();
                    if (addr.isLoopbackAddress() || !(addr instanceof java.net.Inet4Address))
                        continue;
                    String ip = addr.getHostAddress();  // ponytail: 只要 IPv4，不帶 %wlan0
                    return ip == null ? null : ip.split("%")[0];
                }
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        stopRecv();
        if (cameraIo != null) cameraIo.shutdown();
        if (isFinishing()) {  // ponytail: 真離開停播並回預設；轉向重建不清
            stopServer();
            keptPreviewOn = true;
            keptFrozen = false;
            keptJpeg = null;
        } else {
            keptServer = server;  // ponytail: 轉向 server 不停播，PC 不斷線
            server = null;
        }
    }
}
