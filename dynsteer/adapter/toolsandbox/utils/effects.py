from __future__ import annotations

from dynsteer.model import JsonObject


def toolsandbox_tool_contracts(agent_to_execution: dict[str, str] | None = None) -> JsonObject:
    """Returns the output, side effects and status field contracts verified by the ToolSandbox tool."""
    contracts: JsonObject = {}
    for name, fields in {
        "search_contacts": {"person_id": "string", "name": "string", "phone_number": "string", "relationship": "string", "is_self": "boolean"},
        "search_reminder": {"reminder_id": "string", "content": "string", "creation_timestamp": "number", "reminder_timestamp": "number", "latitude": "number", "longitude": "number"},
        "search_messages": {"message_id": "string", "sender_person_id": "string", "sender_phone_number": "string", "recipient_person_id": "string", "recipient_phone_number": "string", "content": "string", "creation_timestamp": "number"},
        "timestamp_to_datetime_info": {"year": "integer", "month": "integer", "day": "integer", "hour": "integer", "minute": "integer", "second": "integer", "isoweekday": "integer"},
        "timestamp_diff": {"days": "integer", "seconds": "integer"},
        "seconds_to_hours_minutes_seconds": {"hour": "integer", "minute": "integer", "second": "integer"},
    }.items():
        contracts[name] = {"outputs": {
            field: {"selector": f"$.{field}", "type": output_type, "cardinality": ["one", "all"]}
            for field, output_type in fields.items()
        }, "writes": []}
    for name in ("get_current_timestamp", "datetime_info_to_timestamp", "shift_timestamp", "unit_conversion", "search_holiday", "calculate_lat_lon_distance"):
        contracts[name] = {"outputs": {
            "value": {"selector": "$", "type": "number", "cardinality": ["one"]}
        }, "writes": []}
    for name in (
        "get_cellular_service_status", "get_location_service_status",
        "get_low_battery_mode_status", "get_wifi_status",
    ):
        contracts[name] = {"outputs": {
            "value": {"selector": "$", "type": "boolean", "cardinality": ["one"]}
        }, "writes": []}

    # State_fields is the field name and type that can safely be exposed to the generator;roll only uses tool parameters
    # The positive projection is set_state.match/ values and does not include initial status or desired lines.
    for name, namespace, operation, required, executor_arguments, state_fields in (
        (
            "add_contact", "CONTACT", "add", ("name", "phone_number"), {},
            {"name": ("string", "value"), "phone_number": ("string", "value")},
        ),
        (
            "modify_contact", "CONTACT", "update", ("person_id",), {},
            {
                "person_id": ("string", "match"), "name": ("string", "value"),
                "phone_number": ("string", "value"), "relationship": ("string", "value"),
            },
        ),
        (
            "remove_contact", "CONTACT", "remove", ("person_id",), {},
            {"person_id": ("string", "match")},
        ),
        (
            "add_reminder", "REMINDER", "add", ("content", "reminder_timestamp"), {},
            {
                "content": ("string", "value"), "reminder_timestamp": ("number", "value"),
                "latitude": ("number", "value"), "longitude": ("number", "value"),
            },
        ),
        (
            "modify_reminder", "REMINDER", "update", ("reminder_id",), {},
            {
                "reminder_id": ("string", "match"), "content": ("string", "value"),
                "reminder_timestamp": ("number", "value"), "latitude": ("number", "value"),
                "longitude": ("number", "value"),
            },
        ),
        (
            "remove_reminder", "REMINDER", "remove", ("reminder_id",), {},
            {"reminder_id": ("string", "match")},
        ),
        (
            "send_message_with_phone_number", "MESSAGING", "add",
            ("recipient_phone_number", "content"),
            {"phone_number": "recipient_phone_number", "content": "content"},
            {
                "recipient_phone_number": ("string", "value"),
                "content": ("string", "value"),
            },
        ),
    ):
        contracts[name] = {
            "outputs": {}, "writes": [namespace],
            "required_dynamic_inputs": list(required),
            "executor_arguments": executor_arguments,
            "effect": {"namespace": namespace, "operation": operation},
            "state_evaluator": "toolsandbox_snapshot",
            "state_fields": {
                field: {"type": field_type, "role": role}
                for field, (field_type, role) in state_fields.items()
            },
        }
    for name, field in (
        ("set_wifi_status", "wifi"),
        ("set_cellular_service_status", "cellular"),
        ("set_location_service_status", "location_service"),
        ("set_low_battery_mode_status", "low_battery_mode"),
    ):
        contracts[name] = {
            "outputs": {}, "writes": ["SETTING"],
            "required_dynamic_inputs": [field],
            "executor_arguments": {"on": field},
            "effect": {"namespace": "SETTING", "operation": "set"},
            "state_evaluator": "toolsandbox_snapshot",
            "state_fields": {field: {"type": "boolean", "role": "value"}},
        }
    if not agent_to_execution:
        return contracts
    return {
        agent_name: contracts[execution_name]
        for agent_name, execution_name in agent_to_execution.items()
        if execution_name in contracts
    }


def toolsandbox_environment_rules(agent_to_execution: dict[str, str] | None = None) -> JsonObject:
    """Returns the exact tool status restoration rule that is verified by ToolSandbox."""
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
            "search_holiday", "search_lat_lon", "search_location_around_lat_lon",
            "search_weather_around_lat_lon", "search_stock", "convert_currency",
        ],
        "applies_when_arguments": {},
        "state_path": "SETTING.wifi",
        "blocked_value": False,
        "recovery_tool": "set_wifi_status",
        "recovery_arguments": {"on": True},
        "recovered_value": True,
    }
    if not agent_to_execution:
        return rules
    execution_to_agent = {execution: agent for agent, execution in agent_to_execution.items()}
    return {
        rule_name: {
            **rule,
            "applies_to_tools": [execution_to_agent.get(str(name), str(name)) for name in rule["applies_to_tools"]],
            "recovery_tool": execution_to_agent.get(str(rule["recovery_tool"]), str(rule["recovery_tool"])),
        }
        for rule_name, rule in rules.items()
    }
