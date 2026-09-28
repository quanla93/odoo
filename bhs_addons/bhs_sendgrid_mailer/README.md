# BHSoft SendGrid Mailer

`bhs_sendgrid_mailer` is an Odoo 20 addon for importing and processing personalized bulk email through SendGrid. It provides a managed queue, randomized cumulative delays, delivery-status synchronization, duplicate filtering, scheduled-message cancellation, and optional Google Sheets synchronization.

`bhs_sendgrid_mailer` is the module's original and permanent technical name. A fresh installation does not require any migration or SQL script.

## Requirements

- Odoo 20.0
- PostgreSQL 16 or newer
- Python packages:
  - `cryptography`
  - `google-auth`
  - `google-api-python-client`
  - `openpyxl`

Install the Python dependencies in the same environment or container that runs Odoo. The repository Docker image installs them from the root `requirements.txt`.

## Installation

1. Copy `bhs_sendgrid_mailer/` into a directory listed in Odoo's `addons_path`.
2. Install the Python requirements.
3. Restart Odoo and update the Apps list.
4. Install **BHSoft SendGrid Mailer** from Apps, or run:

```bash
./odoo-bin \
  -c /etc/odoo/odoo.conf \
  -d <database> \
  -i bhs_sendgrid_mailer \
  --stop-after-init
```

For Docker, mount the parent addon directory into the Odoo container and include that path in `addons_path`.

## Upgrade

Always back up the PostgreSQL database and Odoo filestore before upgrading. Replace the addon source and run:

```bash
./odoo-bin \
  -c /etc/odoo/odoo.conf \
  -d <database> \
  -u bhs_sendgrid_mailer \
  --stop-after-init
```

Migration scripts are only required by releases that explicitly document a data-model migration. They are not needed for a fresh installation.

## Configuration

Administrators can configure:

- SendGrid API key and sender email;
- signed SendGrid Event Webhook updates;
- SendGrid Activity API polling and lookback period;
- Google Service Account credentials and Google Sheets sources;
- duplicate filtering for each import batch;
- scheduled queue processing and Odoo status synchronization;
- batch size and minimum/maximum delay between messages.

Secrets are stored as Odoo system parameters. Do not commit API keys, webhook verification keys, or Google Service Account JSON to Git.

## Workflow

1. Import recipients from Excel or synchronize a configured Google Sheets source.
2. Validate and deduplicate email addresses within the import batch.
3. Process the queue in bounded batches with cumulative randomized delays.
4. Submit messages to SendGrid, or fall back to Odoo's mail queue when no API key is configured.
5. Synchronize processed, delivered, opened, clicked, bounced, dropped, and spam-report events through signed webhooks, API polling, or both.
6. Cancel scheduled SendGrid messages before their send time when the provider allows it.

## Webhook

Configure SendGrid Event Webhook to send signed HTTP POST requests to:

```text
https://<odoo-host>/webhook/sendgrid
```

Enable signature verification and store the SendGrid verification public key in the module settings before enabling webhook tracking.

## Security and operations

- Restrict module settings to system administrators.
- Use HTTPS for public webhook traffic.
- Back up both the database and filestore.
- Test upgrades against a restored backup before deploying to production.
- Never include real customer lists, credentials, database dumps, or filestore data in an addon package.
