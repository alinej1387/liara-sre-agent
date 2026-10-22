# Liara SRE Agent

دستیار هوشمند برای راه‌اندازی، مدیریت، پایش و عیب‌یابی وب‌سایت روی پلتفرم [لیارا](https://liara.ir).

## ایده

کاربری که برنامه‌نویس است ولی از دیپلوی و زیرساخت سر در نمی‌آورد، با یک درخواست ساده‌ی متنی می‌تواند اپش را روی لیارا مدیریت کند. Agent هم عملیات را انجام می‌دهد و هم برای عملیات حساس از کاربر تأیید می‌گیرد (Human-in-the-Loop).

## معماری
کاربر (Streamlit UI)
│
▼
LangGraph (گراف ۳ گره‌ای)
├── decide: LLM تصمیم می‌گیرد چه ابزاری صدا بزند
├── tool: ابزار MCP را اجرا می‌کند (با HITL برای عملیات حساس)
└── checkpointer (MemorySaver): حالت جلسه را نگه می‌دارد
│
▼
liara-mcp (MCP Server)
│
▼
Liara PaaS API


- **Orchestration:** LangGraph 1.x
- **اتصال به لیارا:** `liara-mcp`
- **مدل زبانی:** سرویس هوش مصنوعی لیارا (سازگار با OpenAI SDK)
- **HITL:** `interrupt()` + `Command(resume=...)` + `MemorySaver`
- **UI:** Streamlit

## نحوه‌ی اتصال به APIهای لیارا

از طریق MCP انجام می‌شود. دو کلید جدا لازم است:

| متغیر | کاربرد | محل دریافت |
|---|---|---|
| `LIARA_API_TOKEN` | مدیریت اپ‌ها (MCP) | کنسول لیارا → منوی **API** |
| `LIARA_AI_API_KEY` | فراخوانی مدل زبانی | کنسول لیارا → **هوش مصنوعی** → کلیدها |
| `LIARA_TEAM_ID` | (اختیاری) اگر اپ‌ها در تیم هستند | کنسول لیارا → تیم → API |

### حالت ابزارها

در نسخه‌ی نهایی از حالت **غیر consolidated** استفاده شده تا ابزارهای دقیق SRE (مثل `liara_get_metrics` و `liara_get_logs`) در دسترس باشند.

## Human-in-the-Loop (HITL)

عملیات به دو دسته تقسیم می‌شوند:

**خودکار (بدون تأیید):**
- `liara_get_logs`
- `liara_get_metrics`
- `liara_list_apps` / `liara_get_app`
- `liara_get_infrastructure_overview`

**نیازمند تأیید:**
- `liara_restart_app` / `liara_resize_app` / `liara_delete_app` / `liara_create_app`
- `liara_deploy_release` / `liara_rollback_release`
- `liara_set_env_var` / `liara_delete_env_var`
- `liara_create_database` / `liara_delete_database`

تأییدیه با `interrupt()` نمایش داده می‌شود و کاربر با دکمه‌های «✅ تأیید» و «❌ رد» پاسخ می‌دهد. اگر رد شود، Agent پیام «کاربر رد کرد» را ثبت می‌کند.

## نحوه‌ی تعامل کاربر

UI وب با Streamlit:
1. `streamlit run app.py`
2. درخواست متنی در چت‌باکس (مثلاً «لاگ‌های اپ X رو بررسی کن»)
3. Agent ابزار مناسب را انتخاب می‌کند
4. اگر عملیات حساس بود: نمایش `⚠️ تأیید لازم` + دکمه‌های تأیید/رد
5. نمایش نتیجه در چت

## محدودیت‌ها

- نیاز به **Node.js >= 18** برای اجرای `npx liara-mcp`
- وابسته به دسترسی شبکه به `ai.liara.ir` — در برخی شبکه‌های ایران مسدود است (راه‌حل: هات‌اسپات موبایل یا پروکسی)
- MCP از `stdio_client` استفاده می‌کند که با event loop وب‌فریمورک‌ها سازگار نیست؛ راه‌حل: نگه‌داشتن یک event loop پایدار در `session_state` و ارسال هدر `Connection: close` به مدل
- پارامتر `restart` باید **نام اپ** باشد، نه ID. اگر Agent اشتباهاً ID بفرستد، خطای «پروژه پیدا نشد» می‌دهد
- متریک‌های CPU/RAM فقط برای اپ‌های **مستقر (deployed)** ثبت می‌شوند؛ برای اپ غیرمستقر داده‌ای برنمی‌گردد
- در حال حاضر فقط از یک جلسه پشتیبانی می‌کند؛ حالت چندکاربره ندارد

## اجرا

```bash
python -m venv venv
# ویندوز:
venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py"# liara-sre-agent" 
