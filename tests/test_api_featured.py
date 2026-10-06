"""Editor's Picks: who may change them, where they are kept, and when they exist.

Cambridge Calendar's feature endpoints had no authentication, and the picks
were written to a container filesystem that every deploy (one a day, from the
scrape's push) threw away.
"""
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import src.api.main as main
import src.config as config
from src.models.event import Event, EventCreate

TOKEN = "s3cret-token"


def event(title, days_ahead, source="The Chapel", hour=19):
    start = (main.now_local() + timedelta(days=days_ahead)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return Event.from_create(EventCreate(
        title=title, description=f"{title}.", start_datetime=start,
        source_name=source, source_url=f"https://example.org/{title.replace(' ', '-')}",
        venue_name=source, city="San Francisco")).model_dump(mode="json")


@pytest.fixture
def api(tmp_path, monkeypatch):
    """The API over a temporary data directory, with Editor's Picks on."""
    events = [event("Jazz Night", 3), event("Jazz Night", 10), event("Poetry Slam", 5, source="Bird & Beckett")]
    (tmp_path / "events.json").write_text(json.dumps(events))
    monkeypatch.setattr(main, "EVENTS_FILE", tmp_path / "events.json")
    monkeypatch.setattr(main, "REPO_FEATURED_FILE", tmp_path / "featured.json")
    monkeypatch.delenv("FEATURED_PATH", raising=False)
    monkeypatch.setitem(config.FEATURES, "editors_picks", True)
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    main.reset_cache()
    yield TestClient(main.app), events, tmp_path
    main.reset_cache()


def auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def test_choosing_picks_needs_the_token(api):
    client, events, _ = api
    url = f"/events/{events[0]['id']}/feature"
    assert client.post(url).status_code == 401
    assert client.post(url, headers=auth("wrong")).status_code == 401
    assert client.delete(url, headers={"Authorization": TOKEN}).status_code == 401, "Bearer scheme required"
    assert client.get("/featured").json() == []


def test_without_admin_token_set_nobody_can_choose(api, monkeypatch):
    client, events, _ = api
    monkeypatch.delenv("ADMIN_TOKEN")
    response = client.post(f"/events/{events[0]['id']}/feature", headers=auth(""))
    assert response.status_code == 503
    assert "ADMIN_TOKEN" in response.json()["detail"]


def test_a_pick_covers_every_date_and_can_be_removed(api):
    client, events, tmp_path = api
    assert client.get("/admin/verify", headers=auth()).json() == {"ok": True}

    response = client.post(f"/events/{events[0]['id']}/feature", headers=auth())
    assert response.json()["status"] == "featured"
    assert client.get("/featured").json() == [{"title": "Jazz Night", "source_name": "The Chapel"}]

    slim = {(e["title"], e["start_datetime"]): e["featured"] for e in client.get("/events/slim").json()}
    assert [v for (title, _), v in slim.items() if title == "Jazz Night"] == [True, True]
    assert [v for (title, _), v in slim.items() if title == "Poetry Slam"] == [False]
    assert client.get(f"/events/{events[1]['id']}").json()["featured"] is True

    assert client.post(f"/events/{events[1]['id']}/feature", headers=auth()).json()["status"] == "already_featured"
    assert client.delete(f"/events/{events[1]['id']}/feature", headers=auth()).json()["status"] == "unfeatured"
    assert client.get("/featured").json() == []
    assert json.loads((tmp_path / "featured.json").read_text()) == []


def test_featured_path_keeps_picks_off_the_deployed_filesystem(api, monkeypatch):
    """On Railway the repo checkout is replaced on every deploy; a volume is not."""
    client, events, tmp_path = api
    (tmp_path / "featured.json").write_text(json.dumps([{"title": "Poetry Slam", "source_name": "Bird & Beckett"}]))
    volume = tmp_path / "volume" / "featured.json"
    monkeypatch.setenv("FEATURED_PATH", str(volume))
    main.reset_cache()

    # Until the first save, the committed picks seed the list
    assert client.get("/featured").json() == [{"title": "Poetry Slam", "source_name": "Bird & Beckett"}]

    client.post(f"/events/{events[0]['id']}/feature", headers=auth())
    assert {p["title"] for p in json.loads(volume.read_text())} == {"Poetry Slam", "Jazz Night"}
    assert json.loads((tmp_path / "featured.json").read_text()) == [
        {"title": "Poetry Slam", "source_name": "Bird & Beckett"}], "the committed copy is left alone"


def test_saving_drops_picks_whose_events_are_gone(api):
    client, events, tmp_path = api
    (tmp_path / "featured.json").write_text(json.dumps([{"title": "Last Year's Gala", "source_name": "The Chapel"}]))
    main.reset_cache()
    client.post(f"/events/{events[2]['id']}/feature", headers=auth())
    assert client.get("/featured").json() == [{"title": "Poetry Slam", "source_name": "Bird & Beckett"}]


def test_admin_page_is_served_with_the_site_name(api):
    client, _, _ = api
    for path in ("/admin", "/admin/featured"):
        page = client.get(path)
        assert page.status_code == 200
        assert config.SITE_NAME.split()[0] in page.text and "{{SITE_NAME}}" not in page.text


def test_with_editors_picks_off_there_is_nothing_to_find(api, monkeypatch):
    client, events, tmp_path = api
    (tmp_path / "featured.json").write_text(json.dumps([{"title": "Jazz Night", "source_name": "The Chapel"}]))
    monkeypatch.setitem(config.FEATURES, "editors_picks", False)
    main.reset_cache()
    assert client.get("/admin").status_code == 404
    assert client.post(f"/events/{events[0]['id']}/feature", headers=auth()).status_code == 404
    assert client.get("/featured").json() == []
    assert not any(e["featured"] for e in client.get("/events/slim").json())


def test_read_endpoints_work_over_the_same_data(api):
    client, events, _ = api
    assert client.get("/health").json()["total_events"] == 3
    assert client.get("/events/search", params={"q": "poetry"}).json()[0]["title"] == "Poetry Slam"
    ics = client.get(f"/events/{events[2]['id']}/calendar.ics")
    assert ics.headers["content-type"].startswith("text/calendar")
    assert f"PRODID:-//{config.SITE_NAME}//Events//EN" in ics.text
    assert client.get("/stats").json()["total_events"] == 3
    assert client.get("/sources").json()["sources"] == {"The Chapel": 2, "Bird & Beckett": 1}
    assert client.get("/version").json()["timezone"] == config.TIMEZONE_NAME
