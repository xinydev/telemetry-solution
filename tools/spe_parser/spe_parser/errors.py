# SPDX-License-Identifier: Apache-2.0
# Copyright 2023-2025 Arm Limited


class SPEError(Exception):
    pass


class InvalidDataSource(SPEError):
    pass


class InvalidRecordType(SPEError):
    pass


class InvalidLoadStorePacket(SPEError):
    pass


class InvalidBranchPacket(SPEError):
    pass


class InvalidBrOps(SPEError):
    pass


class SPEBadPacket(SPEError):
    pass


class InvalidAddrPacket(SPEError):
    pass


class EndOfFile(SPEError):
    pass


class ParseRegionError(SPEError):
    pass
