// ============================================================
//  Farmer's Smart Friend (صديق الفلاحين) - إعدادات ESP32-CAM
// ============================================================
#pragma once

// ---- الواي فاي (نفس الشبكة اللي عليها اللاب/السيرفر) ----
#define WIFI_SSID      "7ayar"
#define WIFI_PASSWORD  "yaa amo osama"

// ---- السيرفر ----
// الـ ESP32 بيلاقي السيرفر لوحده (UDP broadcast على بورت 4210).
// لو الشبكة بتمنع الـ broadcast اكتب IP اللاب هنا (يظهر في شاشة السيرفر) مثال: "192.168.1.20"
#define SERVER_IP_FALLBACK  ""
#define SERVER_PORT         8080
#define DEVICE_ID           "FSF-CAM-01"     // لازم يطابق default_device_id في config.json

// ---- تشغيل/إيقاف عمدان الحماية (الشمع) ----
// وصّل كل خرج بـ Relay/محرك: CLOSE بيلف المحرك ناحية القفل، OPEN ناحية الفتح.
// (GPIO 13 و 14 حرّين على ESP32-CAM. ما تستخدمش 12/16/0 - بتوقّف الإقلاع/الكاميرا)
#define PIN_CLOSE      13
#define PIN_OPEN       14
#define RELAY_ACTIVE   HIGH                  // لو الريلاي بيشتغل بـ LOW غيّرها لـ LOW
#define COVER_RUN_MS   20000                 // مدة تشغيل المحرك لحد ما العمدان تقفل/تفتح (عدّلها بعد التجربة)

// ---- الكاميرا ----
#define FRAME_SIZE_PSRAM   FRAMESIZE_VGA     // 640x480 (لو PSRAM موجودة)
#define JPEG_QUALITY       12                // 10 أحسن جودة .. 63 أضعف
#define STREAM_PORT        81                // /stream  (السيرفر بيسحب منه)
#define POLL_INTERVAL_MS   2000              // كل قد إيه يسأل السيرفر عن أوامر
