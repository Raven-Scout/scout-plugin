from migrate_wishlist_research import parse_wishlist_item, parse_research_item

def test_parses_in_progress_high_with_date_and_source():
    bullet = ("**[in progress] HIGH — Upgrade the graph system Scout relies on** "
              "(2026-06-12 — Jordan Slack DM `123`). Evaluate TinkerPop + Gremlin.")
    item = parse_wishlist_item(bullet)
    assert item.status == "in-progress"
    assert item.priority == "high"
    assert item.title == "Upgrade the graph system Scout relies on"
    assert item.date == "2026-06-12"
    assert item.source == "Jordan Slack DM `123`"
    assert "Evaluate TinkerPop" in item.body

def test_defaults_open_medium_when_unmarked():
    item = parse_wishlist_item("**Some idea with no markers** and a description.")
    assert item.status == "open"
    assert item.priority == "medium"
    assert item.title == "Some idea with no markers"
    assert item.date is None

def test_done_marker_maps_to_done():
    item = parse_wishlist_item("**[done] MEDIUM — Shipped thing** delivered notes.", in_done_file=False)
    assert item.status == "done"
    assert item.priority == "medium"

def test_research_urgent_checked_done():
    line = "- [x] 🔴 **START IMMEDIATELY — Upgrade the graph system** evaluate TinkerPop."
    item = parse_research_item(line, area="graph")
    assert item.status == "done"
    assert item.priority == "urgent"
    assert item.title == "Upgrade the graph system"
    assert item.area == "graph"
    assert "evaluate TinkerPop" in item.body

def test_research_open_yellow_default():
    line = "- [ ] 🟡 **Locate the engg-general message** reconcile the date."
    item = parse_research_item(line)
    assert item.status == "open"
    assert item.priority == "medium"
    assert item.title == "Locate the engg-general message"

def test_research_green_low():
    line = "- [ ] 🟢 **G6 · CEE conference entities** create event nodes."
    item = parse_research_item(line)
    assert item.priority == "low"
    assert item.title == "G6 · CEE conference entities"

def test_research_start_immediately_keyword_without_emoji_is_urgent():
    item = parse_research_item("- [ ] **START IMMEDIATELY — Some title** body")
    assert item.priority == "urgent"
    assert item.title == "Some title"

from migrate_wishlist_research import slugify, render_item, filename_for, Item

def test_slugify_basic():
    assert slugify("Upgrade the graph system Scout relies on!") == "upgrade-the-graph-system-scout-relies-on"
    assert slugify("G6 · CEE conference entities") == "g6-cee-conference-entities"

def test_filename_uses_date_then_slug():
    item = Item(title="Tighten the budget gate", status="open", priority="high",
                date="2026-06-10", source=None, body="b")
    assert filename_for(item) == "2026-06-10-tighten-the-budget-gate.md"

def test_filename_falls_back_to_default_date_when_none():
    item = Item(title="No date here", status="open", priority="medium",
                date=None, source=None, body="b")
    assert filename_for(item, default_date="2026-06-16") == "2026-06-16-no-date-here.md"

def test_render_item_emits_frontmatter_and_body():
    item = Item(title="Tighten the budget gate", status="open", priority="high",
                date="2026-06-10", source="Jordan DM", body="The gate overruns.")
    out = render_item(item)
    assert out.startswith("---\n")
    assert 'title: "Tighten the budget gate"' in out
    assert "status: open" in out
    assert "priority: high" in out
    assert "date: 2026-06-10" in out
    assert 'source: "Jordan DM"' in out
    assert out.rstrip().endswith("The gate overruns.")
    assert "\n# Tighten the budget gate\n" in out

def test_render_omits_absent_optional_fields():
    item = Item(title="t", status="open", priority="low", date=None, source=None, body="b")
    out = render_item(item)
    assert "source:" not in out
    assert "area:" not in out
    assert "date:" not in out

def test_render_item_quotes_titles_with_colons_for_valid_yaml():
    item = Item(title="Build a config: key/value store", status="open",
                priority="medium", date=None, source=None, body="b")
    out = render_item(item)
    assert 'title: "Build a config: key/value store"' in out

from migrate_wishlist_research import migrate_wishlist_file, split_bullets

def test_split_bullets_separates_top_level_items():
    text = "intro\n\n* **A** body a\n* **[done] B** body b\n\n## Section\n* **C** c"
    bullets = split_bullets(text)
    assert len(bullets) == 3
    assert bullets[0].startswith("**A**")

def test_migrate_wishlist_file_writes_one_file_per_bullet(tmp_path):
    src = tmp_path / "Wishlist.md"
    src.write_text("# Wishlist\n\n* **HIGH — Alpha thing** (2026-06-10 — DM) do alpha.\n"
                   "* **[in progress] MEDIUM — Beta thing** do beta.\n")
    out_dir = tmp_path / "wishlist"
    n = migrate_wishlist_file(src, out_dir, in_done_file=False, default_date="2026-06-16")
    assert n == 2
    files = sorted(p.name for p in out_dir.glob("*.md"))
    assert files == ["2026-06-10-alpha-thing.md", "2026-06-16-beta-thing.md"]
    alpha = (out_dir / "2026-06-10-alpha-thing.md").read_text()
    assert "priority: high" in alpha and "status: open" in alpha
    beta = (out_dir / "2026-06-16-beta-thing.md").read_text()
    assert "status: in-progress" in beta

from migrate_wishlist_research import split_research_items

def test_split_research_captures_all_h2_sections_with_clean_area():
    text = ("## Queue\n- [ ] 🟡 **Q item** body\n\n"
            "## 🟡 Standing lane — Productionize + release Scout\n- [ ] 🟢 **Standing item** body\n\n"
            "### 🔵 KG gap analysis — ✅ done 2026-06-02\n- [x] **G1 thing** done\n")
    rows = list(split_research_items(text))
    assert len(rows) == 3
    assert rows[0] == ("- [ ] 🟡 **Q item** body", None)               # generic Queue → no area
    assert rows[1][1] == "standing-lane-productionize-release-scout"   # H2 area, suffix dropped
    assert rows[2][1] == "kg-gap-analysis"                             # H3 area, ✅-done suffix dropped
