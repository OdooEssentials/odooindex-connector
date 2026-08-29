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
        """Start pairing immediately from the settings screen."""
        self.ensure_one()
        wizard = self.env["odooindex.connector.pair.wizard"].create({})
        return wizard.action_start_pairing()

    def action_sync_modules(self):
        """Trigger a manual OdooIndex sync and show a notification."""
        self.ensure_one()
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
