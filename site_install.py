"""Anonymous MCP install cards for the status page and leaderboard.

Snippets match README.md / llms-install.md. The free tier connects with no
key and no Authorization header. The optional quota note uses placeholders
only; it never embeds a real key.
"""
import base64, html, json

MCP_URL = "https://toolweather.toolweather.workers.dev/mcp"

CURSOR_JSON = json.dumps(
    {"mcpServers": {"toolweather": {"url": MCP_URL}}}, indent=2)
CLAUDE_CODE_JSON = json.dumps(
    {"mcpServers": {"toolweather": {"type": "http", "url": MCP_URL}}}, indent=2)
# One-line args, same text as README.md / llms-install.md (json.dumps wraps the array).
CLAUDE_DESKTOP_JSON = """{
  "mcpServers": {
    "toolweather": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "%s"]
    }
  }
}""" % MCP_URL
if json.loads(CLAUDE_DESKTOP_JSON) != {
    "mcpServers": {"toolweather": {"command": "npx", "args": ["-y", "mcp-remote", MCP_URL]}}
}:
    raise RuntimeError("Claude Desktop snippet drifted from the documented shape")
SMITHERY_JSON = json.dumps({"type": "http", "url": MCP_URL}, indent=2)
CLAUDE_CLI = f"claude mcp add --transport http toolweather {MCP_URL}"
_cursor_cfg = json.dumps({"url": MCP_URL}, separators=(",", ":"))
CURSOR_DEEPLINK = (
    "cursor://anysphere.cursor-deeplink/mcp/install?name=toolweather&config="
    + base64.b64encode(_cursor_cfg.encode()).decode())

RAISE_TEXT = f"""Cursor and Claude Code, inside toolweather:
"headers": {{ "X-API-Key": "<key from get_api_key>" }}

Claude Code CLI:
claude mcp add --transport http toolweather {MCP_URL} --header "X-API-Key: <key>"

Claude Desktop args, plus "env": {{ "TW_KEY": "<key>" }}:
"--header", "X-API-Key:${{TW_KEY}}"
"""

INSTALL_CSS = """
.install{margin:8px 0 22px}
.install>h2{font-family:var(--display);font-weight:500;font-size:32px;letter-spacing:.015em;line-height:1.15;margin:0 0 8px;color:var(--ink)}
.install>h2::before{content:"";display:block;width:36px;height:2px;background:var(--clay);margin:0 0 12px}
.install .try{margin:0 0 14px;color:var(--mut);font-size:14.5px}
.snipgrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;align-items:start}
.card.snip{margin:0;border-top:2px solid var(--clay);min-width:0;padding:16px 16px 18px}
.sniphead{display:flex;align-items:flex-start;gap:8px}
.sniphead h3{font-family:var(--display);font-size:26px;font-weight:500;letter-spacing:.01em;margin:0;color:var(--ink);line-height:1.15}
.where{margin:2px 0 10px;color:var(--mut);font-family:var(--mono);font-size:12px}
.note-sm{margin:8px 0 0;color:var(--mut);font-size:12.5px}
.blocklab{display:flex;align-items:baseline;gap:8px;margin:12px 0 6px}
.blocklab h4{margin:0;font:500 13.5px var(--sans);color:var(--ink)}
.blocklab span{color:var(--mut);font-family:var(--mono);font-size:12px}
.install .copy{margin-left:auto;flex:none;background:var(--card);border:1px solid var(--clay);color:var(--acc);border-radius:8px;padding:3px 10px;font:500 13px var(--sans);cursor:pointer}
.install .copy:hover{border-color:var(--ink)}
.snip pre,.quota pre{white-space:pre-wrap;overflow-wrap:anywhere;overflow:auto;margin:0}
.addcursor{display:inline-flex;margin:0 0 10px}.addcursor img{height:32px;width:auto}
.addcursor .for-dark{display:none}
:root[data-theme=dark] .addcursor .for-light{display:none}
:root[data-theme=dark] .addcursor .for-dark{display:inline}
.deeplink{margin-top:10px}
.deeplink summary{cursor:pointer;color:var(--acc);font-size:13px}
.card.quota{margin-top:14px}
.card.quota h3{margin:0;font:500 16px var(--sans);color:var(--ink)}
.quotahead{display:flex;align-items:baseline;gap:8px;margin:0 0 8px}
@media(max-width:980px){.snipgrid{grid-template-columns:1fr}.install>h2{font-size:28px}}
"""

