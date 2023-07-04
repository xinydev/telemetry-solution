# SPDX-License-Identifier: Apache-2.0
#
# Copyright (C) Arm Ltd. 2023


from unittest import TestCase, main, mock

from spe_parser.symbols import search_symbols_by_addr_batch


class TestParseSymbol(TestCase):
    @mock.patch("spe_parser.symbols.init_search_symbols")
    def test_search_symbols(self, init_search_symbols) -> None:
        init_search_symbols.return_value = (
            [0xFFFFDF617F69B0B0, 0xFFFFDF617F69D670],
            {
                0xFFFFDF617F69D670: (
                    "[kernel.kallsyms] ipmi_set_gets_events",
                    0xFFFFDF617F69D902,
                ),
                0xFFFFDF617F69B0B0: (
                    "[kernel.kallsyms] ipmi_addr_length",
                    0xFFFFDF617F69B0EE,
                ),
            },
        )

        records = [
            (0xFFFFDF617F69D670, "[kernel.kallsyms] ipmi_set_gets_events"),
            (0xFFFFDF617F69D673, "[kernel.kallsyms] ipmi_set_gets_events"),
            (0xFFFFDF617F69D902, "[kernel.kallsyms] ipmi_set_gets_events"),
            (0xFFFFDF617F69B0B0, "[kernel.kallsyms] ipmi_addr_length"),
            (0xFFFFDF617F69B0C0, "[kernel.kallsyms] ipmi_addr_length"),
            (0xFFFFDF617F69B0EE, "[kernel.kallsyms] ipmi_addr_length"),
            (0xFFFFDF617F69C000, ""),
            (0xFFFFFFFFFFFFFFFF, ""),
            (0xFFFF000000000000, ""),
        ]

        inputs = [x[0] for x in records]
        outputs = [x[1] for x in records]
        self.assertEqual(outputs, search_symbols_by_addr_batch("", inputs, 1))


if __name__ == "__main__":
    main()
