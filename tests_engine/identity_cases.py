"""Labelled synthetic identity cases (phase 5). Fictional people, organisations and .example domains.

Each case labels the expected status of every document and the expected subject status. Subject
statuses 'confirmed' and 'strongly_supported' mean RESOLVED; everything else must stay unresolved.
"""

from __future__ import annotations

from datetime import date

TODAY = date(2026, 10, 17)
SUBJECT = dict(name="Priya Raman", organization="Nimbus Compute", organization_domain="nimbuscompute.example",
               role="VP of Infrastructure", location="Bengaluru", known_urls=[])
NO_DOMAIN = dict(SUBJECT, organization_domain=None)


def doc(i, url, text, published=None, same_as=None, dup_of=None):
    host = url.split("/")[2]
    return dict(doc_id=f"d{i}", url=url, domain=host, registrable=".".join(host.split(".")[-2:]),
                published_at=published, duplicate_of=dup_of, title=None, text=text,
                metadata={"same_as": same_as or []})


BIO = "Priya Raman is VP of Infrastructure at Nimbus Compute, where she leads the GPU cloud team."
STRONG_A = "Priya Raman, VP of Infrastructure at Nimbus Compute, said GPU capacity will double next year."
STRONG_B = "Priya Raman of Nimbus Compute, based in Bengaluru, spoke about inference costs at the summit."
CHEF = "Priya Raman, the pastry chef at Saffron Bakehouse in Chennai, opened a second bakery."
STRIKER = "Priya Raman, the striker at Chennai City FC, scored twice on Sunday."

