# Layoffs.fyi Schema Notes

**Source:** `/home/yosef/Desktop/intensive/Layoffs_Data/layoffs_data.csv`  
**Records:** 3,622 layoff events  
**License:** CC-BY  
**Date Range:** 2020-03-11 → 2024-05-24

## Columns

| Column | Type | Null Rate | Notes |
|--------|------|-----------|-------|
| `Company` | string | 0% | Company name — join key to Crunchbase |
| `Location_HQ` | string | ~0% | City/region (e.g., "SF Bay Area", "New York City") |
| `Industry` | string | ~0% | Industry label (e.g., "Transportation", "Marketing") |
| `Laid_Off_Count` | integer | 34% | Absolute headcount affected — often missing |
| `Percentage` | float | 35% | Fraction of workforce (0.0–1.0) — often missing |
| `Date` | date (YYYY-MM-DD) | 0% | Date of layoff event — always present |
| `Source` | URL string | ~0% | Source article URL |
| `Funds_Raised` | float (millions USD) | 10% | Total funding raised by company (in $M) |
| `Stage` | string | 16% | Funding stage at time of layoff |
| `Date_Added` | datetime | 0% | When record was added to dataset |
| `Country` | string | 0% | Country name (e.g., "United States") |
| `List_of_Employees_Laid_Off` | string | 98% | Almost always "Unknown" — not useful |

## Key Fields for Conversion Engine

### Company Matching
- **`Company`** — Use for fuzzy name matching against Crunchbase `name` field
- No Crunchbase ID — must join by company name (fuzzy match recommended)

### Layoff Event Detection (120-day window)
- **`Date`** — Filter: `Date >= today - 120 days`
  - Format: `YYYY-MM-DD` (ISO 8601, always present)
- **`Laid_Off_Count`** — Headcount affected (integer, 34% null)
- **`Percentage`** — Fraction of workforce cut (float 0.0–1.0, 35% null)
  - Example: `0.06` = 6%, `0.25` = 25%

### Stage Values
`Seed`, `Series A`, `Series B`, `Series C`, `Series D`, `Series E`, `Series F`, `Series G`, `Series H`, `Series I`, `Series J`, `Post-IPO`, `Private Equity`, `Acquired`, `Subsidiary`, `Unknown`

## Data Quality Notes

1. **`Laid_Off_Count` and `Percentage` are both 34–35% null** — handle gracefully; record as `null` in `HiringSignalBrief`
2. **No Crunchbase ID** — join to Crunchbase by company name; use fuzzy matching (e.g., `difflib.SequenceMatcher` or `rapidfuzz`)
3. **Date range ends May 2024** — data is not live; treat as static snapshot
4. **`Funds_Raised` is in millions USD** — not the same as Crunchbase `money_raised` (which is in USD)
5. **`Stage` is 16% null** — use Crunchbase `funding_rounds_list` as primary source for stage

## Implementation Notes for `LayoffScanner`

```python
# Filter logic
from datetime import date, timedelta

cutoff = date.today() - timedelta(days=120)
matches = [
    row for row in layoffs_data
    if row['Company'].lower() == company_name.lower()   # or fuzzy match
    and date.fromisoformat(row['Date']) >= cutoff
]

# Result mapping → HiringSignalBrief.layoff_event
{
    "event_date": row['Date'],                          # str YYYY-MM-DD
    "headcount_affected": int(row['Laid_Off_Count']) if row['Laid_Off_Count'] else None,
    "percentage_cut": float(row['Percentage']) if row['Percentage'] else None,
    "confidence": "high"  # direct CSV record
}
```
