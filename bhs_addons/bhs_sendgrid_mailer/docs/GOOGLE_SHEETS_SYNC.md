# Google Sheets synchronization

The module pulls rows from Google Sheets on a scheduled job. Import and email delivery are separate: synchronization creates or updates pending queue records, while the existing mail cron submits them later.

## 1. Google Cloud setup

1. Create a Google Cloud Service Account and enable **Google Sheets API**.
2. Create a JSON key for that Service Account.
3. In Odoo, open **BHSoft Mailer → Settings** and paste the full JSON into **Google Service Account JSON**.
4. Save and click **Validate Credentials**.
5. Share each spreadsheet with the displayed Service Account email as **Editor**. Editor permission is required because Odoo writes stable row IDs back to the sheet.

The credential is stored in `ir.config_parameter`, like the SendGrid API key. Only System Administrators should have access to Settings and database backups.

## 2. Sheet columns

The default mapping supports the existing `Sending-Emails.xlsx` structure:

| Column | Required | Notes |
|---|---|---|
| `Odoo Row ID` | Managed by Odoo | Leave it absent or empty for new rows. Odoo creates the header in the first unused column within the configured range, then writes a UUID into each row. |
| `Contact` | No | Recipient display name. |
| `Email` | Yes | Recipient email address. |
| `Subject` | Yes | Email subject. |
| `Body` | Yes | Plain text. Line breaks are preserved and sent to SendGrid as `text/plain`. |
| `HubSpot ID` | No | Business reference only; it is not a unique synchronization key. |
| `LinkedIn` | No | LinkedIn URL. |
| `Company` | No | Company name. |
| `Company Website` | No | Company website URL. |

`No.`, `Sending Status`, `Sent date`, and `Sent time` may remain in the sheet but are not imported. Configure a range that includes all source columns plus one free column for `Odoo Row ID`, for example `A:M`. For the supplied `Sending-Emails.xlsx` layout, Odoo places `Odoo Row ID` in column M when that header is absent.

Do not copy an existing `Odoo Row ID` into another row. Duplicate Row IDs are quarantined and neither row is imported.

`Body` is plain text. Odoo preserves spaces and line breaks from the source and submits the value to SendGrid as `text/plain`. HTML tags entered in the Sheet are treated as literal text, not rendered markup.

## 3. Source configuration

Open **BHSoft Mailer → Google Sheet Sources** and create a source with:

- Spreadsheet ID from the Google Sheets URL;
- exact sheet tab name;
- column range and header row;
- synchronization interval;
- column mappings if the headers differ from the defaults.

Use **Test Connection**, then **Sync Now**. Review the Sync Run and queue before enabling automatic synchronization.

The global Google Sheets cron is installed inactive. Enable the scheduled action only after at least one successful manual synchronization.

## 4. Synchronization rules

- A new row receives an `Odoo Row ID` and creates one pending queue record.
- Re-reading an unchanged row does not create another record.
- A changed pending row updates the existing queue record.
- A row changed after it enters processing, scheduling, or a terminal state is not overwritten; it is marked as a source conflict.
- A row removed from the sheet is marked **Missing From Source** and is not automatically cancelled.
- Within one Google Sheet source, rows with the same normalized email, subject, and body are marked duplicate and are not sent.
- Errors are recorded per row. Valid rows in the same synchronization run are still imported.

## 5. Operations and recovery

Review **Sheet Sync Runs** and **Sheet Row Results** for invalid addresses, missing fields, duplicate Row IDs, content duplicates, and source conflicts.

Typical connection failures:

- `403`: the spreadsheet was not shared with the Service Account as Editor;
- `404`: incorrect Spreadsheet ID or deleted spreadsheet;
- missing tab: the configured Sheet Name does not match exactly;
- protected `Odoo Row ID` column: Odoo cannot write UUIDs;
- `429`/`5xx`: Google quota or temporary service failure; the client retries with bounded backoff.

Never place Service Account JSON in source control, chatter, or application logs.

## 6. SendGrid safety settings

If webhook tracking is enabled, copy the **Event Webhook Verification Key** from SendGrid into BHSoft Mailer Settings. Odoo rejects unsigned or invalidly signed webhook requests and de-duplicates repeated events before changing queue state.

A SendGrid timeout or connection failure after submission is marked **Provider Outcome Unknown**. Do not retry that queue row until SendGrid activity has been reconciled; this prevents an automatic duplicate submission when SendGrid accepted the message but its HTTP response did not reach Odoo.
