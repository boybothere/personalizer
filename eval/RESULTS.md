# Evaluation results (heuristic mode)

Synthetic corpus: `eval/dataset.py`. Regenerate with `python eval/run_eval.py`.

| Metric | Value |
|---|---|
| source attribution accuracy | 1.0 |
| identity match accuracy | 1.0 |
| identity false accepts | 0 |
| false positive rate | 0.0 |
| relevance precision at 5 | 1.0 |
| checks passed | 5 |
| cases | 5 |
| avg cost usd per run | 0.0 |

## Per case

### priya: PASS
- [x] no forbidden content
- [x] common ground as expected
- [x] expected sources used
- [x] identity resolution as expected
- [x] every brief item cites suitable evidence
- findings 11 (suitable 9), excluded items 7, attribution 11/11, identity 10/10, relevant in top-5 5/5

### priya-no-personal: PASS
- [x] no forbidden content
- [x] common ground as expected
- [x] identity resolution as expected
- [x] every brief item cites suitable evidence
- findings 11 (suitable 7), excluded items 7, attribution 11/11, identity 10/10, relevant in top-5 5/5

### daniel: PASS
- [x] no forbidden content
- [x] expected sources used
- [x] expected warning raised
- [x] identity resolution as expected
- [x] every brief item cites suitable evidence
- findings 5 (suitable 4), excluded items 2, attribution 5/5, identity 4/4, relevant in top-5 4/4

### unknown-person: PASS
- [x] no forbidden content
- [x] no findings fabricated
- [x] identity resolution as expected
- [x] every brief item cites suitable evidence
- findings 0 (suitable 0), excluded items 0, attribution 0/0, identity 0/0, relevant in top-5 0/0

### sensitive-request: PASS
- [x] refused as expected
- refused: Meeting purpose references 'religion', which is outside scope. This engine only prepares professional meeting context and never researches sensitive or private attributes.
