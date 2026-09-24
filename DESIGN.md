# AI Monitor — Design

> **สถานะ:** Phase 1 ทำแล้วใน [`ai-monitor/`](ai-monitor/README.md) โดยปรับจากแบบเดิมตามคำตอบของผู้ใช้:
> - รันบน **Windows native**; Hermes home = `%LOCALAPPDATA%\hermes` (ตรวจจากซอร์สของ hermes-agent แล้ว)
> - Sparkx / Qwen รันผ่าน **Ollama**
> - ChatGPT / Claude ใช้ **subscription** → เช็คจาก status page อย่างเดียว
> - ไม่รู้ชื่อโมเดลแน่ชัด → ใช้ `model_hint` ให้ระบบหาชื่อจริงจาก `/models`
> - แจ้งเตือน **บนจอ Xeneon Edge** (การ์ดแดงกะพริบ + event log) แทน Telegram
> - ตัด APScheduler / Jinja / HTMX ออก ใช้ asyncio + HTML/JS ล้วน (dependency น้อยลง)

Dashboard เดียวสำหรับดูสถานะของ AI ทุกตัวที่ใช้งาน (Cloud API, Free tier, Local) พร้อม Status Card เฉพาะของ Hermes Agent

## 1. เป้าหมาย

- ดูได้ในหน้าเดียวว่า AI แต่ละตัว **ใช้ได้ / ช้า / ล่ม**
- วัด latency, error rate และ rate limit ที่เหลือของ Free tier
- Hermes Agent มี Status Card แบบละเอียด (process, gateway, model, session, cron, error)
- แจ้งเตือนเมื่อสถานะเปลี่ยน (ล่ม / กลับมา)
- **ใช้ Token ให้น้อยที่สุด**: เช็คด้วย endpoint ฟรีก่อน แล้วค่อยยิง prompt จริงแบบห่าง ๆ

## 2. รายการ AI ที่จะ Monitor

| # | ชื่อ | ประเภท | วิธีเช็คหลัก (ไม่เสีย Token) | Probe จริง (เสีย Token) |
|---|------|--------|------------------------------|-------------------------|
| 1 | ChatGPT | Cloud (OpenAI) | Status page ของ OpenAI + `GET /v1/models` | chat 1 token ทุก 30 นาที (ปิดได้) |
| 2 | Claude | Cloud (Anthropic) | Status page ของ Anthropic + `GET /v1/models` | chat 1 token ทุก 30 นาที (ปิดได้) |
| 3 | Hermes Agent | Local agent | process + gateway + ไฟล์ใน `~/.hermes/` | ไม่ยิง (ดูจาก log / session แทน) |
| 4 | MiMo (Xiaomi) | Cloud, OpenAI-compatible | `GET /models` | chat 1 token ทุก 30 นาที |
| 5 | Z.ai GLM Flash | Free tier, OpenAI-compatible | `GET /models` | chat 1 token ทุก 30 นาที + อ่าน rate-limit header |
| 6 | Sparkx 2.5 | Local | `GET /models` หรือ health endpoint ของ runtime | chat 1 token ทุก 10 นาที (local ไม่เสียเงิน) |
| 7 | Qwen 3.5 | Local (Ollama / llama.cpp / LM Studio) | `GET /api/tags` (Ollama) หรือ `/v1/models` | chat 1 token ทุก 10 นาที |
| 8 | NVIDIA Nemotron 3 | Free tier (build.nvidia.com) | `GET /v1/models` | chat 1 token ทุก 30 นาที + อ่าน rate-limit header |

> ชื่อโมเดล, base URL และ port ทั้งหมดเก็บใน config ไม่ hard-code เปลี่ยนเวอร์ชันโมเดลได้โดยไม่ต้องแก้โค้ด

## 3. สถาปัตยกรรม

