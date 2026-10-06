# FINN Alert — ارسال آگهی‌های جدید به تلگرام

پایتون 3.10 یا جدیدتر، مناسب Ubuntu 22.04 / 24.04. جست‌وجوی ارائه‌شده از FINN هر ۳۰۰ ثانیه بررسی می‌شود. لینک آگهی و ترجمهٔ انگلیسی عنوان و متن کامل آن به گروه یا کانال می‌رود. متن بلند به چند پیام تقسیم می‌شود. فایل SQLite آگهی‌های دیده‌شده و صف ارسال را نگه می‌دارد.

## نصب روی اوبونتو

فایل‌های این پوشه را به `/opt/finn-alert` روی سرور کپی کنید، سپس:

```bash
sudo apt update
sudo apt install -y python3 python3-venv
sudo useradd --system --home /opt/finn-alert --shell /usr/sbin/nologin finn-alert
sudo chown -R finn-alert:finn-alert /opt/finn-alert
cd /opt/finn-alert
sudo -u finn-alert python3 -m venv .venv
sudo -u finn-alert .venv/bin/pip install -r requirements.txt
sudo -u finn-alert cp config.example.json config.json
sudo chmod 600 config.json
sudo nano config.json
```

اگر کاربر `finn-alert` از قبل وجود دارد، دستور `useradd` را تکرار نکنید.

در فایل `config.json` این مقادیر را تنظیم کنید:

| گزینه | کاربرد |
|---|---|
| `telegram.bot_token` | توکن بات ساخته‌شده با @BotFather |
| `telegram.chat_id` | مثلاً `@channel_username` یا شناسهٔ عددی گروه/کانال خصوصی مانند `-1001234567890` |
| `poll_interval_seconds` | فاصلهٔ شروع بررسی‌ها؛ پیش‌فرض ۳۰۰ ثانیه |
| `max_pages` | تعداد صفحه‌های جست‌وجو در هر بررسی؛ پیش‌فرض ۳ |
| `request_delay_seconds` | مکث بین درخواست‌ها؛ پیش‌فرض ۲ ثانیه |
| `initial_mode` | `send`: ارسال آگهی‌های موجود در محدودهٔ بررسی در اجرای اول؛ `skip`: ثبت آن‌ها بدون ارسال و ارسال فقط موارد جدید از بررسی بعد |
| `translation.provider` | `google_web` یا `google_cloud` |
| `translation.google_api_key` | فقط برای روش رسمی `google_cloud` |
| `database` | مسیر SQLite، نسبت به پوشهٔ کانفیگ |

بات را به گروه اضافه کنید و اجازهٔ ارسال پیام بدهید. برای کانال، بات باید ادمین با دسترسی انتشار پیام باشد. برای کانال عمومی می‌توانید مستقیماً از `@username` استفاده کنید.

برای به‌دست‌آوردن شناسهٔ گروه خصوصی، بات را اضافه کنید، یک دستور مثل `/start@YourBotUsername` در گروه بفرستید و روی کامپیوتر خود این کد را اجرا کنید. توکن به‌صورت مخفی پرسیده می‌شود؛ فقط شناسه و نام چت چاپ می‌شود:

```bash
.venv/bin/python - <<'PY'
from getpass import getpass
import requests
token = getpass('Bot token: ')
data = requests.get(f'https://api.telegram.org/bot{token}/getUpdates', timeout=30).json()
for update in data.get('result', []):
    msg = update.get('message') or update.get('channel_post') or update.get('my_chat_member') or {}
    chat = msg.get('chat', {})
    if chat:
        print(chat.get('id'), chat.get('title', chat.get('username', '')))
PY
```

این روش برای باتی است که webhook یا مصرف‌کنندهٔ دیگر `getUpdates` ندارد. برنامهٔ اصلی فقط پیام ارسال می‌کند و آپدیت‌های بات را مصرف نمی‌کند. متغیرهای محیطی `TELEGRAM_BOT_TOKEN`، `TELEGRAM_CHAT_ID` و `GOOGLE_TRANSLATE_API_KEY` نیز می‌توانند مقدارهای کانفیگ را جایگزین کنند.

### گرفتن شناسهٔ گروه از مرورگر

