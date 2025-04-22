# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

from typing import List, Optional, Sequence, TypeVar


T = TypeVar("T")


def unwrap(value: Optional[T], message: str = "Unexpected None value") -> T:
    """
    Ensures that the given optional value is not None.

    Parameters:
        value (Optional[T]): The value to check.
        message (str): The error message to raise if value is None.

    Returns:
        T: The non-None value.

    Raises:
        ValueError: If value is None.
    """
    if value is None:
        raise ValueError(message)
    return value


def normalize_str(name: str) -> str:
    """Normalize strings (lower case and underscores) for consistent key matching."""
    return name.lower().replace("_", "").replace("-", "")


def range_decode(arg: str) -> Optional[List[int]]:
    """
    Converts a string representing ranges and individual numbers into a sorted list of integers.

    The input should contain numbers separated by commas (,) for individual elements or hyphens (-) to indicate a range.
    Examples:
      "1"           -> [1]
      "1,3,5"       -> [1, 3, 5]
      "1-3"         -> [1, 2, 3]
      "1-3,5,7-9"   -> [1, 2, 3, 5, 7, 8, 9]
    """
    if arg is None:
        return None
    intermediate_result = set()
    for value_range in arg.split(","):
        if value_range.isdecimal():
            element = int(value_range)
            assert element >= 0
            intermediate_result.add(element)
        else:
            range_start, range_stop = map(int, value_range.split("-", 1))
            assert 0 <= range_start <= range_stop
            intermediate_result.update(range(range_start, range_stop + 1))
    return sorted(intermediate_result)


def range_encode(nums: Sequence[int]) -> Optional[str]:
    if not nums:
        return None

    # Ensure the list is sorted
    nums = sorted(nums)
    ranges = []
    start = nums[0]
    end = nums[0]

    for n in nums[1:]:
        if n == end + 1:
            end = n
        else:
            if start == end:
                ranges.append(str(start))
            else:
                ranges.append(f"{start}-{end}")
            start = n
            end = n

    # Add the last group
    if start == end:
        ranges.append(str(start))
    else:
        ranges.append(f"{start}-{end}")
    return ",".join(ranges)


class ArgsError(Exception):
    pass