```
                 ┌──────────────── config.yaml + .env (API keys) ────────────────┐
                 ▼                                                                 │
┌───────────────────────────┐    ┌──────────────┐    ┌─────────────────────────┐  │
│ Scheduler (APScheduler)   │───▶│  Checkers    │───▶│ SQLite (checks, events) │  │
│ ตั้งรอบเช็คแยกต่อ provider │    │ - status_page│    └───────────┬─────────────┘  │
└───────────────────────────┘    │ - openai_compat           │                  │
                                 │ - ollama     │            ▼                  │
                                 │ - hermes     │    ┌─────────────────────────┐  │
                                 └──────┬───────┘    │ FastAPI + HTML/HTMX     │  │
                                        │            │ Dashboard (auto refresh)│  │
                                        ▼            └─────────────────────────┘  │
                                 ┌──────────────┐                                  │
                                 │ Alerter      │── Telegram / LINE / Email ◀──────┘
                                 └──────────────┘
```

- **ภาษา**: Python 3.11+
- **Web**: FastAPI + Jinja2 + HTMX (ไม่ต้องมี build step ฝั่ง frontend)
- **DB**: SQLite ไฟล์เดียว
- **HTTP**: httpx (async) พร้อม timeout ต่อ provider
- **รันที่ไหน**: ต้องรันบน **เครื่องเดียวกับ Hermes / Local model** (หรือเครื่องที่เข้าถึง port ได้) เพราะ Cloud container มองไม่เห็น localhost ของคุณ

## 4. Checker (ปลั๊กอินแบบเลือกตาม `type`)

ทุก checker คืนค่า `CheckResult` แบบเดียวกัน:

```python
@dataclass
class CheckResult:
    provider: str
    status: Literal["up", "degraded", "down", "unknown"]
    latency_ms: int | None
    detail: str                 # ข้อความสั้น ๆ เช่น "HTTP 429" หรือ "model loaded"
    rate_limit_remaining: int | None
    extra: dict                 # ข้อมูลเฉพาะ provider (Hermes ใช้ตรงนี้)
```

| type | ใช้กับ | ทำอะไร |
|------|--------|--------|
| `status_page` | ChatGPT, Claude | อ่าน JSON ของ status page ทางการ แปลง `none/minor/major/critical` → up/degraded/down |
| `openai_compat` | ChatGPT, Claude*, MiMo, GLM, Nemotron, Sparkx, Qwen | `GET /models` (ping), ถ้าเปิด probe ยิง `chat/completions` ด้วย `max_tokens=1` และอ่าน header `x-ratelimit-*` |
| `ollama` | Qwen (ถ้าใช้ Ollama) | `GET /api/tags` เช็คว่ามีโมเดล, `GET /api/ps` เช็คว่าโหลดอยู่ใน VRAM ไหม |
| `hermes` | Hermes Agent | ดูหัวข้อ 5 |

\* Claude ใช้ Anthropic API ตรง (`/v1/messages`) แยก adapter เล็ก ๆ

**กฎตัดสินสถานะ**

- `down`: เชื่อมต่อไม่ได้, timeout, HTTP 5xx, 401/403 (key ใช้ไม่ได้)
- `degraded`: latency เกิน threshold, HTTP 429, rate limit เหลือต่ำกว่า 10%, status page รายงาน minor
- `up`: ผ่านทั้งหมด
- ต้องล้ม **2 ครั้งติดกัน** ก่อนเปลี่ยนเป็น down (กัน alert หลอก)

## 5. Hermes Agent Status Card

ข้อมูลที่แสดงบนการ์ด:

| ช่อง | แหล่งข้อมูล |
|------|-------------|
| Process: Running / Stopped, PID, uptime | `psutil` หา process `hermes` |
| Gateway: เชื่อมต่อ Telegram / Discord / ฯลฯ หรือไม่ | process ของ gateway + log |
| Model / Provider ที่ใช้อยู่ | `~/.hermes/config.yaml` |
| Model ปลายทางใช้ได้ไหม | เอา provider ใน config ไปเทียบกับผลเช็คข้อ 2 (ไม่ยิงซ้ำ) |
| Session ล่าสุด, จำนวน session วันนี้ | ไฟล์ / DB session ใน `~/.hermes/` |
| Cron jobs: จำนวน, รอบถัดไป, ผลรอบล่าสุด | ไฟล์ cron ใน `~/.hermes/` |
| Error ล่าสุด 5 รายการ | tail ไฟล์ log ใน `~/.hermes/` |
| Skills / Memory: จำนวน | นับไฟล์ในโฟลเดอร์ skills / memories |

