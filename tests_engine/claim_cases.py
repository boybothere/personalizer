"""Labelled synthetic claim cases (phase 6). Fictional people, organisations and .example domains.

Every sentence carries the label the rules are expected to produce:
  pref+ / pref-   stated preference (positive / negative or no longer)
  activity        something the subject did
  association     a relationship with an organisation or group
  rejected        recognised but rejected (question, hypothetical, sarcasm, quoting others, double negative, repost)
  insufficient    recognised preference with an unclear object (unverified inference)
  out_of_scope    sensitive trait, whereabouts, residence, other people: never stored
  no_claim        everything else: mentions, identity sentences, other people, and sentences on pages that fail the
                  identity gate

These labels were written by the same author as the rules. Agreement with them is NOT real-world accuracy.
"""

from __future__ import annotations

from datetime import date

TODAY = date(2026, 10, 18)
SUBJECT = dict(name="Priya Raman", organization="Nimbus Compute", organization_domain="nimbuscompute.example",
               role="VP of Infrastructure", location="Bengaluru", known_urls=[])
P, NEG, A, S, N, R, I, O = "pref+", "pref-", "activity", "association", "no_claim", "rejected", "insufficient", \
    "out_of_scope"

# identity sentences that make a news page "strong" (organisation + role terms); worded differently so that
# independent pages are not mistaken for copies
ID = [
    "Priya Raman, VP of Infrastructure at Nimbus Compute, said capacity planning has changed.",
    "Nimbus Compute infrastructure chief Priya Raman described the new GPU region.",
    "At Nimbus Compute, Priya Raman runs infrastructure for the GPU cloud.",
    "Priya Raman leads infrastructure at Nimbus Compute and manages the data centre roadmap.",
    "Priya Raman heads infrastructure engineering at Nimbus Compute these days.",
    "Speaking for Nimbus Compute, infrastructure head Priya Raman outlined the plan.",
]
ANCHOR_TEXT = "Priya Raman is VP of Infrastructure at Nimbus Compute, where she leads the GPU cloud team."


def D(url, sents, published=None, author=None, title=None, canonical=None, kind="fetched_page",
      date_conflict=False, meta=None, known=False):
    if known:
        author = author or "Priya Raman"
        title = title or "Notes by Priya Raman"
    return dict(url=url, sents=sents, published=published, author=author, title=title, canonical=canonical,
                kind=kind, date_conflict=date_conflict, meta=meta or {}, known=known)


def anchor(published="2026-01-10"):
    return D("https://nimbuscompute.example/team/priya", [(ANCHOR_TEXT, N)], published)


def blog(slug, sents, published="2026-06-01", **kw):
    return D(f"https://priyaraman.example/notes/{slug}", sents, published, known=True, **kw)


def news(site, slug, t, sents, published="2026-05-01", **kw):
    return D(f"https://{site}.example/{slug}", [(ID[t], N)] + sents, published, **kw)


def C(id, group, docs, expect=(), subject=None, identity=None, no_claims=False, withheld=None, origins=None):
    return dict(id=id, group=group, docs=docs, expect=list(expect), subject=subject or SUBJECT, identity=identity,
                no_claims=no_claims, withheld=withheld, origins=origins)


def X(type, key, status, pol=None, conf=None, flags=(), no_flags=(), mode=None, groups=None):
    return dict(type=type, key=key, status=status, pol=pol, conf=conf, flags=list(flags), no_flags=list(no_flags),
                mode=mode, groups=groups)


NO_DOMAIN = dict(SUBJECT, organization_domain=None)

