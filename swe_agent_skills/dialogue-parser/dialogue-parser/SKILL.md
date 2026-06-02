---
name: dialogue-parser
description: Parse a branching dialogue script file into a JSON graph and DOT visualization. Use when given a script.txt with [NodeId] headers, Speaker: Text -> Target lines, and numbered choice lines.
---

# Dialogue Parser Skill

Convert `/app/script.txt` → `/app/dialogue.json` + `/app/dialogue.dot` by implementing `parse_script(text)` in `solution.py`.

## Script Format

```
[NodeId]
Speaker: Dialogue text. -> TargetNodeId

[ChoiceNodeId]
1. Choice text -> TargetA
2. [SkillTag] Choice text -> TargetB
```

## Output Schema

**`dialogue.json`**
```json
{
  "nodes": [{"id": "...", "text": "...", "speaker": "...", "type": "line|choice"}],
  "edges": [{"from": "...", "to": "...", "text": "..."}]
}
```

**Node types:**
- `"line"` — a node containing a single `Speaker: Text -> Target` line
- `"choice"` — a node that presents numbered options (lines starting with `1.`, `2.`, …)

**Edge `text`:** For choice edges, use the full choice line text (e.g., `"1. I seek the Crimson Blade."`). For line edges, use `""`.

**Special target `"End"`** is valid — do not require it to exist as a node.

## Implementation Steps

### 1. Parse blocks

Split the file on `[NodeId]` headers. Each block starts with a header and contains its body lines.

```python
import re, json

def parse_script(text: str) -> dict:
    nodes = []
    edges = []
    blocks = re.split(r'^\[([^\]]+)\]', text, flags=re.MULTILINE)
    # blocks[0] is pre-header text (ignore), then alternating: id, body, id, body, ...
    it = iter(blocks[1:])
    for node_id, body in zip(it, it):
        body = body.strip()
        lines = [l for l in body.splitlines() if l.strip()]
        ...
```

### 2. Classify node type

- If **all** content lines are numbered choices (`^\d+\.`), type = `"choice"`, speaker = `""`
- Otherwise type = `"line"` with a `Speaker: text` line

### 3. Parse line nodes

Pattern: `Speaker: text -> Target`

```python
m = re.match(r'^([^:]+):\s+(.+?)\s*->\s*(\S+)$', line)
speaker, text, target = m.group(1), m.group(2), m.group(3)
```

Add node `{"id": node_id, "text": text, "speaker": speaker, "type": "line"}`.  
Add edge `{"from": node_id, "to": target, "text": ""}`.

### 4. Parse choice nodes

Pattern per option: `^\d+\.\s+(?:\[[^\]]+\]\s+)?(.+?)\s*->\s*(\S+)$`

```python
for line in lines:
    m = re.match(r'^(\d+\.\s+(?:\[[^\]]+\]\s+)?.+?)\s*->\s*(\S+)$', line)
    choice_text, target = m.group(1).strip(), m.group(2)
    edges.append({"from": node_id, "to": target, "text": choice_text})
```

For choice nodes, set `text = ""` (the node itself has no single speech act).

### 5. Write JSON

```python
graph = {"nodes": nodes, "edges": edges}
with open("/app/dialogue.json", "w") as f:
    json.dump(graph, f, indent=2)
```

### 6. Generate DOT visualization

Use the `graphviz` Python package (already installed). Choice nodes → `shape=diamond`, line nodes → `shape=box, style=rounded`.

```python
import graphviz

def write_dot(graph: dict, path: str = "/app/dialogue.dot"):
    dot = graphviz.Digraph()
    for n in graph["nodes"]:
        if n["type"] == "choice":
            dot.node(n["id"], n["id"], shape="diamond", style="filled", fillcolor="lightblue")
        else:
            label = f'{n["speaker"]}: {n["text"][:30]}...' if len(n["text"]) > 30 else f'{n["speaker"]}: {n["text"]}'
            dot.node(n["id"], label, shape="box", style="rounded")
    for e in graph["edges"]:
        dot.edge(e["from"], e["to"], label=e["text"][:20] if e["text"] else "")
    # Save source only (no render needed)
    with open(path, "w") as f:
        f.write(dot.source)
```

### 7. Main entrypoint

```python
if __name__ == "__main__":
    with open("/app/script.txt") as f:
        text = f.read()
    graph = parse_script(text)
    with open("/app/dialogue.json", "w") as f:
        json.dump(graph, f, indent=2)
    write_dot(graph)
```

## Constraints to satisfy

1. **All nodes reachable** from the first node (`Start`). Every `[NodeId]` in the script must be reachable via edges from `Start`. Since the script itself defines the edges, a faithful parse satisfies this.
2. **Edge targets exist** — every `-> Target` must correspond to a `[Target]` block, except `-> End` which is always valid.
3. **Multiple endings** — multiple edges can target `"End"`.
4. The **first block** in the file is `Start`; the reachability BFS starts there.

## Common Pitfalls

- **Mixed blocks**: Some `[NodeId]` blocks have a single `Speaker: text -> Target` line (type=`"line"`). Others have only numbered options (type=`"choice"`). Detect by checking if any line matches `^\d+\.`.
- **`[SkillTag]` prefix on choices**: Strip or preserve — the full raw choice line including the tag should be the edge `text`.
- **Empty speaker for choice nodes**: Set `speaker: ""`, not null.
- **`End` as virtual node**: Never add `End` to the nodes list; it's a terminal sentinel.
- **Whitespace**: Strip leading/trailing whitespace from all fields.
