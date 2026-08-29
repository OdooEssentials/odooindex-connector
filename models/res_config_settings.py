from odoo import _, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    odooindex_api_url = fields.Char(
        string="OdooIndex API URL",
        config_parameter="odooindex_connector.api_url",
        default="https://odooindex.com/api/v1",
    )
    odooindex_api_token = fields.Char(
        string="OdooIndex API Token",
        config_parameter="odooindex_connector.api_token",
        password=True,
    )
    odooindex_target_version = fields.Char(
        string="Target Odoo Version",
        config_parameter="odooindex_connector.target_version",
        help="Target Odoo version to check migration readiness for (e.g. 19.0).",
    )

    def action_open_odooindex_pair_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Connect to OdooIndex"),
            "res_model": "odooindex.connector.pair.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_api_url": (
                    self.env["ir.config_parameter"]
                    .sudo()
                    .get_param("odooindex_connector.api_url")
                    or "https://odooindex.com/api/v1"
                ),
            },
        }
