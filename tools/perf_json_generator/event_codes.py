# SPDX-License-Identifier: Apache-2.0
# Copyright 2022 Arm Limited


def is_common(code):
    # These pmu event number ranges were taken from the Arm V8 architecture
    # references manual, version G.b, page D7-2875 available at
    # https://developer.arm.com/documentation/ddi0487/gb/?lang=en

    if isinstance(code, str):
        code = to_int(code)

    if code <= 0x003F:
        return True

    if code >= 0x4000 and code <= 0x403F:
        return True

    if code >= 0x8000 and code <= 0x80FF:
        return True

    # Previously reserved but applies for Armv8.6 onwards
    if code >= 0x8100 and code <= 0x8124:
        return True

    # Previously reserved but applies for Armv8.6 onwards
    if code >= 0x8128 and code <= 0x81FF:
        return True

    # The Arm ARM calls 0x8200-0xC0BF reserved, but defines events up to 0x82A3
    if code >= 0x8200 and code <= 0x82A3:
        return True

    return False


def is_recommended(code):
    # These pmu event number ranges were taken from the Arm V8 architecture
    # references manual, version G.b, page D7-2875 available at
    # https://developer.arm.com/documentation/ddi0487/gb/?lang=en

    if isinstance(code, str):
        code = to_int(code)

    if code >= 0x0040 and code <= 0x00BF:
        return True

    if code >= 0x4040 and code <= 0x40BF:
        return True

    return False


def is_impdef(event_code):
    # These pmu event number ranges were taken from the Arm V8 architecture
    # references manual, version G.b, page D7-2875 available at
    # https://developer.arm.com/documentation/ddi0487/gb/?lang=en

    if isinstance(event_code, str):
        event_code = to_int(event_code)

    return (event_code >= 0x00C0 and event_code <= 0x03FF) or \
           (event_code >= 0x400 and event_code <= 0x3FFF) or \
           (event_code >= 0xC0C0 and event_code <= 0xFFFF)


def to_int(event_code_str):
    return int(event_code_str, 16)
