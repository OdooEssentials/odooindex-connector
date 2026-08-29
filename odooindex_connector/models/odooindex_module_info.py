import json
import logging
import urllib.error
import urllib.request

import odoo
from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

ODOOINDEX_API_URL = "https://odooindex.com/api/v1"


class OdooIndexModuleInfo(models.Model):
    _name = "odooindex.module.info"
    _description = "OdooIndex Module Update Info"
    _order = "name"

    module_id = fields.Many2one(
        "ir.module.module",
        index=True,
        ondelete="cascade",
    )
    name = fields.Char(
        string="Technical Name",
        index=True,
        required=True,
    )
    installed_version = fields.Char(
        related="module_id.latest_version",
        string="Installed Version",
        store=True,
        readonly=True,
    )
    latest_version = fields.Char()
    target_version = fields.Char()
    migration_status = fields.Selection(
        [
            ("unknown", "Unknown"),
            ("not_ready", "Not Ready"),
            ("in_progress", "In Progress"),
            ("ready", "Ready"),
        ],
        default="unknown",
    )
    pr_count = fields.Integer(string="PRs", default=0)
    pr_ids = fields.One2many(
        "odooindex.module.pr",
        "info_id",
        string="Pull Requests",
    )
    last_sync = fields.Datetime()

    _sql_constraints = [
        (
            "module_id_unique",
            "unique(module_id)",
            "A module can only have one OdooIndex info record.",
        ),
        (
            "name_unique",
            "unique(name)",
            "A module technical name can only be tracked once.",
        ),
    ]

    @api.model
    def _get_config(self):
        """Read the connector settings from ir.config_parameter."""
        icp = self.env["ir.config_parameter"].sudo()
        return {
            "api_token": icp.get_param("odooindex_connector.api_token") or "",
            "target_version": icp.get_param("odooindex_connector.target_version") or "",
            "instance_name": icp.get_param("odooindex_connector.instance_name")
            or self.env.cr.dbname,
        }

    @api.model
    def _get_db_uuid(self):
        """Return the stable database UUID that identifies this instance."""
        return self.env["ir.config_parameter"].sudo().get_param("database.uuid")

    @api.model
    def _odooindex_request(self, path, method="GET", payload=None):
        """Make an authenticated HTTPS request to the OdooIndex API."""
        config = self._get_config()
        if not config["api_token"]:
            raise UserError(self.env._("OdooIndex API token must be configured."))

        url = f"{ODOOINDEX_API_URL}{path}"
        data = None
        headers = {
            "Authorization": "Bearer {}".format(config["api_token"]),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8") if exc else ""
            raise UserError(self.env._("OdooIndex API error: %s", body)) from exc
        except Exception as exc:
            raise UserError(
                self.env._("OdooIndex request failed: %s", str(exc))
            ) from exc

    @api.model
    def _build_inventory_payload(self):
        """Build the payload sent to OdooIndex: only module metadata."""
        uuid = self._get_db_uuid()
        if not uuid:
            raise UserError(self.env._("Database UUID not found."))

        config = self._get_config()
        modules = []
        for module in (
            self.env["ir.module.module"].sudo().search([("state", "=", "installed")])
        ):
            modules.append(
                {
                    "name": module.name,
                    "version": module.latest_version or "",
                    "author": module.author or "",
                    "website": module.website or "",
                    "license": module.license or "",
                    "summary": module.summary or "",
                }
            )

        return {
            "uuid": uuid,
            "name": config["instance_name"],
            "odoo_version": odoo.release.series,
            "target_version": config["target_version"],
            "modules": modules,
        }

    @api.model
    def _upload_inventory(self):
        """Upload the current installed module inventory to OdooIndex.

        The payload is split into chunks to stay within the server's
        per-request module limit. Chunks are uploaded sequentially; the server
        stores partial chunks and finalises the inventory when the last one
        arrives.
        """
        payload = self._build_inventory_payload()
        modules = payload["modules"]
        chunk_size = 200
        total_chunks = max(1, (len(modules) + chunk_size - 1) // chunk_size)

        for index in range(total_chunks):
            chunk = modules[index * chunk_size : (index + 1) * chunk_size]
            chunk_payload = {
                "uuid": payload["uuid"],
                "name": payload["name"],
                "odoo_version": payload["odoo_version"],
                "target_version": payload["target_version"],
                "modules": chunk,
                "chunk_index": index,
                "total_chunks": total_chunks,
            }
            result = self._odooindex_request(
                "/instances/inventory",
                method="POST",
                payload=chunk_payload,
            )
            if not result:
                raise UserError(self.env._("OdooIndex inventory upload failed."))
            if result.get("status") == "completed":
                return result
            if result.get("status") != "chunk_received":
                raise UserError(self.env._("Unexpected upload response: %s", result))

        return result

    @api.model
    def _download_updates(self):
        """Download migration/update information for the configured target version."""
        uuid = self._get_db_uuid()
        if not uuid:
            raise UserError(self.env._("Database UUID not found."))

        config = self._get_config()
        return self._odooindex_request(
            "/instances/updates",
            method="POST",
            payload={
                "uuid": uuid,
                "target_version": config["target_version"],
            },
        )

    @api.model
    def _apply_updates(self, updates):
        """Persist the update/migration information returned by OdooIndex."""
        if isinstance(updates, dict):
            updates = updates.get("updates", [])
        if not updates:
            return

        Module = self.env["ir.module.module"].sudo()
        now = fields.Datetime.now()

        for item in updates:
            name = item.get("name")
            if not name:
                continue

            module = Module.search([("name", "=", name)], limit=1)
            vals = {
                "module_id": module.id,
                "name": name,
                "latest_version": item.get("latest_version", ""),
                "target_version": item.get("target_version", ""),
                "migration_status": item.get("migration_status", "unknown"),
                "pr_count": len(item.get("pull_requests", [])),
                "last_sync": now,
            }
            info = self.search([("name", "=", name)], limit=1)
            if info:
                info.write(vals)
            else:
                info = self.create(vals)

            info.pr_ids.unlink()
            for pr in item.get("pull_requests", []):
                self.env["odooindex.module.pr"].sudo().create(
                    {
                        "info_id": info.id,
                        "name": pr.get("title", ""),
                        "url": pr.get("url", ""),
                        "state": pr.get("state", ""),
                        "version": pr.get("version", ""),
                    }
                )

    @api.model
    def action_sync(self):
        """Run the full sync: upload inventory then download updates."""
        upload_result = self._upload_inventory() or {}
        updates = self._download_updates()
        self._apply_updates(updates)
        return {
            "success": True,
            "module_count": upload_result.get("module_count", 0),
        }

    @api.model
    def action_sync_cron(self):
        """Run the full sync from the cron, skipping neutralized databases."""
        is_neutralized = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("database.is_neutralized", "False")
            .lower()
            == "true"
        )
        if is_neutralized:
            _logger.warning(
                "OdooIndex sync skipped because the database is neutralized."
            )
            return False
        return self.action_sync()
