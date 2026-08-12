#!/usr/bin/env python3
"""FRU Parser
This script parses a binary FRU (Field Replaceable Unit) file and extracts
information from it. The FRU file is expected to follow the IPMI FRU format.
The script reads the FRU file, validates checksums, and extracts information
such as chassis, board, and product information. The extracted data is then
saved in a JSON format.
Usage:
    python fru_parser.py --fru-bin <path_to_fru_file> --output <output_json_file>
Example:
    python fru_parser.py --fru-bin fru.bin --output fru.json
"""
import os
import struct
import json
import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Constants for FRU areas offsets
AREA_SIZE_UNIT = 8


class FRUParseError(Exception):
    """Base class for all FRU parsing errors."""


class FRUChecksumError(FRUParseError):
    """Raised when a checksum validation fails."""

    def __init__(self, checksum, expected):
        super().__init__(
            f"Checksum Error: Expected {expected:#02x}, but got {checksum:#02x}"
        )


class FRUInvalidValueError(FRUParseError):
    """Raised when value is not valid"""

    def __init__(self, field_name, value, expected):
        super().__init__(
            f"{field_name} get invalid value {value:#02x}, but expected {expected:#02x}"
        )


def parse_fru(file_path, json_path=None):
    """ Parse the FRU binary file and extract information. """
    if json_path is None:
        json_path = os.path.basename(file_path) + ".json"
    fru_data = {}

    with open(file_path, "rb") as f:
        # Step 1: Parse the Common Header (8 bytes)
        common_header = f.read(8)
        header_format = "BBBBBBBB"
        header_fields = struct.unpack(header_format, common_header)

        # Common Header interpretation
        common_header_format_version = header_fields[0]
        if common_header_format_version != 0x01:
            raise FRUInvalidValueError(
                "Common Header Format Version", common_header_format_version, 0x01
            )
        internal_use_offset = header_fields[1] * AREA_SIZE_UNIT
        chassis_offset = header_fields[2] * AREA_SIZE_UNIT
        board_offset = header_fields[3] * AREA_SIZE_UNIT
        product_offset = header_fields[4] * AREA_SIZE_UNIT
        multirecord_offset = header_fields[5] * AREA_SIZE_UNIT
        common_header_pad = header_fields[6]
        if common_header_pad != 0x00:
            raise FRUInvalidValueError("Common Header PAD", common_header_pad, 0x00)
        common_header_checksum = header_fields[7]
        expected_checksum = (0x100 - (sum(header_fields[0:6]) % 0x100)) & 0xFF
        if common_header_checksum != expected_checksum:
            raise FRUChecksumError(common_header_checksum, expected_checksum)
        logger.info("%s", header_fields)
        logger.info("parse common header ends")

        # Step 2: Parse Internal Use Area (if specified)
        if internal_use_offset:
            f.seek(internal_use_offset)
            format_version = f.read(1)[0]
            if format_version != 0x01:
                raise FRUInvalidValueError(
                    "Internal Use Format Version", format_version, 0x01
                )
            # assume length
            length = chassis_offset - internal_use_offset - 1
            internal_use_data = f.read(length)
            fru_data["internal"] = internal_use_data.hex().upper()

        # Step 3: Parse Chassis Info Area
        if chassis_offset:
            check_checksum(f, chassis_offset)
            f.seek(chassis_offset)
            fru_data["chassis"] = parse_chassis_info_area(f)

        # Step 4: Parse Board Info Area
        if board_offset:
            check_checksum(f, board_offset)
            f.seek(board_offset)
            fru_data["board"] = parse_board_info_area(f)

        # Step 5: Parse Product Info Area
        if product_offset:
            check_checksum(f, product_offset)
            f.seek(product_offset)
            fru_data["product"] = parse_product_info_area(f)

        # # Step 6: Parse Multi-Record Area
        # if multirecord_offset:
        #     f.seek(multirecord_offset)
        #     fru_data["multirecord"] = parse_multi_record_area(f)

    # Output the parsed data as JSON
    with open(json_path, "w", encoding='utf-8') as json_file:
        json.dump(fru_data, json_file, indent=4)
    return fru_data


def check_checksum(f, area_offset):
    """ Check the checksum of a given area in the FRU file. """
    # Read the area length and checksum
    current_position = f.tell()
    f.seek(area_offset)
    area_length = struct.unpack("BB", f.read(2))[1] * 8
    f.seek(area_offset)
    area_bytes = f.read(area_length)
    f.seek(current_position)
    expected_checksum = (0x100 - (sum(area_bytes[:-1]) % 0x100)) & 0xFF
    checksum = area_bytes[-1]
    if checksum != expected_checksum:
        raise FRUChecksumError(checksum, expected_checksum)


def parse_chassis_info_area(f):
    """Parse the Chassis Info Area of the FRU file."""
    logger.info("Parsing Chassis Info Area...")
    format_version = struct.unpack("B", f.read(1))[0]
    if format_version != 0x01:
        raise FRUInvalidValueError(
            "Chassis Info Area Format Version", format_version, 0x01
        )
    #area_length = struct.unpack("B", f.read(1))[0]
    f.read(1)
    chassis_type = struct.unpack("B", f.read(1))[0]

    chassis_info = {
        "type": chassis_type,
        "pn": decode_fru_string(f),
        "serial": decode_fru_string(f),
        "custom": [],
    }
    while string := decode_fru_string(f):
        chassis_info["custom"].append(string)
    return chassis_info


