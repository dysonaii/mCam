package com.mcam.app;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.provider.Settings;
import android.graphics.ImageFormat;
import android.graphics.Rect;
import android.graphics.YuvImage;
import android.media.Image;
import android.os.Bundle;
import android.util.Size;
import android.view.WindowManager;
import android.widget.TextView;

import androidx.activity.ComponentActivity;
import androidx.camera.core.CameraSelector;
import androidx.camera.core.ImageAnalysis;
import androidx.camera.core.ImageProxy;
import androidx.camera.core.Preview;
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
    private static final int REQ_CAMERA = 1;
    private static final int PORT = 8080;
    private MjpegServer server;
    private ExecutorService cameraIo;
    private PreviewView previewView;
    private TextView status;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        setContentView(R.layout.activity_main);
        previewView = findViewById(R.id.preview);
        status = findViewById(R.id.status);
        findViewById(R.id.tetherBtn).setOnClickListener(v -> openTetherSettings());

        cameraIo = Executors.newSingleThreadExecutor();
        server = new MjpegServer(PORT);
        try {
            server.start();
        } catch (Exception e) {
            status.setText("port 8080 開不起來: " + e.getMessage());
            return;
        }
        refreshStatus();

        if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            startCamera();
        } else {
            requestPermissions(new String[]{Manifest.permission.CAMERA}, REQ_CAMERA);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (status != null) refreshStatus();  // ponytail: 從系統設定頁回來順手更新，不用重開 App
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
        boolean on = usbTetherOn();
        String s = "USB共享 : http://192.168.42.129:8080/video\n"
                + "本機IP: http://" + localIp() + ":8080/video";
        status.setText(s);
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

    @Override
    public void onRequestPermissionsResult(int code, String[] p, int[] r) {
        super.onRequestPermissionsResult(code, p, r);
        if (code == REQ_CAMERA && r.length > 0 && r[0] == PackageManager.PERMISSION_GRANTED) {
            startCamera();
        } else {
            status.setText("沒相機權限就沒畫面");
        }
    }

    private void startCamera() {
        ListenableFuture<ProcessCameraProvider> f = ProcessCameraProvider.getInstance(this);
        f.addListener(() -> {
            try {
                ProcessCameraProvider provider = f.get();
                Preview preview = new Preview.Builder().build();
                preview.setSurfaceProvider(previewView.getSurfaceProvider());
                ImageAnalysis analysis = new ImageAnalysis.Builder()
                        .setTargetResolution(new Size(1280, 720))
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .build();
                analysis.setAnalyzer(cameraIo, this::onFrame);
                provider.unbindAll();
                provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis);
            } catch (Exception e) {
                status.setText("相機起不來: " + e.getMessage());
            }
        }, ContextCompat.getMainExecutor(this));
    }

    private void onFrame(ImageProxy proxy) {
        try {
            Image img = proxy.getImage();
            if (img != null && img.getFormat() == ImageFormat.YUV_420_888) {
                int w = img.getWidth(), h = img.getHeight();
                byte[] nv21 = yuv420ToNv21(img);
                // ponytail: sensor 橫的，直屏時 rotationDegrees=90；NV21 先轉正再壓 JPEG，
                // /video /snapshot /info 全都正，PC 不用動
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
                server.pushFrame(out.toByteArray(), w, h);
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

    // ponytail: 只列第一個非迴路 IPv4，網段怪時(172.24.x)照抄即可
    static String localIp() {
        try {
            for (java.util.Enumeration<java.net.NetworkInterface> e =
                 java.net.NetworkInterface.getNetworkInterfaces(); e.hasMoreElements(); ) {
                for (java.util.Enumeration<java.net.InetAddress> a =
                     e.nextElement().getInetAddresses(); a.hasMoreElements(); ) {
                    java.net.InetAddress addr = a.nextElement();
                    if (!addr.isLoopbackAddress() && addr instanceof java.net.Inet4Address)
                        return addr.getHostAddress();
                }
            }
        } catch (Exception ignored) {
        }
        return "?";
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (server != null) server.stop();
        if (cameraIo != null) cameraIo.shutdown();
    }
}
