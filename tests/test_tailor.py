from app import humanize
from app.profile import load_profile
from app.tailor import score, validate

P = load_profile()


def test_profile_ids_and_unconfirmed():
    assert "4,200 responses" in P.bullet("exp0.b0")
    assert "exp0.b0" not in P.entries["exp0"].unconfirmed     # approved bullet
    assert "exp0.b3" in P.entries["exp0"].unconfirmed         # marked confirm: true
    assert "[exp0]" in P.as_prompt()


SPARK = next(e.id for e in P.entries.values() if e.heading.startswith("Apache Spark"))


def test_validate_drops_untraceable_content():
    draft = {
        "keywords": ["SQL", "Tableau"], "summary": "x", "cover_letter": "x", "needs": [],
        "skills": [{"category": "Data", "items": ["Python", "SQL", "Tableau"]}],   # Tableau not in profile
        "sections": [
            {"entry_id": SPARK, "bullets": [
                {"text": "Processed weather data with Spark SQL.", "source": [SPARK + ".b0"]},
                {"text": "Cut costs by 40%.", "source": []},                    # no source -> dropped
                {"text": "Borrowed bullet.", "source": ["exp1.b0"]},            # wrong entry -> dropped
            ]},
            {"entry_id": "proj99", "bullets": [{"text": "fake", "source": ["proj99.b0"]}]},
        ],
    }
    d = validate(draft, P)
    assert d["skills"] == [{"category": "Data", "items": ["Python", "SQL"]}]
    proj = next(s for s in d["sections"] if s["entry_id"] == SPARK)
    assert [b["text"] for b in proj["bullets"]] == ["Processed weather data with Spark SQL."]
    assert not any(s["entry_id"] == "proj99" for s in d["sections"])
    # experiences the model left out are put back
    assert {"exp0", "exp1"} <= {s["entry_id"] for s in d["sections"]}
    assert any("Tableau" in w for w in d["warnings"])


def test_ats_coverage_and_fixable():
    d = validate({"keywords": ["SQL", "Spark SQL", "R"], "summary": "Data student using SQL.",
                  "cover_letter": "", "needs": [],
                  "skills": [{"category": "Data", "items": ["SQL"]}],
                  "sections": []}, P)
    s = score(d, P)["ats"]
    assert "SQL" in s["matched"]
    assert "Spark SQL" in s["fixable"]   # in your profile but not on this resume


def test_humanize_check_flags_tells():
    robotic = ("I am writing to express my strong interest in the role. Moreover, I am passionate "
               "about data. Furthermore, I leverage Python to deliver robust, scalable, and reliable "
               "solutions — every time. It's not just about code, it's about impact.")
    r = humanize.check(robotic, "letter")
    text = " ".join(r["issues"])
    assert "dash" in text and "leverage" in text and "transition" in text and "Contrast" in text
    assert r["score"] < 60

    human = ("Dear Hiring Manager,\n\nI'd like to join Stripe's data team this winter. This fall I "
             "cleaned three years of survey data for a local nonprofit and found the "
             "programs that actually kept people coming back. Short ones won. And I'm curious whether the same pattern "
             "shows up in merchant data, which is the kind of question your posting says this team works "
             "on every day.\n\nBest,\nAlex Taylor")
    assert humanize.check(human, "letter")["score"] >= 80


def test_rules_load_from_skill():
    assert "NEEDS" in humanize.rules_text()
