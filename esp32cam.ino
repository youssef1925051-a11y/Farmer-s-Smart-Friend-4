// ============================================================
//  Farmer's Smart Friend  -  ESP32-CAM (AI-Thinker)
//  1) بيبث فيديو MJPEG:   http://<IP>:81/stream   و  http://<IP>/capture
//  2) بيلاقي السيرفر لوحده (UDP 4210) ويقوله "أنا هنا"
//  3) بيسحب أوامر "قفل الأرض / فتح الأرض" وينفّذها على GPIO ويبعت تأكيد
//  التحليل (الذكاء الاصطناعي) بيحصل في السيرفر مش هنا - الكاميرا بتنقل الصورة بس.
//
//  Arduino IDE:  Board = "AI Thinker ESP32-CAM"  |  Partition = Huge APP
//  Core: esp32 by Espressif (2.0.x أو 3.x)  - مفيش مكتبات إضافية.
// ============================================================
#include "esp_camera.h"
#include "esp_http_server.h"
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"
#include <WiFi.h>
#include <WiFiUdp.h>
#include <HTTPClient.h>
#include "config.h"

// ---- AI-Thinker pins ----
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22

static const char *BOUNDARY = "\r\n--frame\r\n";
static const char *STREAM_CT = "multipart/x-mixed-replace;boundary=frame";
static const char *PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

httpd_handle_t cam_httpd = NULL, stream_httpd = NULL;
String serverIp = SERVER_IP_FALLBACK;
int serverPort = SERVER_PORT;
WiFiUDP udp;
unsigned long lastPoll = 0, lastHello = 0;
String coverState = "open";

// ------------------------------------------------ camera
bool initCamera() {
  camera_config_t c;
  c.ledc_channel = LEDC_CHANNEL_0;  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM;  c.pin_d1 = Y3_GPIO_NUM;  c.pin_d2 = Y4_GPIO_NUM;  c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM;  c.pin_d5 = Y7_GPIO_NUM;  c.pin_d6 = Y8_GPIO_NUM;  c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM;  c.pin_pclk = PCLK_GPIO_NUM;  c.pin_vsync = VSYNC_GPIO_NUM;  c.pin_href = HREF_GPIO_NUM;
  c.pin_sscb_sda = SIOD_GPIO_NUM;  c.pin_sscb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM;  c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;  c.pixel_format = PIXFORMAT_JPEG;
  c.grab_mode = CAMERA_GRAB_LATEST;
  if (psramFound()) {
    c.frame_size = FRAME_SIZE_PSRAM; c.jpeg_quality = JPEG_QUALITY; c.fb_count = 2; c.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    c.frame_size = FRAMESIZE_QVGA; c.jpeg_quality = 14; c.fb_count = 1; c.fb_location = CAMERA_FB_IN_DRAM;
  }
  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) { Serial.printf("camera init failed 0x%x\n", err); return false; }
  sensor_t *s = esp_camera_sensor_get();
  if (s) { s->set_whitebal(s, 1); s->set_awb_gain(s, 1); s->set_exposure_ctrl(s, 1); s->set_gain_ctrl(s, 1); }
  return true;
}

// ------------------------------------------------ HTTP handlers
static esp_err_t capture_handler(httpd_req_t *req) {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) { httpd_resp_send_500(req); return ESP_FAIL; }
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  esp_err_t r = httpd_resp_send(req, (const char *)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  return r;
}

static esp_err_t stream_handler(httpd_req_t *req) {
  esp_err_t res = httpd_resp_set_type(req, STREAM_CT);
  if (res != ESP_OK) return res;
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  char part[64];
  while (true) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) { delay(50); continue; }
    size_t hl = snprintf(part, sizeof(part), PART, fb->len);
    res = httpd_resp_send_chunk(req, BOUNDARY, strlen(BOUNDARY));
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, part, hl);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);
    esp_camera_fb_return(fb);
    if (res != ESP_OK) break;   // العميل قفل
    delay(60);                  // ~12-15 fps كفاية للتحليل وبيخفف الحمل
  }
  return res;
}

static esp_err_t status_handler(httpd_req_t *req) {
  char buf[160];
  snprintf(buf, sizeof(buf), "{\"device\":\"%s\",\"cover\":\"%s\",\"rssi\":%d,\"heap\":%u}", DEVICE_ID, coverState.c_str(), WiFi.RSSI(), ESP.getFreeHeap());
  httpd_resp_set_type(req, "application/json");
  return httpd_resp_send(req, buf, strlen(buf));
}

void startServers() {
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = 80;
  httpd_uri_t cap = {.uri = "/capture", .method = HTTP_GET, .handler = capture_handler, .user_ctx = NULL};
  httpd_uri_t sta = {.uri = "/status", .method = HTTP_GET, .handler = status_handler, .user_ctx = NULL};
  if (httpd_start(&cam_httpd, &cfg) == ESP_OK) { httpd_register_uri_handler(cam_httpd, &cap); httpd_register_uri_handler(cam_httpd, &sta); }
  cfg.server_port = STREAM_PORT;
  cfg.ctrl_port += 1;
  httpd_uri_t str = {.uri = "/stream", .method = HTTP_GET, .handler = stream_handler, .user_ctx = NULL};
  if (httpd_start(&stream_httpd, &cfg) == ESP_OK) httpd_register_uri_handler(stream_httpd, &str);
}

