# Upgrade to version 20.0.1.2.0

This release adds a durable recipient suppression list and bounded retention for operational email data. It is an in-place upgrade of the existing `bhs_sendgrid_mailer` addon. Do not uninstall the module, because uninstalling removes module-owned data instead of applying the migration.

## What changes in 1.2

- Blocks future mail to addresses with a SendGrid hard bounce, spam report, unsubscribe, or group unsubscribe.
- Keeps successful deliveries eligible for later campaigns.
- Shows suppressed imports as **Skipped** queue rows with a reason.
- Rechecks suppression during queue claim and immediately before provider submission.
- Attempts to cancel scheduled SendGrid or unsent SMTP messages when an address becomes suppressed.
- Stores provider events as structured records and limits operational-data growth.
- Deletes old terminal non-Google-Sheet queues and compacts terminal Google Sheet queues while retaining their durable row identity.
- Adds configurable retention periods. A value of `0` disables the corresponding cleanup.

Default retention periods:

| Data | Default |
|---|---:|
| Terminal non-Google-Sheet queues | 90 days |
| Provider events and diagnostic logs | 30 days |
| Google Sheet row audit | 30 days |
| Google Sheet run summaries | 365 days |
| Suppressions | Indefinite |

## Before upgrading

1. Back up the PostgreSQL database and Odoo filestore.
2. Test the upgrade on a restored copy before production deployment.
3. Confirm the Odoo runtime has these Python packages:
   - `cryptography`
   - `google-auth`
   - `google-api-python-client`
   - `openpyxl`
4. Keep the existing module directory name exactly `bhs_sendgrid_mailer`.
5. Do not include API keys, webhook public keys, Google Service Account JSON, customer lists, database dumps, or filestore content in the release package or Git repository.

## Upgrade from the ZIP package

The ZIP must extract directly to one addon directory:

```text
bhs_sendgrid_mailer/
├── __init__.py
├── __manifest__.py
├── migrations/
├── models/
└── ...
```

Stop Odoo, replace the old `bhs_sendgrid_mailer` directory with the directory from the ZIP, then run the module upgrade:

```bash
./odoo-bin \
  -c /etc/odoo/odoo.conf \
  -d <database> \
  -u bhs_sendgrid_mailer \
  --stop-after-init
```

Start Odoo normally after the command completes successfully.

## Upgrade in this Docker Compose repository

From the repository root:

```bash
docker compose stop web

docker compose run --rm web \
  python3 ./odoo-bin \
  -c /app/odoo.conf \
  -d bhsoft_v20_db \
  -u bhs_sendgrid_mailer \
  --stop-after-init \
  --no-http

docker compose up -d web
```

Check application readiness:

```bash
curl -I http://127.0.0.1:8069/web/login
```

A successful response should return HTTP 200. If startup or upgrade fails, inspect the logs:

```bash
docker compose logs --tail=200 web
```

## Upgrade from the Odoo interface

Command-line upgrade is preferred for this release because it includes a database migration. If command-line access is unavailable:

1. Stop Odoo and replace the addon source.
2. Start Odoo.
3. Enable developer mode.
4. Open **Apps** and run **Update Apps List**.
5. Find **BHSoft SendGrid Mailer** and select **Upgrade**.
6. Review the server log and confirm the migration completed without errors.

Do not use **Uninstall** followed by **Install** as an upgrade method.

## Post-upgrade validation

1. Confirm the module version is `20.0.1.2.0`.
2. Open **BHSoft Mailer → Suppression List** and verify administrators can create and deactivate a manual suppression.
3. Import an Excel row using a suppressed address. Confirm a visible queue row is created with status **Skipped** and a suppression reason.
4. Synchronize a Google Sheet row using a suppressed address and confirm the same behavior.
5. Deactivate the suppression, synchronize the Sheet again, and confirm only the row skipped by that suppression can return to Pending.
6. Confirm the retention settings appear in **BHSoft Mailer → Settings**.
7. Confirm the SendGrid Event Webhook verification key is still configured and invalid or unsigned requests are rejected.
8. If SendGrid ASM is used, enable **Unsubscribe** and **Group Unsubscribe** events in the SendGrid Event Webhook configuration.

## Operational notes

- Webhook signature verification remains mandatory. Suppression from unmatched webhook events is accepted only from a valid signed payload.
- A SendGrid timeout may leave a row in **Provider Outcome Unknown**. Reconcile it with SendGrid before retrying to avoid duplicate delivery.
- Deferred and dropped events do not automatically suppress recipients.
- Old signed webhook requests outside the accepted freshness window are rejected.
- Suppression records are retained indefinitely and are not removed by retention cleanup.

## Rollback

Do not roll back only the addon files after the migration has run. The upgraded database contains new models, fields, statuses, and references that older code does not understand.

To roll back safely:

1. Stop Odoo.
2. Restore the database and filestore backup taken before the upgrade.
3. Restore the previous addon version.
4. Start Odoo and validate the restored environment.
