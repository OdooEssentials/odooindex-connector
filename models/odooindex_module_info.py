import json
import urllib.error
import urllib.parse
import urllib.request

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class OdooIndexModuleInfo(models.Model):
    _name = "odooindex.module.info"
    _description = "OdooIndex Module Update Info"
    _order = "name"

    module_id = fields.Many2one(
        "ir.module.module",
        string="Module",
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
    latest_version = fields.Char(string="Latest Version")
    target_version = fields.Char(string="Target Version")
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
    last_sync = fields.Datetime(string="Last Sync")

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
        icp = self.env["ir.config_parameter"].sudo()
        return {
            "api_url": (icp.get_param("odooindex_connector.api_url") or "").rstrip("/"),
            "api_token": icp.get_param("odooindex_connector.api_token") or "",
            "target_version": icp.get_param("odooindex_connector.target_version") or "",
        }

    @api.model
    def _get_db_uuid(self):
        return self.env["ir.config_parameter"].sudo().get_param("database.uuid")

    @api.model
    def _odooindex_request(self, path, method="GET", payload=None):
        config = self._get_config()
        if not config["api_url"] or not config["api_token"]:
            raise UserError(_("OdooIndex API URL and token must be configured."))

        url = "{}{}".format(config["api_url"], path)
        data = None
        headers = {
            "Authorization": "Bearer {}".format(config["api_token"]),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8") if exc else ""
            raise UserError(_("OdooIndex API error: %s", body)) from exc
        except Exception as exc:
            raise UserError(_("OdooIndex request failed: %s", str(exc))) from exc

    @api.model
    def _build_inventory_payload(self):
        uuid = self._get_db_uuid()
        if not uuid:
            raise UserError(_("Database UUID not found."))

        config = self._get_config()
        modules = []
        for module in self.env["ir.module.module"].sudo().search(
            [("state", "=", "installed")]
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
            "target_version": config["target_version"],
            "modules": modules,
        }

    @api.model
    def _upload_inventory(self):
        payload = self._build_inventory_payload()
        uuid = payload["uuid"]
        return self._odooindex_request(
            "/instances/{}/inventory".format(uuid),
            method="POST",
            payload=payload,
        )

    @api.model
    def _download_updates(self):
        uuid = self._get_db_uuid()
        if not uuid:
            raise UserError(_("Database UUID not found."))

        config = self._get_config()
        params = urllib.parse.urlencode(
            {"target_version": config["target_version"]}
        )
        return self._odooindex_request(
            "/instances/{}/updates?{}".format(uuid, params),
            method="GET",
        )

    @api.model
    def _apply_updates(self, updates):
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
        self._upload_inventory()
        updates = self._download_updates()
        self._apply_updates(updates)
        return True