> path และรูปแบบไฟล์ของ Hermes ต้อง **ตรวจกับเวอร์ชันที่ติดตั้งจริง** ก่อนเขียนโค้ด (ใส่ path ไว้ใน config เผื่อเปลี่ยน)
> Checker อ่านอย่างเดียว ไม่แก้ไฟล์ของ Hermes

## 6. หน้า Dashboard (Wireframe)

```
┌────────────────────────────────────────────────────────────────────┐
│ AI Monitor                         อัปเดตล่าสุด 10:42:05  [Refresh] │
├────────────────────────────────────────────────────────────────────┤
│ ┌─ Hermes Agent ──────────────────────────────── 🟢 Running ─────┐ │
│ │ Uptime 3d 4h   PID 12345   Model: qwen3.5 (local) 🟢            │ │
│ │ Gateway: Telegram 🟢  Discord ⚪                                 │ │
│ │ Sessions today: 18   Last: 10:31   Cron: 3 jobs, next 11:00     │ │
│ │ Errors (24h): 2  ▸ ดูรายละเอียด                                  │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│ Cloud                    Free tier                 Local             │
│ ┌──────────┐┌──────────┐ ┌──────────┐┌──────────┐ ┌──────────┐┌──────────┐
│ │ChatGPT 🟢││Claude  🟢│ │GLM     🟡││Nemotron🟢│ │Qwen3.5 🟢││Sparkx  🔴│
│ │ 420 ms   ││ 380 ms   │ │ 429 rate ││ 900 ms   │ │ 120 ms   ││ refused  │
│ │ ▁▂▁▃▁▂   ││ ▁▁▂▁▁▁   │ │ quota 8% ││ quota 64%│ │ VRAM 11G ││ 5 นาที    │
│ └──────────┘└──────────┘ └──────────┘└──────────┘ └──────────┘└──────────┘
│ ┌──────────┐                                                          │
│ │MiMo    🟢│                                                          │
│ └──────────┘                                                          │
│                                                                      │
│ Event log: 10:37 Sparkx 🟢→🔴 connection refused  |  09:12 GLM 🟢→🟡 │
└────────────────────────────────────────────────────────────────────┘
```

- การ์ดแต่ละใบกดเข้าไปดูกราฟ latency / uptime 24 ชม. และ 7 วันได้
- รองรับมือถือ (การ์ดเรียงเป็นคอลัมน์เดียว)
- Auto refresh ทุก 30 วินาทีด้วย HTMX

## 7. Config ตัวอย่าง

```yaml
# config.yaml  (API key อยู่ใน .env เท่านั้น ห้าม commit)
defaults:
  ping_interval_s: 60
  probe_interval_s: 1800
  timeout_s: 15
  degraded_latency_ms: 5000

providers:
  - id: chatgpt
    name: ChatGPT
    group: cloud
    checks:
      - type: status_page
        url: https://status.openai.com/api/v2/status.json
      - type: openai_compat
        base_url: https://api.openai.com/v1
        api_key_env: OPENAI_API_KEY
        model: gpt-5-mini
        probe: true

  - id: claude
    name: Claude
    group: cloud
    checks:
      - type: status_page
        url: https://status.anthropic.com/api/v2/status.json
      - type: anthropic
        api_key_env: ANTHROPIC_API_KEY
        model: claude-haiku-4-5-20251001
        probe: true

  - id: glm
    name: Z.ai GLM Flash
    group: free
    checks:
      - type: openai_compat
        base_url: https://api.z.ai/api/paas/v4
        api_key_env: ZAI_API_KEY
        model: glm-flash        # แก้เป็นชื่อโมเดลจริง
        probe: true

  - id: nemotron
    name: NVIDIA Nemotron 3
    group: free
    checks:
      - type: openai_compat
        base_url: https://integrate.api.nvidia.com/v1
        api_key_env: NVIDIA_API_KEY
        model: nvidia/nemotron-3   # แก้เป็นชื่อโมเดลจริง
        probe: true

  - id: qwen
    name: Qwen 3.5
    group: local
    checks:
      - type: ollama
        base_url: http://localhost:11434
        model: qwen3.5

  - id: hermes
    name: Hermes Agent
    group: agent
    checks:
      - type: hermes
        home: ~/.hermes

alerts:
  telegram:
    bot_token_env: TELEGRAM_BOT_TOKEN
    chat_id_env: TELEGRAM_CHAT_ID
  notify_on: [down, recovered]
  cooldown_s: 900
```

