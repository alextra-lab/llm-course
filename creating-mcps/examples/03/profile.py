"""
Unit 3: profile an index pattern -- which of its mapped fields actually hold data?

A mapping lists every field an index *could* have. This asks OpenSearch, as a persona, how
many documents really have each one, and the most common values of the keyword fields. It's
the first draft of a catalog: a person then decides what each populated field means.

    python examples/03/profile.py logs-web-*                    # as l1_analyst
    python examples/03/profile.py logs-web-* --persona l3_responder --top 5
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # creating-mcps/examples/
from common_mcp import load_env, os_client  # noqa: E402


def mapped_fields(props: dict, prefix: str = "") -> dict[str, str]:
    """{'a.b.c': 'keyword', ...} from a mapping's nested `properties`."""
    out = {}
    for key, spec in props.items():
        if "properties" in spec:
            out.update(mapped_fields(spec["properties"], f"{prefix}{key}."))
        else:
            out[prefix + key] = spec.get("type", "object")
    return out


def profile(client, pattern: str, top: int = 3) -> dict:
    """Mapped fields, how many documents hold each, and top values for keyword fields."""
    mapping = client.indices.get_mapping(index=pattern)
    fields = mapped_fields(next(iter(mapping.values()))["mappings"]["properties"])
    total = client.count(index=pattern)["count"]
    names = sorted(fields)
    body = []
    for name in names:              # one msearch: a count per field, and its top values
        aggs = ({"top": {"terms": {"field": name, "size": top}}}
                if fields[name] in ("keyword", "ip", "short", "integer") else {})
        body += [{"index": pattern},
                 {"size": 0, "track_total_hits": True, "query": {"exists": {"field": name}},
                  "aggs": aggs}]
    responses = client.msearch(body=body)["responses"]
    rows = []
    for name, r in zip(names, responses):
        docs = r["hits"]["total"]["value"]
        values = [b["key"] for b in r.get("aggregations", {}).get("top", {}).get("buckets", [])]
        rows.append({"field": name, "type": fields[name], "docs": docs,
                     "share": round(docs / total, 4) if total else 0.0, "top": values})
    return {"pattern": pattern, "indices": len(mapping), "documents": total,
            "mapped": len(fields), "populated": sum(1 for r in rows if r["docs"]),
            "mapping_bytes": len(json.dumps(mapping)), "fields": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="Which mapped fields hold data?")
    parser.add_argument("pattern")
    parser.add_argument("--persona", default="l1_analyst")
    parser.add_argument("--top", type=int, default=3)
    args = parser.parse_args()
    load_env()
    p = profile(os_client(args.persona), args.pattern, args.top)
    print(f"{p['pattern']} as {args.persona}: {p['indices']} indices, {p['documents']} docs, "
          f"{p['populated']} of {p['mapped']} mapped fields populated; "
          f"raw _mapping is {p['mapping_bytes']:,} bytes")
    for r in p["fields"]:
        if r["docs"]:
            print(f"  {r['field']:<32} {r['type']:<8} {r['share']:>7.1%}  {r['top']}")


if __name__ == "__main__":
    main()
