# AI Monitor — Hand-off: การ์ด Hermes Agent (Sessions / Tokens / Agents / Kanban)

**วันที่:** 2026-09-25 · **Branch:** `claude/hopeful-faraday-7c9bsw` · **Tests:** 33 passed · **EXE:** build ผ่าน (Release `ai-monitor-latest`)
ต่อจาก `docs/mimo-changes.md`

---

## 1. ปัญหาที่ผู้ใช้เห็น

การ์ด Hermes Agent ขึ้น `SESSIONS 0`, `TOKENS 0`, `AGENTS 0` ทั้งที่ผู้ใช้สั่ง Hermes ทำงานอยู่ 2 session และ Kanban กำลังรัน

| ช่อง | สาเหตุ |
|------|--------|
| SESSIONS / TOKENS | อ่านแค่ `%LOCALAPPDATA%\hermes\state.db` ของ Hermes หลัก — แต่ profile (เช่น `mimo`) เก็บ session แยกใน `profiles\<ชื่อ>\state.db` |
| AGENTS | อ่านแค่ `active_agents` จาก `gateway_state.json` — gateway เปิดอยู่แต่ 0 ช่องทาง, งานผ่าน CLI จึงไม่ถูกนับ |
| Kanban | ไม่เคยอ่านเลย |

---

## 2. สิ่งที่แก้ (`ai_monitor/checkers/hermes.py`, `ai_monitor/web/app.js`, `app.css`)

### 2.1 Sessions / Tokens รวมทุก profile
- อ่าน `state.db` ของ Hermes หลัก + `profiles\*\state.db` แล้วรวมกัน
- **SESSIONS** = session ที่เริ่มวันนี้ (เวลาเครื่อง) บรรทัดเล็กบอกว่ามาจาก profile ไหน เช่น `วันนี้ · main, mimo`
- **TOKENS** = `input + output + cache_read + cache_write` ของ session วันนี้ (schema เก่าที่ไม่มี cache column → ใช้ `input + output`)
- บรรทัดเล็กของ TOKENS: `ล่าสุด <เวลา> · <profile> · <source>` จาก session ล่าสุดของทุก db

### 2.2 AGENTS = session ที่กำลังทำงาน
- นับ session ที่ `ended_at IS NULL` และ `COALESCE(last_activity_at, started_at)` อยู่ใน **5 นาทีล่าสุด** (Hermes อัปเดต `last_activity_at` ราว ๆ ทุก 60 วินาที)
- รวมทุก profile ทั้ง CLI และ gateway; ถ้า gateway มี `active_agents` จะแสดงต่อท้ายเป็น `กำลังทำงาน · gateway N`
- schema เก่าที่ไม่มี `ended_at` / `last_activity_at` → นับเป็น 0 (ไม่ error)

### 2.3 แถว KANBAN (ใหม่)
- ตำแหน่ง: ใต้ชื่อ Hermes / ชิปช่องทาง, เหนือกล่อง 6 ช่อง
- แหล่งข้อมูล (อ่านอย่างเดียว, ตามตำแหน่งที่ Hermes ใช้จริงใน `hermes_cli/kanban_db.py`):
  - board `default`: `<root>\kanban.db`
  - board อื่น: `<root>\kanban\boards\*\kanban.db`
  - `<root>` = `HERMES_KANBAN_HOME` ถ้าตั้งไว้ ไม่งั้น Hermes home หลัก
- นับจากตาราง `tasks` คอลัมน์ `status` รวมทุก board:

| ป้าย | status ที่นับ |
|------|---------------|
| `RUNNING` (เขียว กะพริบ) | `running` |
| `QUEUE` | `triage`, `todo`, `scheduled`, `ready` |
| `BLOCKED` | `blocked` |
| `REVIEW` | `review` |
| `DONE วันนี้` | `done` และ `completed_at` ≥ เที่ยงคืนวันนี้ |

- ไม่มี `kanban.db` เลย → ไม่แสดงแถวนี้

### 2.4 Test ที่เพิ่ม
- `test_hermes_sessions_include_profiles_and_running` — รวม profile, นับ token รวม cache, นับเฉพาะ session ที่ยังทำงาน, session ค้างเก่าไม่นับ
- `test_hermes_kanban_counts` — หลาย board, การจัดกลุ่ม status, done วันนี้, ไม่มี db → ไม่แสดง

---

## 3. สิ่งที่ต้องทำบนเครื่อง

1. ปิด `AI-Monitor.exe`
2. PowerShell:
   ```powershell
   irm https://raw.githubusercontent.com/rattapoompradit/rattapoompradit/claude/hopeful-faraday-7c9bsw/ai-monitor/install.ps1 | iex
   ```
3. ตรวจ:
   ```powershell
   cd C:\Users\Acer\ai-monitor
   .venv\Scripts\python -m pytest -q      # ต้องได้ 33 passed
   ```
4. เปิด `AI-Monitor.exe` แล้วเทียบกับของจริง:
   - `AGENTS` ควรเท่ากับจำนวน session ที่กำลังทำงาน (ผู้ใช้บอกว่า 2)
   - `SESSIONS` / `TOKENS` ควรไม่เป็น 0 ถ้าวันนี้มีการใช้งานใน profile ใดก็ตาม
   - แถว `KANBAN` ต้องขึ้น และ `RUNNING` ตรงกับงานที่รันอยู่

---

## 4. ถ้าตัวเลขไม่ตรง ให้รายงานกลับมาพร้อมข้อมูลนี้ (ห้ามส่ง key / เนื้อหาแชท)

```powershell
$h = "$env:LOCALAPPDATA\hermes"
"HERMES_HOME=$env:HERMES_HOME  HERMES_KANBAN_HOME=$env:HERMES_KANBAN_HOME"
Get-ChildItem $h -Recurse -Filter state.db  | ForEach-Object { "state.db:  " + $_.FullName }
Get-ChildItem $h -Recurse -Filter kanban.db | ForEach-Object { "kanban.db: " + $_.FullName }
```

และสำหรับแต่ละ `state.db` / `kanban.db` ที่เจอ (ใช้ Python ใน `.venv`):

```powershell
.venv\Scripts\python -c "import sqlite3,sys,time; c=sqlite3.connect(sys.argv[1]); print(c.execute('select count(*), sum(ended_at is null), max(last_activity_at) from sessions').fetchone(), time.time())" "<path ของ state.db>"
.venv\Scripts\python -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute('select status, count(*) from tasks group by status').fetchall())" "<path ของ kanban.db>"
```

สิ่งที่อยากรู้: ไฟล์อยู่ที่ไหน, session ที่รันอยู่มี `ended_at` เป็น NULL และ `last_activity_at` อัปเดตภายใน 5 นาทีไหม, status ของ Kanban ที่รันอยู่ชื่ออะไร

---

## 5. ข้อจำกัดที่รู้อยู่แล้ว

- "กำลังทำงาน" อิง heartbeat `last_activity_at` — ถ้า session เงียบเกิน 5 นาทีระหว่างรอโมเดลตอบยาว ๆ อาจหลุดจากการนับชั่วคราว
- ช่อง MODEL ยังเป็น `model.default` ของ Hermes หลัก (ไม่ใช่โมเดลของ profile ที่กำลังใช้)
- ทั้งหมดอ่านอย่างเดียว ไม่แก้ไฟล์ของ Hermes
