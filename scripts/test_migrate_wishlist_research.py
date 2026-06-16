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
