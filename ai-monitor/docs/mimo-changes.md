# AI Monitor — รายละเอียดการแก้การ์ด MiMo

**วันที่:** 2026-09-24 · **Branch:** `claude/hopeful-faraday-7c9bsw` · **Tests:** 30 passed (หลังรอบ 2 ดู §7) · **EXE:** build ผ่าน (Release `ai-monitor-latest`)

---

## 1. สรุปปัญหาที่เจอ

| # | อาการ | สาเหตุ |
|---|-------|--------|
| 1 | การ์ด MiMo ขึ้น `API key ใช้ไม่ได้ (HTTP 401)` | key เป็น **Token Plan** (`tp-...`) แต่เรียกไปที่ `api.xiaomimimo.com` ซึ่งเป็น endpoint ของ key แบบจ่ายตามใช้ |
| 2 | ใส่ key แล้วยัง 401 | key ใน `.env` ของ AI Monitor ไม่ตรงกับ key ที่ MiMo ยอมรับ |
| 3 | `ยังไม่ได้ใส่ MIMO_API_KEY ใน .env (และไม่พบใน Hermes)` | Hermes ไม่ได้เก็บ key ใน `.env` แต่เก็บใน **credential pool** (`profiles\mimo\auth.json`) |
| 4 | แก้โค้ดแล้วไม่มีผล | โปรแกรมที่รันอยู่คือ `AI-Monitor.exe` ตัวเก่า ต้องติดตั้ง / build ใหม่ |
| 5 | การ์ด Hermes ขึ้น "Gateway ไม่ได้รัน" ผิด ๆ | `HERMES_HOME` ชี้ไปที่ `profiles\mimo` ซึ่งไม่มี gateway ของตัวเอง |
| 6 | `24ชม. 0%` ถูกเข้าใจว่าเป็นโควตา | จริง ๆ คือ uptime (% การเช็คสำเร็จ) ที่ยังมี 401 ค้างในประวัติ |
| 7 | ไม่มีข้อมูลโควตา MiMo | MiMo ไม่มี API โควตา และไม่ส่ง header `x-ratelimit-*` |

---

## 2. สิ่งที่แก้ (เรียงตาม commit)

### 2.1 แสดงข้อความ error จริงจากผู้ให้บริการ
- การ์ด API แสดงข้อความที่เซิร์ฟเวอร์ตอบกลับ เช่น `API key ใช้ไม่ได้ (HTTP 401: Invalid API Key)`
- เพิ่ม option `auth_header` (เช่น `api-key`) สำหรับเจ้าที่ไม่ใช้ `Authorization: Bearer`
- ไฟล์: `ai_monitor/checkers/openai_compat.py`

### 2.2 อ่าน `.env` / `config.yaml` ที่เซฟด้วย Notepad ได้
- อ่านด้วย `utf-8-sig` เพื่อไม่ให้ BOM ทำให้บรรทัดแรกหาย
- ไฟล์: `ai_monitor/config.py`

### 2.3 เตือนเมื่อคัดลอก key แบบที่ถูกปิดไว้
- ถ้า key มี `*` (คัดลอกจากข้อความ `tp-sipyn8****…`) จะไม่ส่งไปเรียก API และแจ้งให้กดปุ่ม Copy
- ไฟล์: `ai_monitor/checkers/openai_compat.py`

### 2.4 ใช้ Dedicated Base URL ของ Token Plan
- ค่าเริ่มต้นของ MiMo: `https://token-plan-sgp.xiaomimimo.com/v1`
- เช็คทุก 5 นาที (`interval_s: 300`) เรียกแค่ `GET /models` ไม่ส่ง prompt และไม่ใช้ credit
- ไฟล์: `config.yaml`

### 2.5 ใช้ key เดียวกับ Hermes
ลำดับการหา key (`resolve_key`):

