# Crunchbase ODM Schema Notes

**Source:** `/home/yosef/Desktop/intensive/Crunchbase-dataset-samples/crunchbase-companies-information.csv`  
**Records:** 1,000 companies  
**License:** Apache 2.0  
**Total Columns:** 92

## Key Fields for Conversion Engine

### Company Identification
- **`name`** — Company name (string)
- **`id`** — Crunchbase permalink/slug (string, e.g., "consolety")
- **`uuid`** — Unique identifier (UUID string)

### Sector / Industry
- **`industries`** — JSON array of objects with `id` and `value` fields
  - Example: `[{"id":"e-commerce-275d","value":"E-Commerce"},{"id":"jewelry","value":"Jewelry"}]`
  - Can be `null` or empty array `[]`
  - **Note:** The design doc references `category_groups_list` but the actual field is `industries`

### Employee Count
- **`num_employees`** — String range, not integer
  - Values: `"1-10"`, `"11-50"`, `"51-100"`, `"101-250"`, `"251-500"`, `"501-1000"`, `"1001-5000"`, `"5001-10000"`, `"10001+"`, or empty string `""`
  - **Not a direct integer** — requires parsing to band

### Funding Stage
- **`investment_stage`** — String (often empty in this dataset)
  - Values observed: mostly empty `""`
  - May contain: `"Seed"`, `"Series A"`, `"Series B"`, `"Series C"`, etc. when populated
- **`ipo_status`** — String: `"private"`, `"public"`, `"acquired"`, etc.

### Funding Events
- **`funding_rounds_list`** — JSON array of funding round objects
  - Each round contains:
    - `announced_on` (date string, YYYY-MM-DD)
    - `id` (round identifier)
    - `title` (e.g., "Series A - Cdfortis.com")
    - `money_raised` (object with `currency`, `value`, `value_usd`)
    - `lead_investors` (array of investor objects)
    - `uuid` (round UUID)
  - Example:
    ```json
    [{
      "announced_on": "2015-09-15",
      "id": "cdfortis-com-series-a--d3fa5f88",
      "money_raised": {
        "currency": "CNY",
        "value": 50000000,
        "value_usd": 7849646
      },
      "title": "Series A - Cdfortis.com",
      "uuid": "d3fa5f88-4ee4-4518-bd35-076e1d41d11b"
    }]
    ```
  - Can be empty array `[]` or `null`

- **`funds_raised`** — JSON array of investment events (different structure from `funding_rounds_list`)
  - Each entry:
    - `announced_on` (date)
    - `id` (event ID)
    - `money_raised` (integer, USD)
    - `type` (e.g., "investment")
    - `value` (description string)
  - Can be empty array `[]`

- **`funds_total`** — Total funding raised (often `null` in this dataset)

- **`funding_rounds`** — Object/dict (often empty `{}`)

### Leadership Changes
- **`leadership_hire`** — JSON array of leadership appointment events
  - Each entry:
    - `key_event_date` (date string, YYYY-MM-DD)
    - `label` (headline/description)
    - `link` (source URL)
    - `uuid` (event UUID)
  - Example:
    ```json
    [{
      "key_event_date": "2023-03-22",
      "label": "Williams Blackstock Architects names new CEO, president, leadership team",
      "link": "https://www.bizjournals.com/...",
      "uuid": "b61243f6-5b77-4fe5-858f-8a2f73852e9e"
    }]
    ```
  - Can be empty array `[]`
  - **Note:** Does not specify role (CTO/VP Eng) — requires parsing `label` field

### Layoff Events
- **`layoff`** — JSON array of layoff events
  - Each entry:
    - `key_event_date` (date string, YYYY-MM-DD)
    - `label` (headline/description)
    - `link` (source URL)
    - `uuid` (event UUID)
  - Example:
    ```json
    [{
      "key_event_date": "2023-03-01",
      "label": "Conversational AI Startup Yellow.ai Fires 200 Employees",
      "link": "https://inc42.com/...",
      "uuid": "3c535b3f-ac8f-475b-9184-c46731e8ca3f"
    }]
    ```
  - Can be empty array `[]`
  - **Note:** Does not include structured headcount/percentage — requires parsing `label`

### Tech Stack / AI Signals
- **`builtwith_tech`** — JSON array of detected technologies (BuiltWith data)
- **`siftery_products`** — JSON array of products used (Siftery data)
- **`active_tech_count`** — Integer count of active technologies
- **`builtwith_num_technologies_used`** — Integer count

### Other Useful Fields
- **`founded_date`** — Date string (YYYY-MM-DD)
- **`website`** — Company website URL
- **`location`** — Location string
- **`headquarters_regions`** — JSON array of region objects
- **`country_code`** — Two-letter country code (e.g., "NL", "US")
- **`about`** — Short description
- **`full_description`** — Long description

## Data Quality Notes

1. **Many fields are sparse** — `null`, empty strings `""`, or empty arrays `[]` are common
2. **JSON fields require parsing** — `industries`, `funding_rounds_list`, `leadership_hire`, `layoff` are JSON strings
3. **No direct "category_groups_list"** — use `industries` instead
4. **Employee count is banded** — not a direct integer
5. **Funding stage often empty** — must infer from `funding_rounds_list` and `ipo_status`
6. **Leadership changes lack role detail** — must parse `label` text for "CTO", "VP Engineering", etc.
7. **Layoff events lack structured data** — must parse `label` for headcount/percentage

## Implementation Recommendations

1. **FirmographicEnricher**: Parse `industries` JSON, map `num_employees` bands to integers
2. **FundingEventFetcher**: Parse `funding_rounds_list` JSON, filter by `announced_on` date (last 180 days)
3. **LeadershipChangeDetector**: Parse `leadership_hire` JSON, filter by date (last 90 days), regex match `label` for CTO/VP Eng roles
4. **LayoffScanner**: Cross-reference with layoffs.fyi CSV (more structured) rather than parsing Crunchbase `layoff` labels
5. **AIMaturityScorer**: Use `builtwith_tech`, `siftery_products`, `industries` (check for AI/ML), and `about`/`full_description` text analysis
