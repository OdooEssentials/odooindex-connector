from odoo import fields, models


class OdooIndexModulePR(models.Model):
    _name = "odooindex.module.pr"
    _description = "OdooIndex Module Pull Request"
    _order = "info_id, id"

    info_id = fields.Many2one(
        "odooindex.module.info",
        string="Module Info",
        required=True,
        index=True,
        ondelete="cascade",
    )
    name = fields.Char(string="Title", required=True)
    url = fields.Char(string="URL")
    state = fields.Char()
    version = fields.Char()
