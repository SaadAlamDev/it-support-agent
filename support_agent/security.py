"""Small single-worker demo guard. API credentials stay on the Streamlit server."""

import hashlib
import hmac
import re
import threading
import time
from collections import deque

SESSION_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")


class DemoGuard:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.buckets = {}
        self.active_run = threading.BoundedSemaphore(1)

    def owner(self, api_key, session):
        expected = self.settings.support_api_key.get_secret_value()
        if (
            not api_key
            or len(api_key) > 256
            or not hmac.compare_digest(api_key.encode(), expected.encode())
        ):
            return None
        if not session or not SESSION_PATTERN.fullmatch(session):
            return None
        return hmac.new(expected.encode(), session.encode(), hashlib.sha256).hexdigest()

    def allowed(self, owner, action=False):
        now = time.monotonic()
        with self.lock:
            # Bound memory even if an admitted frontend creates many session tokens.
            stale = [
                key for key, bucket in self.buckets.items() if not bucket or bucket[-1] <= now - 60
            ]
            for key in stale:
                self.buckets.pop(key, None)
            if len(self.buckets) >= 4096 and (owner, "read") not in self.buckets:
                return False
            limits = [("read", self.settings.demo_requests_per_minute)]
            if action:
                limits.append(("action", self.settings.demo_actions_per_minute))
            for name, limit in limits:
                bucket = self.buckets.setdefault((owner, name), deque())
                while bucket and bucket[0] <= now - 60:
                    bucket.popleft()
                if len(bucket) >= limit:
                    return False
            for name, _ in limits:
                self.buckets[(owner, name)].append(now)
        return True
