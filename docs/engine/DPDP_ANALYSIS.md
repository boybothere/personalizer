# DPDP Act 2023: applicability and commencement analysis (DRAFT)

> **Status: draft for qualified Indian legal review. This is not legal advice.** It was prepared on 2026-10-17 from secondary sources and the Act's text as reproduced on dpdprules.org; the citations should be checked against the official Gazette text. Decision D4.5 / item 7: **no production use until counsel has reviewed this document.** Public availability is not treated as blanket permission.

## 1. Commencement (what is in force when)

| Provisions | Commencement | Basis |
|---|---|---|
| Act passed | Presidential assent 11 Aug 2023; sections commence on dates appointed under s.1(2) | Act s.1(2) |
| DPDP Rules 2025 notified; Rules 1, 2, 17–21 (Data Protection Board and procedural provisions) | **13 Nov 2025** (some sources say 14 Nov) | G.S.R. notification |
| s.6(9) and the Consent Manager provisions | **13 Nov 2026** (+12 months) | as above |
| Substantive obligations, including **ss.3–5, 7–17 and penalties ss.28–34** | **~13–15 May 2027** (+18 months; secondary sources differ by up to two days) | G.S.R. 843(E), as reported |

**Implication:** the Act's core data-fiduciary obligations are **not yet in force** (as of October 2026), but they will be before any realistic production timeline completes. Design for them now. ⚖ Counsel to confirm what regime governs the interim period; the IT Act s.43A and SPDI Rules 2011 are generally understood to continue until the DPDP provisions commence.

## 2. Does the Act apply to this engine?

| Question | Analysis | Confidence |
|---|---|---|
| Is it "digital personal data"? | Yes: web pages, posts and profiles about an identifiable person, processed digitally. | High |
| Territorial scope | s.3(a) covers processing within India; the operator is in India. s.3(b) additionally covers processing abroad connected with offering goods or services to people in India. | High |
| s.3(c)(i) personal or domestic purpose exclusion | **Not available**: preparing for business meetings is not personal or domestic. | High |
| s.3(c)(ii) "made publicly available by the Data Principal", or by a person under a legal obligation to publish | **Partial at best.** The Act's illustration is a person who publishes her *own* data on her blog or social media. Content the subject published themselves (own blog, own posts, own bio) **may** fall outside the Act. **Content others published about them** (news articles, conference pages, third-party profiles, search snippets) **is not covered** by this exclusion. | Medium; ⚖ |
| Is the operator a Data Fiduciary? | Yes: it decides the purpose (meeting preparation) and the means (this engine). | High |

**Conclusion (draft):** once ss.3–17 commence, the Act will apply to most of what this engine processes. Only material verifiably self-published by the subject may be outside it.

## 3. Lawful basis for processing (s.4)

Processing needs **consent (s.6)** or a **legitimate use (s.7)**.

- **s.7 legitimate uses:**
  - (a) data voluntarily provided for a specified purpose
  - (b)–(e) State functions, legal obligations, court orders
  - (f)–(h) medical emergencies, epidemics, disasters
  - (i) employment

  **None plainly covers commercial meeting preparation.** s.7(a) could apply only where the person themselves gave the data for this purpose (for example, a bio they sent ahead of the meeting). ⚖
- **s.17(2)(b) research, archiving or statistical exemption:** requires that the data "is not to be used to take any decision specific to a Data Principal" and that processing follows prescribed standards. A brief about one person, used to prepare for a meeting with that person, is person-specific. **This exemption is unlikely to fit.** ⚖
- **Consent:** possible in principle, for example by telling invitees that public professional information may be reviewed, with an opt-out, or by asking them. Notice requirements under s.5 apply. ⚖

## 4. Obligations that would apply (once in force)

| Obligation | Engine status today |
|---|---|
| Notice (s.5) | **Gap:** there is no notice mechanism. |
| Purpose limitation / minimisation | Partial: purpose screening, sensitive-category exclusion, no hobby-only searches (prototype), retention limits. |
| Accuracy and completeness (s.8(3)) | Partial: verbatim excerpts, date provenance, conflict detection; correction workflow planned (phases 5–7). |
| Reasonable security safeguards (s.8(5); penalty up to ₹250 crore) | Partial: local SQLite, `secure_delete`, no keys in the database. **Gaps:** no encryption at rest, no access control, no audit of users. |
| Breach notification (s.8(6); up to ₹200 crore) | **Gap:** no process. |
| Erasure when the purpose is served (s.8(7)) | Implemented: 30-day run retention, 7-day text retention, subject deletion (tested, including on-disk overwrite). |
| Grievance redress, Data Principal rights (ss.11–14: access, correction, erasure, grievance, nomination) | Partial: deletion and opt-out exist in the prototype; the engine has subject deletion. **Gaps:** no access or correction request handling, no identity verification of requesters. |
| Children (s.9) | The engine is not designed for minors; no age screening. **Gap** if any subject could be a child. |

## 5. Engine design consequences (proposed, pending counsel)

1. **Tag provenance by publisher:** `self_published` (the author is the subject) versus `third_party`. Planned for identity in phase 5. This lets a deployment restrict itself to s.3(c)(ii)-arguable material if counsel advises.
2. **A consent or notice mode:** a run flag recording the basis for processing (consent reference, or "self-published only").
3. **Keep the existing minimisation:** no special categories, no whereabouts, short retention, deletion that is verifiably enforced.
4. **Provider terms are a separate layer:** Brave (transient storage only), Exa (copying restriction), Tavily (AUP bans surveillance, doxxing and special-category data). These bind independently of the DPDP Act.

## 6. Questions for counsel

1. Does s.3(c)(ii) exclude only data the person published *themselves*? How should that be evidenced (account verification, authorship)?
2. Is there any lawful basis, other than consent, for compiling third-party-published professional information about a business contact? Does the answer change once ss.4–7 commence?
3. What notice or consent design would be adequate for meeting invitees? Does it change for non-Indian subjects (GDPR Art. 14 interplay)?
4. Does storing search-provider snippets, or provider-extracted copies, create additional obligations or third-party rights issues?
5. What interim obligations apply before May 2027 (IT Act s.43A / SPDI Rules)?
6. Data processor terms with the search providers: are processing agreements needed?

## Sources consulted

- [dpdprules.org: Act s.3](https://dpdprules.org/act/3), [s.7](https://dpdprules.org/act/7), [s.17](https://dpdprules.org/act/17)
- [DPDP Act commencement notification summary](https://www.easydp.in/dpdp-commencement-notification/)
- [Shardul Amarchand Mangaldas: enforcement and rules](https://www.amsshardul.com/insight/enforcement-of-the-dpdp-act-and-notification-of-the-dpdp-rules/)
- [Taxmann: analysis of the Act and Rules 2025](https://www.taxmann.com/post/blog/analysis-indias-dpdp-act-and-rules)
- [MeitY Gazette text (PDF)](https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf)
