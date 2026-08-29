# OdooIndex Connector

Connects an Odoo instance to [OdooIndex.com](https://odooindex.com) so the installed module inventory and version/migration status can be tracked and viewed on the OdooIndex account page.

## Purpose

The module periodically:

1. **Uploads** a list of installed modules and their versions, linked to the Odoo `database.uuid`.
2. **Downloads** information about available updates and migration readiness for a configurable target Odoo version.
3. **Surfaces** the downloaded information inside Odoo for administrators.

## Design Overview

### Instance identity

The Odoo `database.uuid` system parameter (`ir.config_parameter`) is used as the stable instance identifier. It is included in every API call so OdooIndex can correlate data with the right account.

### Configuration

Settings are stored in `ir.config_parameter` and exposed through *Settings → General Settings → OdooIndex Connector*:

- `odooindex_connector.api_url` — base URL of the OdooIndex API.
- `odooindex_connector.api_token` — secret API token for authentication.
- `odooindex_connector.target_version` — target Odoo version to check migration readiness (e.g. `19.0`).

### Data model

- `odooindex.module.info` — one row per tracked module, with installed version, latest available version, migration status, and timestamp of last sync.
- `odooindex.module.pr` — child rows for open pull requests / updates related to a module.

### Sync flow

A scheduled action runs `odooindex.module.info.action_sync()` once a day (inactive by default).

1. **Upload inventory**
   - `POST /instances/{uuid}/inventory`
   - Payload: `uuid`, `target_version`, and the list of installed modules with `name`, `version`, `author`, `website`, `license`, and `summary`.
2. **Download updates**
   - `GET /instances/{uuid}/updates?target_version={target_version}`
   - Response: per-module `latest_version`, `migration_status`, and an array of `pull_requests`.
3. **Store**
   - Update or create `odooindex.module.info` records and replace the related `odooindex.module.pr` records.

### User interface

- *Settings → General Settings → OdooIndex Connector* to configure the API URL, token, and target version.
- *OdooIndex → Module Updates* menu shows installed modules with installed version, latest available version, migration status, and PR count.

### Security & Privacy

This section explains, in plain language, what the connector does with your data.

**What the module does**

- It sends a list of the *installed modules* on your Odoo instance (names, versions, authors, licenses and short summaries) to OdooIndex.com.
- It sends your Odoo `database.uuid`. This is a random-looking identifier Odoo creates for the database; it is used so OdooIndex can match the uploaded list to your account.
- It does **not** send business records, customer data, documents, emails, passwords, user lists, or any information from inside the modules.
- It downloads migration and update information for those modules (e.g. "module X is ready for Odoo 19.0") and stores it inside your Odoo database.

**What is stored on OdooIndex**

- Your GitHub login and email (from signing in).
- The module list described above, linked to your account and your database UUID.
- An API token hash (not the token itself) so the module can authenticate future uploads.

**What could go wrong?**

- If the API token stored in your Odoo database was leaked, an attacker could upload a fake module list or read migration status for that instance. They could **not** access your Odoo server or business data, because the token only allows the module list endpoints.
- If OdooIndex's server data were leaked, someone could see which modules you have installed and their versions. For most organizations this is low-risk, but if you use custom modules with revealing names or summaries, that information would be exposed.
- The database UUID by itself does not grant access to your Odoo system.

**How we reduce risk**

- Communication is always over HTTPS.
- The API token is stored as an `ir.config_parameter` and is only visible to the *Administration / Settings* group.
- Pairing uses a one-time link, browser login, and a short-lived PIN, so the token is only handed to the Odoo instance that started the pairing.
- You can stop syncing at any time by removing or disabling the scheduled action.

## OdooIndex API Contract

The module expects a REST API with the following endpoints:

### `POST /instances/{uuid}/inventory`

Upload the current module inventory.

Request body:

```json
{
  "uuid": "uuid-of-the-odoo-database",
  "target_version": "19.0",
  "modules": [
    {
      "name": "base",
      "version": "19.0.1.0",
      "author": "Odoo S.A.",
      "website": "https://www.odoo.com",
      "license": "LGPL-3",
      "summary": "Base module"
    }
  ]
}
```

Expected response: `200 OK` with an empty JSON object or a simple status.

### `GET /instances/{uuid}/updates`

Download available updates for the configured target version.

Query parameters:

- `target_version` (string) — e.g. `19.0`

Expected response:

```json
{
  "updates": [
    {
      "name": "my_module",
      "latest_version": "19.0.2.0",
      "target_version": "19.0",
      "migration_status": "ready",
      "pull_requests": [
        {
          "title": "[MIG] my_module: migrate to 19.0",
          "url": "https://github.com/OCA/.../pull/123",
          "state": "open",
          "version": "19.0.2.0"
        }
      ]
    }
  ]
}
```

`migration_status` should be one of: `unknown`, `not_ready`, `in_progress`, `ready`.

## Installation

1. Add the repository to your Odoo addons path.
2. Install the `odooindex_connector` module.
3. Go to *Settings → General Settings → OdooIndex Connector* and fill in the API URL, token, and target version.
4. Activate the scheduled action *OdooIndex: sync modules*.
