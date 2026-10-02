"""Pure-Python DriveClarify route-switch lifecycle; no simulator imports."""

import hashlib
import json


class RouteSwitchEventLifecycle:
    """Emit exactly 16 markers per newly consumed authoritative route."""

    def __init__(
            self, enabled=False, duration_outer_forwards=16,
            parameter_sha256=None):
        if duration_outer_forwards != 16:
            raise ValueError("ROUTE_SWITCH_DURATION_MUST_BE_16")
        self.enabled = bool(enabled)
        self.duration_outer_forwards = duration_outer_forwards
        self._event_key = None
        self._seen_event_keys = set()
        self._next_index = 0
        self._last_emitted_index = None
        if parameter_sha256 is not None and not self._valid_sha256(parameter_sha256):
            raise ValueError("ROUTE_SWITCH_PARAMETER_SHA256_INVALID")
        self._parameter_sha256 = parameter_sha256

    @staticmethod
    def _valid_sha256(value):
        if not isinstance(value, str) or len(value) != 64:
            return False
        try:
            int(value, 16)
        except ValueError:
            return False
        return value == value.lower()

    @staticmethod
    def _event_from_state(state):
        if not isinstance(state, dict) or state.get("committed") is not True:
            return None
        generation = state.get("generation_number")
        installed = state.get("installed_route_identity")
        active = state.get("active_route_identity")
        consumed = state.get("consumed_route_identity")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            return None
        if not all(isinstance(value, str) and value.strip()
                   for value in (installed, active, consumed)):
            return None
        if not installed == active == consumed:
            return None
        transaction_id = state.get("transaction_id")
        if transaction_id is not None:
            if not isinstance(transaction_id, str) or not transaction_id.strip():
                return None
            return ("transaction", transaction_id)
        return ("generation_route", generation, installed)

    def before_outer_forward(self, authoritative_state=None):
        """Advance once at the sole outer DrivingModel.forward boundary."""
        self._last_emitted_index = None
        if not self.enabled:
            return False
        event_key = self._event_from_state(authoritative_state)
        if event_key is None:
            # Malformed authoritative state never holds or resurrects a window.
            if self._event_key is not None:
                self._seen_event_keys.add(self._event_key)
            self._event_key = None
            self._next_index = 0
            return False
        if event_key != self._event_key:
            if event_key in self._seen_event_keys:
                return False
            if self._event_key is not None:
                self._seen_event_keys.add(self._event_key)
            self._event_key = event_key
            self._next_index = 0
        if self._next_index >= self.duration_outer_forwards:
            self._seen_event_keys.add(event_key)
            return False
        self._last_emitted_index = self._next_index
        self._next_index += 1
        if self._next_index == self.duration_outer_forwards:
            self._seen_event_keys.add(event_key)
        return True

    def receipt(self):
        """Return provenance for logging only; never a model input."""
        event_key_sha256 = None
        if self._event_key is not None:
            event_key_sha256 = hashlib.sha256(json.dumps(
                self._event_key, separators=(",", ":"), ensure_ascii=True
            ).encode("utf-8")).hexdigest()
        return {
            "enabled": self.enabled,
            "duration_outer_forwards": self.duration_outer_forwards,
            "event_origin": "NEW_AUTHORITATIVE_ROUTE_ACTIVE_AND_CONSUMED",
            "event_key_sha256": event_key_sha256,
            "parameter_sha256": self._parameter_sha256,
            "active_forward_index": self._last_emitted_index,
            "seen_event_count": len(self._seen_event_keys),
        }

    def receipt_json(self):
        """Serialize the provenance receipt without exposing route identities."""
        return json.dumps(
            self.receipt(), sort_keys=True, separators=(",", ":")
        )