CASES = [
    # ------------------------------------------------------------ G1 identity gate
    C("g1_unresolved_possible", "G1", [
        D("https://blog.example/x", [("Priya Raman of Nimbus Compute talks about GPU planning.", N),
                                     ("Priya Raman loves sailing.", N)], "2026-05-01")],
      subject=NO_DOMAIN, identity="possible", no_claims=True, withheld="identity_unresolved"),
    C("g1_conflicting", "G1", [
        D("https://press.example/x", [("Priya Raman, VP at Nimbus Compute and CTO at Orbit Labs, said both teams "
                                       "share tooling.", N), ("She loves sailing.", N)], "2026-05-01")],
      subject=NO_DOMAIN, identity="conflicting", no_claims=True, withheld="identity_unresolved"),
    C("g1_insufficient_namesakes_only", "G1", [
        D("https://chefstories.example/c", [("Priya Raman, the pastry chef at Saffron Bakehouse, loves sourdough.", N),
                                            ("She is a member of the Bakers Guild.", N)], "2026-05-01")],
      subject=NO_DOMAIN, identity="insufficient_evidence", no_claims=True, withheld="identity_unresolved"),
    C("g1_namesake_page_not_processed", "G1", [
        anchor(),
        D("https://chefstories.example/c", [("Priya Raman, the pastry chef at Saffron Bakehouse, loves sourdough.", N),
                                            ("She ran the Chennai Marathon in 2024.", N)], "2026-05-01")],
      identity="confirmed", no_claims=True),
    C("g1_other_person_same_page", "G1", [
        anchor(),
        news("techpress", "panel", 0, [("Arjun Mehta loves cricket.", N),
                                       ("Mehta also ran the Mumbai Marathon in 2024.", N),
                                       ("The panel was moderated by Arjun Mehta.", N)])],
      identity="confirmed", no_claims=True),
    C("g1_historical_page", "G1", [
        anchor(),
        D("https://profile.example/x", [("Priya Raman, formerly VP at Nimbus Compute, ran the Pune Half Marathon "
                                         "in 2023.", A)], "2024-01-05")],
      [X("activity", "pune half marathon", "tentative", conf="low", flags=["historical_identity", "single_origin"])],
      identity="confirmed"),
    C("g1_two_strong_pages", "G1", [
        news("techpress", "a", 0, [("She is a mentor at Women Who Code.", S)], "2026-05-01"),
        news("summit", "b", 1, [("Raman is also a mentor at Women Who Code, she said.", S)], "2026-06-01")],
      [X("association", "women who code", "strongly_supported", conf="high", groups=2)],
      subject=NO_DOMAIN, identity="strongly_supported"),
    C("g1_possible_page_with_resolved_subject", "G1", [
        anchor(),
        D("https://blog.example/y", [("Priya Raman of Nimbus Compute talks about GPU planning.", N),
                                     ("Priya Raman loves sailing.", N)], "2026-05-01")],
      identity="confirmed", no_claims=True),

    # ------------------------------------------------------------ G2 copies and syndication
    C("g2_agency_story_three_sites", "G2", [
        anchor(),
        news("dailyone", "s", 0, [("She is a member of the Open Compute Project.", S),
                                  ("This story was filed by the wire (PTI).", N)]),
        news("dailytwo", "s", 1, [("Raman has been a member of the Open Compute Project for years.", S),
                                  ("Reporting by wire staff (PTI).", N)]),
        news("dailythree", "s", 2, [("Raman is a member of the Open Compute Project too.", S),
                                    ("Copy supplied (PTI).", N)])],
      [X("association", "open compute project", "tentative", groups=1, flags=["single_origin"])],
      origins={"links": ["news_agency"]}),
    C("g2_shared_quote_rewrites", "G2", [
        anchor(),
        news("outletone", "q", 0, [('"I love trail running more than anything else," said Raman.', P)]),
        news("outlettwo", "q", 3, [('Raman put it simply: "I love trail running more than anything else."', N),
                                   ("Priya Raman added that the team is hiring.", N)])],
      [X("stated_preference", "trail running more than anything else", "tentative", pol=1, groups=1,
         mode="quoted")],
      origins={"links": ["shared_quote"]}),
    C("g2_cross_domain_canonical", "G2", [
        anchor(),
        news("original", "story", 0, [("She is a fellow of the Infrastructure Society.", S)]),
        news("mirror", "story", 4, [("Raman is a fellow of the Infrastructure Society as well.", S)],
             canonical="https://original.example/story")],
      [X("association", "infrastructure society", "tentative", groups=1)], origins={"links": ["canonical_or_based_on"]}),
    C("g2_is_based_on", "G2", [
        anchor(),
        news("source", "piece", 0, [("She is an advisor to the GPU Commons.", N)]),
        news("rewrite", "piece", 1, [("Raman serves as an advisor at Open Grid Labs.", S)],
             meta={"is_based_on": ["https://source.example/piece"]})],
      [X("association", "open grid labs", "tentative", groups=1)], origins={"links": ["canonical_or_based_on"]}),
    C("g2_press_release_wire", "G2", [
        anchor(),
        news("businesswire", "release", 0, [("Raman is a member of the Cloud Native Council.", S)]),
        news("techdaily", "release", 1, [("She is a member of the Cloud Native Council, per PRNewswire.", S)]),
        news("bizdaily", "release", 2, [("Raman serves as a member of the Cloud Native Council (Business Wire).", S)])],
      [X("association", "cloud native council", "tentative", groups=1)], origins={"links": ["press_release_wire"]}),
    C("g2_independent_interviews", "G2", [
        anchor(),
        news("podcastone", "ep", 0, [('"I love trail running," Raman said.', P)]),
        news("magazinetwo", "ep", 1, [('In the interview she said: "I really love trail running."', P)],
             "2026-07-01")],
      [X("stated_preference", "trail running", "strongly_supported", pol=1, conf="high", groups=2, mode="quoted")]),
    C("g2_same_site_two_pages", "G2", [
        anchor(),
        news("techpress", "one", 0, [("She is a member of the Rust Foundation.", S)]),
        news("techpress", "two", 1, [("Raman joined as a member of the Rust Foundation last spring.", S)])],
      [X("association", "rust foundation", "tentative", groups=1)], origins={"links": ["same_site"]}),
    C("g2_same_byline", "G2", [
        anchor(),
        news("siteone", "x", 0, [("She is a trustee of the Data Commons Trust.", S)], author="Kavya Iyer"),
        news("sitetwo", "y", 1, [("Raman serves as a trustee of the Data Commons Trust.", S)], author="Kavya Iyer")],
      [X("association", "data commons trust", "tentative", groups=1)], origins={"links": ["same_byline"]}),
    C("g2_originally_published", "G2", [
        anchor(),
        news("firstsite", "x", 0, [("She is a patron of the Indie Hardware Fund.", S)]),
        news("secondsite", "y", 1, [("Raman is a patron of the Indie Hardware Fund.", S),
                                    ("This article originally appeared on Firstsite.", N)])],
      [X("association", "indie hardware fund", "tentative", groups=1)], origins={"links": ["originally_published"]}),

    # ------------------------------------------------------------ G3 organisation substrings
    C("g3_bank_superstring", "G3", [
        anchor(),
        D("https://finance.example/x", [("Priya Raman, analyst at Nimbus Compute Bank, loves jazz.", N)], "2026-05-01")],
      no_claims=True),
    C("g3_bare_prefix", "G3", [
        anchor(),
        D("https://drones.example/x", [("Priya Raman of Nimbus, the drone maker, loves jazz.", N)], "2026-05-01")],
      no_claims=True),
    C("g3_different_name", "G3", [
        anchor(),
        D("https://hardware.example/x", [("Priya Raman of Nimbus Computers India loves jazz.", O)], "2026-05-01")],
      no_claims=True),
    C("g3_descriptor_extension_unclear", "G3", [
        anchor(),
        D("https://labs.example/x", [("Priya Raman of Nimbus Compute Labs Ltd loves jazz.", N)], "2026-05-01")],
      no_claims=True),
    C("g3_domain_alias_counts", "G3", [
        anchor(),
        D("https://alias.example/x", [("Priya Raman, VP of Infrastructure at NimbusCompute, loves jazz.", P)],
          "2026-05-01")],
      [X("stated_preference", "jazz", "tentative", pol=1, mode="reported", flags=["reported_not_authored"])]),
    C("g3_lowercase_not_matched", "G3", [
        anchor(),
        D("https://lower.example/x", [("Priya Raman of nimbus compute loves jazz.", N)], "2026-05-01")],
      no_claims=True),
    C("g3_user_alias", "G3", [
        anchor(),
        D("https://oldname.example/x", [("Priya Raman, VP of Infrastructure at Stratus GPU, loves jazz.", P)],
          "2026-05-01")],
      [X("stated_preference", "jazz", "tentative", pol=1)],
      subject=dict(SUBJECT, organization_aliases=["Stratus GPU"])),

    # ------------------------------------------------------------ G4 preference vs activity vs association vs mention
    C("g4_explicit_positive", "G4", [blog("p1", [("I love trail running.", P), ("I really enjoy sourdough baking.", P)])],
      [X("stated_preference", "trail running", "verified", pol=1, conf="high", mode="authored"),
       X("stated_preference", "sourdough baking", "verified", pol=1)]),
    C("g4_explicit_negative", "G4", [blog("p2", [("I can't stand golf.", NEG), ("I don't enjoy karaoke nights.", NEG),
                                                 ("I'm not a fan of open-plan offices.", NEG)])],
      [X("stated_preference", "golf", "verified", pol=-1), X("stated_preference", "karaoke nights", "verified", pol=-1),
       X("stated_preference", "open-plan offices", "verified", pol=-1)]),
    C("g4_fan_and_favourite", "G4", [blog("p3", [("I'm a huge fan of Formula 1.", P),
                                                 ("My favourite editor is Neovim.", P)])],
      [X("stated_preference", "formula 1", "verified", pol=1), X("stated_preference", "neovim", "verified", pol=1)]),
    C("g4_hypothetical", "G4", [blog("p4", [("I'd love to try surfing someday.", R),
                                            ("I would like a quieter office.", R),
                                            ("If I had time, I would enjoy pottery.", R)])],
      [X("stated_preference", "try surfing someday", "rejected")]),
    C("g4_question", "G4", [blog("p5", [("Do I like karaoke?", R), ("Why do I enjoy debugging so much?", R)])]),
    C("g4_sarcasm_markers", "G4", [blog("p6", [("Oh great, I just love Monday standups 🙄.", R),
                                               ("I love fixing printers /s.", R),
                                               ('I "love" four-hour meetings.', R)])]),
    C("g4_quoting_others", "G4", [blog("p7", [("As my mentor likes to say, I love chaos.", R),
                                              ('My manager once wrote: "I love spreadsheets."', R)])]),
    C("g4_double_negative", "G4", [blog("p8", [("I don't hate meetings.", R), ("Not that I dislike travel days.", R)])]),
    C("g4_vague_object", "G4", [blog("p9", [("I love it when a plan comes together.", I), ("I enjoy this.", I)])],
      [X("stated_preference", "it", "insufficient_evidence"), X("stated_preference", "this", "insufficient_evidence")]),
    C("g4_repost", "G4", [blog("p10", [("RT @someone: I love open source.", R)])]),
    C("g4_activity_not_preference", "G4", [
        anchor(),
        news("jazzweekly", "night", 0, [("Priya Raman attended a jazz evening in 2025.", A),
                                        ("The event, featuring Priya Raman, celebrated jazz.", N)], "2026-03-01")],
      [X("activity", "jazz evening", "tentative", conf="low")]),
    C("g4_mention_only", "G4", [
        anchor(),
        news("listings", "x", 1, [("Guests included Priya Raman and several founders.", N),
                                  ("The keynote covered golf course design.", N)])], no_claims=True),
    C("g4_runs_team_not_running", "G4", [
        anchor(),
        news("teamnews", "x", 3, [("Priya Raman runs the platform team with a light touch.", N)])], no_claims=True),
    C("g4_association_kinds", "G4", [
        anchor(),
        news("civic", "x", 2, [("Raman is a member of the Rotary Club of Whitefield.", O),
                               ("Priya Raman sits on the board of the Open Source Initiative.", S),
                               ("She volunteers with Code for Good.", S),
                               ("Raman was a trustee of Hackerspace Collective.", S)])],
      [X("association", "open source initiative", "tentative"), X("association", "code for good", "tentative"),
       X("association", "hackerspace collective", "tentative", flags=["past_tense"])]),
    C("g4_activities_kinds", "G4", [
        anchor(),
        news("devnews", "x", 4, [("Priya Raman wrote about GPU scheduling in 2025.", A),
                                 ("She spoke at KubeCon in 2025.", A),
                                 ("Raman organised a hardware hackathon in 2024.", A),
                                 ("She plays chess on weekends.", A)])],
      [X("activity", "gpu scheduling", "tentative"), X("activity", "kubecon", "tentative"),
       X("activity", "hardware hackathon", "tentative"), X("activity", "chess", "tentative")]),
    C("g4_first_person_activities", "G4", [blog("a1", [("I play chess on weekends.", A),
                                                       ("I am a member of the Python Software Foundation.", S),
                                                       ("I volunteer with Code for Good.", S),
                                                       ("I wrote about memory allocators in 2026.", A)])],
      [X("activity", "chess", "verified", mode="authored"),
       X("association", "python software foundation", "verified")]),
    C("g4_third_party_reported", "G4", [
        anchor(),
        news("profiles", "x", 0, [("Priya Raman loves jazz, according to colleagues.", P),
                                  ("Her colleague Arjun Mehta says she loves birdwatching.", P)])],
      [X("stated_preference", "jazz", "tentative", pol=1, mode="reported", flags=["reported_not_authored"]),
       X("stated_preference", "birdwatching", "tentative", pol=1, flags=["reported_not_authored"])]),
    C("g4_third_party_reported_twice_still_tentative", "G4", [
        anchor(),
        news("profilesone", "x", 0, [("Priya Raman loves jazz, according to colleagues.", P)]),
        news("profilestwo", "y", 1, [("Friends say Raman, a keen listener, loves jazz.", P)]),
        news("profilesthree", "z", 2, [("Raman loves jazz and owns a record collection.", P)])],
      [X("stated_preference", "jazz", "tentative", pol=1, conf="low", flags=["reported_not_authored"])]),
    C("g4_reported_hedged_modal", "G4", [
        anchor(),
        news("gossip", "x", 5, [("Priya Raman might enjoy sailing.", N), ("She would love to try surfing.", N)])],
      no_claims=True),
    C("g4_pattern_positive", "G4", [
        anchor(),
        news("runnews", "a", 0, [("Priya Raman ran the Pune Half Marathon in 2023.", A)], "2023-12-01"),
        news("sportsdaily", "b", 1, [("She ran the Mumbai Marathon in 2024.", A)], "2024-12-01"),
        news("citypaper", "c", 2, [("Raman completed the Hyderabad 10K race in 2025.", A)], "2025-12-01")],
      [X("activity_pattern", "recurring:ran", "strongly_supported", groups=3)]),
    C("g4_pattern_same_event_three_reports", "G4", [
        anchor(),
        news("runnews", "a", 0, [("Priya Raman ran the Pune Half Marathon in 2025.", A)], "2025-12-01"),
        news("sportsdaily", "b", 1, [("She finished the Pune Half Marathon in 2025.", A)], "2025-12-02"),
        news("citypaper", "c", 2, [("Raman also ran the Pune Half Marathon in 2025, it emerged.", A)], "2025-12-03")]),
    C("g4_pattern_one_year_only", "G4", [
        anchor(),
        news("runnews", "a", 0, [("Priya Raman ran the Pune Half Marathon in March 2025.", A)], "2025-03-10"),
        news("sportsdaily", "b", 1, [("She ran the Mumbai Marathon in June 2025.", A)], "2025-06-10"),
        news("citypaper", "c", 2, [("Raman completed the Hyderabad 10K race in September 2025.", A)], "2025-09-10")]),
    C("g4_pattern_undated", "G4", [
        anchor(),
        news("runnews", "a", 0, [("Priya Raman ran the Pune Half Marathon in 2023.", A)], "2023-12-01"),
        news("sportsdaily", "b", 1, [("She completed a trail race.", A)], None),
        news("citypaper", "c", 2, [("Raman finished an ultramarathon.", A)], None)]),
    C("g4_pattern_same_origin", "G4", [
        anchor(),
        news("runnews", "a", 0, [("Priya Raman ran the Pune Half Marathon in 2023.", A)], "2023-12-01"),
        news("runnews", "b", 1, [("She ran the Mumbai Marathon in 2024.", A)], "2024-12-01"),
        news("citypaper", "c", 2, [("Raman completed the Hyderabad 10K race in 2025.", A)], "2025-12-01")]),

    # ------------------------------------------------------------ G6 evidence quality
    C("g6_ordered_contradiction", "G6", [
        blog("coffee-1", [("I love coffee.", P)], "2025-01-15"),
        blog("coffee-2", [("I gave up coffee.", NEG)], "2026-02-01")],
      [X("stated_preference", "coffee", "tentative", pol=1, flags=["superseded_by_newer"]),
       X("stated_preference", "coffee", "verified", pol=-1, conf="high")]),
    C("g6_unordered_contradiction", "G6", [
        anchor(),
        blog("tea", [("I love tea.", P)], None),
        news("snacks", "x", 0, [("Priya Raman dislikes tea.", NEG)], None)],
      [X("stated_preference", "tea", "conflicting", pol=1, conf="none", flags=["contradicted"]),
       X("stated_preference", "tea", "conflicting", pol=-1, flags=["contradicted"])]),
    C("g6_stale", "G6", [blog("old", [("I love sailing.", P)], "2019-04-01")],
      [X("stated_preference", "sailing", "tentative", pol=1, conf="low", flags=["stale"])]),
    C("g6_undated", "G6", [blog("nodate", [("I love sailing.", P)], None)],
      [X("stated_preference", "sailing", "tentative", pol=1, flags=["date_missing"])]),
    C("g6_event_date_in_sentence", "G6", [blog("dated", [("I spoke at RustConf in 2025.", A)], None)],
      [X("activity", "rustconf", "verified", no_flags=["date_missing"])]),
    C("g6_date_conflict", "G6", [blog("conflict", [("I love sailing.", P)], "2026-03-01", date_conflict=True)],
      [X("stated_preference", "sailing", "tentative", pol=1, flags=["date_conflict"])]),
    C("g6_provider_text", "G6", [blog("provider", [("I love sailing.", P)], "2026-03-01", kind="provider_extract")],
      [X("stated_preference", "sailing", "tentative", pol=1, flags=["provider_text"])]),
    C("g6_insufficient", "G6", [blog("vague", [("I love this.", I)], "2026-03-01")],
      [X("stated_preference", "this", "insufficient_evidence", conf="none")]),
    C("g6_corroborated_preference", "G6", [
        blog("pottery", [("I love pottery classes.", P)], "2026-04-01"),
        news("craftmag", "x", 0, [('"I love pottery classes," Raman told us.', P)], "2026-05-01")],
      [X("stated_preference", "pottery classes", "verified", pol=1, groups=2)]),

    # ------------------------------------------------------------ G7 out of scope
    C("g7_sensitive_traits", "G7", [blog("s1", [
        ("I love praying at the temple every Sunday.", O),
        ("I voted for the BJP in 2024.", O),
        ("Running helped me through depression.", O),
        ("My husband and I love hiking.", O),
        ("My salary doubled last year.", O),
        ("I love pottery.", P)])],
      [X("stated_preference", "pottery", "verified", pol=1)]),
    C("g7_whereabouts", "G7", [blog("s2", [
        ("I'm in Goa this week.", O),
        ("Flying to Delhi tomorrow for a conference.", O),
        ("See you in Pune next month.", O),
        ("I'll be speaking at PyCon next month.", O),
        ("I love long train journeys.", P)])],
      [X("stated_preference", "long train journeys", "verified", pol=1)]),
    C("g7_residence", "G7", [blog("s3", [
        ("I live in Koramangala.", O),
        ("Back home in Chennai for the holidays.", O),
        ("We moved to Indiranagar last year.", O),
        ("I enjoy quiet mornings.", P)])],
      [X("stated_preference", "quiet mornings", "verified", pol=1)]),
    C("g7_travel", "G7", [blog("s4", [
        ("I trekked in Ladakh.", O),
        ("I love visiting Lisbon.", O),
        ("I spoke at PyCon India in Hyderabad in 2024.", A)])],
      [X("activity", "pycon india in hyderabad", "verified")]),
    C("g7_third_person_sensitive", "G7", [
        anchor(),
        news("society", "x", 0, [("Priya Raman was arrested in 2019.", O), ("Raman lives in Indiranagar.", O),
                                 ("Her daughter loves painting.", O), ("She is a member of the Chess Club.", S)])],
      [X("association", "chess club", "tentative")]),
    C("g7_activity_reveals_trait", "G7", [
        anchor(),
        news("charity", "x", 1, [("Priya Raman ran a 10K for a cancer charity in 2025.", O),
                                 ("She attended a church fundraiser in 2024.", O),
                                 ("Raman organised a hardware meetup in 2025.", A)])],
      [X("activity", "hardware meetup", "tentative")]),
]
