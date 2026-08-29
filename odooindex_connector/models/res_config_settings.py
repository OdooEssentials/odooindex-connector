from odoo import _, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    odooindex_api_token = fields.Char(
        string="API Token",
        config_parameter="odooindex_connector.api_token",
        password=True,
    )
    odooindex_target_version = fields.Char(
        string="Target Odoo Version",
        config_parameter="odooindex_connector.target_version",
        help="Target Odoo version to check migration readiness for (e.g. 19.0).",
    )

    def action_open_odooindex_pair_wizard(self):
        """Open the pairing wizard from the settings screen."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Connect to OdooIndex"),
            "res_model": "odooindex.connector.pair.wizard",
            "view_mode": "form",
            "target": "new",
        }

    def action_sync_modules(self):
        """Trigger a manual OdooIndex sync and show a notification."""
        self.ensure_one()
        self.env["odooindex.module.info"].action_sync()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("OdooIndex sync"),
                "message": _(
                    "Module inventory and updates have been synchronized."
                ),
                "type": "success",
                "sticky": False,
            },
        }
