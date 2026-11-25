"""Database schema migrations (H6.2, H6.5).

``MIGRATIONS[0]`` makes a new database as HouseMind 0.2.0 had it: schema version ``FIRST``, in one
step (code QA 3, punt 20 – the twenty-two steps of before 0.2.0 are merged; nobody runs an older
version). ``MIGRATIONS[n]`` for ``n >= 1`` brings the schema from ``FIRST + n - 1`` to
``FIRST + n``. A database older than ``FIRST`` is left untouched (safe mode). Never change a
released step once it is out – add a new one.
"""

from __future__ import annotations

FIRST = 22  # the schema version of HouseMind 0.2.0

MIGRATIONS: list[list[str]] = [
    # nothing -> 22: every table and index of HouseMind 0.2.0
    [
        """CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""",
        """CREATE TABLE areas (
            area_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            floor_id TEXT,
            role TEXT,
            role_source TEXT
        )""",
        """CREATE TABLE devices_known (
            registry_id TEXT PRIMARY KEY,
            entity_id TEXT NOT NULL,
            kind_key TEXT,
            match_quality TEXT NOT NULL,
            role TEXT,
            entity_role TEXT NOT NULL,
            followed INTEGER NOT NULL DEFAULT 1,
            excluded INTEGER NOT NULL DEFAULT 0,
            stricter_max_step INTEGER,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        )""",
        "CREATE INDEX devices_known_entity ON devices_known(entity_id)",
        """CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            old_state TEXT,
            new_state TEXT,
            attributes TEXT,
            source TEXT NOT NULL
        )""",
        "CREATE INDEX events_at ON events(at)",
        "CREATE INDEX events_entity_at ON events(entity_id, at)",
        """CREATE TABLE decisions (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            source_layer TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            action TEXT NOT NULL,
            data TEXT NOT NULL,
            outcome TEXT NOT NULL,
            reason TEXT,
            trace TEXT NOT NULL
        )""",
        "CREATE INDEX decisions_at ON decisions(at)",
        "CREATE INDEX decisions_entity ON decisions(entity_id, at)",
        """CREATE TABLE people (
            user_id TEXT PRIMARY KEY,
            display_name TEXT,
            level INTEGER NOT NULL DEFAULT 2,
            preferences TEXT NOT NULL DEFAULT '{}',
            consents TEXT NOT NULL DEFAULT '{}',
            created TEXT NOT NULL
        )""",
        """CREATE TABLE presence_history (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            user_id TEXT NOT NULL,
            home INTEGER NOT NULL
        )""",
        "CREATE INDEX presence_history_at ON presence_history(at)",
        """CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            user_id TEXT,
            question TEXT NOT NULL,
            answer TEXT NOT NULL
        )""",
        "CREATE INDEX conversations_at ON conversations(at)",
        """CREATE TABLE skill_counts (
            skill TEXT NOT NULL,
            area_id TEXT NOT NULL,
            bucket TEXT NOT NULL,
            outcome TEXT NOT NULL,
            weight REAL NOT NULL,
            updated TEXT NOT NULL,
            PRIMARY KEY (skill, area_id, bucket, outcome)
        )""",
        """CREATE TABLE skill_steps (
            skill TEXT NOT NULL,
            area_id TEXT NOT NULL,
            step INTEGER NOT NULL,
            score REAL,
            situations INTEGER NOT NULL DEFAULT 0,
            since TEXT NOT NULL,
            first_seen TEXT,
            history TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (skill, area_id)
        )""",
        """CREATE TABLE skill_values (
            skill TEXT NOT NULL,
            area_id TEXT NOT NULL,
            bucket TEXT NOT NULL,
            key TEXT NOT NULL,
            mean REAL NOT NULL,
            weight REAL NOT NULL,
            updated TEXT NOT NULL,
            PRIMARY KEY (skill, area_id, bucket, key)
        )""",
        """CREATE TABLE proposals (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            skill TEXT,
            area_id TEXT,
            entity_id TEXT NOT NULL,
            action TEXT NOT NULL,
            data TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            answered_at TEXT,
            answered_by TEXT,
            answer TEXT,
            until TEXT
        )""",
        "CREATE INDEX proposals_status ON proposals(status, at)",
        """CREATE TABLE notifications (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            kind TEXT NOT NULL,
            topic TEXT NOT NULL,
            text_key TEXT NOT NULL,
            params TEXT NOT NULL,
            severity INTEGER NOT NULL,
            resolved_at TEXT,
            snoozed_until TEXT,
            delivered TEXT NOT NULL DEFAULT '{}',
            concerns TEXT NOT NULL DEFAULT 'home'
        )""",
        "CREATE INDEX notifications_topic ON notifications(topic, resolved_at)",
        """CREATE TABLE house_rules (
            id INTEGER PRIMARY KEY,
            created TEXT NOT NULL,
            created_by TEXT,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            effect TEXT NOT NULL,
            action TEXT,
            value REAL,
            time_from TEXT,
            time_to TEXT,
            conditions TEXT NOT NULL DEFAULT '{}',
            enabled INTEGER NOT NULL DEFAULT 1,
            source_text TEXT
        )""",
        """CREATE TABLE ai_log (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            purpose TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            sent TEXT NOT NULL,
            received TEXT NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost REAL NOT NULL DEFAULT 0,
            ok INTEGER NOT NULL,
            instruction TEXT
        )""",
        "CREATE INDEX ai_log_at ON ai_log(at)",
        """CREATE TABLE corrections (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            skill TEXT NOT NULL,
            area_id TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            trial INTEGER NOT NULL DEFAULT 0
        )""",
        "CREATE INDEX corrections_at ON corrections(at)",
        """CREATE TABLE automations (
            entity_id TEXT PRIMARY KEY,
            config_id TEXT,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'seen',
            seeded INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL,
            changed_at TEXT,
            changed_by TEXT
        )""",
        """CREATE TABLE energy_quarters (
            start TEXT PRIMARY KEY,
            import_kwh REAL NOT NULL DEFAULT 0,
            export_kwh REAL NOT NULL DEFAULT 0,
            solar_kwh REAL NOT NULL DEFAULT 0,
            battery_in_kwh REAL NOT NULL DEFAULT 0,
            battery_out_kwh REAL NOT NULL DEFAULT 0,
            car_kwh REAL NOT NULL DEFAULT 0,
            house_w REAL NOT NULL DEFAULT 0,
            price REAL,
            export_price REAL,
            cost REAL NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE energy_days (
            day TEXT PRIMARY KEY,
            import_kwh REAL NOT NULL DEFAULT 0,
            export_kwh REAL NOT NULL DEFAULT 0,
            solar_kwh REAL NOT NULL DEFAULT 0,
            car_kwh REAL NOT NULL DEFAULT 0,
            cost REAL NOT NULL DEFAULT 0,
            outdoor_sum REAL NOT NULL DEFAULT 0,
            outdoor_n INTEGER NOT NULL DEFAULT 0,
            price_sum REAL NOT NULL DEFAULT 0,
            price_n INTEGER NOT NULL DEFAULT 0,
            planning INTEGER NOT NULL DEFAULT 0,
            baseline_kwh REAL,
            baseline_cost REAL
        )""",
        """CREATE TABLE energy_profiles (
            kind TEXT NOT NULL,
            day_type TEXT NOT NULL,
            quarter INTEGER NOT NULL,
            mean REAL NOT NULL,
            weight REAL NOT NULL,
            PRIMARY KEY (kind, day_type, quarter)
        )""",
        """CREATE TABLE day_notes (
            day TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            source TEXT NOT NULL,
            at TEXT NOT NULL
        )""",
        """CREATE TABLE scenes (
            id INTEGER PRIMARY KEY,
            area_id TEXT NOT NULL,
            key TEXT NOT NULL,
            name TEXT,
            source TEXT NOT NULL,
            states TEXT NOT NULL,
            created_at TEXT NOT NULL,
            used_at TEXT,
            uses INTEGER NOT NULL DEFAULT 0,
            UNIQUE (area_id, key)
        )""",
        """CREATE TABLE reports (
            week TEXT PRIMARY KEY,
            at TEXT NOT NULL,
            data TEXT NOT NULL
        )""",
        """CREATE TABLE spoken (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            area_id TEXT,
            kind TEXT NOT NULL
        )""",
        "CREATE INDEX spoken_at ON spoken(at)",
        """CREATE TABLE vesta_said (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            user_id TEXT,
            topic TEXT NOT NULL,
            variant INTEGER NOT NULL,
            key TEXT NOT NULL,
            question INTEGER NOT NULL DEFAULT 0
        )""",
        "CREATE INDEX vesta_said_user ON vesta_said(user_id, at)",
        """CREATE TABLE skill_seen (
            skill TEXT NOT NULL,
            area_id TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            PRIMARY KEY (skill, area_id)
        )""",
        """CREATE TABLE history_evidence (
            skill TEXT NOT NULL,
            area_id TEXT NOT NULL,
            bucket TEXT NOT NULL,
            hit INTEGER NOT NULL,
            of_days INTEGER NOT NULL,
            PRIMARY KEY (skill, area_id, bucket)
        )""",
        """CREATE TABLE history_automations (
            automation_id TEXT PRIMARY KEY,
            acted INTEGER NOT NULL,
            undone INTEGER NOT NULL,
            matched INTEGER NOT NULL
        )""",
        """CREATE TABLE skill_earn (
            skill TEXT NOT NULL,
            area_id TEXT NOT NULL,
            setbacks TEXT NOT NULL DEFAULT '[]',
            since TEXT,
            situations REAL,
            answers REAL,
            back_to INTEGER,
            held INTEGER,
            PRIMARY KEY (skill, area_id)
        )""",
        """CREATE TABLE thumbs (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            decision_id INTEGER,
            user_id TEXT,
            part TEXT,
            good INTEGER NOT NULL,
            learned TEXT
        )""",
        "CREATE INDEX thumbs_at ON thumbs(at)",
        "CREATE INDEX thumbs_decision ON thumbs(decision_id)",
        """CREATE TABLE detections (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            kind TEXT NOT NULL,
            area_id TEXT,
            camera TEXT
        )""",
        "CREATE INDEX detections_at ON detections(at)",
        """CREATE TABLE part_changes (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            part TEXT NOT NULL,
            state TEXT NOT NULL,
            user_id TEXT
        )""",
        "CREATE INDEX part_changes_at ON part_changes(at)",
        """CREATE TABLE house_knowledge (
            id INTEGER PRIMARY KEY,
            subject_kind TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            field TEXT NOT NULL,
            value TEXT NOT NULL,
            source TEXT NOT NULL,
            certainty REAL,
            status TEXT NOT NULL,
            since TEXT NOT NULL,
            ai_log INTEGER,
            UNIQUE (subject_kind, subject_id, field)
        )""",
    ],
    # 22 -> 23: the anonymous weekly count is gone (E-442); its yes and when it last went.
    [
        "DELETE FROM settings WHERE key IN ('counter', 'counted_at')",
    ],
    # 23 -> 24: Test het huis (E-476, B-394): a test that is still open and how its device goes
    # back – so a restart in the middle still puts it back.
    [
        """CREATE TABLE IF NOT EXISTS tests (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            area_id TEXT,
            entity_id TEXT,
            user_id TEXT,
            started TEXT NOT NULL,
            until TEXT NOT NULL,
            back TEXT NOT NULL
        )""",
    ],
    # 24 -> 25: the alarm in the Logboek (E-533): switched on, gone off, switched off, and the
    # house put back (E-534) – with how many lamps and coverings, and how long it went off.
    # Kept 30 days like the decisions.
    [
        """CREATE TABLE IF NOT EXISTS alarm_log (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            alarm TEXT NOT NULL,
            state TEXT NOT NULL,
            previous TEXT,
            lamps INTEGER NOT NULL DEFAULT 0,
            covers INTEGER NOT NULL DEFAULT 0,
            seconds INTEGER
        )""",
        "CREATE INDEX IF NOT EXISTS alarm_log_at ON alarm_log(at)",
    ],
]
