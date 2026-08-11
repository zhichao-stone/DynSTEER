from __future__ import annotations

from dynsteer.model import JsonObject


def toolsandbox_environment_rules() -> JsonObject:
    """返回由 ToolSandbox 实现核实的确切工具状态恢复规则。"""
    rules: JsonObject = {}
    for setting, state_field in (
        ("set_location_service_status", "location_service"),
        ("set_cellular_service_status", "cellular"),
        ("set_wifi_status", "wifi"),
    ):
        rules[f"low_battery_blocks_{state_field}_enable"] = {
            "applies_to_tools": [setting],
            "applies_when_arguments": {"on": True},
            "state_path": "SETTING.low_battery_mode",
            "blocked_value": True,
            "recovery_tool": "set_low_battery_mode_status",
            "recovery_arguments": {"on": False},
            "recovered_value": False,
        }
    rules["cellular_required_for_message_send"] = {
        "applies_to_tools": ["send_message_with_phone_number"],
        "applies_when_arguments": {},
        "state_path": "SETTING.cellular",
        "blocked_value": False,
        "recovery_tool": "set_cellular_service_status",
        "recovery_arguments": {"on": True},
        "recovered_value": True,
    }
    rules["wifi_required_for_network_search"] = {
        "applies_to_tools": [
            "search_holiday",
            "search_lat_lon",
            "search_location_around_lat_lon",
            "search_weather_around_lat_lon",
            "search_stock",
            "convert_currency",
        ],
        "applies_when_arguments": {},
        "state_path": "SETTING.wifi",
        "blocked_value": False,
        "recovery_tool": "set_wifi_status",
        "recovery_arguments": {"on": True},
        "recovered_value": True,
    }
    return rules
