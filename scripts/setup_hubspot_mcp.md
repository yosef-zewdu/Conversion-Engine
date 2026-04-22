# HubSpot Developer Sandbox Setup

Follow these steps once to get a working HubSpot access token and MCP server.

---

## 1. Sign up at developers.hubspot.com

1. Go to <https://developers.hubspot.com> and click **Get started for free**.
2. Create a developer account (no credit card required).
3. From the developer dashboard, click **Create a test account** to get a free sandbox CRM.

---

## 2. Create a Private App

Private Apps are the recommended way to authenticate with the HubSpot API.
They issue a long-lived bearer token scoped to exactly the permissions you grant.

1. Inside your **test account** (not the developer account), go to:
   **Settings → Integrations → Private Apps → Create a private app**
2. Give it a name, e.g. `conversion-engine-dev`.
3. Under the **Scopes** tab, enable the following:

   | Scope | Purpose |
   |---|---|
   | `crm.objects.contacts.read` | Read contact records |
   | `crm.objects.contacts.write` | Create / update contacts |
   | `crm.objects.deals.read` | Read deal records |
   | `crm.objects.deals.write` | Create / update deals |
   | `crm.schemas.contacts.read` | Read contact property schema |

4. Click **Create app** and then **Continue creating**.
5. On the confirmation screen, copy the **Access token** (starts with `pat-`).

---

## 3. Copy the token to `.env`

Open `.env` in the project root and set:

```
HUBSPOT_ACCESS_TOKEN=pat-na1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

---

## 4. Install the HubSpot MCP server

The official HubSpot MCP server lets AI agents call HubSpot tools directly.

```bash
npx @hubspot/mcp-server
```

Or install it globally:

```bash
npm install -g @hubspot/mcp-server
hubspot-mcp-server
```

The server reads `HUBSPOT_ACCESS_TOKEN` from the environment automatically.
Make sure the token is exported before starting the server:

```bash
export $(grep HUBSPOT_ACCESS_TOKEN .env | xargs)
npx @hubspot/mcp-server
```

---

## 5. Verify with the test script

Run the helper script to create a test contact and confirm the token works:

```bash
python scripts/test_hubspot_contact.py
```

Expected output:

```
Contact created successfully.
  id:           12345678
  hs_object_id: 12345678
```

If you see an error, double-check that:
- The token in `.env` starts with `pat-` and has no extra spaces.
- The Private App was created inside the **test account**, not the developer account.
- All five scopes listed above are enabled.
