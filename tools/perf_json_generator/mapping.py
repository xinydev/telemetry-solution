#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2022 Arm Limited

import csv
import json
from dataclasses import dataclass


def name_to_key(name: str):
    return name.lower().replace(" ", "-")


def midr_string(midr: int):
    return f"{midr:#0{18}x}"


def read_mrs_cpu_info_dict(filename: str):
    """
    Read CPU info from the specified MRS cpus.json file and return dict that maps CPU name
    to MidrFields.

    See https://github.com/ARM-software/data/blob/master/cpus.json
    """
    with open(filename) as f:
        cpus = json.load(f)["cpus"]

    return {name_to_key(cpu_dict["name"]):
            MidrFields.from_arm_data(cpu_dict["name"], cpu_dict["cpuid"])
            for cpu_dict in cpus}


def read_perf_cpu_mappings(filename):
    """Read Perf CPU mappings from specified mapfile.csv"""
    with open(filename) as f:
        return PerfCpuMappings(f)


def read_telemetry_cpu_id(json_path):
    with open(json_path) as f:
        prod = json.load(f)["product_configuration"]

    return MidrFields(implementer=int(prod["implementer"], 0),
                      name=prod["product_name"],
                      partnum=int(prod["part_num"], 0))


@dataclass(frozen=True)
class MidrFields:
    """
    Represents the fields in the MIDR register
    """
    implementer: int
    name: str
    partnum: int
    architecture: int = 0xf
    variant: int = 0x0
    revision: int = 0x0

    @property
    def key(self):
        return name_to_key(self.name)

    @staticmethod
    def from_arm_data(name: str, cpuid: str):
        """
        Arm-data contains implementer as an integer, but also includes it
        in cpuid which is implementer and partnum concatenated
        as a hex string so it needs to be split.
        """
        cpuid_int = int(cpuid, 16)
        implementer = (cpuid_int & 0xff000) >> 12
        part_num = (cpuid_int & 0x00fff)
        return MidrFields(implementer=implementer,
                          name=name,
                          partnum=part_num)

    @property
    def midr(self):
        # Variant, Architecture, and Revision are all 4-bit
        assert 0 <= self.variant <= 0xf
        assert 0 <= self.architecture <= 0xf
        assert 0 <= self.revision <= 0xf

        return ((self.implementer << 24) | (self.variant << 20) |
                (self.architecture << 16) | (self.partnum << 4) | self.revision)

    @property
    def midr_string(self):
        return midr_string(self.midr)


@dataclass
class PerfCpuMapping:
    """Represents one mapping entry found in Perf's mapfile.csv"""

    # Field order must match column order in mapfile.csv
    family_model: str = midr_string(0)
    version: str = "v1"
    filename: str = ""
    event_type: str = "core"

    @property
    def name(self):
        return self.filename.split("/")[-1]

    @property
    def key(self):
        return name_to_key(self.name)

    @property
    def midr(self):
        return int(self.family_model, 16)

    @midr.setter
    def midr(self, midr_value):
        self.family_model = midr_string(midr_value)

    def set_filename_from_name(self, name):
        self.filename = "arm/" + name


class PerfCpuMappings:
    """
    Represents data found in a Perf mapfile.csv.

    Intended usage is to read, add CPU(s), then write.

    Containers PerfCpuMapping entries as well as comments (so they can be restored when
    re-writing file)
    """
    COLUMN_NAMES = ["family_model", "version", "filename", "event_type"]

    def __init__(self, file):
        lines = file.read().split("\n")

        # Assume all comments are at the start of the doc, save them so we can write them out later
        self.comments = [line for line in lines if line and line[0] == "#"]

        reader = csv.DictReader([line for line in lines if line and line[0] != "#"],
                                PerfCpuMappings.COLUMN_NAMES)
        self.mappings = [PerfCpuMapping(**x) for x in reader]

    def add_if_not_present(self, mrs_mapping: MidrFields, perf_name: str = ""):
        assert mrs_mapping

        name = name_to_key(perf_name or mrs_mapping.name)
        mapping = next((x
                        for x in self.mappings
                        if x.midr == mrs_mapping.midr and x.name == name),
                       None)

        if mapping:
            return

        mapping = PerfCpuMapping()
        mapping.midr = mrs_mapping.midr
        mapping.set_filename_from_name(name)
        self.mappings.append(mapping)

    def write_fn(self, filename):
        """Writes mappings to the specified filename"""
        with open(filename, "w") as f:
            return self.write(f)

    def write(self, file):
        """Writes mappings to the specified file object"""

        def sort_key(mapping):
            """
            Existing format sorts by MIDR, but groups by filename

            Create compound key with min midr matching the filename and midr of current item
            """
            min_midr = min([x.midr for x in self.mappings if x.filename == mapping.filename])
            return (min_midr, mapping.midr)

        if self.comments:
            file.write("\n".join(self.comments))
            file.write("\n")

        writer = csv.writer(file, lineterminator="\n")
        for mapping in sorted(self.mappings, key=sort_key):
            writer.writerow(mapping.__dict__.values())

    def __iter__(self):
        return self.mappings.__iter__()

    def __repr__(self):
        return f"{self.__class__.__name__}({self.comments}, {self.mappings})"