1. `key_from: env` → ใช้ `.env` ของ AI Monitor เท่านั้น
2. `key_from: auto` (ค่าเริ่มต้น) → `.env` ของ AI Monitor ก่อน ถ้าว่างค่อยไปหาใน Hermes
3. `key_from: hermes` (ค่าเริ่มต้นของ MiMo) → หาใน Hermes ก่อน:
   1. `%LOCALAPPDATA%\hermes\.env` → `XIAOMI_API_KEY` (+ `XIAOMI_BASE_URL`)
   2. **credential pool** → `auth.json` แล้ว `profiles\*\auth.json` → `credential_pool.xiaomi[]`
      - เลือกรายการที่ `priority` ต่ำสุด และข้ามรายการที่ `last_status: exhausted`
      - endpoint: `inference_base_url` > `XIAOMI_BASE_URL` > `base_url` ใน config.yaml > `base_url` ของ pool (ดู §7.1)

| AI Monitor | Hermes `.env` | Hermes credential pool |
|------------|---------------|------------------------|
| `MIMO_API_KEY` | `XIAOMI_API_KEY`, `XIAOMI_BASE_URL` | `xiaomi` |
| `ZAI_API_KEY` | `GLM_API_KEY`, `GLM_BASE_URL` | `zai` |
| `NVIDIA_API_KEY` | `NVIDIA_API_KEY`, `NVIDIA_BASE_URL` | `nvidia` |

- อ่านอย่างเดียว ไม่แก้ไฟล์ของ Hermes และส่ง key ไปที่ผู้ให้บริการเจ้าของเท่านั้น
- การ์ดจะต่อท้ายว่า `· key จาก Hermes` เมื่อใช้ key ของ Hermes
- ไฟล์: `ai_monitor/checkers/openai_compat.py`, `ai_monitor/checkers/hermes.py` (`read_pool_credential`), `ai_monitor/config.py`

### 2.6 `HERMES_HOME` ชี้ไปที่ profile
- ถ้า `HERMES_HOME` เป็น `<root>\profiles\<ชื่อ>` จะใช้ `<root>` แทน (gateway, state, log อยู่ที่นั่น)
- ไฟล์: `ai_monitor/checkers/hermes.py` (`default_home`)

### 2.7 ป้าย `UP 24H`
- เปลี่ยน `24H` → `UP 24H` บนทุกการ์ด หน้ารายละเอียดเขียนว่า "เช็คสำเร็จ ไม่ใช่โควตา"
- ไฟล์: `ai_monitor/web/app.js`

### 2.8 แถบ Credits รายเดือน (ประมาณการ)
MiMo ไม่มี API โควตา จึงคำนวณจากข้อมูลการใช้งานที่ Hermes บันทึกเอง

- **แหล่งข้อมูล:** `state.db` ของ Hermes หลัก + `profiles\*\state.db` ตาราง `session_model_usage`
  - `input_tokens` = input ที่ไม่โดน cache (Hermes หัก cache ออกแล้ว)
  - `cache_read_tokens` = input ที่โดน cache
  - `cache_write_tokens` = คิดเป็น cache miss
  - `output_tokens`
- **สูตร:** `credits = hit × rate[0] + (miss + write) × rate[1] + output × rate[2]`
  คูณ `0.8` ถ้า `last_seen` อยู่ช่วง 16:00–24:00 UTC (23:00–07:00 เวลาไทย)
- **รอบบิล:** 30 วัน นับย้อนจาก `renews_at` และเลื่อนไปข้างหน้าเองเมื่อต่ออายุอัตโนมัติ
- **จับคู่อัตรา:** ชื่อโมเดลต้องตรงเป๊ะ (ตัด vendor prefix / `:tag` ออกก่อน) ดู §7.2
- **โมเดลที่ไม่มีอัตรา:** ไม่นับ และการ์ดจะแสดงชื่อไว้
- **การแสดงผล:** `Credits (ประมาณ) · 754.0M / 4.10B · 18.4% · รีเซ็ต 22 ต.ค.` พร้อมหมายเหตุ "ประมาณการจาก token ที่ Hermes ใช้ (เครื่องมืออื่นไม่นับ)" และลิงก์ console ในหน้ารายละเอียด
- ไฟล์ใหม่: `ai_monitor/checkers/credits.py`

