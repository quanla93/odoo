# BHSoft Odoo 20

This repository is BHSoft's Odoo 20 fork. It keeps the official Odoo repository as an upstream remote and stores BHSoft modules separately from the standard addons.

## Repository layout

- `addons/`: standard Odoo addons;
- `bhs_addons/`: BHSoft custom addons;
- `bhs_addons/bhs_sendgrid_mailer/`: SendGrid mail queue and Google Sheets synchronization;
- `docker-compose.yml`: local Odoo 20 and PostgreSQL 16 development stack.

All BHSoft addon technical names use the `bhs_` prefix. `bhs_sendgrid_mailer` is the mailer's original technical name; a fresh installation does not require a migration or SQL script.

## Git remotes

```text
origin    https://github.com/quanla93/odoo.git
upstream  https://github.com/odoo/odoo.git
```

Use `origin/20.0` for BHSoft development and `upstream/20.0` when synchronizing Odoo updates.

## Local Docker environment

Copy the environment template and choose local-only credentials:

```bash
cp .env.example .env
```

The `.env` file is ignored by Git. Do not commit production passwords or customer-specific settings.

Create the persistent PostgreSQL 16 volume once, then build and start the stack:

```bash
docker volume create odoo-db-pg16
docker compose up -d --build db web
```

The defaults expose:

- Odoo: `http://localhost:8069`
- PostgreSQL: `localhost:5432`

The Odoo filestore is kept in the ignored `odoo_data/` directory. PostgreSQL data is kept in the external `odoo-db-pg16` Docker volume. Neither belongs in Git.

## Odoo configuration

`odoo.conf` is intentionally ignored because it is deployment-specific. A Compose-compatible configuration needs at least:

```ini
[options]
addons_path = addons,bhs_addons
db_host = db
db_port = 5432
db_user = odoo
db_password = <local-password>
http_interface = 0.0.0.0
proxy_mode = True
```

Keep the values synchronized with `.env`. Use a secret manager or protected runtime environment for production credentials.

## Install the BHSoft mailer

For a clean database:

```bash
./odoo-bin \
  -c odoo.conf \
  -d <database> \
  -i bhs_sendgrid_mailer \
  --stop-after-init
```

No migration or manual SQL is needed for a fresh installation.

For a normal future release upgrade, first back up the database and filestore, update the source, and run:

```bash
./odoo-bin \
  -c odoo.conf \
  -d <database> \
  -u bhs_sendgrid_mailer \
  --stop-after-init
```

See `bhs_addons/bhs_sendgrid_mailer/README.md` for module requirements and configuration.

## Repository hygiene

Never commit:

- `.env` or local Odoo configuration containing credentials;
- SendGrid keys or Google Service Account JSON;
- `odoo_data/`, PostgreSQL volumes, database dumps, or backups;
- logs, virtual environments, caches, or customer data.
