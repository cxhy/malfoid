# 11: C 阶段在试译语料上验收

**What to build:** A translator can run the full 审查 architecture on the existing two-block preview and confirm that alignment false positives are gone before committing to Book 1 mass translation.

**Blocked by:** 06, 08, 09, 10

**Status:** ready-for-agent

- [ ] Review runs end-to-end on the migrated two-block preview.
- [ ] Reported issues trace to real source/target positions; no issue is caused by English/Chinese segment misalignment.
- [ ] A false-positive rate is recorded from the preview run.
- [ ] The preview review result is used to make a go/no-go decision for Book 1.
- [ ] An integration test covers the preview review path without a live model endpoint.
