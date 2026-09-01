import base64
import io

try:
    import qrcode
except ImportError:
    qrcode = None

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..core import OdooIndexClient, OdooIndexError, PairingService


class OdooIndexConnectorPairWizard(models.TransientModel):
    _name = "odooindex.connector.pair.wizard"
    _description = "OdooIndex Pairing Wizard"

    instance_name = fields.Char(
        default=lambda self: (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("odooindex_connector.instance_name")
            or self.env.cr.dbname
        ),
    )
    pairing_id = fields.Char(string="Pairing ID")
    pairing_url = fields.Char(string="Pairing URL")
    qr_code = fields.Image(
        string="QR Code",
        max_width=256,
        max_height=256,
    )
    status = fields.Selection(
        [
            ("draft", "Draft"),
            ("pending", "Waiting for browser login"),
            ("awaiting_pin", "Enter the PIN"),
            ("done", "Connected"),
            ("error", "Error"),
        ],
        default="draft",
    )
    pin = fields.Char(string="Handshake PIN")
    pairing_secret = fields.Char()
    message = fields.Char()

    @api.model
    def _get_db_uuid(self):
        """Return the Odoo database UUID used to identify this instance."""
        return self.env["ir.config_parameter"].sudo().get_param("database.uuid")

    def _render_qr_code(self, url):
        """Render a URL as a base64-encoded PNG QR code, if qrcode is installed."""
        if not qrcode or not url:
            return False
        try:
            img = qrcode.make(url, box_size=4, border=2)
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            return base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception:  # noqa: BLE001
            # QR generation is optional; keep the link available.
            return False

    def _reopen_wizard(self):
        """Return an action that reopens the current transient wizard record."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "context": {"active_id": self.id},
        }

    def action_start_pairing(self):
        """Start the pairing handshake and show the QR code/link."""
        self.ensure_one()
        uuid = self._get_db_uuid()
        if not uuid:
            raise UserError(self.env._("Database UUID not found."))

        service = PairingService(OdooIndexClient())
        try:
            result = service.start(uuid, self.instance_name or self.env.cr.dbname)
        except OdooIndexError as exc:
            raise UserError(
                self.env._("OdooIndex pairing failed: %s", str(exc))
            ) from exc

        self.env["ir.config_parameter"].sudo().set_param(
            "odooindex_connector.instance_name",
            self.instance_name or self.env.cr.dbname,
        )
        self.pairing_id = result.get("pairing_id")
        self.pairing_secret = result.get("pairing_secret")
        self.pairing_url = result.get("pairing_url")
        self.status = "pending"
        self.message = self.env._(
            "Open the link or scan the QR code, sign in with your OdooIndex account, "
            "then enter the PIN shown on the site."
        )
        self.qr_code = self._render_qr_code(self.pairing_url)
        return self._reopen_wizard()

    def action_check_status(self):
        """Poll OdooIndex for the pairing status."""
        self.ensure_one()
        if not self.pairing_id:
            raise UserError(self.env._("Start pairing first."))

        service = PairingService(OdooIndexClient())
        try:
            result = service.status(self.pairing_id)
        except OdooIndexError as exc:
            raise UserError(
                self.env._("OdooIndex pairing failed: %s", str(exc))
            ) from exc

        status = result.get("status", "pending")
        self.status = status if status in ("pending", "awaiting_pin") else "error"

        if self.status == "awaiting_pin":
            self.message = self.env._(
                "Login complete. Enter the PIN displayed on the site and click Verify."
            )
        elif self.status == "pending":
            self.message = self.env._("Waiting for you to sign in on the browser.")
        return self._reopen_wizard()

    def action_verify_pin(self):
        """Verify the PIN and store the returned API token."""
        self.ensure_one()
        if not self.pairing_id or not self.pin:
            raise UserError(self.env._("Pairing ID and PIN are required."))

        service = PairingService(OdooIndexClient())
        try:
            result = service.verify(self.pairing_id, self.pairing_secret, self.pin)
        except OdooIndexError as exc:
            raise UserError(
                self.env._("OdooIndex pairing failed: %s", str(exc))
            ) from exc

        if result.get("status") != "completed":
            self.status = "awaiting_pin"
            self.message = self.env._(
                "The PIN was not accepted. Make sure you entered it exactly as shown."
            )
            return self._reopen_wizard()

        token = result.get("api_token")
        if token:
            # Store the bearer token in ir.config_parameter. It is only readable
            # by the Administration/Settings group, the same group that can
            # install modules and configure this wizard.
            self.env["ir.config_parameter"].sudo().set_param(
                "odooindex_connector.api_token", token
            )
            self.status = "done"
            self.message = self.env._("Connected. The API token has been saved.")
        else:
            self.status = "error"
            self.message = self.env._("Pairing completed but no token was returned.")
        return self._reopen_wizard()
