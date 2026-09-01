import logging

import odoo
from odoo import api, fields, models
from odoo.exceptions import UserError

from ..core import (
    ConnectorConfig,
    OdooIndexClient,
    OdooIndexError,
    SyncService,
    build_inventory_payload,
)

_logger = logging.getLogger(__name__)


class IrModuleModule(models.Model):
    _inherit = "ir.module.module"

    odooindex_latest_version = fields.Char(
        string="Latest OCA Version",
        help="Latest version available in OCA for the configured target Odoo.",
    )
    odooindex_target_version = fields.Char(
        string="Target Version",
        help="Odoo version this module information was checked for.",
    )
    odooindex_migration_status = fields.Selection(
        [
            ("unknown", "Unknown"),
            ("not_ready", "Not Ready"),
            ("in_progress", "In Progress"),
            ("ready", "Ready"),
        ],
        string="Migration Status",
        default="unknown",
    )
    odooindex_pr_count = fields.Integer(
        string="Open PRs",
        default=0,
        help="Number of open OCA pull requests for this module.",
    )
    odooindex_last_sync = fields.Datetime(
        string="Last OdooIndex Sync",
    )
    odooindex_has_update = fields.Boolean(
        string="Has Update",
        compute="_compute_odooindex_has_update",
        store=True,
        help="True when a newer version is available.",
    )

    @api.depends("latest_version", "odooindex_latest_version")
    def _compute_odooindex_has_update(self):
        for module in self:
            module.odooindex_has_update = bool(
                module.odooindex_latest_version
                and module.latest_version
                and module.odooindex_latest_version != module.latest_version
            )

    @api.model
    def _get_config(self):
        """Read the connector settings from ir.config_parameter."""
        icp = self.env["ir.config_parameter"].sudo()
        return ConnectorConfig(
            api_token=icp.get_param("odooindex_connector.api_token") or "",
            target_version=icp.get_param("odooindex_connector.target_version") or "",
            instance_name=icp.get_param("odooindex_connector.instance_name")
            or self.env.cr.dbname,
        )

    @api.model
    def _get_db_uuid(self):
        """Return the stable database UUID that identifies this instance."""
        return self.env["ir.config_parameter"].sudo().get_param("database.uuid")

    @api.model
    def _build_inventory_payload(self):
        """Build the payload sent to OdooIndex: only module metadata."""
        uuid = self._get_db_uuid()
        if not uuid:
            raise UserError(self.env._("Database UUID not found."))

        config = self._get_config()
        modules = self.sudo().search([("state", "=", "installed")])
        return build_inventory_payload(
            uuid=uuid,
            instance_name=config.instance_name,
            odoo_version=odoo.release.series,
            target_version=config.target_version,
            modules=modules,
        )

    @api.model
    def _upload_inventory(self):
        """Upload the current installed module inventory to OdooIndex."""
        payload = self._build_inventory_payload()
        config = self._get_config()
        client = OdooIndexClient(api_token=config.api_token, base_url=config.base_url)
        try:
            return SyncService(client).upload_inventory(payload)
        except OdooIndexError as exc:
            raise UserError(
                self.env._("OdooIndex upload failed: %s", str(exc))
            ) from exc

    @api.model
    def _download_updates(self):
        """Download migration/update information for the configured target version."""
        uuid = self._get_db_uuid()
        if not uuid:
            raise UserError(self.env._("Database UUID not found."))

        config = self._get_config()
        client = OdooIndexClient(api_token=config.api_token, base_url=config.base_url)
        try:
            return SyncService(client).download_updates(
                uuid=uuid, target_version=config.target_version
            )
        except OdooIndexError as exc:
            raise UserError(
                self.env._("OdooIndex download failed: %s", str(exc))
            ) from exc

    @api.model
    def _apply_updates(self, updates):
        """Persist the update/migration information returned by OdooIndex."""
        if isinstance(updates, dict):
            updates = updates.get("updates", [])
        if not updates:
            return

        now = fields.Datetime.now()

        for item in updates:
            name = item.get("name")
            if not name:
                continue

            module = self.sudo().search([("name", "=", name)], limit=1)
            if not module:
                continue

            module.sudo().write(
                {
                    "odooindex_latest_version": item.get("latest_version", ""),
                    "odooindex_target_version": item.get("target_version", ""),
                    "odooindex_migration_status": item.get(
                        "migration_status", "unknown"
                    ),
                    "odooindex_pr_count": len(item.get("pull_requests", [])),
                    "odooindex_last_sync": now,
                }
            )

    @api.model
    def action_sync(self):
        """Run the full sync: upload inventory then download updates."""
        try:
            upload_result = self._upload_inventory() or {}
            updates = self._download_updates()
        except OdooIndexError as exc:
            raise UserError(self.env._("OdooIndex sync failed: %s", str(exc))) from exc
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