۱. بات خودتان را به گروه اضافه کنید و در همان گروه `/start@YourBotUsername` بفرستید؛ نام کاربری واقعی بات را جایگزین کنید.

۲. در آدرس زیر، `YOUR_BOT_TOKEN` را با کل توکن BotFather جایگزین کنید و آدرس را در مرورگر باز کنید:

```text
https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
```

۳. در خروجی، بخش `chat` را که `title` آن نام گروه شماست پیدا کنید:

```json
"chat": {
  "id": -1001234567890,
  "title": "گروه من",
  "type": "supergroup"
}
```

۴. عدد `chat.id` را با علامت منفی، بدون افزودن `@`، در کانفیگ وارد کنید؛ `from.id` شناسهٔ فرستنده است و برای این کار مناسب نیست:

```json
"chat_id": "-1001234567890"
```

عدد مثال را کپی نکنید؛ عدد واقعی گروه خودتان را استفاده کنید. اگر `result` خالی بود، دستور مرحلهٔ اول را دوباره در گروه بفرستید و صفحه را تازه کنید. اگر بات webhook فعال یا برنامهٔ دیگری برای دریافت آپدیت‌ها دارد، `getUpdates` ممکن است خطا بدهد یا آپدیت‌ها قبلاً مصرف شده باشند. توکن و آدرس دارای توکن را منتشر نکنید؛ روش ترمینال بالا توکن را وارد تاریخچهٔ مرورگر نمی‌کند.

### رفع خطای 404 و بررسی توکن

برای پاسخ `{"ok":false,"error_code":404,"description":"Not Found"}` ابتدا ساختار URL را بررسی کنید. کلمهٔ `bot` باید دقیقاً قبل از توکن و بدون فاصله باشد. کل توکن، شامل دو بخش قبل و بعد از `:`، لازم است؛ علامت‌های `< >`، کوتیشن یا فاصله را وارد نکنید. ساختار نمونه با توکن ساختگی:

```text
https://api.telegram.org/bot123456789:ABCDEF_example/getMe
```

توکن واقعی را جایگزین کنید. پاسخ `"ok":true` به `getMe` همراه مشخصات بات، صحت توکن را تأیید می‌کند؛ سپس `getMe` را به `getUpdates` تغییر دهید. خطای 401 معمولاً نشان‌دهندهٔ توکن نامعتبر یا باطل‌شده است. اگر خطا باقی ماند، فقط کد و توضیح خطا را برای عیب‌یابی بفرستید، نه توکن یا URL حاوی آن.

