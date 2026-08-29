from odoo import fields, models


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
