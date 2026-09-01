from .client import OdooIndexError


class PairingService:
    """Device pairing handshake with OdooIndex."""

    def __init__(self, client):
        self.client = client

    def start(self, uuid, instance_name):
        return self.client.request(
            "/auth/pair",
            method="POST",
            payload={"uuid": uuid, "name": instance_name},
        )

    def status(self, pairing_id):
        if not pairing_id:
            raise OdooIndexError("Pairing ID is required.")
        return self.client.request(f"/auth/pair/{pairing_id}", method="GET")

    def verify(self, pairing_id, pairing_secret, pin):
        if not pairing_id or not pairing_secret or not pin:
            raise OdooIndexError("Pairing ID, secret and PIN are required.")
        return self.client.request(
            "/auth/pair/verify",
            method="POST",
            payload={
                "pairing_id": pairing_id,
                "pairing_secret": pairing_secret,
                "pin": pin,
            },
        )
