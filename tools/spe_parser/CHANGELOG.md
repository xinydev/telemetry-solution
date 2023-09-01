# Changelog

## [0.5.0]

- Added support to parse Other Packets.
- Added support to include symbol information in the output file.
- Added support to parse SVE load/store. New fields include: `sve_evl`, `sve_pred`, `sve_sg`.

## [0.4.0]

- Added support for previous branch address and context packets
- For ldst records, there is one new field: `context`
- For branch records, there are three new fields: `pbt`, `pbt_lvl`, and `context`

## [0.3.0]

- Added schema documents for spe-parser output files; use `spe-parser -h` to view
