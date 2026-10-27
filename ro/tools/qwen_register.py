"""First-pass register labels for GAME.TXT sections, done by the local Qwen model.

Mechanical job: each dialog section gets 'ui', 'fiction' or 'mixed' plus a short
reason. Claude checks the result; Qwen's labels are a draft, not a decision.
Output: ro/work/register-qwen.json (git-ignored: it quotes game text).
"""
import json, re, sys, time, urllib.request

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
GAME = ARGS[0] if len(ARGS) > 0 else r"D:\Games\Games\MPS\COLONIZE\GAME.TXT"
OUT = ARGS[1] if len(ARGS) > 1 else "ro/work/register-qwen.json"
URL = "http://127.0.0.1:8080/v1/chat/completions"
BATCH = 10

SYSTEM = """You label dialog sections of the 1994 game Sid Meier's Colonization.
Labels:
- "ui": talks to the PLAYER about the program: menus, options, save/load, errors,
  prompts to type a name, tutorial hints about keys or mouse, confirmations like Yes/No.
- "fiction": part of the story world: speech by the King, natives, advisers, Congress,
  Founding Fathers, captains; event narration; anything that happens in 1492-1776.
- "mixed": clearly both in one section (e.g. a story message plus a keyboard hint).
Yes/No or other answer choices under a story message are the player's reply inside
the story: that section is "fiction", not "mixed".
Answer JSON only: {"SECTION": {"r": "ui|fiction|mixed", "why": "max 8 words"}, ...}
Label every section you are given, exactly once."""


def sections(path):
    text = open(path, "rb").read().decode("latin1").replace("\r", "")
    out, cur, buf = [], None, []
    for line in text.split("\n"):
        if line.startswith(";"):
            continue
        m = re.match(r"@([A-Z0-9_]+)\s*$", line)
        if m:
            if cur:
                out.append((cur, " / ".join(buf)[:400]))
            cur, buf = m.group(1), []
            continue
        if line.startswith("@") or not line.strip() or set(line.strip()) <= set("_^"):
            continue
        buf.append(line.strip().lstrip("^"))
    if cur:
        out.append((cur, " / ".join(buf)[:400]))
    return out


def ask(batch):
    user = "\n".join(f"[{name}] {txt}" for name, txt in batch)
    body = {"model": "qwen-local", "temperature": 0, "max_tokens": 4000,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}
    req = urllib.request.Request(URL, json.dumps(body).encode(), {"Content-Type": "application/json"})
    content = json.load(urllib.request.urlopen(req, timeout=600))["choices"][0]["message"]["content"]
    raw = json.loads(content.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
    # Qwen sometimes keys by the whole input line "[NAME] text..." instead of NAME.
    out = {}
    for k, v in raw.items():
        m = re.match(r"\s*\[?([A-Z0-9_]+)\]?", k)
        if m:
            out[m.group(1)] = v
    return out


secs = sections(GAME)
result, t0 = {}, time.time()
if "--redo-missing" in sys.argv:          # keep good labels, ask again only for the gaps
    result = json.load(open(OUT, encoding="utf-8"))
    secs = [s for s in secs if result.get(s[0], {}).get("r") in (None, "MISSING")]
for i in range(0, len(secs), BATCH):
    batch = secs[i:i + BATCH]
    for attempt in (1, 2):
        try:
            got = ask(batch)
            break
        except Exception as e:
            got = {}
            print(f"batch {i}: attempt {attempt} failed: {e}", flush=True)
    for name, _ in batch:
        v = got.get(name)
        result[name] = v if isinstance(v, dict) and v.get("r") in ("ui", "fiction", "mixed") else {"r": "MISSING", "why": ""}
    print(f"{min(i + BATCH, len(secs))}/{len(secs)} sections, {time.time() - t0:.0f}s", flush=True)

json.dump(result, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
counts = {}
for v in result.values():
    counts[v["r"]] = counts.get(v["r"], 0) + 1
print("done", counts, f"{time.time() - t0:.0f}s ->", OUT)
