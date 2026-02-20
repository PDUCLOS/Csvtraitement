"""Tests pour patterns, types, et corrections."""

import pytest
from utils.patterns import (
    detect_cell_type, CellType, normalize_decimal,
    parse_date, detect_decimal_format, normalize_whitespace
)
from core.fixer import fix_date_cell, fix_decimal_cell


class TestDetectCellType:
    def test_empty(self):
        assert detect_cell_type('') == CellType.EMPTY
        assert detect_cell_type('   ') == CellType.EMPTY

    def test_masked_empty(self):
        for val in ['N/A', 'n/a', 'NULL', 'null', '#REF!', '-', '--', '?', 'TBD']:
            assert detect_cell_type(val) == CellType.MASKED_EMPTY, f"Failed for {val}"

    def test_date_dmy(self):
        assert detect_cell_type('15.03.1985') == CellType.DATE
        assert detect_cell_type('15/03/1985') == CellType.DATE
        assert detect_cell_type('15-03-1985') == CellType.DATE

    def test_date_ymd(self):
        assert detect_cell_type('2023-06-01') == CellType.DATE

    def test_date_invalid(self):
        assert detect_cell_type('00.00.0000') == CellType.DATE_INVALID

    def test_datetime(self):
        assert detect_cell_type('15.03.2023 14:30') == CellType.DATE
        assert detect_cell_type('2023-06-01 08:00:00') == CellType.DATE

    def test_boolean(self):
        for val in ['TRUE', 'true', 'FALSE', 'OUI', 'NON', 'yes', 'no']:
            assert detect_cell_type(val) == CellType.BOOLEAN, f"Failed for {val}"

    def test_integer(self):
        assert detect_cell_type('12345') == CellType.INTEGER
        assert detect_cell_type('-42') == CellType.INTEGER

    def test_decimal_fr(self):
        assert detect_cell_type('1.234,56') == CellType.DECIMAL
        assert detect_cell_type('1.987.239,99') == CellType.DECIMAL
        assert detect_cell_type('987,50') == CellType.DECIMAL

    def test_decimal_us(self):
        assert detect_cell_type('1,234.56') == CellType.DECIMAL
        assert detect_cell_type('1,987,239.99') == CellType.DECIMAL
        assert detect_cell_type('987.50') == CellType.DECIMAL

    def test_decimal_parens(self):
        assert detect_cell_type('(1.234,56)') == CellType.DECIMAL
        assert detect_cell_type('(42)') == CellType.DECIMAL

    def test_text(self):
        assert detect_cell_type('Lyon') == CellType.TEXT_SHORT
        assert detect_cell_type('A' * 60) == CellType.TEXT_LONG


class TestNormalizeDecimal:
    def test_fr_with_thousands(self):
        assert normalize_decimal('1.987.239,99') == 1987239.99

    def test_fr_simple(self):
        assert normalize_decimal('987,50') == 987.50

    def test_us_with_thousands(self):
        assert normalize_decimal('1,987,239.99') == 1987239.99

    def test_us_simple(self):
        assert normalize_decimal('987.50') == 987.50

    def test_parentheses_negative(self):
        result = normalize_decimal('(1.234,56)')
        assert result is not None
        assert abs(result - (-1234.56)) < 0.01

    def test_integer_fallback(self):
        assert normalize_decimal('12345') == 12345.0

    def test_invalid(self):
        assert normalize_decimal('abc') is None


class TestParseDate:
    def test_dmy_dot(self):
        assert parse_date('15.03.1985') == '1985-03-15'

    def test_dmy_slash(self):
        assert parse_date('15/03/1985') == '1985-03-15'

    def test_ymd(self):
        assert parse_date('2023-06-01') == '2023-06-01'

    def test_datetime_dmy(self):
        assert parse_date('15.03.2023 14:30') == '2023-03-15'

    def test_datetime_ymd(self):
        assert parse_date('2023-06-01 08:00:00') == '2023-06-01'

    def test_invalid(self):
        assert parse_date('00.00.0000') is None

    def test_garbage(self):
        assert parse_date('not a date') is None


class TestNormalizeWhitespace:
    def test_multiple_spaces(self):
        assert normalize_whitespace('hello   world') == 'hello world'

    def test_nbsp(self):
        assert normalize_whitespace('hello\xa0world') == 'hello world'

    def test_tabs(self):
        assert normalize_whitespace('hello\t\tworld') == 'hello world'

    def test_empty(self):
        assert normalize_whitespace('') == ''


class TestFixDateCell:
    def test_invalid_date_becomes_empty(self):
        result, changed = fix_date_cell('00.00.0000')
        assert result == ''
        assert changed is True

    def test_valid_date_converted(self):
        result, changed = fix_date_cell('15.03.1985')
        assert result == '1985-03-15'
        assert changed is True

    def test_empty_unchanged(self):
        result, changed = fix_date_cell('')
        assert result == ''
        assert changed is False


class TestFixDecimalCell:
    def test_fr_normalized(self):
        result, changed = fix_decimal_cell('1.987.239,99')
        assert changed is True
        assert float(result) == 1987239.99

    def test_us_normalized(self):
        result, changed = fix_decimal_cell('1,987,239.99')
        assert changed is True
        assert float(result) == 1987239.99

    def test_detect_format(self):
        assert detect_decimal_format('1.234,56') == 'FR'
        assert detect_decimal_format('1,234.56') == 'US'
        assert detect_decimal_format('12345') is None
