# SPDX-License-Identifier: Apache-2.0
#
# Copyright (C) Arm Ltd. 2023


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
