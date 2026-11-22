"""The Defender XDR schema check for model-written KQL."""

from app.detection.kql import to_defender_xdr_kql
from app.detection.kql_schema import check_kql
from app.detection.sigma import parse_sigma

DNS_QUERY = """DeviceEvents
| where ActionType == "DnsQueryResponse"
| extend QueryName = tostring(parse_json(AdditionalFields).query)
| where QueryName endswith "update-check.example.net"
| project Timestamp, DeviceName, InitiatingProcessFileName, QueryName"""


def test_a_query_using_real_tables_and_columns_passes():
    result = check_kql(DNS_QUERY)

    assert result.tables == ["DeviceEvents"]
    assert result.errors == []


def test_an_invented_column_is_named():
    result = check_kql('DeviceNetworkEvents\n| where DnsQueryName has "evil" and RemoteUrl has "x"\n| project Timestamp, ProcessName')

    assert result.unknown_columns == ["DnsQueryName", "ProcessName"]
    assert result.errors == ["Columns not in DeviceNetworkEvents: DnsQueryName, ProcessName."]


def test_an_invented_table_is_named():
    result = check_kql('DeviceDnsEvents | where QueryName has "evil"')

    assert result.unknown_tables == ["DeviceDnsEvents"]
    assert "Unknown Defender XDR tables: DeviceDnsEvents." in result.errors


def test_names_in_strings_comments_members_and_functions_are_not_columns():
    kql = """// Looks for FakeColumn in comments
DeviceEvents
| where ActionType == "ServiceInstalled" and AdditionalFields.ServiceName has 'Updater'
| extend Details = parse_json(AdditionalFields)
| where tostring(Details.ServiceStartType) == "Auto"
| summarize Hits = count() by DeviceName, bin(Timestamp, 1h)
| project-rename Host = DeviceName"""

    assert check_kql(kql).errors == []


def test_joined_and_let_bound_tables_contribute_their_columns():
    kql = """let Launches = DeviceProcessEvents | where FileName =~ "rundll32.exe";
Launches
| join kind=inner (DeviceNetworkEvents | where RemotePort == 443) on DeviceId
| project Timestamp, ProcessCommandLine, RemoteIP"""

    result = check_kql(kql)

    assert set(result.tables) == {"DeviceProcessEvents", "DeviceNetworkEvents"}
    assert result.errors == []


def test_a_query_with_no_table_fails():
    assert check_kql('print "hello"').errors == ["The query reads no Defender XDR table."]


def test_the_converter_output_passes_its_own_catalog():
    rule = parse_sigma(
        "title: t\nlogsource:\n  category: process_creation\n  product: windows\ndetection:\n  sel:\n"
        "    Image|endswith: '\\\\schtasks.exe'\n    CommandLine|contains: '/create'\n  condition: sel\n"
    ).rule
    kql = to_defender_xdr_kql(rule).kql

    assert check_kql(kql).errors == [], kql


def test_a_verbatim_string_ending_in_a_backslash_is_still_a_string():
    kql = r'DeviceFileEvents | where FolderPath startswith @"C:\ProgramData\" and FileName endswith ".dll"'

    assert check_kql(kql).errors == []
    # ...and the names after it are still checked
    assert check_kql(r'DeviceFileEvents | where FolderPath startswith @"C:\Temp\" and FakeColumn == 1').unknown_columns == [
        "FakeColumn"
    ]


def test_tables_inside_materialize_union_subqueries_and_hinted_joins_are_found():
    materialized = 'let P = materialize(DeviceProcessEvents | where FileName =~ "a.exe"); P | project ProcessCommandLine'
    unioned = 'union (DeviceProcessEvents | where FileName has "x"), (DeviceFileEvents | where PreviousFileName has "x")'
    hinted = ('DeviceProcessEvents | join kind=inner hint.strategy=shuffle (DeviceNetworkEvents | where RemotePort == 443) '
              'on DeviceId | project RemoteIP')

    for kql in (materialized, unioned, hinted):
        assert check_kql(kql).errors == [], kql


def test_literals_and_names_defined_by_parse_withsource_and_datatable_are_not_columns():
    kql = """let Allow = datatable(Name:string)["a.exe"];
union withsource=TableName DeviceProcessEvents, DeviceEvents
| where Timestamp > datetime(2024-05-01T00:00:00Z) and Timestamp < ago(1h)
| parse ProcessCommandLine with * "-enc " Encoded " " *
| where FileName !in (Allow) and isnotempty(Encoded)
| project TableName, Encoded"""

    assert check_kql(kql).errors == []

