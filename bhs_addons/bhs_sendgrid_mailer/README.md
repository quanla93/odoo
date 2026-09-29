# BHSoft SendGrid Mailer

`bhs_sendgrid_mailer` is an Odoo 20 addon for importing and processing personalized bulk email through SendGrid. It provides a managed queue, randomized cumulative delays, delivery-status synchronization, duplicate filtering, recipient suppression, data retention, scheduled-message cancellation, and optional Google Sheets synchronization.

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

## ZIP package layout

The release ZIP must extract to exactly one addon directory:

```text
bhs_sendgrid_mailer/
├── __init__.py
├── __manifest__.py
├── controllers/
├── data/
├── models/
├── security/
├── services/
├── views/
└── wizard/
```

Do not add an extra version directory such as `bhs_sendgrid_mailer-20.0.1.2.0/bhs_sendgrid_mailer/`. Odoo must be able to find `bhs_sendgrid_mailer/__manifest__.py` directly below an addons directory.

## Install on Odoo On-Premise

1. Back up the PostgreSQL database and Odoo filestore.
2. Extract the ZIP into a custom addons directory, for example:

   ```bash
   sudo unzip bhs_sendgrid_mailer-20.0.1.2.0.zip -d /opt/odoo/custom_addons
   ```

3. Verify the resulting path:

   ```text
   /opt/odoo/custom_addons/bhs_sendgrid_mailer/__manifest__.py
   ```

4. Install the required Python packages in the same Python environment that runs Odoo:

   ```bash
   /opt/odoo/venv/bin/pip install \
     cryptography \
     google-auth \
     google-api-python-client \
     openpyxl
   ```

5. Ensure the parent directory is present in `odoo.conf`:

   ```ini
   [options]
   addons_path = /opt/odoo/addons,/opt/odoo/custom_addons
   ```

6. Restart Odoo and update the Apps list.
7. Install **BHSoft SendGrid Mailer** from Apps, or run:

   ```bash
   ./odoo-bin \
     -c /etc/odoo/odoo.conf \
     -d <database> \
     -i bhs_sendgrid_mailer \
     --stop-after-init
   ```

For Docker, do not install Python packages interactively in a running container because they disappear when the container is recreated. Add the packages to the image requirements, rebuild the image, and mount the parent addon directory into the container's `addons_path`.

## Install on Odoo.sh

Odoo.sh deploys source code from the Git repository connected to the Odoo.sh project. It does not install a downloaded addon ZIP directly into a persistent server directory.

1. Download and extract the release ZIP locally.
2. Copy `bhs_sendgrid_mailer/` into the root of the customer's Odoo.sh Git repository:

   ```text
   customer-odoo-sh/
   ├── bhs_sendgrid_mailer/
   │   ├── __init__.py
   │   └── __manifest__.py
   └── requirements.txt
   ```

3. Add the Python dependencies to the repository-level `requirements.txt`:

   ```text
   cryptography
   google-auth
   google-api-python-client
   openpyxl
   ```

4. Commit and push the files to a development branch:

   ```bash
   git add bhs_sendgrid_mailer requirements.txt
   git commit -m "Add BHSoft SendGrid Mailer"
   git push origin <development-branch>
   ```

5. Odoo.sh automatically starts a build for the pushed commit. Development builds install custom modules automatically.
6. Validate the module and its tests on the development build, then merge the branch into staging.
7. After validation on a neutralized production copy, merge staging into production.

For staging and production builds, Odoo.sh automatically upgrades custom modules that are already installed when their manifest version increases. A newly added module is not installed automatically in those stages; install it once from **Apps** or first introduce it through a development build before promoting the branch.

For ongoing Git-based delivery, the module may also be kept in a separate repository and linked to the customer's Odoo.sh repository as a Git submodule. Private repositories require SSH access/deploy-key configuration. Pin customer deployments to a reviewed release tag or commit instead of tracking an unrestricted moving branch.

## Upgrade

Always back up the PostgreSQL database and Odoo filestore before upgrading. Replace the addon source and run:

```bash
./odoo-bin \
  -c /etc/odoo/odoo.conf \
  -d <database> \
  -u bhs_sendgrid_mailer \
  --stop-after-init
```

Version `20.0.1.2.0` includes a post-migration that creates the durable suppression registry, backfills suppressions from existing bounced/spam-report queues, and initializes terminal timestamps. Do not uninstall and reinstall the module during this upgrade: run an in-place module upgrade so the migration executes and existing configuration is preserved.

Detailed upgrade, rollback, and validation instructions are available in [`docs/UPGRADE_1_2.md`](docs/UPGRADE_1_2.md).

Migration scripts are only required by releases that explicitly document a data-model migration. They are not needed for a fresh installation.

## Configuration

Administrators can configure:

- SendGrid API key and sender email;
- signed SendGrid Event Webhook updates;
- SendGrid Activity API polling and lookback period;
- Google Service Account credentials and Google Sheets sources;
- duplicate filtering for each import batch;
- scheduled queue processing and Odoo status synchronization;
- batch size and minimum/maximum delay between messages;
- retention periods for terminal queues, provider events, diagnostic logs, and Google Sheets sync audit records.

Secrets are stored as Odoo system parameters. Do not commit API keys, webhook verification keys, or Google Service Account JSON to Git.

## Suppression policy

The suppression list keeps one normalized row per blocked email address. It blocks future submissions after a SendGrid hard bounce, spam report, unsubscribe, group unsubscribe, or an administrator's manual block. Deferred and dropped events do not automatically suppress an address, and a successful delivery does not globally block that recipient in later campaigns.

Excel imports and Google Sheets synchronization still create a queue row for a suppressed recipient, but mark it **Skipped** with the suppression reason. The sender rechecks suppression when claiming pending work and immediately before provider submission. A scheduled future SendGrid batch is cancelled when possible if its address becomes suppressed.

Administrators may deactivate a suppression deliberately. Google Sheets rows skipped by that inactive suppression can return to Pending on the next synchronization; other terminal or duplicate rows are not reopened.

## Data retention

Default retention periods are:

- terminal non-Google-Sheet queue records: 90 days;
- SendGrid events and diagnostic text logs: 30 days;
- Google Sheets row-level synchronization audit: 30 days;
- Google Sheets run summaries: 365 days;
- suppression records: retained indefinitely.

Set a configurable retention period to `0` to disable that cleanup. Terminal queue records linked to Google Sheets are compacted rather than deleted so their durable row identity and final state continue to prevent accidental recreation and resending.

## Workflow

1. Import recipients from Excel or synchronize a configured Google Sheets source.
2. Validate and deduplicate email addresses within the import batch.
3. Process the queue in bounded batches with cumulative randomized delays.
4. Submit messages to SendGrid, or fall back to Odoo's mail queue when no API key is configured.
5. Synchronize processed, delivered, opened, clicked, bounced, dropped, spam-report, unsubscribe, and group-unsubscribe events through signed webhooks, API polling, or both.
6. Cancel scheduled SendGrid messages before their send time when the provider allows it.

## Webhook

Configure SendGrid Event Webhook to send signed HTTP POST requests to:

```text
https://<odoo-host>/webhook/sendgrid
```

Enable signature verification and store the SendGrid verification public key in the module settings before enabling webhook tracking. Enable **Unsubscribe** and **Group Unsubscribe** events when SendGrid ASM is used so those recipients enter the suppression list.

## Security and operations

- Restrict module settings to system administrators.
- Use HTTPS for public webhook traffic.
- Back up both the database and filestore.
- Test upgrades against a restored backup before deploying to production.
- Never include real customer lists, credentials, database dumps, or filestore data in an addon package.
