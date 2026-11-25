import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from support_agent.schemas import CreateTicketArgs, UpdateTicketArgs
from support_agent.seed import ARTICLES, SERVICES


class ConflictError(Exception):
    pass


class NotFoundError(Exception):
    pass


class QuotaError(Exception):
    pass


class Repository:
    def __init__(self, path: Path, approval_ttl: int = 1800, isolated: bool = False):
        self.path = path
        self.approval_ttl = approval_ttl
        self.isolated = isolated
        path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        with self.connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS services (
                    name TEXT PRIMARY KEY, status TEXT NOT NULL, incident TEXT, detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS troubleshooting (
                    id INTEGER PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
                    keywords TEXT NOT NULL, steps TEXT NOT NULL, escalation TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                    description TEXT NOT NULL, category TEXT NOT NULL, priority TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open', notes TEXT NOT NULL DEFAULT '[]',
                    version INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    call_id TEXT NOT NULL, tool TEXT NOT NULL, arguments TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', result TEXT, created_at REAL NOT NULL,
                    expires_at REAL NOT NULL, ticket_version INTEGER,
                    UNIQUE(conversation_id, call_id)
                );
                CREATE TABLE IF NOT EXISTS activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id), call_id TEXT NOT NULL,
                    tool TEXT NOT NULL, arguments TEXT NOT NULL, status TEXT NOT NULL,
                    result TEXT NOT NULL, created_at REAL NOT NULL,
                    UNIQUE(conversation_id, call_id)
                );
                CREATE TABLE IF NOT EXISTS usage_counters (
                    name TEXT PRIMARY KEY, used INTEGER NOT NULL
                );
            """)
            # Additive migration: preserve pre-cloud local data and approval receipts.
            for table in ("conversations", "tickets"):
                if "owner_id" not in {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN owner_id TEXT")
            c.execute("CREATE INDEX IF NOT EXISTS conversations_owner ON conversations(owner_id)")
            c.executemany("INSERT OR IGNORE INTO services VALUES (?, ?, ?, ?)", SERVICES)
            c.executemany(
                "INSERT OR IGNORE INTO troubleshooting VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (i, title, category, keywords, json.dumps(steps), escalation)
                    for i, (title, category, keywords, steps, escalation) in enumerate(ARTICLES, 1)
                ],
            )
            # A fixed sample ticket; initialization never resets user edits.
            c.execute(
                "INSERT OR IGNORE INTO tickets (id,title,description,category,priority,status,created_at,updated_at) VALUES (1,?,?,?,?,?,?,?)",
                (
                    "SIM: VPN gateway disconnects",
                    "Synthetic EU gateway incident; no real employee data.",
                    "vpn",
                    "high",
                    "in_progress",
                    1735689600,
                    1735689600,
                ),
            )

    def create_conversation(self, owner_id=None, max_per_owner=5, max_total=200):
        value = {"id": str(uuid4()), "title": "New investigation", "created_at": time.time()}
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if self.isolated:
                if owner_id is None:
                    raise NotFoundError("A demo session is required")
                owned = c.execute(
                    "SELECT COUNT(*) FROM conversations WHERE owner_id=?", (owner_id,)
                ).fetchone()[0]
                total = c.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
                if owned >= max_per_owner or total >= max_total:
                    raise QuotaError(
                        "Demo investigation capacity reached. Reuse an existing investigation or return after the operator resets the demo."
                    )
            c.execute(
                "INSERT INTO conversations (id,title,created_at,owner_id) VALUES (?,?,?,?)",
                (value["id"], value["title"], value["created_at"], owner_id),
            )
        return value

    def conversation(self, conversation_id, owner_id=None):
        with self.connect() as c:
            row = c.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if row is None or (owner_id is not None and row["owner_id"] != owner_id):
            raise NotFoundError("Conversation not found")
        return {k: row[k] for k in ("id", "title", "created_at")}

    def conversations(self, owner_id=None):
        if self.isolated and owner_id is None:
            raise NotFoundError("A demo session is required")
        with self.connect() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT id,title,created_at FROM conversations WHERE (? IS NULL OR owner_id=?) ORDER BY created_at DESC LIMIT 100",
                    (owner_id, owner_id),
                )
            ]

    def consume_quota(self, name, limit):
        """Reserve before inference; failed requests still consume the budget."""
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO usage_counters VALUES (?,0)", (name,))
            used = c.execute("SELECT used FROM usage_counters WHERE name=?", (name,)).fetchone()[0]
            if used >= limit:
                raise QuotaError(
                    "The demo's daily model-call budget is exhausted. Please try another day."
                )
            c.execute("UPDATE usage_counters SET used=used+1 WHERE name=?", (name,))

    def _check_ticket_owner(self, c, ticket_id, conversation_id, write=False):
        if not self.isolated:
            return
        owner = c.execute(
            "SELECT owner_id FROM conversations WHERE id=?", (conversation_id,)
        ).fetchone()
        ticket = c.execute("SELECT owner_id FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        if not owner or not owner[0] or not ticket:
            raise NotFoundError("Ticket not found")
        if ticket[0] != owner[0] and not (ticket_id == 1 and ticket[0] is None and not write):
            raise NotFoundError("Ticket not found")

    def set_title(self, conversation_id, text):
        with self.connect() as c:
            c.execute(
                "UPDATE conversations SET title=? WHERE id=? AND title='New investigation'",
                (text[:70], conversation_id),
            )

    def status(self, service):
        with self.connect() as c:
            rows = c.execute(
                "SELECT * FROM services WHERE (?='all' OR name=?) ORDER BY name", (service, service)
            ).fetchall()
        return {"simulated": True, "services": [dict(r) for r in rows]}

    def lookup(self, category, query, limit):
        # Token-based ranking over a tiny structured catalog, not semantic retrieval.
        terms = set(query.lower().split())
        with self.connect() as c:
            rows = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM troubleshooting WHERE category=? ORDER BY id", (category,)
                )
            ]
        rows.sort(
            key=lambda r: -sum(t in (r["title"] + " " + r["keywords"]).lower() for t in terms)
        )
        for row in rows:
            row["steps"] = json.loads(row["steps"])
            row.pop("keywords")
        return {"simulated": True, "untrusted_reference_data": True, "articles": rows[:limit]}

    @staticmethod
    def _ticket(c, ticket_id):
        row = c.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        if row is None:
            raise NotFoundError("Ticket not found")
        value = dict(row)
        value.pop("owner_id", None)
        value["notes"] = json.loads(value["notes"])
        return value

    def ticket(self, ticket_id, conversation_id=None):
        with self.connect() as c:
            self._check_ticket_owner(c, ticket_id, conversation_id)
            return {"simulated": True, "ticket": self._ticket(c, ticket_id)}

    def activity(self, conversation_id):
        with self.connect() as c:
            rows = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM activity WHERE conversation_id=? ORDER BY id", (conversation_id,)
                )
            ]
        for row in rows:
            row["arguments"] = json.loads(row["arguments"])
            row["result"] = json.loads(row["result"])
        return rows

    def record_activity(self, conversation_id, call, status, result):
        with self.connect() as c:
            c.execute(
                """INSERT INTO activity (conversation_id,call_id,tool,arguments,status,result,created_at)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(conversation_id,call_id)
                DO UPDATE SET status=excluded.status,result=excluded.result""",
                (
                    conversation_id,
                    call["id"],
                    call["name"],
                    json.dumps(call["args"]),
                    status,
                    json.dumps(result),
                    time.time(),
                ),
            )

    def cached_read_result(self, conversation_id, call):
        """Reuse observed read evidence when an interrupted tool batch replays."""
        with self.connect() as c:
            row = c.execute(
                "SELECT tool,arguments,status,result FROM activity WHERE conversation_id=? AND call_id=?",
                (conversation_id, call["id"]),
            ).fetchone()
        if row is None:
            return None
        if row["tool"] != call["name"] or json.loads(row["arguments"]) != call["args"]:
            raise ConflictError("Tool call id was reused with different arguments")
        return json.loads(row["result"])

    @staticmethod
    def _approval(row):
        value = dict(row)
        value["arguments"] = json.loads(value["arguments"])
        value["result"] = json.loads(value["result"]) if value["result"] else None
        return value

    def approval(self, conversation_id, approval_id):
        with self.connect() as c:
            row = c.execute(
                "SELECT * FROM approvals WHERE id=? AND conversation_id=?",
                (approval_id, conversation_id),
            ).fetchone()
        if row is None:
            raise NotFoundError("Approval not found for this conversation")
        return self._approval(row)

    def propose(self, conversation_id, call):
        # The model's call id is scoped to its conversation; proposal replay returns the same receipt.
        approval_id = str(uuid5(NAMESPACE_URL, conversation_id + ":" + call["id"]))
        now = time.time()
        with self.connect() as c:
            existing = c.execute(
                "SELECT * FROM approvals WHERE id=? AND conversation_id=?",
                (approval_id, conversation_id),
            ).fetchone()
            if existing is not None:
                proposal = self._approval(existing)
                if proposal["tool"] != call["name"] or proposal["arguments"] != call["args"]:
                    raise ConflictError("Tool call id was reused with different arguments")
                # Node replay must use the immutable ledger even if the referenced
                # ticket has changed or disappeared. Interrupt ordering stays stable.
                return proposal
            version = None
            if call["name"] == "update_ticket":
                self._check_ticket_owner(c, call["args"]["ticket_id"], conversation_id, write=True)
                version = self._ticket(c, call["args"]["ticket_id"])["version"]
            c.execute(
                """INSERT OR IGNORE INTO approvals
                (id,conversation_id,call_id,tool,arguments,created_at,expires_at,ticket_version)
                VALUES (?,?,?,?,?,?,?,?)""",
                (
                    approval_id,
                    conversation_id,
                    call["id"],
                    call["name"],
                    json.dumps(call["args"], sort_keys=True),
                    now,
                    now + self.approval_ttl,
                    version,
                ),
            )
        proposal = self.approval(conversation_id, approval_id)
        if proposal["tool"] != call["name"] or proposal["arguments"] != call["args"]:
            raise ConflictError("Tool call id was reused with different arguments")
        return proposal

    def decide(self, conversation_id, approval_id, approved):
        """Only write gateway: consume an immutable proposal and mutate in ONE transaction.

        Receipts make retries and LangGraph node replay safe even after process restarts.
        """
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT * FROM approvals WHERE id=? AND conversation_id=?",
                (approval_id, conversation_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("Approval not found for this conversation")
            proposal = self._approval(row)
            if proposal["status"] != "pending":
                raise ConflictError("This proposal already has a decision")
            now = time.time()
            if not approved:
                status, result = (
                    "rejected",
                    {
                        "error": "approval_rejected",
                        "message": "The user rejected this change. No ticket was changed.",
                    },
                )
            elif now > proposal["expires_at"]:
                status, result = (
                    "expired",
                    {
                        "error": "approval_expired",
                        "message": "Approval expired. Request a new proposal; no ticket was changed.",
                    },
                )
            else:
                try:
                    result = self._apply_ticket_change(c, proposal, now)
                    result["execution_status"] = "completed"
                    result["approval"] = {"id": approval_id, "status": "approved"}
                    status = "approved"
                except (NotFoundError, ConflictError) as exc:
                    status, result = "failed", {"error": "ticket_conflict", "message": str(exc)}
            c.execute(
                "UPDATE approvals SET status=?,result=? WHERE id=?",
                (status, json.dumps(result), approval_id),
            )
        return self.approval(conversation_id, approval_id)

    def _apply_ticket_change(self, c, proposal, now):
        # Revalidate stored arguments at the mutation boundary; never accept arguments from the approval POST.
        if proposal["tool"] == "create_ticket":
            args = CreateTicketArgs.model_validate(proposal["arguments"])
            owner = c.execute(
                "SELECT owner_id FROM conversations WHERE id=?", (proposal["conversation_id"],)
            ).fetchone()[0]
            cursor = c.execute(
                """INSERT INTO tickets
                (title,description,category,priority,created_at,updated_at,owner_id) VALUES (?,?,?,?,?,?,?)""",
                (args.title, args.description, args.category, args.priority, now, now, owner),
            )
            ticket_id = cursor.lastrowid
        elif proposal["tool"] == "update_ticket":
            args = UpdateTicketArgs.model_validate(proposal["arguments"])
            ticket_id = args.ticket_id
            self._check_ticket_owner(c, ticket_id, proposal["conversation_id"], write=True)
            current = self._ticket(c, ticket_id)
            if current["version"] != proposal["ticket_version"]:
                raise ConflictError("Ticket changed after this proposal. Review a fresh proposal.")
            notes = current["notes"]
            if args.note:
                notes.append({"text": args.note, "created_at": now})
            c.execute(
                "UPDATE tickets SET status=?,priority=?,notes=?,version=version+1,updated_at=? WHERE id=?",
                (
                    args.status or current["status"],
                    args.priority or current["priority"],
                    json.dumps(notes),
                    now,
                    ticket_id,
                ),
            )
        else:
            raise ConflictError("Unsupported ticket operation")
        return {
            "simulated": True,
            "operation": proposal["tool"],
            "ticket": self._ticket(c, ticket_id),
        }
