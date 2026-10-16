import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcphealth import diff_tools, norm_tools

def T(name, schema): return {"name": name, "inputSchema": schema}

def test_bool_property_schema():
    old, _ = norm_tools([T("a", {"properties": {"x": {"type": "string"}, "y": True}})])
    new, _ = norm_tools([T("a", {"properties": {"x": True, "y": False}})])
    ch = diff_tools(old, new)
    assert any(c[1] == "param_type_changed" for c in ch)

def test_bool_input_schema_and_bad_required():
    old, _ = norm_tools([T("a", True), T("b", {"properties": True, "required": True})])
    new, _ = norm_tools([T("a", {"properties": {"x": {}}, "required": ["x"]}), T("b", False)])
    ch = diff_tools(old, new)
    assert ("breaking", "required_param_added", "a", "x") in ch

def test_non_dict_tools_ignored():
    assert diff_tools(json.dumps([True, {"name": "a"}]), json.dumps([{"name": "a"}])) == []