---

## 3. Config ที่ต้องมีใน `config.yaml` (ส่วน MiMo)

```yaml
  - id: mimo
    name: MiMo
    group: Cloud
    type: openai_compat
    base_url: https://token-plan-sgp.xiaomimimo.com/v1
    api_key_env: MIMO_API_KEY
    key_from: hermes
    interval_s: 300
    model_hint: mimo
    credits:
      plan: Lite
      monthly: 4100000000
      renews_at: "2026-10-22T23:59:59Z"
      cycle_days: 30
      match: mimo
      rates:                        # [input cache hit, input cache miss, output] ต่อ token
        mimo-v2.6-pro: [2.5, 300, 600]
        mimo-v2.6-flash: [2, 100, 200]
        mimo-v2.5-pro: [2.5, 300, 600]
        mimo-v2.5: [2, 100, 200]
      offpeak_utc: [16, 24]
      offpeak_factor: 0.8
      console_url: https://platform.xiaomimimo.com/#/console/plan-manage
```

> ย่อหน้าด้วย Space เท่านั้น: `- id:` 2 ช่อง, บรรทัดในส่วน MiMo 4 ช่อง, ใต้ `credits:` 6 ช่อง, ใต้ `rates:` 8 ช่อง

---

## 4. วิธีอัปเดตบนเครื่อง

1. ปิด AI Monitor (Alt+F4)
2. PowerShell:
   ```powershell
   irm https://raw.githubusercontent.com/rattapoompradit/rattapoompradit/claude/hopeful-faraday-7c9bsw/ai-monitor/install.ps1 | iex
   ```
3. เพิ่มหัวข้อ `credits:` ใน `C:\Users\Acer\ai-monitor\config.yaml` ตามข้อ 3 (ตัวติดตั้งไม่ทับไฟล์นี้)
4. ไม่บังคับ: ลบ `MIMO_API_KEY=...` ใน `.env` ของ AI Monitor เพื่อให้ใช้ key จาก Hermes เสมอ
5. ตรวจด้วย `check.bat` → ควรเห็น `[OK  ] MiMo  API ใช้ได้ · key จาก Hermes`

---

## 5. การทดสอบ

| Test | ตรวจอะไร |
|------|----------|
| `test_openai_compat_error_message_and_auth_header` | ข้อความ error จากเซิร์ฟเวอร์, header `api-key` |
| `test_openai_compat_masked_key` | key ที่มี `*` ไม่ถูกส่ง |
| `test_api_key_from_hermes` | ลำดับ `.env` / Hermes `.env` / base URL ของ Hermes |
| `test_api_key_from_hermes_credential_pool` | อ่าน `profiles\mimo\auth.json`, priority, exhausted, base_url |
| `test_hermes_home_ignores_profile_override` | `HERMES_HOME` ที่เป็น profile |
| `test_mimo_credit_estimate` | สูตร credit, cache hit/miss, off-peak 0.8, รอบบิลเลื่อนเอง, ไม่นับรอบก่อน / โมเดลอื่น, แจ้งโมเดลที่ไม่มีอัตรา |

ผล: **28 passed** · GitHub Actions (Windows) build + selftest ผ่าน

---

## 6. งานที่ยังค้าง

- [x] **อัตรา credit ของ `mimo-v2.6-pro` / `mimo-v2.6-flash`** — เพิ่มแล้ว (§7.3)
- [ ] ยอดจริงดูได้เฉพาะหน้า console (ต้องล็อกอิน) — ตั้งใจไม่ดึงด้วย cookie เพราะไม่เป็นทางการและเปราะบาง
- [ ] ถ้าใช้ Token Plan เดียวกันกับเครื่องมืออื่น (Claude Code, OpenClaw ฯลฯ) ยอดจริงจะสูงกว่าที่แถบประมาณไว้