def parse_board_info_area(f):
    """ Parse the Board Info Area of the FRU file."""
    logger.info("Parsing Board Info Area...")
    format_version = struct.unpack("B", f.read(1))[0]
    if format_version != 0x01:
        raise FRUInvalidValueError(
            "Chassis Info Area Format Version", format_version, 0x01
        )
    area_length = struct.unpack("B", f.read(1))[0]
    lang_code = struct.unpack("B", f.read(1))[0]
    mfg_datetime_bytes = struct.unpack("BBB", f.read(3))
    minutes = int.from_bytes(mfg_datetime_bytes, byteorder="little")
    mfg_datetime = datetime(1996, 1, 1, 0, 0, tzinfo=timezone.utc) + timedelta(
        minutes=minutes
    )
    d = mfg_datetime
    board_info = {
        # "date": mfg_datetime.strftime("%-d/%-m/%Y %H:%M:%S"),
        "date": f"{d.day}/{d.month}/{d.year} {d.hour:#02d}:{d.minute:#02d}:{d.second:#02d}",
        "mfg": decode_fru_string(f),  # Manufacturer Name
        "pname": decode_fru_string(f),  # Product Name
        "serial": decode_fru_string(f),  # Serial Number
        "pn": decode_fru_string(f),  # Part Number
        "file": decode_fru_string(f),
        "custom": [],
    }
    while string := decode_fru_string(f):
        board_info["custom"].append(string)
    return board_info


def parse_product_info_area(f):
    """ Parse the Product Info Area of the FRU file."""
    logger.info("Parsing Product Info Area...")
    format_version = struct.unpack("B", f.read(1))[0]
    if format_version != 0x01:
        raise FRUInvalidValueError(
            "Chassis Info Area Format Version", format_version, 0x01
        )
    area_length = struct.unpack("B", f.read(1))[0]
    lang_code = struct.unpack("B", f.read(1))[0]
    product_info = {
        "lang": 1,  # Language code
        "mfg": decode_fru_string(f),  # Manufacturer
        "pname": decode_fru_string(f),  # Product Name
        "pn": decode_fru_string(f),  # Part Number
        "ver": decode_fru_string(f),  # Version
        "serial": decode_fru_string(f),  # Serial Number
        "atag": decode_fru_string(f),  # Asset Tag
        "file": decode_fru_string(f),
        "custom": [],
    }
    while string := decode_fru_string(f):
        product_info["custom"].append(string)
    return product_info


def parse_multi_record_area(f):
    """ Parse the Multi-Record Area of the FRU file."""
    logger.info("Parsing Multi-Record Area...")
    # Example Multi-Record parsing
    multi_record = [
        {
            "type": "management",
            "subtype": "uuid",
            "uuid": "9bd70799-ccf0-4915-a7f9-7ce7d64385cf",
        }
    ]
    return multi_record


def decode_fru_string(f):
    """Decodes a FRU string based on the Type/Length byte."""
    type_length = f.read(1)[0]
    if type_length == 0xC1:
        return None
    length = type_length & 0x3F
    encoding_type = (type_length >> 6) & 0x03
    string_bytes = f.read(length)
    if encoding_type == 0:
        return {"type": "binary", "data": string_bytes.hex().upper() if length else ""}
    elif encoding_type == 1:
        return {"type": "bcdplus", "data": decode_bcd_plus(string_bytes)}
    elif encoding_type == 2:
        return {"type": "6bitascii", "data": decode_6bit_ascii(string_bytes)}
    elif encoding_type == 3:
        return {"type": "text", "data": string_bytes.decode("ascii")}


def decode_bcd_plus(byte_array):
    """ Decode BCD+ encoded bytes to a string. """
    chars = []
    for byte in byte_array:
        high = (byte >> 4) & 0x0F
        low = byte & 0x0F
        chars.append(bcd_plus_digit_to_char(high))
        chars.append(bcd_plus_digit_to_char(low))
    return "".join(chars).strip()


def bcd_plus_digit_to_char(digit):
    """ Convert a BCD+ digit to its corresponding character. """
    if 0 <= digit <= 9:
        return chr(ord("0") + digit)
    elif digit == 0xA:
        return " "
    elif digit == 0xB:
        return "-"
    elif digit == 0xC:
        return "."
    else:
        return "?"


def decode_6bit_ascii(byte_array):
    """ Decode 6-bit ASCII encoded bytes to a string. """
    table = """ !"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_"""
    chars = []

    def append_six_bits_char(six_bits):
        char_value = int(six_bits, 2)
        char = table[char_value]
        chars.append(char)

    for index, byte in enumerate(byte_array):
        bitstring = f"{byte:08b}"
        if index % 3 == 0:
            six_bits = bitstring[2:]
            append_six_bits_char(six_bits)
            remain = bitstring[0:2]
        elif index % 3 == 1:
            six_bits = bitstring[4:] + remain
            append_six_bits_char(six_bits)
            remain = bitstring[0:4]
        elif index % 3 == 2:
            six_bits = bitstring[6:] + remain
            append_six_bits_char(six_bits)
            six_bits = bitstring[:6]
            append_six_bits_char(six_bits)
    return "".join(chars)


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stdout, level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fru-bin", default="fru.bin", help="path of input FRU binary file"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="path of output json file (default: <fru-bin filename>.json)",
    )
    args = parser.parse_args()
    fru_bin = args.fru_bin
    output_json_path = args.output
    parse_fru(fru_bin, output_json_path)
