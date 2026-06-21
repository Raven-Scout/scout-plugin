"""
SCOUT Knowledge Graph Parser

Loads the ontology schema and entity files from the knowledge base,
builds an in-memory graph, and exposes a query interface.

Usage:
    from knowledge_base.ontology.parser import KnowledgeGraph

    graph = KnowledgeGraph(schema_path="path/to/schema.yaml", kb_root="path/to/knowledge-base")
    graph.load()
    results = graph.query(type="task", domain="personal", status="open")
"""

from __future__ import annotations

import json as _json
import os
import re
from pathlib import Path
from typing import Any

import yaml


class KnowledgeGraph:
    """Markdown-native knowledge graph with YAML frontmatter entities."""

    def __init__(self, schema_path: str, kb_root: str):
        self.schema_path = schema_path
        self.kb_root = kb_root
        self.schema = self._load_schema()
        self.entities: dict[str, dict[str, Any]] = {}
        self.relationships: list[dict[str, str]] = []

    def _load_schema(self) -> dict:
        with open(self.schema_path) as f:
            return yaml.safe_load(f)

    def load(self) -> "KnowledgeGraph":
        """Walk all .md files in kb_root, extract frontmatter, build graph."""
        self.entities = {}
        self.relationships = []

        for md_file in Path(self.kb_root).rglob("*.md"):
            frontmatter = self._extract_frontmatter(md_file)
            if not frontmatter or "name" not in frontmatter or "type" not in frontmatter:
                continue

            name = frontmatter["name"]
            frontmatter["_source_path"] = str(md_file)
            raw_relationships = frontmatter.pop("relationships", [])
            self.entities[name] = frontmatter

            for rel in raw_relationships or []:
                target = self._resolve_wikilink(rel.get("target", ""))
                rel_type = rel.get("type", "")
                if target and rel_type:
                    self.relationships.append(
                        {"source": name, "type": rel_type, "target": target}
                    )
                    inverse = self._get_inverse(rel_type)
                    if inverse:
                        self.relationships.append(
                            {"source": target, "type": inverse, "target": name}
                        )

        return self

    def _extract_frontmatter(self, path: Path) -> dict | None:
        """Extract YAML frontmatter from a markdown file."""
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        if not text.startswith("---"):
            return None

        end = text.find("---", 3)
        if end == -1:
            return None

        try:
            return yaml.safe_load(text[3:end])
        except yaml.YAMLError:
            return None

    def _resolve_wikilink(self, text: str) -> str:
        """Extract entity name from '[[Name]]' syntax."""
        match = re.search(r"\[\[(.+?)]]", text)
        return match.group(1) if match else text

    def entity(self, name: str) -> dict[str, Any] | None:
        """Get an entity by name. Returns None if not found."""
        return self.entities.get(name)

    def query(self, **filters: Any) -> list[dict[str, Any]]:
        """Query entities by property filters.

        Special filters:
            deadline_before: str (ISO date) — matches entities with deadline <= value
            birthday_month: int — matches entities with birthday in that month

        All other filters match exact property values.
        """
        results = []
        for entity in self.entities.values():
            if self._matches_filters(entity, filters):
                results.append(entity)
        return results

    def related(self, name: str) -> list[dict[str, str]]:
        """Get all relationships where `name` is the source."""
        return [r for r in self.relationships if r["source"] == name]

    def export_json(self, indent: int = 2) -> str:
        """Export the full knowledge graph as JSON."""
        clean_entities = {}
        for name, entity in self.entities.items():
            clean_entities[name] = {
                k: v for k, v in entity.items() if not k.startswith("_")
            }

        return _json.dumps(
            {"entities": clean_entities, "relationships": self.relationships},
            indent=indent,
            default=str,
        )

    def validate(self) -> list[dict[str, str]]:
        """Validate all entities against the schema. Returns list of errors."""
        errors = []
        entity_types = self.schema.get("entity_types", {})
        rel_types = self.schema.get("relationship_types", {})

        for name, entity in self.entities.items():
            etype = entity.get("type", "")
            type_def = entity_types.get(etype)

            if not type_def:
                errors.append({"entity": name, "message": f"Unknown entity type: {etype}"})
                continue

            # Check required properties
            for prop in type_def["properties"].get("required", []):
                if prop not in entity:
                    errors.append({"entity": name, "message": f"Missing required property: {prop}"})

            # Check relationships from this entity
            for rel in self.relationships:
                if rel["source"] == name:
                    if rel["type"] not in rel_types:
                        errors.append(
                            {"entity": name, "message": f"Invalid relationship type: {rel['type']}"}
                        )

            # Check for orphaned entities (no relationships at all)
            has_rels = any(
                r["source"] == name or r["target"] == name for r in self.relationships
            )
            if not has_rels:
                errors.append({"entity": name, "message": "Orphaned entity — no relationships"})

        return errors

    def _matches_filters(self, entity: dict, filters: dict) -> bool:
        """Check if an entity matches all provided filters."""
        for key, value in filters.items():
            if key == "deadline_before":
                deadline = entity.get("deadline", "")
                if not deadline or str(deadline) > str(value):
                    return False
            elif key == "birthday_month":
                birthday = entity.get("birthday", "")
                if not birthday:
                    return False
                try:
                    month = int(str(birthday).split("-")[1])
                    if month != value:
                        return False
                except (IndexError, ValueError):
                    return False
            elif key == "exclude_status":
                # Negative filter — entity's status must NOT be in this set.
                # Accepts a single string or a comma-separated string.
                excluded = {s.strip() for s in str(value).split(",")} if isinstance(value, str) else set(value)
                if str(entity.get("status", "")) in excluded:
                    return False
            else:
                if entity.get(key) != value:
                    return False
        return True

    def _get_inverse(self, rel_type: str) -> str | None:
        """Look up the inverse relationship type from the schema."""
        rel_def = self.schema.get("relationship_types", {}).get(rel_type)
        if rel_def:
            return rel_def.get("inverse")
        return None

    def name_lookup(
        self,
        token: str,
        max_results: int = 5,
        distance_threshold: float = 0.7,
    ) -> list[dict[str, Any]]:
        """Resolve a transcribed name token against `type: person` entities.

        Implements [[scout-mistake-audit]] Pattern #58 fix — call before writing any
        transcript-derived name into action-items, KB, or DM. Returns ranked candidates;
        callers should treat `match_strength == "exact"` as authoritative,
        `"fuzzy"` as a propose-with-marker, and an empty list as "route to review-queue
        and never elevate to a 🔴 SCOUT-DM headline" (DREAMING.md §Phase 1, Pattern #58).

        Match strategy:
            1. Exact (case-insensitive) match against `name` or any value in `aliases`.
            2. Fuzzy match via difflib `SequenceMatcher.ratio()`. The default 0.7
               threshold ≈ Levenshtein-distance-2 on short strings (e.g. "Padak" vs
               "Pock" scores 0.444 — below threshold; "Padak" vs "padek" scores 0.8 —
               above). Lower threshold (0.5) catches more aggressive drift.
        """
        from difflib import SequenceMatcher

        token_lower = token.lower().strip()
        if not token_lower:
            return []

        candidates: list[dict[str, Any]] = []

        for entity_name, entity in self.entities.items():
            if entity.get("type") != "person":
                continue

            slug = Path(entity.get("_source_path", "")).stem
            field_pool: list[tuple[str, str]] = [("name", entity_name)]
            for alias in entity.get("aliases", []) or []:
                if alias:
                    field_pool.append(("alias", str(alias)))

            best_score = 0.0
            best_field = ""
            best_strength = ""

            for field_name, field_value in field_pool:
                value_lower = field_value.lower().strip()
                if not value_lower:
                    continue
                if value_lower == token_lower:
                    best_score = 1.0
                    best_field = field_name
                    best_strength = "exact"
                    break
                ratio = SequenceMatcher(None, value_lower, token_lower).ratio()
                if ratio > best_score:
                    best_score = ratio
                    best_field = field_name
                    best_strength = "fuzzy" if ratio >= distance_threshold else ""

            if best_strength:
                candidates.append({
                    "name": entity_name,
                    "slug": slug,
                    "match_strength": best_strength,
                    "matched_field": best_field,
                    "score": round(best_score, 3),
                })

        candidates.sort(key=lambda c: (c["match_strength"] != "exact", -c["score"]))
        return candidates[:max_results]

    # ------------------------------------------------------------------
    # Graph traversal layer — pure-stdlib BFS over the entity graph.
    # `traverse()`/`path()` carry no third-party dependency; `to_networkx()`
    # imports networkx lazily so the core commands never require it. Inverse
    # edges added by load() are already in self.relationships, so these walk
    # the full reachability graph.
    # ------------------------------------------------------------------

    def _adjacency(self) -> dict[str, list[tuple[str, str]]]:
        """Build + cache {source: [(rel_type, target), ...]} from relationships."""
        adj = getattr(self, "_adj", None)
        if adj is None:
            adj = {}
            for r in self.relationships:
                adj.setdefault(r["source"], []).append((r["type"], r["target"]))
            self._adj = adj
        return adj

    def traverse(
        self, start: str, max_hops: int = 2, rel_types: Any = None
    ) -> list[dict[str, Any]]:
        """BFS from `start` up to `max_hops`. Returns each reachable entity with
        its hop distance, the relationship it was first reached by (`via`), and
        the shortest typed path. `rel_types` optionally restricts the walk to a
        set of relationship types (e.g. {"works_with", "works_on"})."""
        if start not in self.entities:
            return []
        adj = self._adjacency()
        allow = set(rel_types) if rel_types else None
        seen = {start}
        queue: list[tuple[str, int, list]] = [(start, 0, [])]
        out: list[dict[str, Any]] = []
        while queue:
            node, hops, path = queue.pop(0)
            if hops >= max_hops:
                continue
            for rel_type, target in adj.get(node, []):
                if allow is not None and rel_type not in allow:
                    continue
                if target in seen:
                    continue
                seen.add(target)
                new_path = path + [(rel_type, target)]
                out.append(
                    {"entity": target, "hops": hops + 1, "via": rel_type, "path": new_path}
                )
                queue.append((target, hops + 1, new_path))
        return out

    def path(self, source: str, target: str, max_hops: int = 6) -> list | None:
        """Shortest typed path from `source` to `target` (BFS). Returns a list of
        (rel_type, node) steps from the first hop, [] if source == target, or
        None if no path within `max_hops`."""
        if source not in self.entities or target not in self.entities:
            return None
        if source == target:
            return []
        adj = self._adjacency()
        seen = {source}
        queue: list[tuple[str, list]] = [(source, [])]
        while queue:
            node, p = queue.pop(0)
            if len(p) >= max_hops:
                continue
            for rel_type, nxt in adj.get(node, []):
                if nxt in seen:
                    continue
                new_path = p + [(rel_type, nxt)]
                if nxt == target:
                    return new_path
                seen.add(nxt)
                queue.append((nxt, new_path))
        return None

    def to_networkx(self):
        """Optional bridge to a networkx.MultiDiGraph for richer algorithms
        (centrality, components). Imports lazily; raises a friendly RuntimeError
        if networkx is absent so the core commands never carry a hard dependency."""
        try:
            import networkx as nx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "networkx not installed — traverse()/path() work without it "
                "(pure-stdlib BFS); only to_networkx() needs `pip install networkx`."
            ) from exc
        g = nx.MultiDiGraph()
        for name, ent in self.entities.items():
            g.add_node(name, type=ent.get("type"))
        for r in self.relationships:
            g.add_edge(r["source"], r["target"], type=r["type"])
        return g


