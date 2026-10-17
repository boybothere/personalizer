"""Held-out labelled claim cases (phase 6). Written and frozen BEFORE they were first run, and NOT used to tune the
rules. Their agreement is reported as measured, including the failures (PHASE6_RESULTS.md). Fictional data only.

Same label vocabulary as claim_cases.py. Labels say what a careful reader would expect the rules to output.
"""

from __future__ import annotations

from claim_cases import ID, A, C, D, I, N, NEG, O, P, R, S, anchor, blog, news

N_ = N  # noqa: F841 (readability alias)

HELDOUT = [
    C("h_pref_variants", "H", [blog("h1", [
        ("I absolutely adore hand-drawn maps.", P),
        ("I genuinely enjoy writing documentation.", P),
        ("I prefer tea to coffee.", P),
        ("I really dislike long commutes.", NEG),
        ("I never enjoyed group projects.", NEG)])]),
    C("h_no_longer", "H", [blog("h2", [
        ("I used to enjoy crossword puzzles.", NEG),
        ("I no longer like spicy food.", NEG),
        ("I stopped enjoying conference travel.", NEG)])]),
    C("h_hypothetical_more", "H", [blog("h3", [
        ("I'd really like to learn the cello.", R),
        ("I want to try rock climbing.", R),
        ("One day I might enjoy gardening.", R)])]),
    C("h_questions_and_sarcasm", "H", [blog("h4", [
        ("Who doesn't love a good spreadsheet?", R),
        ("Yeah right, I love waking up at 5am.", R),
        ("I love paperwork, said no one ever.", R)])]),
    C("h_mixed_page", "H", [blog("h5", [
        ("Last weekend was busy.", N),
        ("I love bird photography.", P),
        ("The weather was grey.", N),
        ("I wrote about camera lenses in 2025.", A),
        ("I am a volunteer at the Lens Library.", S)])]),
    C("h_third_person_news", "H", [
        anchor(),
        news("hnews", "a", 0, [
            ("Raman enjoys long-distance cycling.", P),
            ("She hates jargon.", NEG),
            ("Priya Raman spoke at a fintech meetup in 2024.", A),
            ("Raman is a member of the Women in Hardware network.", S)])]),
    C("h_pronoun_chain_breaks", "H", [
        anchor(),
        news("hchain", "a", 1, [
            ("The panel also featured Arjun Mehta.", N),
            ("He loves cricket.", N),
            ("Priya Raman closed the session.", N)])]),
    C("h_other_person_quote", "H", [
        anchor(),
        news("hquote", "a", 2, [
            ('"I love trail running," said Arjun Mehta.', N),
            ('Priya Raman said: "I enjoy mentoring new engineers."', P)])]),
    C("h_appositive_activity", "H", [
        anchor(),
        news("happos", "a", 3, [
            ("Priya Raman, who joined the company in 2019, ran the Bengaluru Marathon in 2024.", A),
            ("She organised an open-source sprint in 2025.", A)])]),
    C("h_association_variants", "H", [
        anchor(),
        news("hassoc", "a", 4, [
            ("Raman serves as an adviser to Open Grid Alliance.", S),
            ("She has been a fellow of the Hardware Guild.", S),
            ("Priya Raman sat on the board of the Edge Compute Forum.", S)])]),
    C("h_mentions_only", "H", [
        anchor(),
        news("hment", "a", 5, [
            ("Tickets for the talk by Priya Raman sold out.", N),
            ("Photos of Priya Raman were shared online.", N),
            ("Priya Raman thanked the organisers.", N)])]),
    C("h_out_of_scope_mixed", "H", [blog("h6", [
        ("My kids love board games.", O),
        ("I fast during Ramadan.", O),
        ("I'm heading to Mumbai next week.", O),
        ("I enjoy board games.", P),
        ("My therapist suggested journaling.", O),
        ("I grew up in Kochi.", O)])]),
    C("h_out_of_scope_third_person", "H", [
        anchor(),
        news("hoos", "a", 0, [
            ("Priya Raman campaigned for the AAP in 2023.", O),
            ("Raman recently moved to Whitefield.", O),
            ("She is a member of the Board Game Society.", S)])]),
    C("h_namesake_with_prefs", "H", [
        anchor(),
        D("https://hsports.example/x", [("Priya Raman, the striker at Chennai City FC, loves penalty kicks.", O),
                                        ("She enjoys pre-match rituals.", N)], "2026-05-01")]),
    C("h_unresolved_subject", "H", [
        D("https://hblog.example/x", [("Priya Raman wrote about GPU cooling.", N),
                                      ("Priya Raman loves gardening.", N)], "2026-05-01")],
      subject=dict(name="Priya Raman", organization="Nimbus Compute", organization_domain=None,
                   role="VP of Infrastructure", location="Bengaluru", known_urls=[])),
    C("h_vague_objects", "H", [blog("h7", [
        ("I love that.", I),
        ("I enjoy everything about it.", I),
        ("I like how simple it is.", I)])]),
    C("h_fan_variants", "H", [blog("h8", [
        ("I'm a big fan of mechanical keyboards.", P),
        ("I am passionate about accessibility.", P),
        ("I'm not a fan of cold showers.", NEG)])]),
    C("h_favourite_variants", "H", [blog("h9", [
        ("My favourite book is Dune.", P),
        ("My favourite programming language has to be Rust.", P)])]),
    C("h_reposts_and_quotes", "H", [blog("h10", [
        ("Reposted from a friend: I love sunsets.", R),
        ('As my grandmother used to say, "I love a good argument."', O)])]),
    C("h_activity_variants", "H", [blog("h11", [
        ("I built a home lab in 2023.", A),
        ("I taught a workshop on Rust in 2025.", A),
        ("I won a hackathon in 2022.", A)])]),
    C("h_dated_travel", "H", [blog("h12", [
        ("I visited Kyoto in 2019.", A),
        ("I hiked in the Alps.", O)])]),
    C("h_third_party_variants", "H", [
        anchor(),
        news("hthird", "a", 1, [
            ("According to friends, Raman adores salsa dancing.", P),
            ("Colleagues say she can't stand long meetings.", NEG)])]),
    C("h_ordered_change", "H", [
        blog("h13", [("I enjoy marathon running.", P)], "2024-02-01"),
        blog("h14", [("I no longer enjoy marathon running.", NEG)], "2026-03-01")]),
    C("h_double_neg", "H", [blog("h15", [("I don't mind early meetings.", R),
                                         ("I wouldn't say I love early meetings.", R)])]),
]

_ = (ID, A, S, R, I)
