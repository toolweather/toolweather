import io, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcphealth import diff_endpoint, diff_tools, norm_tools

def T(name, schema): return {"name": name, "inputSchema": schema}

def js(*tools):
    return json.dumps(list(tools), sort_keys=True, separators=(",", ":"))

def tool(name, schema, description="d", output=None):
    return {"name": name, "description": description, "inputSchema": schema, "outputSchema": output}

def test_bool_property_schema():
    # dict -> boolean is a type change (boolean has no type); true -> false is the whole value
    old, _ = norm_tools([T("a", {"properties": {"x": {"type": "string"}, "y": True}})])
    new, _ = norm_tools([T("a", {"properties": {"x": True, "y": False}})])
    ch = sorted(diff_tools(old, new))
    assert ch == [
        ("breaking", "param_type_changed", "a", "x: string -> None"),
        ("harmless", "param_schema_changed", "a", "y"),
    ]

def test_bool_input_schema_and_bad_required():
    old, _ = norm_tools([T("a", True), T("b", {"properties": True, "required": True})])
    new, _ = norm_tools([T("a", {"properties": {"x": {}}, "required": ["x"]}), T("b", False)])
    ch = diff_tools(old, new)
    assert ch == [("breaking", "required_param_added", "a", "x")]

def test_non_dict_tools_ignored():
    assert diff_tools(json.dumps([True, {"name": "a"}]), json.dumps([{"name": "a"}])) == []

def test_bool_null_and_list_property_schemas():
    assert diff_tools(js(tool("t", {"properties": {"q": True}})), js(tool("t", {"properties": {"q": True}}))) == []
    assert diff_tools(js(tool("t", {"properties": {"q": None}})), js(tool("t", {"properties": {"q": None}}))) == []
    assert diff_tools(js(tool("t", {"properties": {"q": True}})), js(tool("t", {"properties": {"q": False}}))) == [
        ("harmless", "param_schema_changed", "t", "q")]
    assert diff_tools(js(tool("t", {"properties": {"q": None}})), js(tool("t", {"properties": {"q": True}}))) == [
        ("harmless", "param_schema_changed", "t", "q")]
    assert diff_tools(js(tool("t", {"properties": {"q": ["string"]}})), js(tool("t", {"properties": {"q": ["number"]}}))) == [
        ("harmless", "param_schema_changed", "t", "q")]
    assert diff_tools(js(tool("t", {"properties": {"q": None}})), js(tool("t", {"properties": {"q": {"type": "integer"}}}))) == [
        ("breaking", "param_type_changed", "t", "q: None -> integer")]
    # unchanged boolean property must not crash or emit a row when something else changes
    old = js(tool("t", {"properties": {"flag": True, "q": {"type": "string"}}}, "a"))
    new = js(tool("t", {"properties": {"flag": True, "q": {"type": "string"}}}, "b"))
    assert diff_tools(old, new) == [("harmless", "description_changed", "t", "")]

def test_non_dict_input_schema():
    old = js(tool("t", None))
    assert diff_tools(old, js(tool("t", ["object"]))) == []
    new = js(tool("t", {"properties": {"a": {"type": "string"}}, "required": ["a"]}))
    assert diff_tools(old, new) == [("breaking", "required_param_added", "t", "a")]

def test_nameless_tools_do_not_crash_or_hide_real_changes():
    old = json.dumps([
        True,
        {"description": "no name", "inputSchema": {"properties": {"q": True}}},
        {"name": None, "inputSchema": False},
        {"name": ["bad"]},
        {"name": "t", "description": "a", "inputSchema": {"properties": {"q": {"type": "string"}}}},
    ])
    new = json.dumps([
        {"name": "t", "description": "b", "inputSchema": {"properties": {"q": None}}},
    ])
    ch = sorted(diff_tools(old, new))
    assert ch == [
        ("breaking", "param_type_changed", "t", "q: string -> None"),
        ("harmless", "description_changed", "t", ""),
    ]

def test_dict_schemas_unchanged():
    schema = {"type": "object", "properties": {"a": {"type": "string", "description": "x"}}, "required": ["a"]}
    assert diff_tools(js(tool("t", schema)), js(tool("t", schema))) == []

    old = tool("t", {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]})
    new = tool("t", {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]})
    assert diff_tools(js(old), js(new)) == [("breaking", "param_type_changed", "t", "a: string -> integer")]

    old = tool("t", {"type": "object", "properties": {"a": {"type": "string"}}})
    new = tool("t", {"type": "object", "properties": {"a": {"type": "string", "description": "x"}}})
    assert diff_tools(js(old), js(new)) == [("harmless", "param_schema_changed", "t", "a")]

    old = tool("t", {"type": "object", "properties": {"a": {"type": "string"}}, "required": []})
    new = tool("t", {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]})
    assert diff_tools(js(old), js(new)) == [("breaking", "required_param_added", "t", "a")]

    old = tool("t", {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]})
    new = tool("t", {"type": "object", "properties": {"a": {"type": "string"}}, "required": []})
    assert diff_tools(js(old), js(new)) == [("harmless", "param_made_optional", "t", "a")]

    old = tool("t", {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "number"}}, "required": ["a"]})
    new = tool("t", {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]})
    assert diff_tools(js(old), js(new)) == [("breaking", "param_removed", "t", "b")]

    old = tool("t", {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]})
    new = tool("t", {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "number"}}, "required": ["a"]})
    assert diff_tools(js(old), js(new)) == [("harmless", "optional_param_added", "t", "b")]

    assert diff_tools(js(tool("a", {})), js(tool("a", {}), tool("b", {}))) == [("harmless", "tool_added", "b", "")]
    assert diff_tools(js(tool("a", {}), tool("b", {})), js(tool("a", {}))) == [("breaking", "tool_removed", "b", "")]
    assert diff_tools(js(tool("t", {}, "a")), js(tool("t", {}, "b"))) == [("harmless", "description_changed", "t", "")]
    assert diff_tools(js(tool("t", {}, output={"type": "object"})), js(tool("t", {}, output={"type": "string"}))) == [
        ("harmless", "output_schema_changed", "t", "")]

    old = tool("t", {"type": "object", "properties": {"a": {"description": "x"}}})
    new = tool("t", {"type": "object", "properties": {"a": {"type": "string"}}})
    assert diff_tools(js(old), js(new)) == [("breaking", "param_type_changed", "t", "a: None -> string")]

    old = tool("t", {"type": "object", "properties": {"a": {"type": ["string", "null"]}}})
    new = tool("t", {"type": "object", "properties": {"a": {"type": "string"}}})
    assert diff_tools(js(old), js(new)) == [("breaking", "param_type_changed", "t", "a: ['string', 'null'] -> string")]

def test_diff_endpoint_logs_and_skips():
    buf = io.StringIO()
    err = sys.stderr
    try:
        sys.stderr = buf
        bad = diff_endpoint("{not json", "[]", "io.example/srv (https://example.test/mcp)")
        nonlist = diff_endpoint(json.dumps({"name": "t"}), json.dumps([{"name": "t"}]), "srv (u)")
    finally:
        sys.stderr = err
    assert bad == [] and nonlist == []
    log = buf.getvalue()
    assert "diff failed for io.example/srv" in log
    assert "diff failed for srv (u)" in log
    # a normal payload still returns rows through the same guard
    rows = diff_endpoint(js(tool("t", {"properties": {"q": True}})), js(tool("t", {"properties": {"q": False}})), "ok")
    assert rows == [("harmless", "param_schema_changed", "t", "q")]

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok", fn.__name__)
    print("%d passed" % len(fns))