---

## 7. รอบ 2 — แก้ตาม hand-off จากเครื่องผู้ใช้ (2026-09-24)

### 7.1 Regression: MiMo 401 เพราะ `base_url` ของ credential pool ทับ config
- **อาการ:** หลัง deploy การ์ด MiMo ขึ้น `HTTP 401: Invalid API Key · key จาก Hermes` เพราะเรียก `https://api.xiaomimimo.com/v1`
- **สาเหตุ:** `hermes auth add` เติม `base_url` ค่าเริ่มต้น (endpoint แบบจ่ายตามใช้) ให้ key แบบ Token Plan และโค้ดเดิมให้ค่านี้ชนะ `base_url` ใน `config.yaml`
- **แก้ที่โค้ด:** `read_pool_credential` คืน `(key, inference_base_url, base_url)` แยกกัน และ `resolve_key` เลือก endpoint ตามลำดับ
  1. `inference_base_url` ของ pool (ที่ Hermes route จริง)
  2. `XIAOMI_BASE_URL` ใน `.env` ของ Hermes
  3. `base_url` ใน `config.yaml` ของ AI Monitor
  4. `base_url` ของ pool (ค่าเริ่มต้นที่ `hermes auth add` เติม) — ใช้เป็นทางสุดท้าย
- **test:** แก้ fixture ให้สอดคล้อง และเพิ่ม `test_pool_default_base_url_does_not_override_config` (สถานการณ์จริงบนเครื่องผู้ใช้)
- การแก้ `auth.json` บนเครื่อง (สำรองไว้ที่ `auth.json.bak-20260924`) ไม่จำเป็นอีกต่อไป แต่คงไว้ได้ ไม่มีผลเสีย

### 7.2 จับคู่อัตรา credit แบบชื่อตรงเป๊ะ
- เดิมจับคู่แบบ "ชื่อที่อยู่ในชื่อโมเดล" ทำให้ `mimo-v2.6-pro-ultraspeed` (แพงกว่า ~10 เท่า) ใช้อัตราของ `mimo-v2.6-pro`
- ตอนนี้ตัด vendor prefix (`xiaomi/`) และ `:tag` แล้วต้องตรงกันทั้งชื่อ ถ้าไม่ตรงจะไม่นับและแจ้งชื่อบนการ์ด
- **test:** `test_credit_rates_match_exact_model_only`

### 7.3 อัตรา v2.6
| model | cache hit | cache miss | output |
|-------|-----------|------------|--------|
| mimo-v2.6-pro | 2.5 | 300 | 600 |
| mimo-v2.6-flash | 2 | 100 | 200 |
| mimo-v2.5-pro | 2.5 | 300 | 600 |
| mimo-v2.5 | 2 | 100 | 200 |

**ที่มา:** <https://mimo.mi.com/docs/en-US/quick-start/faq/token-plan> (Usage & Quota) และหน้า Token Plan / team — หน้า `price/token-plan` (อัปเดต 15 ก.ค. 2026) ยังมีแค่ v2.5 อย่าใช้เป็นแหล่งอัตรา v2.6

### 7.4 ยังค้าง (นอก repo)
- [ ] ยืนยัน `renews_at` กับหน้า Plan usage (แก้ใน `config.yaml` อย่างเดียว)
- [ ] (ไม่บังคับ) รายงาน upstream: `hermes auth add` ไม่ควรเติม `base_url` แบบจ่ายตามใช้ให้ key `tp-`
- [ ] `docs/mimo-usage-brief-for-claude.md` อยู่บนเครื่องผู้ใช้ ไม่ได้อยู่ใน repo — อัปเดตแหล่งอัตราตาม §7.3