CASES = [
    dict(id="official_anchor", label="official domain page", subject=SUBJECT,
         docs=[doc(1, "https://nimbuscompute.example/team/priya", BIO)],
         expect_docs={"d1": "confirmed"}, expect="confirmed"),
    dict(id="two_independent_strong", label="two strong pages, independent domains", subject=NO_DOMAIN,
         docs=[doc(1, "https://techpress.example/a", STRONG_A), doc(2, "https://summit.example/b", STRONG_B)],
         expect_docs={"d1": "strong", "d2": "strong"}, expect="strongly_supported"),
    dict(id="name_plus_org_only", label="bare name + organisation match is not proof", subject=NO_DOMAIN,
         docs=[doc(1, "https://blog.example/x", "Priya Raman of Nimbus Compute talks about GPU planning.")],
         expect_docs={"d1": "possible"}, expect="possible"),
    dict(id="namesakes_with_target", label="same-name chef and footballer alongside the real subject",
         subject=NO_DOMAIN,
         docs=[doc(1, "https://techpress.example/a", STRONG_A), doc(2, "https://summit.example/b", STRONG_B),
               doc(3, "https://chefstories.example/c", CHEF), doc(4, "https://sports.example/d", STRIKER)],
         expect_docs={"d1": "strong", "d2": "strong", "d3": "rejected", "d4": "rejected"},
         expect="strongly_supported", expect_namesakes=2),
    dict(id="namesakes_only", label="only namesakes found", subject=NO_DOMAIN,
         docs=[doc(1, "https://chefstories.example/c", CHEF), doc(2, "https://sports.example/d", STRIKER)],
         expect_docs={"d1": "rejected", "d2": "rejected"}, expect="insufficient_evidence"),
    dict(id="name_only_pages", label="name without any anchor", subject=NO_DOMAIN,
         docs=[doc(1, "https://comments.example/x", "Priya Raman wrote a thoughtful comment on the thread.")],
         expect_docs={"d1": "insufficient"}, expect="insufficient_evidence"),
    dict(id="misleading_company_association", label="organisation on the page, attached to someone else",
         subject=NO_DOMAIN,
         docs=[doc(1, "https://festival.example/x", "Nimbus Compute sponsored the food festival. " + CHEF),
               doc(2, "https://festival.example/speakers",
                   "Speakers: Priya Raman (Saffron Bakehouse) and Arjun Mehta (Nimbus Compute)."),
               doc(3, "https://news.example/y", "Nimbus Compute announced a GPU region. Later that day, "
                                                "Priya Raman gave a cooking demonstration downtown.")],
         expect_docs={"d1": "rejected", "d2": "rejected", "d3": "insufficient"}, expect="insufficient_evidence"),
    dict(id="historical_only", label="only a past-tense affiliation", subject=NO_DOMAIN,
         docs=[doc(1, "https://profile.example/x", "Priya Raman, formerly VP at Nimbus Compute, now advises "
                                                    "startups on infrastructure.")],
         expect_docs={"d1": "historical"}, expect="possible"),
    dict(id="outdated_role_linked", label="same GitHub profile now tied to another company", subject=SUBJECT,
         docs=[doc(1, "https://nimbuscompute.example/team/priya", BIO, "2019-03-01",
                   same_as=["https://github.com/priyaraman"]),
               doc(2, "https://techpress.example/a", STRONG_A, "2019-05-01", same_as=["https://github.com/priyaraman"]),
               doc(3, "https://news.example/2026/orbit", "Priya Raman joined Orbit Labs as CTO this month.",
                   "2026-09-01", same_as=["https://github.com/priyaraman"])],
         expect_docs={"d1": "confirmed", "d2": "strong", "d3": "rejected"}, expect="conflicting"),
    dict(id="stale_unlinked", label="old support only, newer pages tie the name elsewhere", subject=NO_DOMAIN,
         docs=[doc(1, "https://techpress.example/a", STRONG_A, "2018-02-01"),
               doc(2, "https://summit.example/b", STRONG_B, "2018-06-01"),
               doc(3, "https://news.example/c", "Priya Raman, CTO at Orbit Labs, unveiled a new chip.", "2026-08-01")],
         expect_docs={"d1": "strong", "d2": "strong", "d3": "rejected"}, expect="possible"),
    dict(id="duplicate_profiles_linked", label="official page links one GitHub; search finds another",
         subject=SUBJECT, leads=["https://github.com/priya-raman-2"],
         docs=[doc(1, "https://nimbuscompute.example/team/priya", BIO, same_as=["https://github.com/priyaraman"])],
         expect_docs={"d1": "confirmed"}, expect="confirmed",
         expect_profiles={"github.com/priyaraman": "confirmed", "github.com/priya-raman-2": "duplicate_unresolved"}),
    dict(id="duplicate_profiles_unlinked", label="two GitHub handles, both snippet-only", subject=NO_DOMAIN,
         leads=["https://github.com/priyaraman", "https://github.com/praman-dev"],
         docs=[doc(1, "https://techpress.example/a", STRONG_A), doc(2, "https://summit.example/b", STRONG_B)],
         expect_docs={"d1": "strong", "d2": "strong"}, expect="strongly_supported",
         expect_profiles={"github.com/priyaraman": "duplicate_unresolved",
                          "github.com/praman-dev": "duplicate_unresolved"}),
    dict(id="two_confirmed_same_platform", label="user-supplied and officially linked GitHub differ",
         subject=dict(SUBJECT, known_urls=["https://github.com/priyaraman"]),
         docs=[doc(1, "https://nimbuscompute.example/team/priya", BIO, same_as=["https://github.com/priyar"])],
         expect_docs={"d1": "confirmed"}, expect="confirmed",
         expect_profiles={"github.com/priyaraman": "duplicate_unresolved", "github.com/priyar": "duplicate_unresolved"}),
    dict(id="conflicting_same_document", label="one page ties the name to two current employers",
         subject=NO_DOMAIN,
         docs=[doc(1, "https://press.example/x", "Priya Raman, VP at Nimbus Compute and CTO at Orbit Labs, said "
                                                  "both teams share tooling.")],
         expect_docs={"d1": "ambiguous"}, expect="conflicting"),
    dict(id="hedged_attribution", label="forum: 'some say this is...'", subject=NO_DOMAIN,
         docs=[doc(1, "https://forum.example/t", "Some say this is Priya Raman from Nimbus Compute, but the "
                                                  "account is unverified.")],
         expect_docs={"d1": "rejected"}, expect="insufficient_evidence"),
    dict(id="syndicated_copies", label="one article syndicated to three sites", subject=NO_DOMAIN,
         docs=[doc(1, "https://wire.example/a", STRONG_A), doc(2, "https://mirror1.example/a", STRONG_A, dup_of="d1"),
               doc(3, "https://mirror2.example/a", STRONG_A, dup_of="d1")],
         expect_docs={"d1": "strong", "d2": "strong", "d3": "strong"}, expect="possible"),
    dict(id="same_domain_twice", label="two strong pages on one domain are not independent", subject=NO_DOMAIN,
         docs=[doc(1, "https://techpress.example/a", STRONG_A), doc(2, "https://techpress.example/b", STRONG_B)],
         expect_docs={"d1": "strong", "d2": "strong"}, expect="possible"),
    dict(id="known_url_anchor", label="user-supplied personal site", subject=dict(
        NO_DOMAIN, known_urls=["https://priyaraman.example/about"]),
         docs=[doc(1, "https://priyaraman.example/about", "Priya Raman writes about GPU infrastructure here.")],
         expect_docs={"d1": "confirmed"}, expect="confirmed"),
    dict(id="surname_only", label="surname without first name", subject=NO_DOMAIN,
         docs=[doc(1, "https://news.example/s", "Raman spoke at the event about cloud costs.")],
         expect_docs={"d1": "insufficient"}, expect="insufficient_evidence"),
    dict(id="no_documents", label="nothing found at all", subject=NO_DOMAIN, docs=[], expect_docs={},
         expect="insufficient_evidence"),
]

RESOLVED = {"confirmed", "strongly_supported"}