مراجع: [getUpdates](https://core.telegram.org/bots/api#getupdates)، [getMe](https://core.telegram.org/bots/api#getme).

## آزمایش و اجرای دائمی

بررسی توکن، مقصد تلگرام، دریافت FINN و ترجمه، بدون ارسال پیام:

```bash
sudo -u finn-alert .venv/bin/python check_connection.py
```

برای همین بررسی‌ها به‌همراه ارسال **فقط یک پیام آزمایشی** به مقصد کانفیگ:

```bash
sudo -u finn-alert .venv/bin/python check_connection.py --send-test
```

این ابزار دیتابیس اصلی را تغییر نمی‌دهد و وضعیت هر سرویس را جداگانه نشان می‌دهد. در ویندوز به‌جای پیشوند `sudo -u finn-alert .venv/bin/python` از `python` استفاده کنید.

پیش‌نمایش یک آگهی با ترجمه، بدون ارسال تلگرام و بدون تغییر دیتابیس:

```bash
sudo -u finn-alert .venv/bin/python finn_alert.py --preview
```

اجرای یک بار بررسی **و ارسال واقعی**:

```bash
sudo -u finn-alert .venv/bin/python finn_alert.py --once
```

نصب سرویس دائمی، اجرای خودکار بعد از روشن‌شدن سرور و مشاهدهٔ لاگ:

```bash
sudo cp finn-alert.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now finn-alert
sudo systemctl status finn-alert
sudo journalctl -u finn-alert -f
```

بعد از تغییر کانفیگ: `sudo systemctl restart finn-alert`. برای توقف: `sudo systemctl stop finn-alert`.

## ترجمه و رفتار هنگام خطا

`google_web` از وب‌سایت Google Translate استفاده می‌کند، کلید لازم ندارد و غیررسمی است؛ ممکن است گوگل آن را محدود کند یا HTML تغییر کند. گزینهٔ `google_cloud` از [API رسمی Google Cloud Translation Basic](https://docs.cloud.google.com/translate/docs/reference/rest/v2/translate) استفاده می‌کند؛ پروژه، فعال‌سازی سرویس، کلید معتبر و تنظیمات صورتحساب گوگل لازم است. هزینه تابع تعرفه و مصرف حساب شماست.

در آزمایش این محیط، روش رایگان به کپچای گوگل رسید و ترجمهٔ زنده تأیید نشد. قبل از فعال‌کردن سرویس، `--preview` را روی سرور خود اجرا کنید؛ اگر همین محدودیت وجود داشت، `provider` را روی `google_cloud` بگذارید و کلید خود را وارد کنید. API رسمی بدون کلید واقعی آزمایش نشده است.

اگر ترجمه یا ارسال شکست بخورد، آگهی در صف می‌ماند تا در نوبت بعد دوباره امتحان شود. ترجمهٔ آماده ذخیره می‌شود و برای تلاش مجدد ارسال ترجمه نمی‌شود. پیام‌های موفق هر آگهی جداگانه ثبت می‌شوند. در بازهٔ بسیار کوتاه بین موفقیت تلگرام و ثبت در SQLite، یا هنگام timeout با نتیجهٔ نامعلوم، امکان تکرار یک پیام وجود دارد؛ Bot API تضمین exactly-once ندارد. دیتابیس را برای حفظ سابقه حذف نکنید و آن را میان چند مقصد به اشتراک نگذارید؛ برای مقصد جدید از فایل دیتابیس جدید استفاده کنید.

صفحه‌های خالی یا مسدودشده موفق تلقی نمی‌شوند و baseline را تغییر نمی‌دهند. آگهی با ساختار توضیحات نامعتبر برای بررسی بعد باقی می‌ماند. دریافت HTML معمولی است؛ دورزدن کپچا یا ورود به حساب پیاده‌سازی نشده است. تعداد `max_pages` را با حجم آگهی‌ها و مدت قطعی هماهنگ کنید: آگهی‌هایی که بین دو بررسی منتشر و حذف شوند، یا از این محدوده خارج شوند ممکن است دیده نشوند. برنامه همهٔ صفحه‌های تنظیم‌شده را بررسی می‌کند و در اولین آگهی تکراری متوقف نمی‌شود. ترتیب ارسال بر اساس ترتیب نتایج است؛ نتایج ویژه ممکن است ترتیب دقیق زمانی نداشته باشند.

فاصلهٔ بررسی از شروع هر دور محاسبه می‌شود؛ اگر ارسال صف بیشتر طول بکشد، بررسی بعد از پایان آن انجام می‌شود. فایل کانفیگ در شروع برنامه خوانده می‌شود. یک قفل در اوبونتو جلوی اجرای همزمان دو نمونه روی یک دیتابیس را می‌گیرد.

## آزمون‌ها

```bash
.venv/bin/python -m unittest discover -s tests -v
```

آزمون‌ها بدون ارسال پیام و بدون اتصال شبکه اجرا می‌شوند. در تست زندهٔ ۶ اکتبر ۲۰۲۶، اعتبار توکن (`getMe`)، دسترسی به گروه (`getChat`) و ارسال یک پیام آزمایشی همگی موفق بودند. دریافت ۵۴ آگهی از صفحهٔ اول FINN و استخراج متن یک آگهی نیز موفق بود. ترجمهٔ `google_web` با HTTP 429 محدود شد؛ بنابراین ارسال کامل آگهی ترجمه‌شده در این محیط هنوز تأیید نشده است. برای رفع این بخش، API رسمی گوگل را تنظیم کنید یا پیش‌نمایش را روی سرور مقصد آزمایش کنید.

## Git

این پوشه مخزن Git محلی است. `config.json`، فایل‌های `.env`، دیتابیس و لاگ‌ها در `.gitignore` هستند و نباید commit شوند. تنظیمات قابل اشتراک در `config.example.json` قرار دارد. برای دیدن تغییرات از `git status` استفاده کنید. ساخت مخزن روی GitHub و push نیازمند انتخاب مقصد است؛ مخزن محلی به‌تنهایی چیزی را منتشر نمی‌کند.
