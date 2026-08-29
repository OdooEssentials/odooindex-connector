import base64
import io
import json
import urllib.error
import urllib.parse
import urllib.request

from odoo import api, fields, models
from odoo.exceptions import UserError

try:
    import qrcode
except ImportError:
    qrcode = None

ODOOINDEX_API_URL = "https://odooindex.com/api/v1"


class OdooIndexConnectorPairWizard(models.TransientModel):
    _name = "odooindex.connector.pair.wizard"
    _description = "OdooIndex Pairing Wizard"

    instance_name = fields.Char(
        default=lambda self: self.env.cr.dbname,
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
    message = fields.Char()

    @api.model
    def _odooindex_request(self, path, method="GET", payload=None, timeout=30):
        """Make an authenticated request to the OdooIndex API."""
        url = f"{ODOOINDEX_API_URL}{path}"
        data = None
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8") if exc else ""
            raise UserError(self.env._("OdooIndex API error: %s", body)) from exc
        except Exception as exc:
            raise UserError(
                self.env._("OdooIndex request failed: %s", str(exc))
            ) from exc

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
        except Exception:
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

        result = self._odooindex_request(
            "/auth/pair",
            method="POST",
            payload={
                "uuid": uuid,
                "name": self.instance_name or self.env.cr.dbname,
            },
        )

        self.pairing_id = result.get("pairing_id")
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

        result = self._odooindex_request(
            f"/auth/pair/{self.pairing_id}",
            method="GET",
        )
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

        result = self._odooindex_request(
            "/auth/pair/verify",
            method="POST",
            payload={
                "pairing_id": self.pairing_id,
                "pin": self.pin,
            },
        )

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