// ------------------------------------------------ server discovery
void discoverServer() {
  int n = udp.parsePacket();
  while (n > 0) {
    char buf[96]; int len = udp.read(buf, sizeof(buf) - 1); buf[len] = 0;
    String m(buf);                                   // FSF|192.168.1.20|8080
    if (m.startsWith("FSF|")) {
      int a = m.indexOf('|', 4);
      if (a > 0) {
        String ip = m.substring(4, a); int port = m.substring(a + 1).toInt();
        if (ip != serverIp || (port > 0 && port != serverPort)) { serverIp = ip; if (port > 0) serverPort = port; lastHello = 0; Serial.println("server found: " + serverIp); }
      }
    }
    n = udp.parsePacket();
  }
}

String baseUrl() { return "http://" + serverIp + ":" + String(serverPort); }

void sayHello() {
  if (serverIp == "") return;
  HTTPClient http; http.setTimeout(4000);
  if (http.begin(baseUrl() + "/api/device/hello")) {
    http.addHeader("Content-Type", "application/json");
    String body = "{\"device\":\"" + String(DEVICE_ID) + "\",\"ip\":\"" + WiFi.localIP().toString() + "\",\"port\":" + String(STREAM_PORT) + "}";
    int code = http.POST(body); Serial.printf("hello -> %d\n", code); http.end();
  }
}

// ------------------------------------------------ cover control
void relaysOff() { digitalWrite(PIN_CLOSE, !RELAY_ACTIVE); digitalWrite(PIN_OPEN, !RELAY_ACTIVE); }

void runCover(bool closeIt) {
  relaysOff(); delay(200);                           // interlock: عمرهم ما يشتغلوا مع بعض
  digitalWrite(closeIt ? PIN_CLOSE : PIN_OPEN, RELAY_ACTIVE);
  unsigned long t0 = millis();
  while (millis() - t0 < COVER_RUN_MS) { delay(50); }  // الكاميرا بتفضل تبث لأن الـ httpd على task تانية
  relaysOff();
  coverState = closeIt ? "closed" : "open";
}

// parse صغير للـ JSON {"cmd": "close", "id": 3}
String jsonStr(const String &s, const char *key) {
  int k = s.indexOf(String("\"") + key + "\""); if (k < 0) return "";
  int c = s.indexOf(':', k); if (c < 0) return "";
  int i = c + 1; while (i < (int)s.length() && (s[i] == ' ' || s[i] == '"')) i++;
  int j = i; while (j < (int)s.length() && s[j] != '"' && s[j] != ',' && s[j] != '}' && s[j] != ' ') j++;
  return s.substring(i, j);
}

void pollCommands() {
  if (serverIp == "") return;
  HTTPClient http; http.setTimeout(4000);
  if (!http.begin(baseUrl() + "/api/device/command?device=" + DEVICE_ID)) return;
  int code = http.GET();
  if (code == 200) {
    String r = http.getString(); http.end();
    String cmd = jsonStr(r, "cmd"), id = jsonStr(r, "id");
    if (cmd == "close" || cmd == "open") {
      Serial.println("command: " + cmd);
      runCover(cmd == "close");
      HTTPClient a; a.setTimeout(4000);
      if (a.begin(baseUrl() + "/api/device/ack")) {
        a.addHeader("Content-Type", "application/json");
        a.POST("{\"id\":" + id + ",\"state\":\"" + coverState + "\"}"); a.end();
      }
    }
  } else { http.end(); }
}

// ------------------------------------------------ setup / loop
void connectWifi() {
  WiFi.mode(WIFI_STA); WiFi.setSleep(false); WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("WiFi");
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED) { delay(400); Serial.print("."); if (millis() - t0 > 30000) ESP.restart(); }
  Serial.println("\nIP: " + WiFi.localIP().toString());
}

void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);        // بيمنع إعادة التشغيل مع كهرباء ضعيفة
  Serial.begin(115200);
  pinMode(PIN_CLOSE, OUTPUT); pinMode(PIN_OPEN, OUTPUT); relaysOff();
  if (!initCamera()) { delay(3000); ESP.restart(); }
  connectWifi();
  startServers();
  udp.begin(4210);
  Serial.println("Stream: http://" + WiFi.localIP().toString() + ":" + String(STREAM_PORT) + "/stream");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) { connectWifi(); }
  discoverServer();
  unsigned long now = millis();
  if (serverIp != "" && now - lastHello > 15000) { lastHello = now; sayHello(); }
  if (now - lastPoll > POLL_INTERVAL_MS) { lastPoll = now; pollCommands(); }
  delay(20);
}
