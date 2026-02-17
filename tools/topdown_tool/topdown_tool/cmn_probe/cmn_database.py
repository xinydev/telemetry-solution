# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

# pylint: disable=too-many-lines

"""
This module provides the database for CMN. It parses CMN specification JSON file and exposes
methods to retrieve information relevant for capture.
"""

from enum import Enum
from itertools import groupby
from operator import itemgetter
from re import sub
from sqlite3 import connect
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union

from topdown_tool.cmn_probe.common import (
    CmnLocation,
    Event,
    JsonEvent,
    JsonGroup,
    JsonMetric,
    JsonTopdownGroup,
    JsonTopdownMetric,
    JsonWatchpoint,
    Location,
    MetricDetails,
    NodeLocation,
    PortLocation,
    TopdownMetricDetails,
    Watchpoint,
    XpLocation,
)
from topdown_tool.cmn_probe.scheduler import NodeEntry, WatchpointPort, WatchpointPortMap


class DeviceType(Enum):
    """Enum for Device Type (Node / Port)
    """
    NODE = 1
    PORT = 2


# pylint: disable=too-many-public-methods
class CmnDatabase:
    """CMN database class
    Processes CMN specification JSON and exposes methods to retrieve information relevant for
    capture.
    """
    XP_DEVICE_ID: int = 6

    @staticmethod
    def normalize_int(value: int) -> int:
        """Normalize an integer in range [0, 2^64 - 1] to an integer in range [-2^63, 2^63 - 1]

        Args:
            value (int): value to normalize

        Returns:
            int: normalized value
        """
        if value >= 2 ** 63:
            value -= 2 ** 64
        return value

    @staticmethod
    def parse_hex_int(value_string: str) -> int:
        """Convert hex to a signed 64 bit int

        Args:
            s (str): string containing hex number

        Returns:
            int: value of the number
        """
        return CmnDatabase.normalize_int(int(value_string, 16))

    # pylint: disable=too-many-locals, too-many-branches, too-many-statements, invalid-name, line-too-long
    def __init__(
        self,
        cmn_version: str,
        cmn_indices: Sequence[int],
        topology_json: dict,
        specification_json: dict,
    ):
        self.version = cmn_version
        self.cmn_indices = tuple(sorted(cmn_indices))

        self.db_connection = connect(":memory:", check_same_thread=False)

        self.db_connection.executescript(
            """
            PRAGMA foreign_keys = ON;

            -- Constants

            CREATE TABLE port_device_types (
                id        INTEGER PRIMARY KEY,
                name      TEXT NOT NULL,
                full_name TEXT NOT NULL
            );

            CREATE TABLE node_device_types (
                id        INTEGER PRIMARY KEY,
                name      TEXT NOT NULL,
                full_name TEXT NOT NULL
            );

            -- Topology

            CREATE TABLE cmns (
                id      INTEGER PRIMARY KEY,
                version TEXT NOT NULL,
                size_x  INTEGER NOT NULL,
                size_y  INTEGER NOT NULL
            );

            CREATE TABLE crosspoints (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                cmn_id  INTEGER NOT NULL REFERENCES cmns(id),
                x       INTEGER NOT NULL,
                y       INTEGER NOT NULL,
                dtc     INTEGER NOT NULL,
                node_id INTEGER NOT NULL
            );

            CREATE TABLE ports (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                xp_id    INTEGER NOT NULL REFERENCES crosspoints(id),
                port     INTEGER NOT NULL,
                type     INTEGER NOT NULL REFERENCES port_device_types(id),
                cal      BOOLEAN NOT NULL,
                node_id  INTEGER,
                multiple BOOLEAN NOT NULL
            );

            CREATE TABLE nodes (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                port_id INTEGER NOT NULL REFERENCES ports(id),
                type    INTEGER NOT NULL REFERENCES node_device_types(id),
                node_id INTEGER NOT NULL
            );

            -- Groups / Metrics / Events / Watchpoints / Filters

            CREATE TABLE groups (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                node_device_id INTEGER REFERENCES node_device_types(id),
                port_device_id INTEGER REFERENCES port_device_types(id),
                name           TEXT NOT NULL,
                title          TEXT NOT NULL,
                description    TEXT NOT NULL,
                CHECK (
                        node_device_id IS NOT NULL
                        AND port_device_id IS NULL
                    OR
                        node_device_id IS NULL
                        AND port_device_id IS NOT NULL
                )
            );

            CREATE TABLE metrics (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                node_device_id INTEGER REFERENCES node_device_types(id),
                port_device_id INTEGER REFERENCES port_device_types(id),
                name           TEXT NOT NULL,
                title          TEXT NOT NULL,
                description    TEXT NOT NULL,
                units          TEXT NOT NULL,
                formula        TEXT NOT NULL,
                CHECK (
                        node_device_id IS NOT NULL
                        AND port_device_id IS NULL
                    OR
                        node_device_id IS NULL
                        AND port_device_id IS NOT NULL
                )
            );

            CREATE TABLE events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id   INTEGER REFERENCES node_device_types(id),
                name        TEXT NOT NULL,
                title       TEXT NOT NULL,
                description TEXT NOT NULL,
                type        INTEGER NOT NULL,
                event_id    INTEGER
            );

            CREATE TABLE watchpoints (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                node_device_id INTEGER REFERENCES node_device_types(id),
                port_device_id INTEGER REFERENCES port_device_types(id),
                name           TEXT NOT NULL,
                description    TEXT NOT NULL,
                mesh_flit_dir  INTEGER NOT NULL,
                wp_chn_sel     INTEGER NOT NULL,
                wp_grp         INTEGER NOT NULL,
                wp_mask        INTEGER NOT NULL,
                CHECK (
                        node_device_id IS NOT NULL
                        AND port_device_id IS NULL
                    OR
                        node_device_id IS NULL
                        AND port_device_id IS NOT NULL
                )
            );

            CREATE TABLE watchpoints_values (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                watchpoint_id INTEGER NOT NULL REFERENCES watchpoints(id),
                wp_val        INTEGER NOT NULL
            );

            CREATE TABLE filters (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id   INTEGER NOT NULL REFERENCES node_device_types(id),
                name        TEXT NOT NULL,
                description TEXT NOT NULL
            );

            CREATE TABLE filters_encodings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filter_id   INTEGER NOT NULL REFERENCES filters(id),
                encoding    INT NOT NULL,
                name        TEXT NOT NULL,
                description TEXT NOT NULL
            );

            -- Dependencies

            CREATE TABLE metrics_for_groups (
                group_id  INTEGER NOT NULL REFERENCES groups(id),
                metric_id INTEGER NOT NULL REFERENCES metrics(id)
            );

            CREATE TABLE events_for_metrics (
                metric_id INTEGER NOT NULL REFERENCES metrics(id),
                event_id  INTEGER NOT NULL REFERENCES events(id),
                occup_id  INTEGER
            );

            CREATE TABLE sample_events_for_metrics (
                metric_id INTEGER NOT NULL REFERENCES metrics(id),
                event_id  INTEGER NOT NULL REFERENCES events(id),
                occup_id  INTEGER
            );

            CREATE TABLE watchpoints_for_metrics (
                metric_id     INTEGER NOT NULL REFERENCES metrics(id),
                watchpoint_id INTEGER NOT NULL REFERENCES watchpoints(id)
            );

            -- Topdown Groups / Topdown Metrics

            CREATE TABLE topdown_groups (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                title       TEXT NOT NULL,
                description TEXT NOT NULL
            );

            CREATE TABLE topdown_metrics (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                title       TEXT NOT NULL,
                description TEXT NOT NULL,
                units       TEXT NOT NULL,
                formula     TEXT NOT NULL
            );

            -- Dependencies

            CREATE TABLE topdown_metrics_for_topdown_groups (
                topdown_group_id  INTEGER NOT NULL REFERENCES topdown_groups(id),
                topdown_metric_id INTEGER NOT NULL REFERENCES topdown_metrics(id)
            );

            CREATE TABLE base_metrics_for_topdown_groups (
                topdown_group_id INTEGER NOT NULL REFERENCES topdown_groups(id),
                metric_id        INTEGER NOT NULL REFERENCES metrics(id)
            );

            CREATE TABLE metrics_for_topdown_metrics (
                topdown_metric_id INTEGER NOT NULL REFERENCES topdown_metrics(id),
                metric_id         INTEGER NOT NULL REFERENCES metrics(id)
            );

            CREATE TABLE topdown_metrics_for_topdown_metrics (
                derived_metric_id INTEGER NOT NULL REFERENCES topdown_metrics(id),
                source_metric_id  INTEGER NOT NULL REFERENCES topdown_metrics(id)
            );
        """
        )

        # Constants
        self.db_connection.executemany(
            "INSERT INTO port_device_types VALUES (?, ?, ?)",
            (
                (0x01, "RN-I", "RN_I"),            # Non-caching requester
                (0x02, "RN-D", "RN_D"),            # RN-I that can accept snoops on the DVM channel
                (0x04, "RN-F", "RN_F_CHIB"),       # CHI Issue B processor/cluster with built-in SAM    "RN-F_CHIB"
                (0x05, "RN-F", "RN_F_CHIB_ESAM"),  # CHI Issue B processor/cluster with external SAM    "RN-F_CHIB_ESAM"
                (0x06, "RN-F", "RN_F_CHIA"),       # CHI Issue A processor/cluster with built-in SAM    "RN-F_CHIA"
                (0x07, "RN-F", "RN_F_CHIA_ESAM"),  # CHI Issue A processor/cluster with external SAM    "RN-F_CHIA_ESAM"
                (0x08, "HN-T", "HN_T"),            # HN-I with debug/trace control
                (0x09, "HN-I", "HN_I"),            # Home Node I/O, non-coherent
                (0x0A, "HN-D", "HN_D"),            # HN-T, with CFG and DVM, power control etc.
                (0x0B, "HN-P", "HN_P"),
                (0x0C, "SN-F", "SN_F_CHIC"),       # Memory controller
                (0x0D, "SBSX", "SBSX"),            # CHI to AXI bridge
                (0x0E, "HN-F", "HN_F"),            # Home Node Full, fully coherent, with SLC and/or SF
                (0x0F, "SN-F", "SN_F_CHIE"),       # -                                                  "SN-F_CHIE"
                (0x10, "SN-F", "SN_F_CHID"),       # -                                                  "SN-F_CHID"
                (0x11, "CXHA", "CXHA"),
                (0x12, "CXRA", "CXRA"),
                (0x13, "CXRH", "CXRH"),
                (0x14, "RN-F", "RN_F_CHID"),       # -                                                  "RN-F_CHID"
                (0x15, "RN-F", "RN_F_CHID_ESAM"),  # -                                                  "RN-F_CHID_ESAM"
                (0x16, "RN-F", "RN_F_CHIC"),       # -                                                  "RN-F_CHIC"
                (0x17, "RN-F", "RN_F_CHIC_ESAM"),  # -                                                  "RN-F_CHIC_ESAM"
                (0x18, "RN-F", "RN_F_CHIE"),       # -                                                  "RN-F_CHIE"
                (0x19, "RN-F", "RN_F_CHIE_ESAM"),  # -                                                  "RN-F_CHIE_ESAM"
                (0x1A, "HN-S", "HN_S"),
                (0x1B, "LCN",  "LCN"),             # noqa: E241
                (0x1C, "MTSX", "MTSX"),
                (0x1D, "HN-V", "HN_V"),
                (0x1E, "CCG",  "CCG"),             # noqa: E241
                (0x1F, "CCGSMP", "CCGSMP"),
                (0x20, "RN-F", "RN_F_CHIF"),       # -                                                  "RN-F_F"
                (0x21, "RN-F", "RN_F_CHIF_ESAM"),  # -                                                  "RN-F_F_E"
                (0x22, "SN-F", "SN_F_CHIF"),       # -                                                  "SN-F_F"
                (0x24, "SN-F", "RN_F_CHIG_ESAM"),  # -                                                  "RN-F_G_E"
                (0x25, "SN-F", "SN_F_CHIG"),       # -                                                  "SN-F_G"
            )
        )
        self.db_connection.executemany(
            "INSERT INTO node_device_types VALUES (?, ?, ?)",
            (
                (0x0001, "DN",           "DN"),       # "DVM" in Table 2-7; home node for DVMOp operations  # noqa: E241
                (0x0002, "CFG",          "CFG"),      # Root node  # noqa: E241
                (0x0003, "DT",           "DT"),       # Debug and Trace Controller  # noqa: E241
                (0x0004, "HN-I",         "HN_I"),     # -                                                          "HNI"  # noqa: E241
                (0x0005, "HN-F",         "HN_F"),     # Fully coherent Home Node inc. system cache (SLC) and/or SF "HNF"  # noqa: E241
                (0x0006, "XP",           "XP"),       # Switch/router node (mesh crosspoint)  # noqa: E241
                (0x0007, "SBSX",         "SBSX"),     # CHI to ACE5-Lite bridge  # noqa: E241
                (0x0008, "MPAM-S",       "MPAM_S"),   # new in CMN-650                                             "MPAM_S"  # noqa: E241
                (0x0009, "MPAM-NS",      "MPAM_NS"),  # new in CMN-650                                             "MPAM_NS"  # noqa: E241
                (0x000A, "RN-I",         "RN_I"),     # I/O-coherent Request Node bridge                           "RNI"  # noqa: E241
                (0x000D, "RN-D",         "RN_D"),     # -                                                          "RND"  # noqa: E241
                (0x000F, "RN-SAM",       "RN_SAM"),   # -                                                          "RNSAM"  # noqa: E241
                (0x0010, "MTSX",         "MTSX"),     # noqa: E241
                (0x0011, "HN-P",         "HN_P"),     # HN-I optimized for peer-to-peer traffic                    "HNP"  # noqa: E241
                (0x0100, "CXRA",         "CXRA"),     # CCIX Request Agent  # noqa: E241
                (0x0101, "CXHA",         "CXHA"),     # CCIX Home Agent  # noqa: E241
                (0x0102, "CXLA",         "CXLA"),     # CCIX Link Agent  # noqa: E241
                (0x0103, "CCG-RA",       "CCG"),      # -                                                          "CCG_RA"  # noqa: E241
                (0x0104, "CCG-HA",       "CCG"),      # -                                                          "CCG_HA"  # noqa: E241
                (0x0105, "CCLA",         "CCLA"),     # noqa: E241
                (0x0106, "CCLA-RN-I",    "CCLA"),     # -                                                          "CCLA_RNI"  # noqa: E241
                (0x0200, "HN-S",         "HN_S"),     # -                                                          "HNS"  # noqa: E241
                (0x0201, "HN-S-MPAM-S",  "HN_S"),     # -                                                          "HNS_MPAM_S"  # noqa: E241
                (0x0202, "HN-S-MPAM-NS", "HN_S"),     # -                                                          "HNS_MPAM_NS"  # noqa: E241
                (0x1000, "APB",          "APB"),      # APB interface  # noqa: E241
            )
        )

        # Topology
        cursor = self.db_connection.cursor()
        for cmn_index in self.cmn_indices:
            cmn = topology_json["elements"][cmn_index]
            cursor.execute(
                "INSERT INTO cmns VALUES (?, ?, ?, ?)",
                (cmn_index, cmn_version, cmn["config"]["X"], cmn["config"]["Y"]),
            )
            for crosspoint in cmn["config"]["xps"]:
                cursor.execute(
                    "INSERT INTO crosspoints (cmn_id, x, y, dtc, node_id) VALUES (?, ?, ?, ?, ?)",
                    (
                        cmn_index,
                        crosspoint["X"],
                        crosspoint["Y"],
                        crosspoint["dtc"],
                        crosspoint["id"]
                    ),
                )
                xp_id = cursor.lastrowid
                for port in crosspoint["ports"]:
                    cursor.execute(
                        """
                        INSERT INTO
                            ports (xp_id, port, TYPE, cal, node_id, multiple)
                        VALUES
                            (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            xp_id,
                            port["port"],
                            port["type"],
                            "cal" in port and port["cal"],
                            min(port["devices"], key=lambda device: device["id"])["id"]
                            if "devices" in port
                            else None,
                            len(set(device["id"] for device in port["devices"])) >= 2
                            if "devices" in port
                            else False,
                        ),
                    )
                    port_id = cursor.lastrowid
                    for device in port.get("devices", []):
                        cursor.execute(
                            "INSERT INTO nodes (port_id, type, node_id) VALUES (?, ?, ?)",
                            (port_id, device["type"], device["id"]),
                        )
        cursor.close()

        SYS_CMN_CYCLES_TYPE = 3
        MESH_FLIT_DIR_MAPPING: Dict[str, int] = {
            "str::Upload": 0,
            "str::Download": 2,
        }
        CHANNEL_MAPPING: Dict[str, int] = {
            "REQ": 0,
            "RSP": 1,
            "SNP": 2,
            "DAT": 3,
        }
        GROUP_MAPPING: Dict[str, int] = {
            "Primary": 0,
            "Secondary": 1,
            "Tertiary": 2,
            "Quaternary": 3,
        }

        # Groups / Metrics / Events / Watchpoints / Filters
        unavailable_device_id: int = 0
        cmn_cycles_inserted: bool = False
        cursor = self.db_connection.cursor()
        for device_name, spec in specification_json["components"].items():
            # RN-F and SN-F are treated specially as "Port Devices"
            node_device_id = spec["product_configuration"].get("device_id")
            if node_device_id is None:
                normalized_device_name = sub(r"[_-]", "", device_name)
                statement = cursor.execute(
                    """
                    SELECT DISTINCT
                        type
                    FROM
                        ports
                    WHERE
                        type IN (
                            SELECT
                                id
                            FROM
                                port_device_types
                            WHERE
                                REPLACE(REPLACE(full_name, '-', ''), '_', '') = ?
                                OR (
                                    REPLACE(REPLACE(full_name, '-', ''), '_', '') LIKE ?
                                    AND NOT EXISTS (
                                        SELECT
                                            full_name
                                        FROM
                                            port_device_types
                                        WHERE
                                            REPLACE(REPLACE(full_name, '-', ''), '_', '') = ?
                                    )
                                )
                        )
                """,
                    (normalized_device_name, normalized_device_name + "%", normalized_device_name),
                )
                row = statement.fetchone()
                assert statement.fetchone() is None  # Different types of RN-Fs or SN-Fs found
                if row is not None:
                    port_device_id = row[0]
                else:
                    unavailable_device_id -= 1
                    port_device_id = unavailable_device_id
                    statement = cursor.execute(
                        """
                        SELECT
                            id
                        FROM
                            port_device_types
                        WHERE
                            REPLACE(REPLACE(full_name, '-', ''), '_', '') = ?
                            OR (
                                REPLACE(REPLACE(full_name, '-', ''), '_', '') LIKE ?
                                AND NOT EXISTS (
                                    SELECT
                                        full_name
                                    FROM
                                        port_device_types
                                    WHERE
                                        REPLACE(REPLACE(full_name, '-', ''), '_', '') = ?
                                )
                            )
                    """,
                        (normalized_device_name, normalized_device_name + "%", normalized_device_name),
                    )
                    row = statement.fetchone()
                    if row is not None:
                        port_device_id = row[0]
            else:
                port_device_id = None

            if (node_device_id is None or node_device_id < 0) and (port_device_id is None or port_device_id < 0):
                continue

            if "events" in spec:
                for event_name, event_data in spec["events"].items():
                    if "code" in event_data:
                        # Regular event
                        cursor.execute(
                            """
                            INSERT INTO events (
                                device_id,
                                name,
                                title,
                                description,
                                type,
                                event_id
                            ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                            (
                                node_device_id,
                                event_name,
                                event_data["title"],
                                event_data["description"],
                                node_device_id,
                                int(event_data["code"], 16),
                            ),
                        )
                    elif not cmn_cycles_inserted and event_name == "SYS_CMN_CYCLES":
                        # dtc_cycles event inserted once
                        cursor.execute(
                            """
                            INSERT INTO events (
                                name,
                                title,
                                description,
                                type
                            ) VALUES (?, ?, ?, ?)
                        """,
                            (
                                event_name,
                                event_data["title"],
                                event_data["description"],
                                SYS_CMN_CYCLES_TYPE,
                            ),
                        )
                        cmn_cycles_inserted = True

            if "watchpoints" in spec:
                for watchpoint_name, watchpoint_data in spec["watchpoints"].items():
                    cursor.execute(
                        """
                        INSERT INTO watchpoints (
                            node_device_id,
                            port_device_id,
                            name,
                            description,
                            mesh_flit_dir,
                            wp_chn_sel,
                            wp_grp, wp_mask
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            node_device_id,
                            port_device_id,
                            watchpoint_name,
                            watchpoint_data["description"],
                            MESH_FLIT_DIR_MAPPING[watchpoint_data["mesh_flit_dir"]],
                            CHANNEL_MAPPING[watchpoint_data["wp_chn_sel"]],
                            GROUP_MAPPING[watchpoint_data["wp_grp"]],
                            self.parse_hex_int(watchpoint_data["wp_mask"]),
                        ),
                    )
                    watchpoint_id = cursor.lastrowid
                    if isinstance(watchpoint_data["wp_val"], list):
                        for wp_val in watchpoint_data["wp_val"]:
                            cursor.execute(
                                """
                                INSERT INTO watchpoints_values (
                                    watchpoint_id,
                                    wp_val
                                ) VALUES (?, ?)
                            """,
                                (watchpoint_id, self.parse_hex_int(wp_val)),
                            )
                    else:
                        cursor.execute(
                            """
                            INSERT INTO watchpoints_values (
                                watchpoint_id,
                                wp_val
                            ) VALUES (?, ?)
                        """,
                            (watchpoint_id, self.parse_hex_int(watchpoint_data["wp_val"])),
                        )

            if "filter_specification" in spec and "filters" in spec["filter_specification"]:
                for filter_name, filter_data in spec["filter_specification"]["filters"].items():
                    # Filter
                    cursor.execute(
                        """
                        INSERT INTO filters (
                            device_id,
                            name,
                            description
                        ) VALUES (?, ?, ?)
                    """,
                        (node_device_id, filter_name, filter_data["description"]),
                    )
                    filter_id = cursor.lastrowid

                    # Encodings for filter
                    for encoding_name, encoding_data in filter_data["encodings"].items():
                        cursor.execute(
                            """
                            INSERT INTO filters_encodings (
                                filter_id,
                                encoding,
                                name,
                                description
                            ) VALUES (?, ?, ?, ?)
                        """,
                            (
                                filter_id,
                                self.normalize_int(encoding_data["encoding"]),
                                encoding_name,
                                encoding_data["description"],
                            ),
                        )

            for metric_name, metric_data in spec["metrics"].items():
                # Metric
                cursor.execute(
                    """
                    INSERT INTO metrics (
                        node_device_id,
                        port_device_id,
                        name,
                        title,
                        description,
                        units,
                        formula
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        node_device_id,
                        port_device_id,
                        metric_name,
                        metric_data["title"],
                        metric_data["description"],
                        metric_data["units"],
                        metric_data["formula"],
                    ),
                )
                metric_id = cursor.lastrowid

                # Events for metric
                if "events" in metric_data:
                    placeholders = ", ".join(["?"] * len(metric_data["events"]))
                    params = [metric_id, node_device_id]
                    params.extend(metric_data["events"])
                    cursor.execute(
                        f"""
                        INSERT INTO events_for_metrics
                        SELECT
                            ?,
                            id,
                            NULL
                        FROM
                            events
                        WHERE
                            (device_id = ?
                            OR device_id IS NULL)
                            AND name IN ({placeholders})""",
                        params,
                    )

                # Sample events for metric
                if "sample_events" in metric_data:
                    placeholders = ", ".join(["?"] * len(metric_data["sample_events"]))
                    params = [metric_id, node_device_id]
                    params.extend(metric_data["sample_events"])
                    cursor.execute(
                        f"""
                        INSERT INTO sample_events_for_metrics
                        SELECT
                            ?,
                            id,
                            NULL
                        FROM
                            events
                        WHERE
                            (device_id = ?
                            OR device_id IS NULL)
                            AND name IN ({placeholders})""",
                        params,
                    )

                # Watchpoints for metric
                if "watchpoints" in metric_data:
                    placeholders = ", ".join(["?"] * len(metric_data["watchpoints"]))
                    params = [metric_id, node_device_id, port_device_id]
                    params.extend(metric_data["watchpoints"])
                    cursor.execute(
                        f"""
                        INSERT INTO watchpoints_for_metrics
                        SELECT
                            ?,
                            id
                        FROM
                            watchpoints
                        WHERE
                            (node_device_id = ?
                            OR port_device_id = ?)
                            AND name IN ({placeholders})""",
                        params,
                    )

                # Filters for metric
                if "filters" in metric_data:
                    for filter_data in metric_data["filters"]:
                        cursor.execute(
                            """
                            UPDATE events_for_metrics
                            SET
                                occup_id = (
                                    SELECT
                                        encoding
                                    FROM
                                        filters
                                        INNER JOIN filters_encodings ON filters.id = filter_id
                                    WHERE
                                        device_id = ?
                                        AND filters.name = ?
                                        AND filters_encodings.name = ?
                                )
                            WHERE
                                metric_id = ?
                                AND event_id = (
                                    SELECT
                                    id
                                    FROM
                                    events
                                    WHERE
                                        device_id = ?
                                        AND name = ?
                                )
                        """,
                            (
                                node_device_id,
                                filter_data["filter_name"],
                                filter_data["encodings"][0],
                                metric_id,
                                node_device_id,
                                filter_data["event"],
                            ),
                        )
                        cursor.execute(
                            """
                            UPDATE sample_events_for_metrics
                            SET
                                occup_id = (
                                    SELECT
                                        encoding
                                    FROM
                                        filters
                                        INNER JOIN filters_encodings ON filters.id = filter_id
                                    WHERE
                                        device_id = ?
                                        AND filters.name = ?
                                        AND filters_encodings.name = ?
                                )
                            WHERE
                                metric_id = ?
                                AND event_id = (
                                    SELECT
                                        id
                                    FROM
                                        events
                                    WHERE
                                        device_id = ?
                                        AND name = ?
                                )
                        """,
                            (
                                node_device_id,
                                filter_data["filter_name"],
                                filter_data["encodings"][0],
                                metric_id,
                                node_device_id,
                                filter_data["event"],
                            ),
                        )

            if "metrics" in spec["groups"]:
                for group_name, group_data in spec["groups"]["metrics"].items():
                    # Group
                    cursor.execute(
                        """
                        INSERT INTO groups (
                            node_device_id,
                            port_device_id,
                            name,
                            title,
                            description
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            node_device_id,
                            port_device_id,
                            group_name,
                            group_data["title"],
                            group_data["description"],
                        ),
                    )
                    group_id = cursor.lastrowid

                    # Metrics for group
                    placeholders = ", ".join(["?"] * len(group_data["metrics"]))
                    params = [group_id, node_device_id, port_device_id]
                    params.extend(group_data["metrics"])
                    cursor.execute(
                        f"""
                        INSERT INTO metrics_for_groups
                        SELECT
                            ?,
                            id
                        FROM
                            metrics
                        WHERE
                            (node_device_id = ?
                            OR port_device_id = ?)
                            AND name IN ({placeholders})""",
                        params,
                    )

                # FIXME: Metrics not belonging to any group - currently no such metrics?
                statement = cursor.execute(
                    """
                    SELECT
                        *
                    FROM
                        metrics
                    WHERE
                        id NOT IN (
                            SELECT
                                metric_id
                            FROM
                                metrics_for_groups
                        )
                        AND (node_device_id = ?
                        OR port_device_id = ?)
                """,
                    (node_device_id, port_device_id),
                )
                while (row := statement.fetchone()) is not None:
                    pass

        # Topdown metrics
        topdown_metrics_mapping: Dict[str, int] = {}
        for metric_name, metric_data in specification_json["metrics"].items():
            # Metric
            cursor.execute(
                """
                INSERT INTO topdown_metrics (
                    name,
                    title,
                    description,
                    units,
                    formula
                ) VALUES (?, ?, ?, ?, ?)
            """,
                (metric_name, metric_data["title"], metric_data["description"], metric_data["units"], metric_data["formula"]),
            )
            metric_id = cursor.lastrowid
            assert metric_id is not None
            topdown_metrics_mapping[metric_name] = metric_id

            # Metrics for topdown metric
            placeholders = ", ".join(["?"] * len(metric_data["metrics"]))
            params = [metric_id]
            params.extend(metric_data["metrics"])
            cursor.execute(
                f"""
                INSERT INTO metrics_for_topdown_metrics
                SELECT
                    ?,
                    id
                FROM
                    metrics
                WHERE
                    name IN ({placeholders})""",
                params,
            )

        for metric_name, metric_data in specification_json["metrics"].items():
            # Metrics for topdown metric
            placeholders = ", ".join(["?"] * len(metric_data["metrics"]))
            params = [topdown_metrics_mapping[metric_name]]
            params.extend(metric_data["metrics"])
            cursor.execute(
                f"""
                INSERT INTO topdown_metrics_for_topdown_metrics
                SELECT
                    ?,
                    id
                FROM
                    topdown_metrics
                WHERE
                    name IN ({placeholders})""",
                params,
            )

        if "metrics" in specification_json["groups"]:
            for group_name, group_data in specification_json["groups"]["metrics"].items():
                # Group
                cursor.execute(
                    """
                    INSERT INTO topdown_groups (
                        name,
                        title,
                        description
                    ) VALUES (?, ?, ?)
                    """,
                    (group_name, group_data["title"], ""),
                )
                group_id = cursor.lastrowid

                # Metrics for group
                placeholders = ", ".join(["?"] * len(group_data["metrics"]))
                params = [group_id]
                params.extend(group_data["metrics"])
                cursor.execute(
                    f"""
                    INSERT INTO topdown_metrics_for_topdown_groups
                    SELECT
                        ?,
                        id
                    FROM
                        topdown_metrics
                    WHERE
                        name IN ({placeholders})""",
                    params,
                )

                # Metrics for group
                placeholders = ", ".join(["?"] * len(group_data["metrics"]))
                params = [group_id]
                params.extend(group_data["metrics"])
                cursor.execute(
                    f"""
                    INSERT INTO base_metrics_for_topdown_groups
                    SELECT
                        ?,
                        id
                    FROM
                        metrics
                    WHERE
                        name IN ({placeholders})""",
                    params,
                )

        cursor.close()

    def get_dtc_count(self, cmn_index: int) -> int:
        """Returns number of DTC domains for CMN index

        Args:
            cmn_index (int): CMN Index

        Returns:
            int: DTC domains count
        """
        statement = self.db_connection.execute(
            """
            SELECT
                COUNT(DISTINCT dtc)
            FROM
                crosspoints
            WHERE
                cmn_id = ?
            """,
            (cmn_index, )
        )
        row = statement.fetchone()
        return row[0]

    def dtc_of(self, cmn_index: int, crosspoint: int) -> int:
        """Returns DTC Index for a given Node ID / Crosspoint ID and CMN Index

        Args:
            cmn_index (int): CMN Index
            crosspoint (int): Node ID / Crosspoint ID

        Returns:
            int: DTC Index
        """
        statement = self.db_connection.execute(
            """
            SELECT
                dtc
            FROM
                crosspoints
            WHERE
                cmn_id = ?
                AND node_id <= ?
            ORDER BY
                node_id DESC
            LIMIT 1
            """,
            (cmn_index, crosspoint),
        )
        row = statement.fetchone()
        return row[0]

    def cmn_topology(self, cmn_index: int) -> Tuple[NodeEntry, ...]:
        """Returns a sequence of node topology entries for a given CMN index

        Args:
            cmn_index (int): CMN Index

        Returns:
            Tuple[NodeEntry, ...]: Sequence of node topology entries. Each is a 4-tuple:
            (dtc, xp, node, node_type, port)
        """
        statement = self.db_connection.execute(
            """
            SELECT
                dtc,
                crosspoints.node_id,
                nodes.node_id,
                nodes.type,
                ports.port
            FROM
                nodes
                INNER JOIN ports ON port_id = ports.id
                INNER JOIN crosspoints ON xp_id = crosspoints.id
            WHERE
                cmn_id = ?
            """,
            (cmn_index, )
        )
        topology: List[NodeEntry] = []
        while (row := statement.fetchone()) is not None:
            topology.append(NodeEntry(
                dtc=row[0],
                xp=row[1],
                node=row[2],
                node_type=row[3],
                port=row[4],
            ))
        return tuple(topology)

    def watchpoint_port_map(self, cmn_index: int) -> WatchpointPortMap:
        """Returns a mapping between device name and a list of crosspoints and ports where the
        device is present

        Args:
            cmn_index (int): CMN Index

        Returns:
            WatchpointPortMap: Mapping between device name and a list of crosspoints and ports where
            the device is present
        """
        mapping: WatchpointPortMap = {}
        # Port devices
        statement = self.db_connection.execute(
            """
            SELECT
                full_name,
                crosspoints.node_id,
                ports.port
            FROM
                ports
                INNER JOIN crosspoints ON xp_id = crosspoints.id
                INNER JOIN port_device_types ON type = port_device_types.id
            WHERE
                cmn_id = ?
            """,
            (cmn_index, )
        )
        while (row := statement.fetchone()) is not None:
            mapping.setdefault(row[0], []).append(WatchpointPort(xp_id=row[1], port=row[2]))
        # Node devices
        temp_mapping: WatchpointPortMap = {}
        statement = self.db_connection.execute(
            """
            SELECT
                full_name,
                crosspoints.node_id,
                ports.port
            FROM
                nodes
                INNER JOIN ports ON port_id = ports.id
                INNER JOIN crosspoints ON xp_id = crosspoints.id
                INNER JOIN node_device_types ON nodes.type = node_device_types.id
            WHERE
                cmn_id = ?
            """,
            (cmn_index, )
        )
        while (row := statement.fetchone()) is not None:
            temp_mapping.setdefault(row[0], []).append(WatchpointPort(xp_id=row[1], port=row[2]))
        # Merge (override port by node)
        for device, locations in temp_mapping.items():
            mapping[device] = locations
        return mapping

    def get_coordinates(self, location: Location) -> Tuple[Optional[int], Optional[int]]:
        """Returns (x, y) coordinates pair for a given location or a (None, None) pair for a global
        location

        Args:
            location (Location): location (Global / Port / Node)

        Returns:
            Tuple[Optional[int], Optional[int]]: coordinates
        """
        if isinstance(location, CmnLocation):
            return None, None
        statement = self.db_connection.execute(
            """
            SELECT
                x,
                y
            FROM
                crosspoints
            WHERE
                cmn_id = ?
                AND node_id = ?
        """,
            (location.cmn_index, location.xp_id),
        )
        coordinate_x, coordinate_y = statement.fetchone()
        return coordinate_x, coordinate_y

    def get_node_id_of_port(self, location: PortLocation) -> Optional[int]:
        """Returns Node ID for a given port location, or None if port doesn't have any Node ID
        assigned

        Args:
            location (PortLocation): location (Port)

        Returns:
            Optional[int]: Node ID
        """
        statement = self.db_connection.execute(
            """
            SELECT
                ports.node_id
            FROM
                ports
                INNER JOIN crosspoints ON xp_id = crosspoints.id
            WHERE
                cmn_id = ?
                AND crosspoints.node_id = ?
                AND port = ?
        """,
            (location.cmn_index, location.xp_id, location.port),
        )
        row = statement.fetchone()
        return row[0]

    @staticmethod
    def get_dev_id_field(device_type: DeviceType) -> str:
        """Returns field name to use in queries ("node_device_id" / "port_device_id") for a given
        device type

        Args:
            device_type (DeviceType): device type

        Returns:
            str: field name ("node_device_id" / "port_device_id")
        """
        if device_type == DeviceType.NODE:
            return "node_device_id"
        if device_type == DeviceType.PORT:
            return "port_device_id"
        raise ValueError("Unsupported device type")

    @staticmethod
    def get_table_for_device_type(device_type: DeviceType) -> str:
        """Returns table name with a mapping between Device ID and Device Name to use in queries
        ("node_device_types" / "port_device_types") for a given device type

        Args:
            device_type (DeviceType): device type

        Returns:
            str: field name ("node_device_types" / "port_device_types")
        """
        if device_type == DeviceType.NODE:
            return "node_device_types"
        if device_type == DeviceType.PORT:
            return "port_device_types"
        raise ValueError("Unsupported device type")

    def get_indices(self) -> Tuple[int, ...]:
        """Returns CMN Indices present in the system

        Returns:
            Tuple[int, ...]: CMN Indices present in the system
        """
        return self.cmn_indices

    def get_version(self) -> str:
        """Returns version of the CMN

        Returns:
            str: version of the CMN
        """
        return self.version

    def get_devices(
        self, device_type: DeviceType, devices: Optional[Sequence[str]] = None
    ) -> Dict[int, str]:
        """Returns a mapping between Device ID and Device Name for a set of Devices Names, also
        ensuring that these devices have set at least one of these in the specification JSON: group,
        metric, event, watchpoint, filter

        Args:
            device_type (DeviceType): device type
            devices (Optional[Sequence[str]]): devices names

        Returns:
            Dict[int, str]: mapping between Device ID and Device Name
        """
        sql = f"""
            SELECT
                id,
                name
            FROM
                {self.get_table_for_device_type(device_type)}
            WHERE
                id IN (
        """
        if device_type == DeviceType.NODE:
            sql += """
                    SELECT
                        node_device_id
                    FROM
                        groups
                    UNION SELECT
                        node_device_id
                    FROM
                        metrics
                    UNION SELECT
                        device_id
                    FROM
                        events
                    UNION SELECT
                        node_device_id
                    FROM
                        watchpoints
                    UNION SELECT
                        device_id
                    FROM
                        filters
            """
        if device_type == DeviceType.PORT:
            sql += """
                    SELECT
                        port_device_id
                    FROM
                        groups
                    UNION SELECT
                        port_device_id
                    FROM
                        metrics
                    UNION SELECT
                        port_device_id
                    FROM
                        watchpoints
            """
        sql += ")"
        if devices is not None:
            placeholders: str = ", ".join(["?"] * len(devices))
            sql += f" AND LOWER(REPLACE(REPLACE(name, '-', ''), '_', '')) IN ({placeholders})"
            params = tuple(device.replace("_", "").replace("-", "").lower() for device in devices)
        else:
            params = ()
        sql += """
            ORDER BY
                id
        """

        statement = self.db_connection.execute(sql, params)
        result: Dict[int, str] = {}
        while (row := statement.fetchone()) is not None:
            result[row[0]] = row[1]
        return result

    def get_json_events(self, device_id: int) -> Tuple[JsonEvent, ...]:
        """Get events as defined in the specification for a given Node Device ID

        Args:
            device_id (int): node device id

        Returns:
            Tuple[JsonEvent, ...]: events for a given device id
        """
        rows = self.db_connection.execute(
            """
            SELECT
                name,
                title,
                description,
                type,
                event_id
            FROM
                events
            WHERE
                device_id = ?
            ORDER BY
                event_id
        """,
            (device_id,),
        )
        return tuple(
            JsonEvent(
                name=name,
                title=title,
                description=description,
                type=type_,
                eventid=event_id,
            )
            for (name, title, description, type_, event_id) in rows
        )

    # pylint: disable=unused-variable
    def get_json_watchpoints(
        self, device_type: DeviceType, device_id: int
    ) -> Tuple[JsonWatchpoint, ...]:
        """Get watchpoints as defined in the specification for a given Device ID (node / port)

        Args:
            device_type (DeviceType): device type (node / port)
            device_id (int): device id

        Returns:
            Tuple[JsonWatchpoint, ...]: watchpoints for a given device id
        """
        sql = f"""
            SELECT
                watchpoints.id,
                name,
                description,
                mesh_flit_dir,
                wp_chn_sel,
                wp_grp,
                wp_mask,
                wp_val
            FROM
                watchpoints
                INNER JOIN watchpoints_values ON watchpoints.id = watchpoint_id
            WHERE
                {self.get_dev_id_field(device_type)} = ?
            ORDER BY
                watchpoints.id
        """
        statement = self.db_connection.execute(sql, (device_id,))
        result: List[JsonWatchpoint] = []

        for watchpoint_id, grp in groupby(statement, key=itemgetter(0)):
            first = next(grp)  # first row in this id group
            name, description, mesh_flit_dir, wp_chn_sel, wp_grp, wp_mask = first[1:7]
            wp_val: Set[int] = {first[7]}
            for row in grp:
                wp_val.add(row[7])

            result.append(
                JsonWatchpoint(
                    name=name,
                    description=description,
                    mesh_flit_dir=mesh_flit_dir,
                    wp_chn_sel=wp_chn_sel,
                    wp_grp=wp_grp,
                    wp_mask=wp_mask,
                    wp_val=frozenset(wp_val),
                )
            )

        return tuple(result)

    def get_json_metrics(self, device_type: DeviceType, device_id: int) -> Tuple[JsonMetric, ...]:
        """Get metrics as defined in the specification for a given Device ID (node / port)

        Args:
            device_type (DeviceType): device type (node / port)
            device_id (int): device id

        Returns:
            Tuple[JsonMetric, ...]: metrics for a given device id
        """
        metrics_cursor = self.db_connection.cursor()
        events_cursor = self.db_connection.cursor()

        result: List[JsonMetric] = []

        sql = f"""
            SELECT
                id,
                name,
                title,
                description,
                formula,
                units
            FROM
                metrics
            WHERE
                {self.get_dev_id_field(device_type)} = ?
            ORDER BY
                id
        """
        metrics_statement = metrics_cursor.execute(sql, (device_id,))
        while (row1 := metrics_statement.fetchone()) is not None:
            events: Set[str] = set()
            events_statement = events_cursor.execute(
                """
                SELECT
                    name
                FROM
                    events_for_metrics
                    INNER JOIN events ON events_for_metrics.event_id = id
                WHERE
                    metric_id = ?
            """,
                (row1[0],),
            )
            while (row2 := events_statement.fetchone()) is not None:
                events.add(row2[0])

            watchpoints: Set[str] = set()
            events_statement = events_cursor.execute(
                """
                SELECT
                    name
                FROM
                    watchpoints_for_metrics
                    INNER JOIN watchpoints ON watchpoint_id = id
                WHERE
                    metric_id = ?
            """,
                (row1[0],),
            )
            while (row2 := events_statement.fetchone()) is not None:
                watchpoints.add(row2[0])

            sample_events: Set[str] = set()
            events_statement = events_cursor.execute(
                """
                SELECT
                    name
                FROM
                    sample_events_for_metrics
                    INNER JOIN events ON sample_events_for_metrics.event_id = id
                WHERE
                    metric_id = ?
            """,
                (row1[0],),
            )
            while (row2 := events_statement.fetchone()) is not None:
                sample_events.add(row2[0])

            result.append(
                JsonMetric(
                    name=row1[1],
                    title=row1[2],
                    description=row1[3],
                    formula=row1[4],
                    units=row1[5],
                    events=frozenset(events),
                    watchpoints=frozenset(watchpoints),
                    sample_events=frozenset(sample_events),
                )
            )

        metrics_cursor.close()
        events_cursor.close()

        return tuple(result)

    def get_json_groups(self, device_type: DeviceType, device_id: int) -> Tuple[JsonGroup, ...]:
        """Get groups as defined in the specification for a given Device ID (node / port)

        Args:
            device_type (DeviceType): device type (node / port)
            device_id (int): device id

        Returns:
            Tuple[JsonGroup, ...]: groups for a given device id
        """
        groups_cursor = self.db_connection.cursor()
        metrics_cursor = self.db_connection.cursor()

        result: List[JsonGroup] = []

        sql = f"""
            SELECT
                id,
                name,
                title,
                description
            FROM
                groups
            WHERE
                {self.get_dev_id_field(device_type)} = ?
            ORDER BY
                id
        """
        groups_statement = groups_cursor.execute(sql, (device_id,))
        while (row1 := groups_statement.fetchone()) is not None:
            metrics: Set[str] = set()
            metrics_statement = metrics_cursor.execute(
                """
                SELECT
                    name
                FROM
                    metrics_for_groups
                    INNER JOIN metrics ON metric_id = id
                WHERE
                    group_id = ?
            """,
                (row1[0],),
            )
            while (row2 := metrics_statement.fetchone()) is not None:
                metrics.add(row2[0])

            result.append(
                JsonGroup(
                    name=row1[1], title=row1[2], description=row1[3], metrics=frozenset(metrics)
                )
            )

        groups_cursor.close()
        metrics_cursor.close()

        return tuple(result)

    def get_json_topdown_metrics(self) -> Tuple[JsonTopdownMetric, ...]:
        """Get topdown metrics as defined in the specification

        Returns:
            Tuple[JsonTopdownMetric, ...]: topdown metrics
        """
        metrics_cursor = self.db_connection.cursor()
        equation_metrics_cursor = self.db_connection.cursor()

        result: List[JsonTopdownMetric] = []

        sql = """
            SELECT
                id,
                name,
                title,
                formula
            FROM
                topdown_metrics
            ORDER BY
                id
        """
        metrics_statement = metrics_cursor.execute(sql)
        while (row1 := metrics_statement.fetchone()) is not None:
            equation_metrics: Set[str] = set()
            equation_metrics_statement = equation_metrics_cursor.execute(
                """
                SELECT
                    name
                FROM
                    topdown_metrics_for_topdown_metrics
                    INNER JOIN topdown_metrics ON source_metric_id = id
                WHERE
                    derived_metric_id = ?
            """,
                (row1[0],),
            )
            while (row2 := equation_metrics_statement.fetchone()) is not None:
                equation_metrics.add(row2[0])
            equation_metrics_statement = equation_metrics_cursor.execute(
                """
                SELECT
                    name
                FROM
                    metrics_for_topdown_metrics
                    INNER JOIN metrics ON metric_id = id
                WHERE
                    topdown_metric_id = ?
            """,
                (row1[0],),
            )
            while (row2 := equation_metrics_statement.fetchone()) is not None:
                equation_metrics.add(row2[0])

            result.append(
                JsonTopdownMetric(
                    name=row1[1],
                    title=row1[2],
                    formula=row1[3],
                    metrics=frozenset(equation_metrics),
                )
            )

        metrics_cursor.close()
        equation_metrics_cursor.close()

        return tuple(result)

    def get_json_topdown_groups(self) -> Tuple[JsonTopdownGroup, ...]:
        """Get topdown groups as defined in the specification

        Returns:
            Tuple[JsonTopdownGroup, ...]: topdown groups
        """
        groups_cursor = self.db_connection.cursor()
        metrics_cursor = self.db_connection.cursor()

        result: List[JsonTopdownGroup] = []

        sql = """
            SELECT
                id,
                name,
                title
            FROM
                topdown_groups
            ORDER BY
                id
        """
        groups_statement = groups_cursor.execute(sql)
        while (row1 := groups_statement.fetchone()) is not None:
            metrics: Set[str] = set()
            metrics_statement = metrics_cursor.execute(
                """
                SELECT
                    name
                FROM
                    topdown_metrics_for_topdown_groups
                    INNER JOIN topdown_metrics ON topdown_metric_id = id
                WHERE
                    topdown_group_id = ?
            """,
                (row1[0],),
            )
            while (row2 := metrics_statement.fetchone()) is not None:
                metrics.add(row2[0])
            metrics_statement = metrics_cursor.execute(
                """
                SELECT
                    name
                FROM
                    base_metrics_for_topdown_groups
                    INNER JOIN metrics ON metric_id = id
                WHERE
                    topdown_group_id = ?
            """,
                (row1[0],),
            )
            while (row2 := metrics_statement.fetchone()) is not None:
                metrics.add(row2[0])

            result.append(
                JsonTopdownGroup(
                    name=row1[1], title=row1[2], metrics=frozenset(metrics)
                )
            )

        groups_cursor.close()
        metrics_cursor.close()

        return tuple(result)

    def get_collectable_metrics(self, metrics: Sequence[str]) -> Dict[str, Tuple[str, ...]]:
        """Extracts metrics that are possible to collect on a CMN and assigns each collectable
        metric to a group

        Args:
            metrics (Sequence[str]): metrics names

        Returns:
            Dict[str, Tuple[str, ...]]: a mapping between group name and a set of metrics names that
            are possible to collect from the original set of metrics names passed to the function
        """
        collectable_groups: Dict[str, List[str]] = {}
        placeholders: str = ", ".join(["?"] * len(metrics))
        params: List[Union[str, int]] = list(metrics)
        params.append(self.XP_DEVICE_ID)
        statement = self.db_connection.execute(
            f"""
            SELECT
                groups.name,
                metrics.name
            FROM
                groups
                INNER JOIN metrics_for_groups ON groups.id = group_id
                INNER JOIN metrics ON metric_id = metrics.id
            WHERE
                metrics.name IN ({placeholders})
                AND (
                    metrics.node_device_id IN (
                        SELECT DISTINCT
                            type
                        FROM
                            nodes
                    )
                    OR metrics.port_device_id IN (
                        SELECT DISTINCT
                            type
                        FROM
                            ports
                    )
                    OR metrics.node_device_id = ?
                )
            ORDER BY
                groups.id,
                metrics.id
        """,
            params,
        )
        while (row := statement.fetchone()) is not None:
            collectable_groups.setdefault(row[0], []).append(row[1])
        return {
            group: tuple(collectable_metrics)
            for group, collectable_metrics in collectable_groups.items()
        }

    def get_collectable_groups(self, groups: Sequence[str]) -> Dict[str, Tuple[str, ...]]:
        """Extracts groups that are possible to collect on a CMN and assigns all collectable metrics
        to each group

        Args:
            groups (Sequence[str]): metrics names

        Returns:
            Dict[str, Tuple[str, ...]]: a mapping between group name and a set of all metrics names
            that are possible to collect
        """
        collectable_groups: Dict[str, List[str]] = {}
        placeholders: str = ", ".join(["?"] * len(groups))
        params: List[Union[str, int]] = list(groups)
        params.append(self.XP_DEVICE_ID)
        statement = self.db_connection.execute(
            f"""
            SELECT
                groups.name,
                metrics.name
            FROM
                groups
                INNER JOIN metrics_for_groups ON groups.id = group_id
                INNER JOIN metrics ON metric_id = metrics.id
            WHERE
                groups.name IN ({placeholders})
                AND (
                    groups.node_device_id IN (
                        SELECT DISTINCT
                            type
                        FROM
                            nodes
                    )
                    OR groups.port_device_id IN (
                        SELECT DISTINCT
                            type
                        FROM
                            ports
                    )
                    OR groups.node_device_id = ?
                )
        """,
            params,
        )
        while (row := statement.fetchone()) is not None:
            collectable_groups.setdefault(row[0], []).append(row[1])
        return {
            group: tuple(collectable_metrics)
            for group, collectable_metrics in collectable_groups.items()
        }

    # pylint: disable=too-many-arguments, raise-missing-from, too-many-positional-arguments
    def _get_collectable_topdown_metrics_internal(
        self,
        metric_id: int,
        topdown_metrics: Set[str],
        base_metrics: Set[str],
        skip_topdown_metrics: Set[str],
        depth: int = 0,
    ) -> bool:
        """Internal method for recursive updating of required dependencies for a topdown metric.
        Gets information about: dependence on other topdown metrics, dependence on base metrics.

        Args:
            metric_id (int): topdown metric id
            topdown_metrics (Set[str]): current set of topdown metrics that were discovered to be
            necessary to calculate requested topdown metric, passed by reference and modified
            base_metrics (Set[str]): current set of base metrics that were discovered to be
            necessary to calculate requested topdown metric, passed by reference and modified
            skip_topdown_metrics (Set[str]): recursion optimization argument, current set of topdown
            metrics that should be skipped
            depth (int): recursion depth to raise error early if depth exceeded

        Returns:
            Dict[str, Tuple[str, ...]]: a mapping between group name and a set of all metrics names
            that are possible to collect
        """
        collectable: bool = False

        topdown_metrics_statement = self.db_connection.execute(
            """
            SELECT DISTINCT
                source_metric_id,
                name
            FROM
                topdown_metrics_for_topdown_metrics
                INNER JOIN topdown_metrics ON source_metric_id = id
            WHERE
                derived_metric_id = ?
        """,
            (metric_id,),
        )
        while (row := topdown_metrics_statement.fetchone()) is not None:
            if depth >= 5:
                raise RecursionError(row[1])
            if row[1] in skip_topdown_metrics:
                continue
            try:
                if self._get_collectable_topdown_metrics_internal(
                    row[0], topdown_metrics, base_metrics, skip_topdown_metrics, depth + 1
                ):
                    topdown_metrics.add(row[1])
                    collectable = True
            except RecursionError as ex:
                raise RecursionError(str(ex) + f" → {row[1]}")
            skip_topdown_metrics.add(row[1])

        metrics_statement = self.db_connection.execute(
            """
            SELECT
                name
            FROM
                metrics_for_topdown_metrics
                INNER JOIN metrics ON metric_id = id
            WHERE
                topdown_metric_id = ?
                AND
                (
                    metrics.node_device_id IN (
                        SELECT DISTINCT
                            type
                        FROM
                            nodes
                    )
                    OR metrics.port_device_id IN (
                        SELECT DISTINCT
                            type
                        FROM
                            ports
                    )
                    OR metrics.node_device_id = ?
                )
        """,
            (metric_id, self.XP_DEVICE_ID),
        )
        while (row := metrics_statement.fetchone()) is not None:
            base_metrics.add(row[0])
            collectable = True

        return collectable

    # pylint: disable=pointless-exception-statement
    def get_collectable_topdown_metrics(
        self,
        metrics: Optional[Sequence[str]] = None,
        groups: Optional[Sequence[str]] = None
    ) -> Tuple[Dict[str, Dict[str, bool]], Dict[str, Tuple[str, ...]]]:
        """Gets collectable topdown groups. Provides information, which topdown metrics are to be
        displayed and which base metrics are required to collect to be able to calculate topdown
        metric value.

        Returns:
            Tuple[Dict[str, Dict[str, bool]], Dict[str, Tuple[str, ...]]]: a mapping between topdown
            group name and a set of all metrics names that are possible to collect. First element of
            the tuple is a mapping between (topdown group name, topdown metric name) and a boolean
            display flag. Second element of the tuple is a mapping between topdown group name and
            required base metrics to collect to calcaulte topdown metric.
        """
        result_topdown_metrics: Dict[str, Dict[str, bool]] = {}
        result_base_metrics: Dict[str, Tuple[str, ...]] = {}
        last_group_name: Optional[str] = None

        metrics_condition: str
        if metrics is None:
            metrics_condition = "FALSE"
        else:
            metrics_placeholders: str = ", ".join(["?"] * len(metrics))
            metrics_condition = f"topdown_metrics.name IN ({metrics_placeholders})"

        groups_condition: str
        if groups is None:
            groups_condition = "FALSE"
        else:
            groups_placeholders: str = ", ".join(["?"] * len(groups))
            groups_condition = f"topdown_groups.name IN ({groups_placeholders})"

        if metrics is None and groups is None:
            metrics_condition = "TRUE"
            groups_condition = "TRUE"

        params: List[str] = (list(metrics) if metrics is not None else []) + (list(groups) if groups is not None else []) + (list(metrics) if metrics is not None else [])
        topdown_metrics_statement = self.db_connection.execute(
            f"""
            SELECT DISTINCT
                topdown_groups.name,
                topdown_metrics.name,
                topdown_metrics.id
            FROM
                topdown_metrics_for_topdown_groups
                INNER JOIN topdown_groups ON topdown_group_id = topdown_groups.id
                INNER JOIN topdown_metrics ON topdown_metric_id = topdown_metrics.id
            WHERE
                {metrics_condition}
                OR {groups_condition}
                AND topdown_groups.id NOT IN (
                    SELECT
                        topdown_group_id
                    FROM
                        topdown_metrics_for_topdown_groups
                        INNER JOIN topdown_metrics ON topdown_metric_id = topdown_metrics.id
                    WHERE
                        {metrics_condition}
                )
            ORDER BY
                topdown_group_id
        """,
            params
        )
        topdown_metrics: Set[str] = set()
        base_metrics: Set[str] = set()
        skip_topdown_metrics: Set[str] = set()
        displayable: Set[str] = set()
        while (row := topdown_metrics_statement.fetchone()) is not None:
            group_name = row[0]
            if group_name != last_group_name:
                if last_group_name is not None:
                    result_topdown_metrics[last_group_name] = dict.fromkeys(sorted(topdown_metrics), False)
                    for topdown_metric in displayable:
                        result_topdown_metrics[last_group_name][topdown_metric] = True
                    result_base_metrics[last_group_name] = tuple(sorted(base_metrics))
                last_group_name = group_name
                topdown_metrics = set()
                base_metrics = set()
                skip_topdown_metrics = set()
                displayable = set()
            if row[1] in topdown_metrics:
                displayable.add(row[1])
            else:
                try:
                    collectable = self._get_collectable_topdown_metrics_internal(
                        row[2], topdown_metrics, base_metrics, skip_topdown_metrics
                    )
                except RecursionError as ex:
                    RecursionError(
                        "Recursion depth reach while resolving metric: " + str(ex) + f" → {row[1]}"
                    )
                if collectable:
                    topdown_metrics.add(row[1])
                    displayable.add(row[1])
                else:
                    skip_topdown_metrics.add(row[1])
        if last_group_name is not None:
            result_topdown_metrics[last_group_name] = dict.fromkeys(sorted(topdown_metrics), False)
            for topdown_metric in displayable:
                result_topdown_metrics[last_group_name][topdown_metric] = True
            result_base_metrics[last_group_name] = tuple(sorted(base_metrics))

        return result_topdown_metrics, result_base_metrics

    def get_collectable_base_metrics_for_topdown_group(self, groups: Optional[Sequence[str]] = None) -> Dict[str, Tuple[str, ...]]:
        groups_condition: str
        params: List[Union[str, int]]
        if groups is None:
            groups_condition = "TRUE"
            params = []
        else:
            groups_placeholders: str = ", ".join(["?"] * len(groups))
            groups_condition = f"topdown_groups.name IN ({groups_placeholders})"
            params = list(groups)
        params.append(self.XP_DEVICE_ID)
        metrics_statement = self.db_connection.execute(
            f"""
            SELECT
                topdown_groups.name,
                metrics.name
            FROM
                topdown_groups
                INNER JOIN base_metrics_for_topdown_groups ON topdown_groups.id = topdown_group_id
                INNER JOIN metrics ON metric_id = metrics.id
            WHERE
                {groups_condition}
                AND (
                    node_device_id IN (
                        SELECT DISTINCT
                            type
                        FROM
                            nodes
                    )
                    OR port_device_id IN (
                        SELECT DISTINCT
                            type
                        FROM
                            ports
                    )
                    OR node_device_id = ?
                )
            ORDER BY
                topdown_groups.id,
                metrics.id
        """,
            params
        )
        collectable_groups: Dict[str, List[str]] = {}
        while (row := metrics_statement.fetchone()) is not None:
            collectable_groups.setdefault(row[0], []).append(row[1])
        return {
            group: tuple(collectable_metrics)
            for group, collectable_metrics in collectable_groups.items()
        }

    # pylint: disable=too-many-nested-blocks
    def get_schedulable_events_for_metric(
        self, metric: str, global_only: bool
    ) -> Dict[Location, List[Union[Event, List[Event]]]]:
        """Return CMN events for applicable locations for a metric

        Args:
            metric (str): Metric name
            global_only (bool): Return global metrics only

        Returns:
            Dict[Location, List[Union[Event, List[Event]]]]: Events at specific locations
            for a metric
        """
        metrics_statement = self.db_connection.execute(
            """
            SELECT
                id,
                node_device_id,
                port_device_id
            FROM
                metrics
            WHERE
                name = ?
        """,
            (metric,),
        )

        row = metrics_statement.fetchone()
        assert metrics_statement.fetchone() is None

        metric_id = row[0]
        node_device_id = row[1]
        port_device_id = row[2]

        assert (
            node_device_id is not None
            and port_device_id is None
            or node_device_id is None
            and port_device_id is not None
        )

        watchpoints_statement = self.db_connection.execute(
            """
            SELECT
                COUNT(*)
            FROM
                watchpoints_for_metrics
            WHERE
                metric_id = ?
        """,
            (metric_id,),
        )

        row = watchpoints_statement.fetchone()
        aggregate_per_port: bool = row[0] > 0

        metrics: Dict[Location, List[Union[Event, List[Event]]]] = {}

        events_cursor = self.db_connection.cursor()
        locations_cursor = self.db_connection.cursor()

        events_statement = events_cursor.execute(
            """
            SELECT
                name,
                title,
                description,
                type,
                events.event_id,
                occup_id
            FROM
                events
                INNER JOIN events_for_metrics ON id = events_for_metrics.event_id
            WHERE
                metric_id = ?
                AND (
                    device_id <> ?
                    OR device_id IS NULL
                )
        """,
            (metric_id, self.XP_DEVICE_ID),
        )
        location: Location
        last_cmn_id: Optional[int] = None
        if node_device_id is not None:
            params: Tuple[int, ...]
            if node_device_id == self.XP_DEVICE_ID:
                sql = """
                    SELECT
                        cmn_id,
                        node_id
                    FROM
                        crosspoints
                    ORDER BY
                        cmn_id,
                        node_id
                """
                params = ()
            else:
                sql = """
                    SELECT
                        cmn_id,
                        nodes.node_id,
                        crosspoints.node_id,
                        port
                    FROM
                        nodes
                        INNER JOIN ports ON port_id = ports.id
                        INNER JOIN crosspoints ON xp_id = crosspoints.id
                    WHERE
                        nodes.type = ?
                    ORDER BY
                        cmn_id,
                        nodes.node_id
                """
                params = (node_device_id,)
            while (row1 := events_statement.fetchone()) is not None:
                last_location = None
                last_cmn_id = None
                locations_statement = locations_cursor.execute(sql, params)
                while (row2 := locations_statement.fetchone()) is not None:
                    if row2[0] != last_cmn_id:
                        # Global Event
                        location = CmnLocation(cmn_index=row2[0])
                        last_cmn_id = row2[0]
                        metrics.setdefault(location, []).append(
                            Event(
                                name=row1[0],
                                title=row1[1],
                                description=row1[2],
                                cmn_index=row2[0],
                                type=row1[3],
                                eventid=row1[4],
                                occupid=row1[5],
                                nodeid=None,
                            )
                        )
                    if not global_only:
                        # Node Event
                        xp_id = None
                        if row1[0] != "SYS_CMN_CYCLES":
                            if aggregate_per_port:
                                xp_id = row2[2]
                            elif node_device_id == self.XP_DEVICE_ID:
                                xp_id = row2[1]
                            else:
                                xp_id = row2[2]

                        new_event = Event(
                            name=row1[0],
                            title=row1[1],
                            description=row1[2],
                            cmn_index=row2[0],
                            type=row1[3],
                            eventid=row1[4],
                            occupid=row1[5],
                            nodeid=row2[1] if row1[0] != "SYS_CMN_CYCLES" else None,
                            xp_id=xp_id,
                        )
                        if aggregate_per_port:
                            location = PortLocation(cmn_index=row2[0], xp_id=row2[2], port=row2[3])
                            if location != last_location:
                                metrics.setdefault(location, []).append(new_event)
                            elif row1[0] != "SYS_CMN_CYCLES":
                                last_element = metrics[location][-1]
                                if isinstance(last_element, Event):
                                    last_element = [last_element]
                                    metrics[location][-1] = last_element
                                last_element.append(new_event)
                            last_location = location
                        else:
                            if node_device_id == self.XP_DEVICE_ID:
                                location = XpLocation(cmn_index=row2[0], xp_id=row2[1])
                            else:
                                location = NodeLocation(
                                    cmn_index=row2[0], xp_id=row2[2], port=row2[3], node_id=row2[1]
                                )
                            metrics.setdefault(location, []).append(new_event)
        else:
            # There can be only SYS_CMN_CYCLES event here
            row1 = events_statement.fetchone()
            if row1 is None:
                return metrics
            assert events_statement.fetchone() is None

            locations_statement = locations_cursor.execute(
                """
                SELECT
                    cmn_id,
                    crosspoints.node_id,
                    port
                FROM
                    ports
                    INNER JOIN crosspoints ON xp_id = crosspoints.id
                WHERE
                    ports.type = ?
                ORDER BY
                    cmn_id,
                    crosspoints.node_id,
                    port
            """,
                (port_device_id,),
            )
            while (row2 := locations_statement.fetchone()) is not None:
                if row2[0] != last_cmn_id:
                    # Global Event
                    location = CmnLocation(cmn_index=row2[0])
                    last_cmn_id = row2[0]
                    metrics.setdefault(location, []).append(
                        Event(
                            name=row1[0],
                            title=row1[1],
                            description=row1[2],
                            cmn_index=row2[0],
                            type=row1[3],
                            eventid=row1[4],
                            occupid=row1[5],
                            nodeid=None,
                        )
                    )
                if not global_only:
                    # Port Event
                    location = PortLocation(cmn_index=row2[0], xp_id=row2[1], port=row2[2])
                    metrics.setdefault(location, []).append(
                        Event(
                            name=row1[0],
                            title=row1[1],
                            description=row1[2],
                            cmn_index=row2[0],
                            type=row1[3],
                            eventid=row1[4],
                            occupid=row1[5],
                            nodeid=None,
                        )
                    )

        events_cursor.close()
        locations_cursor.close()

        return metrics

    def get_schedulable_xp_events_for_metric(
        self, metric: str, global_only: bool
    ) -> Dict[Location, List[Event]]:
        """Return CMN XP events for applicable locations for a metric

        Args:
            metric (str): Metric name
            global_only (bool): Return global metrics only

        Returns:
            Dict[Location, List[Event]]: XP events at specific locations for a metric
        """
        metrics_statement = self.db_connection.execute(
            """
            SELECT
                id
            FROM
                metrics
            WHERE
                name = ?
        """,
            (metric,),
        )

        row = metrics_statement.fetchone()
        assert metrics_statement.fetchone() is None

        metric_id = row[0]

        metrics: Dict[Location, List[Event]] = {}

        events_cursor = self.db_connection.cursor()
        crosspoints_cursor = self.db_connection.cursor()

        sql = """
            SELECT
                cmn_id,
                node_id
            FROM
                crosspoints
            ORDER BY
                cmn_id,
                node_id
        """
        events_statement = events_cursor.execute(
            """
            SELECT
                name,
                title,
                description,
                type,
                events.event_id,
                occup_id
            FROM
                events
                INNER JOIN events_for_metrics ON id = events_for_metrics.event_id
            WHERE
                metric_id = ?
                AND device_id = ?
        """,
            (metric_id, self.XP_DEVICE_ID),
        )
        location: Location
        while (row1 := events_statement.fetchone()) is not None:
            last_cmn_id: Optional[int] = None
            crosspoints_statement = crosspoints_cursor.execute(sql)
            while (row2 := crosspoints_statement.fetchone()) is not None:
                if row2[0] != last_cmn_id:
                    # Global Event
                    location = CmnLocation(cmn_index=row2[0])
                    last_cmn_id = row2[0]
                    metrics.setdefault(location, []).append(
                        Event(
                            name=row1[0],
                            title=row1[1],
                            description=row1[2],
                            cmn_index=row2[0],
                            type=row1[3],
                            eventid=row1[4],
                            occupid=row1[5],
                            nodeid=None,
                        )
                    )
                if not global_only:
                    # Node Event
                    new_event = Event(
                        name=row1[0],
                        title=row1[1],
                        description=row1[2],
                        cmn_index=row2[0],
                        type=row1[3],
                        eventid=row1[4],
                        occupid=row1[5],
                        nodeid=row2[1] if row1[0] != "SYS_CMN_CYCLES" else None,
                        xp_id=row2[1] if row1[0] != "SYS_CMN_CYCLES" else None,
                    )
                    location = XpLocation(cmn_index=row2[0], xp_id=row2[1])
                    metrics.setdefault(location, []).append(new_event)

        events_cursor.close()
        crosspoints_cursor.close()

        return metrics

    # pylint: disable=too-many-nested-blocks
    def get_schedulable_watchpoints_for_metric(
        self, metric: str, global_only: bool, decompose_global: bool
    ) -> Dict[Location, List[Union[Watchpoint, List[Watchpoint]]]]:
        """Return CMN watchpoints for applicable locations for a metric

        Args:
            metric (str): Metric name
            global_only (bool): Return global metrics only
            decompose_global (bool): Decompose global watchpoints into a set of
            port specific watchpoints (needed on Linux)

        Returns:
            Dict[Location, List[Union[Watchpoint, List[Watchpoint]]]]: Watchpoints
            at specific locations for a metric
        """
        metrics_statement = self.db_connection.execute(
            """
            SELECT
                id,
                node_device_id,
                port_device_id
            FROM
                metrics
            WHERE
                name = ?
        """,
            (metric,),
        )

        row = metrics_statement.fetchone()
        assert metrics_statement.fetchone() is None

        metric_id = row[0]
        node_device_id = row[1]
        port_device_id = row[2]

        assert (
            node_device_id is not None
            and port_device_id is None
            or node_device_id is None
            and port_device_id is not None
        )

        device_str: Optional[str] = None
        if node_device_id is not None:
            if not decompose_global:
                device_types_statement = self.db_connection.execute(
                    """
                    SELECT
                        full_name
                    FROM
                        node_device_types
                    WHERE
                        id = ?
                """,
                    (node_device_id,),
                )
                row = device_types_statement.fetchone()
                assert device_types_statement.fetchone() is None
                device_str = row[0]
            sql = """
                SELECT DISTINCT
                    cmn_id,
                    crosspoints.node_id,
                    port
                FROM
                    nodes
                    INNER JOIN ports ON port_id = ports.id
                    INNER JOIN crosspoints ON xp_id = crosspoints.id
                WHERE
                    nodes.type = ?
                ORDER BY
                    cmn_id,
                    nodes.node_id
            """
            params = (node_device_id,)
        else:
            if not decompose_global:
                device_types_statement = self.db_connection.execute(
                    """
                    SELECT
                        full_name
                    FROM
                        port_device_types
                    WHERE
                        id = ?
                """,
                    (port_device_id,),
                )
                row = device_types_statement.fetchone()
                assert device_types_statement.fetchone() is None
                device_str = row[0]
            sql = """
                SELECT
                    cmn_id,
                    crosspoints.node_id,
                    port
                FROM
                    ports
                    INNER JOIN crosspoints ON xp_id = crosspoints.id
                WHERE
                    type = ?
                ORDER BY
                    cmn_id,
                    crosspoints.node_id,
                    port
            """
            params = (port_device_id,)

        metrics: Dict[Location, List[Union[Watchpoint, List[Watchpoint]]]] = {}

        watchpoints_cursor = self.db_connection.cursor()
        locations_cursor = self.db_connection.cursor()

        last_watchpoint_id: Optional[int] = None
        watchpoints_statement = watchpoints_cursor.execute(
            """
            SELECT
                watchpoints.id,
                name,
                description,
                mesh_flit_dir,
                wp_chn_sel,
                wp_grp,
                wp_mask,
                wp_val
            FROM
                watchpoints
                INNER JOIN watchpoints_values ON watchpoints.id = watchpoints_values.watchpoint_id
                INNER JOIN watchpoints_for_metrics ON
                    watchpoints.id = watchpoints_for_metrics.watchpoint_id
            WHERE
                metric_id = ?
            ORDER BY
                watchpoints.id
        """,
            (metric_id,),
        )
        while (row1 := watchpoints_statement.fetchone()) is not None:
            if row1[0] == last_watchpoint_id:
                append = True
            else:
                append = False
                last_watchpoint_id = row1[0]
            last_cmn_id: Optional[int] = None
            global_append: bool = False
            locations_statement = locations_cursor.execute(sql, params)
            while (row2 := locations_statement.fetchone()) is not None:
                if row2[0] != last_cmn_id:
                    global_location = CmnLocation(cmn_index=row2[0])
                    last_cmn_id = row2[0]
                    global_append = False
                    if not decompose_global:
                        # Global Watchpoints
                        new_watchpoint = Watchpoint(
                            name=row1[1],
                            title="",
                            description=row1[2],
                            cmn_index=row2[0],
                            mesh_flit_dir=row1[3],
                            wp_chn_sel=row1[4],
                            wp_grp=row1[5],
                            wp_mask=row1[6],
                            wp_val=row1[7],
                            xp_id=None,
                            port=None,
                            device=device_str,
                        )
                        if append:
                            last_element = metrics[global_location][-1]
                            if isinstance(last_element, Watchpoint):
                                last_element = [last_element]
                                metrics[global_location][-1] = last_element
                            last_element.append(new_watchpoint)
                        else:
                            metrics.setdefault(global_location, []).append(new_watchpoint)
                if decompose_global:
                    new_watchpoint = Watchpoint(
                        name=row1[1],
                        title="",
                        description=row1[2],
                        cmn_index=row2[0],
                        mesh_flit_dir=row1[3],
                        wp_chn_sel=row1[4],
                        wp_grp=row1[5],
                        wp_mask=row1[6],
                        wp_val=row1[7],
                        xp_id=row2[1],
                        port=row2[2],
                        device=None,
                    )
                    if global_append or append:
                        last_element = metrics[global_location][-1]
                        if isinstance(last_element, Watchpoint):
                            last_element = [last_element]
                            metrics[global_location][-1] = last_element
                        last_element.append(new_watchpoint)
                    else:
                        metrics.setdefault(global_location, []).append(new_watchpoint)
                    global_append = True
                if not global_only:
                    # Port Watchpoints
                    location = PortLocation(cmn_index=row2[0], xp_id=row2[1], port=row2[2])
                    new_watchpoint = Watchpoint(
                        name=row1[1],
                        title="",
                        description=row1[2],
                        cmn_index=row2[0],
                        mesh_flit_dir=row1[3],
                        wp_chn_sel=row1[4],
                        wp_grp=row1[5],
                        wp_mask=row1[6],
                        wp_val=row1[7],
                        xp_id=row2[1],
                        port=row2[2],
                        device=None,
                    )
                    if append:
                        last_element = metrics[location][-1]
                        if isinstance(last_element, Watchpoint):
                            last_element = [last_element]
                            metrics[location][-1] = last_element
                        last_element.append(new_watchpoint)
                    else:
                        metrics.setdefault(location, []).append(new_watchpoint)

        watchpoints_cursor.close()
        locations_cursor.close()

        return metrics

    @staticmethod
    def merge_events(
        destination: Dict[Location, List[Union[Event, List[Event], Watchpoint, List[Watchpoint]]]],
        source: Dict[Location, List[Union[Event, List[Event], Watchpoint, List[Watchpoint]]]],
    ) -> None:
        """Merges perf groups taking location into account, modifies destination passed by reference

        Args:
            destination (Dict[Location, List[Union[Event, List[Event], Watchpoint,
            List[Watchpoint]]]]): destination perf groups indexed by location
            source (Dict[Location, List[Union[Event, List[Event], Watchpoint, List[Watchpoint]]]]):
            source perf groups indexed by location
        """
        for location, events in source.items():
            destination.setdefault(location, []).extend(events)

    # Collect by metric preparation for perf
    # pylint: disable=consider-merging-isinstance
    @staticmethod
    def old_flatten_events(
        metrics: Dict[
            str, Dict[Location, List[Union[Event, List[Event], Watchpoint, List[Watchpoint]]]]
        ],
    ) -> Tuple[Tuple[Union[Event, Watchpoint], ...], ...]:
        """For a given set of perf groups, flatten all perf groups to a flat list of perf groups,
        i.e. remove association of a perf group to metric name and location

        Args:
            metrics (Dict[str, Dict[Location, List[Union[Event, List[Event], Watchpoint,
            List[Watchpoint]]]]]): mapping of metric name and location to a list of perf groups

        Returns:
            Tuple[Tuple[Union[Event, Watchpoint], ...], ...]: flattened perf groups
        """
        perf_metrics: List[Tuple[Union[Event, Watchpoint], ...]] = []
        for metric in metrics.values():
            for located_metric in metric.values():
                perf_metric: List[Union[Event, Watchpoint]] = []
                for event in located_metric:
                    if isinstance(event, Event) or isinstance(event, Watchpoint):
                        perf_metric.append(event)
                    else:
                        for subevent in event:
                            perf_metric.append(subevent)
                perf_metrics.append(tuple(perf_metric))
        return tuple(perf_metrics)

    # Collect by metric preparation for perf
    # pylint: disable=consider-merging-isinstance
    @staticmethod
    def regroup_events(
        metrics: Dict[
            str, Dict[Location, List[Union[Event, List[Event], Watchpoint, List[Watchpoint]]]]
        ],
    ) -> Tuple[Dict[str, Dict[Location, List[Union[List[Union[Event, Watchpoint]]]]]], Dict[str, Dict[Location, List[int]]]]:
        """For a given set of perf groups, flatten all perf groups to a flat list of perf groups,
        i.e. remove association of a perf group to metric name and location

        Args:
            metrics (Dict[str, Dict[Location, List[Union[Event, List[Event], Watchpoint,
            List[Watchpoint]]]]]): mapping of metric name and location to a list of perf groups

        Returns:
            Tuple[Tuple[Union[Event, Watchpoint], ...], ...]: flattened perf groups
        """
        # Regroup metrics
        regrouped_metrics: Dict[
            str, Dict[Location, List[Union[List[Union[Event, Watchpoint]]]]]
        ] = {}
        restore_information: Dict[str, Dict[Location, List[int]]] = {}
        for metric_name, metric in metrics.items():
            regrouped_metrics[metric_name] = {}
            restore_information[metric_name] = {}
            for location, located_metric in metric.items():
                regular_events: List[Union[Event, Watchpoint]] = []
                global_watchpoints: Dict[int, List[Union[Event, Watchpoint]]] = {}  # NodeID
                regular_events_indices: List[int] = []
                global_watchpoints_indices: Dict[int, List[int]] = {}  # NodeID
                index = 0
                for node_event in located_metric:
                    if isinstance(node_event, list):
                        if isinstance(node_event[0], Watchpoint):
                            for watchpoint in node_event:
                                assert isinstance(watchpoint, Watchpoint)
                                xp_id = watchpoint.xp_id
                                port = watchpoint.port
                                assert xp_id is not None and port is not None
                                global_watchpoints.setdefault(xp_id + port, []).append(watchpoint)
                                global_watchpoints_indices.setdefault(xp_id + port, []).append(index)
                                index += 1
                        else:
                            regular_events.extend(node_event)
                            regular_events_indices.extend(range(index, index + len(node_event)))
                            index += len(node_event)
                    else:
                        regular_events.append(node_event)
                        regular_events_indices.append(index)
                        index += 1
                regrouped_metrics[metric_name][location] = ([regular_events] if len(regular_events) > 0 else []) + list(global_watchpoints.values())
                restore_information[metric_name][location] = regular_events_indices
                for x in global_watchpoints_indices.values():
                    restore_information[metric_name][location].extend(x)
        return regrouped_metrics, restore_information

    # Collect by none elimination
    # pylint: disable=consider-merging-isinstance
    @staticmethod
    def eliminate_duplicated_events(
        metrics: Dict[
            str, Dict[Location, List[Union[Event, List[Event], Watchpoint, List[Watchpoint]]]]
        ],
    ) -> Tuple[Union[Event, Watchpoint], ...]:
        """For a given set of perf groups, flatten all events and watchpoints and remove duplicates

        Args:
            metrics (Dict[str, Dict[Location, List[Union[Event, List[Event], Watchpoint,
            List[Watchpoint]]]]]): mapping of metric name and location to a list of perf groups

        Returns:
            Tuple[Union[Event, Watchpoint], ...]: unique events and watchpoints
        """
        unique_events: Set[Union[Event, Watchpoint]] = set()
        for metric in metrics.values():
            for located_metric in metric.values():
                for event in located_metric:
                    if isinstance(event, Event) or isinstance(event, Watchpoint):
                        unique_events.add(event)
                    else:
                        for subevent in event:
                            unique_events.add(subevent)
        return tuple(unique_events)

    def get_metric_details(self, metric: str) -> MetricDetails:
        """Returns details of a metric (title, description, sample events, formula, units) for a
        given metric name

        Args:
            metric (str): metric name

        Returns:
            MetricDetails: metric details (title, description, sample events, formula, units)
        """
        statement = self.db_connection.execute(
            """
            SELECT
                id,
                title,
                description,
                formula,
                units
            FROM
                metrics
            WHERE
                name = ?
        """,
            (metric,),
        )
        metric_id, title, description, formula, units = statement.fetchone()

        sample_events: List[str] = []
        statement = self.db_connection.execute(
            """
            SELECT
                name
            FROM
                sample_events_for_metrics
                INNER JOIN events ON sample_events_for_metrics.event_id = id
            WHERE
                metric_id = ?
        """,
            (metric_id,),
        )
        while (row := statement.fetchone()) is not None:
            sample_events.append(row[0])

        return MetricDetails(
            title=title,
            description=description,
            sample_events=frozenset(sample_events),
            formula=formula,
            units=units,
        )

    def get_group_title(self, group: str) -> str:
        """Returns human friendly group title for a given group name

        Args:
            group (str): group name

        Returns:
            str: human friendly group title
        """
        statement = self.db_connection.execute(
            """
            SELECT
                title
            FROM
                groups
            WHERE
                name = ?
        """,
            (group,),
        )
        row = statement.fetchone()
        assert statement.fetchone() is None
        return row[0]

    def get_topdown_metric_details(self, metric: str) -> TopdownMetricDetails:
        """Returns details of a topdown metric (title, description, formula, units, base metrics
        appearing in its equation, topdown metrics appearing in its equation) for a given topdown
        metric name

        Args:
            metric (str): topdown metric name

        Returns:
            TopdownMetricDetails: topdown metric details (title, description, formula, units, base
            metrics appearing in its equation, topdown metrics appearing in its equation)
        """
        statement = self.db_connection.execute(
            """
            SELECT
                id,
                title,
                description,
                formula,
                units
            FROM
                topdown_metrics
            WHERE
                name = ?
        """,
            (metric,),
        )
        topdown_metric_id, title, description, formula, units = statement.fetchone()

        base_metrics: Set[str] = set()
        statement = self.db_connection.execute(
            """
            SELECT
                name
            FROM
                metrics_for_topdown_metrics
                INNER JOIN metrics ON metric_id = id
            WHERE
                topdown_metric_id = ?
        """,
            (topdown_metric_id,),
        )
        while (row := statement.fetchone()) is not None:
            base_metrics.add(row[0])

        topdown_metrics: Set[str] = set()
        statement = self.db_connection.execute(
            """
            SELECT
                name
            FROM
                topdown_metrics_for_topdown_metrics
                INNER JOIN topdown_metrics ON source_metric_id = id
            WHERE
                derived_metric_id = ?
        """,
            (topdown_metric_id,),
        )
        while (row := statement.fetchone()) is not None:
            topdown_metrics.add(row[0])

        return TopdownMetricDetails(
            title=title,
            description=description,
            formula=formula,
            units=units,
            base_metrics=frozenset(base_metrics),
            topdown_metrics=frozenset(topdown_metrics),
        )

    def get_topdown_group_title(self, group: str) -> str:
        """Returns human friendly topdown group title for a given topdown group name

        Args:
            group (str): topdown group name

        Returns:
            str: human friendly topdown group title
        """
        statement = self.db_connection.execute(
            """
            SELECT
                title
            FROM
                topdown_groups
            WHERE
                name = ?
        """,
            (group,),
        )
        row = statement.fetchone()
        assert statement.fetchone() is None
        return row[0]
