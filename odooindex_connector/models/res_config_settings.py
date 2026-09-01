from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    odooindex_api_token = fields.Char(
        string="API Token",
        config_parameter="odooindex_connector.api_token",
    )
    odooindex_target_version = fields.Char(
        string="Target Odoo Version",
        config_parameter="odooindex_connector.target_version",
        help="Target Odoo version to check migration readiness for (e.g. 19.0).",
    )
    odooindex_instance_name = fields.Char(
        string="Instance Name",
        config_parameter="odooindex_connector.instance_name",
        help=(
            "Display name for this instance on OdooIndex "
            "(defaults to the database name)."
        ),
    )

    def action_open_odooindex_pair_wizard(self):
        """Open the pairing wizard from the settings screen."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Connect to OdooIndex"),
            "res_model": "odooindex.connector.pair.wizard",
            "view_mode": "form",
            "target": "new",
        }

    def action_sync_modules(self):
        """Trigger a manual OdooIndex sync and show a notification."""
        self.ensure_one()
        # Force-reload system parameters before syncing, so values written
        # outside the ORM (e.g. by the clox CLI) are picked up immediately.
        self.env.registry.clear_cache("default")
        result = self.env["ir.module.module"].sudo().action_sync()
        count = (result or {}).get("module_count", 0)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("OdooIndex sync"),
                "message": self.env._("Synchronized %d modules.", count),
                "type": "success",
                "sticky": False,
            },
        }
