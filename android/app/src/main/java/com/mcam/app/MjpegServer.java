package com.mcam.app;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

// ponytail: 手刻 ServerSocket MJPEG，不引 NanoHTTPD；只夠 /video /snapshot /info
public class MjpegServer {
    private static final String BOUNDARY = "frame";
    private final int port;
    private volatile byte[] latest;
    private volatile int frameW;
    private volatile int frameH;
    private ServerSocket server;
    private volatile boolean running;

    public MjpegServer(int port) {
        this.port = port;
    }

    public void start() throws IOException {
        server = new ServerSocket(port);
        running = true;
        new Thread(this::acceptLoop, "mjpeg-accept").start();
    }

    public void stop() {
        running = false;
        try {
            if (server != null) server.close();
        } catch (IOException ignored) {
        }
    }

    public void pushFrame(byte[] jpeg, int w, int h) {
        latest = jpeg;
        frameW = w;
        frameH = h;
    }

    private void acceptLoop() {
        while (running) {
            try {
                Socket s = server.accept();
                new Thread(() -> serve(s), "mjpeg-client").start();
            } catch (IOException e) {
                if (running) e.printStackTrace();
            }
        }
    }

    private void serve(Socket s) {
        try (Socket socket = s) {
            BufferedReader in = new BufferedReader(
                    new InputStreamReader(socket.getInputStream(), StandardCharsets.US_ASCII));
            String line = in.readLine();
            if (line == null) return;
            String[] parts = line.split(" ");
            String path = parts.length > 1 ? parts[1] : "/";
            OutputStream out = socket.getOutputStream();
            if (path.startsWith("/video")) {
                streamVideo(out);
            } else if (path.startsWith("/snapshot")) {
                sendSnapshot(out);
            } else if (path.startsWith("/info")) {
                byte[] body = String.format(Locale.US,
                        "{\"w\":%d,\"h\":%d,\"fps\":15,\"facing\":\"back\",\"ver\":1}",
                        frameW, frameH).getBytes(StandardCharsets.UTF_8);
                write(out, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                        + body.length + "\r\nConnection: close\r\n\r\n");
                out.write(body);
            } else {
                write(out, "HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n");
            }
            out.flush();
        } catch (IOException ignored) {
        }
    }

    private void streamVideo(OutputStream out) throws IOException {
        write(out, "HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary="
                + BOUNDARY + "\r\nConnection: close\r\n\r\n");
        byte[] lastSent = null;
        while (running) {
            byte[] jpeg = latest;
            if (jpeg == null || jpeg == lastSent) {
                sleep(66);
                continue;
            }
            lastSent = jpeg;
            write(out, "--" + BOUNDARY + "\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + jpeg.length + "\r\n\r\n");
            out.write(jpeg);
            write(out, "\r\n");
            out.flush();
        }
    }

    private void sendSnapshot(OutputStream out) throws IOException {
        byte[] jpeg = latest;
        if (jpeg == null) {
            write(out, "HTTP/1.1 503 No Frame Yet\r\nConnection: close\r\n\r\n");
            return;
        }
        write(out, "HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + jpeg.length + "\r\nConnection: close\r\n\r\n");
        out.write(jpeg);
    }

    private static void write(OutputStream out, String s) throws IOException {
        out.write(s.getBytes(StandardCharsets.US_ASCII));
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
