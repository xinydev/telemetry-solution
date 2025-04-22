# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

from typing import List, Optional

from rich.console import Console, ConsoleOptions, RenderResult
from rich.table import Table

# With a large set of CPUs, it might happen that metrics data will not be readable
# when presented in a single table. SplitTable will split the table into multiple
# tables with the maximum number of columns equal to MAX_COLUMNS. Each table
# consists of a few initial columns that will be present in each split table,
# followed by unique data columns, and then a few final columns that will also be
# present in each split table.
# For example, if MAX_COLUMNS = 10, and we have 3 initial columns and 2 final
# columns, and the total number of data columns is 42, then each table will have
# 10 - 3 - 2 = 5 data columns. Therefore, there will be 9 tables: 8 tables with
# 5 data columns each and 1 table with 2 data columns.


class SplitTable:
    MAX_COLUMNS = 10

    class Row:
        def __init__(self, row_prefixes: List[str], row: List[str], row_suffixes: List[str]):
            self.prefixes = row_prefixes
            self.main = row
            self.suffixes = row_suffixes

    def __init__(
        self,
        header_prefixes: List[str],
        headers: List[str],
        header_suffixes: List[str],
        title: Optional[str] = None,
    ):
        self.title = title
        self.header_row = self.Row(header_prefixes, headers, header_suffixes)
        self.rows: List["SplitTable.Row"] = []
        self.max_main_columns = self.MAX_COLUMNS - len(header_prefixes) - len(header_suffixes)

    def add_row(self, row_prefixes: List[str], row: List[str], row_suffixes: List[str]) -> None:
        self.rows.append(self.Row(row_prefixes, row, row_suffixes))

    def __rich_console__(self, _console: Console, _options: ConsoleOptions) -> RenderResult:
        # Extend rows to maximum row length
        max_length = max(len(row.main) for row in self.rows)
        for row in self.rows:
            row.main += [""] * (max_length - len(row.main))

        # Print split tables
        first_table = True
        for column_range in [
            range(i, min(i + self.max_main_columns, len(self.header_row.main)))
            for i in range(0, len(self.header_row.main), self.max_main_columns)
        ]:
            if first_table:
                table = Table(title=self.title)
            else:
                table = Table()
            for column in (
                self.header_row.prefixes
                + self.header_row.main[column_range.start : column_range.stop]
                + self.header_row.suffixes
            ):
                table.add_column(column)
            for row in self.rows:
                table.add_row(
                    *(
                        row.prefixes
                        + row.main[column_range.start : column_range.stop]
                        + row.suffixes
                    )
                )
            if first_table:
                first_table = False
            yield table


class Header:
    HEADER_STYLES = {
        1: "[bold italic]",
        2: "[bold]",
        3: "[italic]",
    }

    def __init__(self, text: str, header_level: Optional[int]):
        self.text = text
        self.header_level = header_level

    def __rich__(self) -> str:
        prefix = (
            self.HEADER_STYLES[self.header_level] if self.header_level in self.HEADER_STYLES else ""
        )
        return prefix + self.text