def main():
    """CLI entry point — load graph and run a command."""
    import argparse

    default_schema = os.path.join(os.path.dirname(__file__), "schema.yaml")
    default_kb = os.path.dirname(os.path.dirname(__file__))

    parser = argparse.ArgumentParser(description="SCOUT Knowledge Graph Parser")
    parser.add_argument("command", choices=["validate", "export", "query", "entity", "related", "stats", "name_lookup", "traverse", "path"])
    parser.add_argument("--name", help="Entity name (for entity/related/traverse, and the source for path)")
    parser.add_argument("--to", dest="to", help="Target entity name (for the path command)")
    parser.add_argument("--hops", type=int, help="Max hops (traverse: default 2; path: default 6)")
    parser.add_argument("--rels", help="Comma-separated relationship types to restrict a traverse (e.g. 'works_with,works_on')")
    parser.add_argument("--token", help="Name token to resolve against people/aliases (name_lookup)")
    parser.add_argument("--type", help="Entity type filter (for query command)")
    parser.add_argument("--status", help="Status filter (e.g. 'open', 'completed') — query command")
    parser.add_argument("--exclude-status", dest="exclude_status", help="Comma-separated statuses to exclude (e.g. 'completed,cancelled') — query command. Use this for 'currently-actionable' tasks since entities use mixed statuses (open / in-progress / scheduled / in_service / ...).")
    parser.add_argument("--domain", help="Domain filter (e.g. 'personal', 'work') — query command")
    parser.add_argument("--deadline-before", dest="deadline_before", help="ISO date — return tasks with deadline <= this date (query command)")
    parser.add_argument("--threshold", type=float, default=0.7, help="Fuzzy match cutoff for name_lookup (0.0–1.0; default 0.7 ≈ Levenshtein-2)")
    parser.add_argument("--schema", default=default_schema, help="Path to schema.yaml")
    parser.add_argument("--kb-root", default=default_kb, help="Path to knowledge-base root")

    args = parser.parse_args()

    graph = KnowledgeGraph(schema_path=args.schema, kb_root=args.kb_root)
    graph.load()

    if args.command == "validate":
        errors = graph.validate()
        if errors:
            for e in errors:
                print(f"  [{e['entity']}] {e['message']}")
            print(f"\n{len(errors)} validation error(s) found.")
        else:
            print("No validation errors.")

    elif args.command == "export":
        print(graph.export_json())

    elif args.command == "stats":
        print(f"Entities: {len(graph.entities)}")
        print(f"Relationships: {len(graph.relationships)}")
        by_type = {}
        for e in graph.entities.values():
            t = e.get("type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1
        for t, count in sorted(by_type.items()):
            print(f"  {t}: {count}")

    elif args.command == "entity":
        if not args.name:
            print("Error: --name required for entity command")
            return
        result = graph.entity(args.name)
        if result:
            print(_json.dumps({k: v for k, v in result.items() if not k.startswith("_")}, indent=2, default=str))
        else:
            print(f"Entity '{args.name}' not found.")

    elif args.command == "related":
        if not args.name:
            print("Error: --name required for related command")
            return
        rels = graph.related(args.name)
        if rels:
            for r in rels:
                print(f"  {r['type']} → {r['target']}")
        else:
            print(f"No relationships found for '{args.name}'.")

    elif args.command == "name_lookup":
        if not args.token:
            print("Error: --token required for name_lookup command")
            return
        results = graph.name_lookup(args.token, distance_threshold=args.threshold)
        if not results:
            print(f"No KB match for '{args.token}' (threshold={args.threshold}). Route to review-queue; do NOT elevate to 🔴.")
            return
        for r in results:
            print(f"  [{r['match_strength']:5s}] {r['name']} (slug={r['slug']}, matched_field={r['matched_field']}, score={r['score']})")
        print(f"\n{len(results)} candidate(s).")

    elif args.command == "traverse":
        if not args.name:
            print("Error: --name required for traverse command")
            return
        rel_types = [s.strip() for s in args.rels.split(",")] if args.rels else None
        results = graph.traverse(args.name, max_hops=args.hops or 2, rel_types=rel_types)
        if not results:
            print(f"No entities reachable from '{args.name}'.")
            return
        for r in results:
            print(f"  [{r['hops']} hop{'s' if r['hops'] != 1 else ''}] {r['entity']} (via {r['via']})")
        print(f"\n{len(results)} reachable entit{'ies' if len(results) != 1 else 'y'}.")

    elif args.command == "path":
        if not args.name or not args.to:
            print("Error: --name (source) and --to (target) required for path command")
            return
        steps = graph.path(args.name, args.to, max_hops=args.hops or 6)
        if steps is None:
            print(f"No path from '{args.name}' to '{args.to}'.")
        elif not steps:
            print(f"'{args.name}' and '{args.to}' are the same entity.")
        else:
            chain = args.name + "".join(f" --{t}--> {n}" for t, n in steps)
            print(f"  {chain}")
            print(f"\n{len(steps)} hop(s).")

    elif args.command == "query":
        filters = {}
        if args.type:
            filters["type"] = args.type
        if args.status:
            filters["status"] = args.status
        if args.domain:
            filters["domain"] = args.domain
        if args.deadline_before:
            filters["deadline_before"] = args.deadline_before
        if args.exclude_status:
            filters["exclude_status"] = args.exclude_status
        results = graph.query(**filters)
        for r in results:
            extras = []
            if r.get("status"):
                extras.append(f"status={r['status']}")
            if r.get("domain"):
                extras.append(f"domain={r['domain']}")
            if r.get("deadline"):
                extras.append(f"deadline={r['deadline']}")
            suffix = f"  ({', '.join(extras)})" if extras else ""
            print(f"  [{r.get('type')}] {r.get('name')}{suffix}")
        print(f"\n{len(results)} result(s).")


if __name__ == "__main__":
    main()