## 8. การแจ้งเตือน

- แจ้งเฉพาะตอน **สถานะเปลี่ยน** (up→down, down→up, และ degraded ถ้าเปิดไว้)
- cooldown 15 นาทีต่อ provider กันแจ้งซ้ำ
- ใช้ Telegram bot แยกจากของ Hermes เพื่อให้ Hermes ล่มแล้วยังแจ้งเตือนได้
- สรุปรายวันตอน 08:00 (uptime %, ตัวที่ช้าที่สุด, error เด่น)

## 9. Database

```sql
CREATE TABLE checks (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,             -- unix time
  provider TEXT NOT NULL,
  check_type TEXT NOT NULL,        -- status_page | ping | probe | hermes
  status TEXT NOT NULL,
  latency_ms INTEGER,
  detail TEXT,
  rate_limit_remaining INTEGER,
  extra_json TEXT
);
CREATE INDEX idx_checks_provider_ts ON checks(provider, ts);

CREATE TABLE events (              -- เก็บเฉพาะตอนสถานะเปลี่ยน
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  provider TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT NOT NULL,
  detail TEXT
);
```

เก็บผลเช็คย้อนหลัง 30 วัน แล้วลบอัตโนมัติ

## 10. โครงสร้างโปรเจกต์

```
ai-monitor/
├── config.example.yaml
├── .env.example
├── pyproject.toml
├── ai_monitor/
│   ├── main.py            # FastAPI app + start scheduler
│   ├── config.py          # โหลดและตรวจ config (pydantic)
│   ├── scheduler.py
│   ├── store.py           # SQLite
│   ├── alerts.py
│   ├── checkers/
│   │   ├── base.py        # CheckResult + interface
│   │   ├── status_page.py
│   │   ├── openai_compat.py
│   │   ├── anthropic.py
│   │   ├── ollama.py
│   │   └── hermes.py
│   └── web/
│       ├── templates/     # dashboard.html, card.html, hermes_card.html
│       └── static/
└── tests/                 # mock HTTP ด้วย respx, ไม่ยิง API จริง
```

## 11. ความปลอดภัย

- API key อยู่ใน `.env` เท่านั้น และใส่ `.env` ไว้ใน `.gitignore`
- Dashboard bind `127.0.0.1` เป็นค่าเริ่มต้น ถ้าจะเปิดจากมือถือให้ใช้ Tailscale หรือตั้ง basic auth
- ไม่แสดง key หรือ prompt / response เต็ม ๆ บนหน้าเว็บ

## 12. แผนการทำ

| Phase | งาน |
|-------|-----|
| 1 (MVP) | config, checker `openai_compat` / `ollama` / `status_page`, SQLite, dashboard การ์ดพื้นฐาน |
| 2 | Hermes Status Card แบบละเอียด + Telegram alert |
| 3 | กราฟย้อนหลัง, สรุปรายวัน, ติดตาม rate limit ของ Free tier |
| 4 (ถ้าต้องการ) | Docker / systemd service, ดู GPU/VRAM ของ Local model ด้วย `nvidia-smi` |

## 13. เรื่องที่ต้องยืนยันก่อนเริ่มเขียนโค้ด

1. **Sparkx 2.5** คือโมเดลหรือ runtime ตัวไหน และรันด้วยอะไร (Ollama / llama.cpp / LM Studio / vLLM) ที่ port เท่าไร
2. Qwen 3.5 รันด้วยอะไร ที่ port เท่าไร
3. Hermes ติดตั้งที่เครื่องไหน, OS อะไร, `~/.hermes` อยู่ path ไหน
4. ชื่อโมเดลจริงของ GLM Flash, Nemotron 3 และ MiMo ที่ใช้
5. ช่องทางแจ้งเตือน: Telegram, LINE หรือ Email
6. ChatGPT / Claude ใช้ผ่าน API key หรือใช้แค่แอปแบบ subscription (ถ้าใช้แค่แอป จะเช็คได้เฉพาะ status page)