_COPY_JS = """<script>
document.querySelectorAll("[data-copy]").forEach(function(b){
  b.addEventListener("click", function(){
    var el=document.getElementById(b.getAttribute("data-copy"));
    if(!el) return;
    var text=el.textContent.replace(/^\\n+|\\n+$/g, "");
    function done(ok){
      b.textContent=ok?"Copied":"Copy failed";
      setTimeout(function(){ b.textContent="Copy"; }, 2500);
    }
    function fallback(){
      var ta=document.createElement("textarea");
      ta.value=text; ta.setAttribute("readonly","");
      ta.style.position="fixed"; ta.style.top="0"; ta.style.left="0"; ta.style.opacity="0";
      document.body.appendChild(ta); ta.focus(); ta.select();
      var ok=false; try{ ok=document.execCommand("copy"); }catch(e){}
      ta.remove();
      done(ok);
    }
    if(navigator.clipboard&&window.isSecureContext){
      navigator.clipboard.writeText(text).then(function(){ done(true); }, fallback);
    } else fallback();
  });
});
</script>"""


def _pre(pid, text):
    return f'<pre id="{pid}">{html.escape(text, quote=False)}</pre>'


def _copy(pid, label):
    return (f'<button class="copy" type="button" data-copy="{pid}" '
            f'aria-label="{html.escape(label, quote=True)}">Copy</button>')


def install_section():
    """Home page and leaderboard block: Cursor, Claude, and Smithery/remote URL."""
    href = html.escape(CURSOR_DEEPLINK, quote=True)
    return f"""<section class="install" id="install" aria-labelledby="install-title">
<h2 id="install-title">Copy this MCP config</h2>
<p class="try">Free to try. No signup. These configs connect with no key (50 calls/day) and no Authorization header. Call <code>get_api_key</code> to raise the quota to 1,000 calls/day and 3 watches, then send that key as <code>X-API-Key</code>.</p>
<div class="snipgrid">
<article class="card snip" id="snip-cursor">
<div class="sniphead"><h3>Cursor</h3>{_copy("cfg-cursor", "Copy Cursor mcp.json")}</div>
<p class="where">~/.cursor/mcp.json</p>
<a class="addcursor" href="{href}"><img class="for-light" src="https://cursor.com/deeplink/mcp-install-dark.svg" alt="Add to Cursor" height="32"><img class="for-dark" src="https://cursor.com/deeplink/mcp-install-light.svg" alt="" height="32"></a>
{_pre("cfg-cursor", CURSOR_JSON)}
<details class="deeplink"><summary>Cursor install link</summary>
<div class="blocklab"><span>cursor:// deeplink</span>{_copy("cfg-deeplink", "Copy Cursor install link")}</div>
{_pre("cfg-deeplink", CURSOR_DEEPLINK)}
</details>
<p class="note-sm">Cursor asks before it installs. The link encodes <code>{{"url":"{MCP_URL}"}}</code>.</p>
</article>
<article class="card snip" id="snip-claude">
<div class="sniphead"><h3>Claude</h3></div>
<p class="where">Code and Desktop. No key.</p>
<div class="blocklab"><h4>Claude Code</h4><span>.mcp.json</span>{_copy("cfg-claude-code", "Copy Claude Code config")}</div>
{_pre("cfg-claude-code", CLAUDE_CODE_JSON)}
<p class="note-sm">Set <code>"type": "http"</code>. Claude Code treats a <code>url</code> without <code>type</code> as stdio and skips it.</p>
<div class="blocklab"><h4>Claude Code CLI</h4>{_copy("cfg-claude-cli", "Copy Claude Code CLI")}</div>
{_pre("cfg-claude-cli", CLAUDE_CLI)}
<div class="blocklab"><h4>Claude Desktop</h4><span>claude_desktop_config.json</span>{_copy("cfg-desktop", "Copy Claude Desktop config")}</div>
{_pre("cfg-desktop", CLAUDE_DESKTOP_JSON)}
<p class="note-sm">Remote HTTP through <code>mcp-remote</code>. No key on the free tier.</p>
</article>
<article class="card snip" id="snip-remote">
<div class="sniphead"><h3>Smithery / remote URL</h3>{_copy("cfg-remote-url", "Copy MCP endpoint URL")}</div>
<p class="where">streamable HTTP</p>
{_pre("cfg-remote-url", MCP_URL)}
<div class="blocklab"><h4>Smithery HTTP config</h4>{_copy("cfg-smithery", "Copy Smithery HTTP config")}</div>
{_pre("cfg-smithery", SMITHERY_JSON)}
<p class="note-sm">Paste the URL into Smithery or any remote HTTP client (Cline uses this same URL). <code>type</code> and <code>url</code> only. No API key and no Authorization header.</p>
</article>
</div>
<article class="card quota">
<div class="quotahead"><h3>Optional: raise the quota</h3><span class="where" style="margin:0">after get_api_key</span>{_copy("cfg-raise", "Copy quota header note")}</div>
<p class="note-sm" style="margin-top:0">Skip this on the free tier. Placeholders only.</p>
{_pre("cfg-raise", RAISE_TEXT)}
</article>
</section>
{_COPY_JS}"""
