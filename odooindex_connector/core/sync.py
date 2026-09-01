from .client import OdooIndexError

DEFAULT_CHUNK_SIZE = 200


class SyncService:
    """Upload module inventories and download migration/update info."""

    def __init__(self, client):
        self.client = client

    def upload_inventory(self, payload, chunk_size=DEFAULT_CHUNK_SIZE):
        self.client.require_token()
        modules = payload["modules"]
        total_chunks = max(1, (len(modules) + chunk_size - 1) // chunk_size)
        result = None
        for index in range(total_chunks):
            chunk = modules[index * chunk_size : (index + 1) * chunk_size]
            chunk_payload = {
                key: value for key, value in payload.items() if key != "modules"
            }
            chunk_payload["modules"] = chunk
            chunk_payload["chunk_index"] = index
            chunk_payload["total_chunks"] = total_chunks
            result = self.client.request(
                "/instances/inventory", method="POST", payload=chunk_payload
            )
            if not result:
                raise OdooIndexError("OdooIndex inventory upload failed.")
            if result.get("status") == "completed":
                return result
            if result.get("status") != "chunk_received":
                raise OdooIndexError(f"Unexpected upload response: {result}")
        return result

    def download_updates(self, uuid, target_version):
        self.client.require_token()
        return self.client.request(
            "/instances/updates",
            method="POST",
            payload={"uuid": uuid, "target_version": target_version},
        )
