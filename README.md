# COROS Data Skill

A [Codex](https://codex.openai.com) skill for querying training data from COROS sports watches via the COROS Training Hub API.

## Features

- **Activity Query** - Search running activities by date range (distance, pace, HR, training load, power)
- **Lap Splits** - Fetch per-lap detail for any activity, with automatic effort/rest classification and interval summarization
- **Dashboard** - LTHR, MaxHR, RHR, HRV, recovery %, HR/pace zones, personal records
- **Daily Metrics** - ATI/CTI, training load (7d/28d), fatigue, performance, stamina level
- **Athlete Profile** - Height, weight, birthday, MaxHR, RHR
- **One-call Snapshot** - Structured athlete status report for coaching analysis

## Installation

```bash
codex skill install https://github.com/shenmiguo/coros-data-skill
```

## Configuration

Create `scripts/.env` with your COROS account credentials:

```bash
cp scripts/.env.example scripts/.env
```

```env
COROS_ACCOUNT=your_email_or_phone
COROS_PASSWORD=your_md5_hashed_password
```

To get the MD5 hash of your password:

```bash
echo -n "your_password" | md5sum
```

## API Reference

### CorosClient

| Method | Params | Description |
|--------|--------|-------------|
| `login()` | - | Authenticate and initialize session |
| `fetchActivity(startDay, endDay, size)` | YYYYMMDD | Running activity list |
| `fetchRecentActivities(days, endDate)` | days, YYYY-MM-DD | Recent N days of activities |
| `fetchActivityDetail(labelId, sportType)` | activity ID, sport type | Raw activity detail (lapList, summary, zoneList) |
| `fetchActivityLaps(labelId, sportType)` | activity ID, sport type | Parsed lap splits with effort/rest classification |
| `fetchDashboard()` | - | LTHR/MaxHR/RHR/HRV/recovery/zones/PRs |
| `fetchDashboardDetail()` | - | Daily ATI/CTI/load/fatigue/weekly distance |
| `fetchAccount()` | - | Height/weight/birthday/MaxHR/RHR |
| `fetchProfile()` | - | Display preferences and sport mode config |
| `fetchTrainingPlans()` | - | Training plan list |
| `fetchAthleteSnapshot()` | - | One-call comprehensive status report |

### Sport Types

| Code | Type |
------|------|
| 100 | Outdoor Run |
| 101 | Indoor Run |
| 102 | Trail Run |
| 103 | Track Run |

## Usage

```js
import { CorosClient, fmtPace } from "./coros.js";

const client = new CorosClient(
  process.env.COROS_ACCOUNT,
  process.env.COROS_PASSWORD
);
await client.login();

// Recent 7 days of activities
const activities = await client.fetchRecentActivities(7);

// Full athlete snapshot for coaching analysis
const snapshot = await client.fetchAthleteSnapshot();

// Lap-level interval analysis
const laps = await client.fetchActivityLaps(labelId, 103);
```

## License

MIT
