# Apple Health Shortcut: Optimization Plan and Research

[Українська версія](../../uk/plans/2026-09-27-apple-health-shortcut-optimization.md)

**Date:** 2026-09-27

**Status:** Empty-URL change implemented and signed. Performance plan proposed; on-device measurements pending.

**Evidence:** Apple documentation and Apple-authored WWDC sessions, fetched and read on 2026-09-27. This research contains no reproduced timings or measured speedup.

## 1. Recommendation

**Measure launch, Health queries, per-sample processing, payload construction, and the HTTP action separately.** Then optimize the largest measured stage while preserving complete snapshots for every declared date and metric family.

The current Shortcut already queries only today. After a device baseline, evaluate **one list append per family instead of per sample** and **returning the HTTP response before sending the Telegram summary**. Choose between these experiments using the measured processing and request times. Consider separate fast/full schedules next, and a native HealthKit collector if the remaining cost warrants it. The URL setup change is complete.

## 2. Local findings and implementation plan

### 2.1 Current execution path

Audited the editable workflow, its signed artifact, ingestion code, and structural tests. These are code findings, not device timing results. [L1] [L2] [L3] [L4]

| Part | Current implementation | Performance implication to measure |
|---|---|---|
| Workflow | 46 stored action entries, including one comment and loop boundaries. | Stored action count understates runtime work because loop bodies repeat. |
| Queries | Eight sequential Find Health Samples queries: steps, active energy, sleep, HRV, resting heart rate, weight, distance, exercise minutes. Ordinary heart rate and workouts are absent. | Measure each query and its returned sample count separately. |
| Date filters | Seven queries use Start Date is today; sleep uses End Date is today. All eight disable the result-count limit. | A generic “change the history window to today” recommendation would not improve this artifact. |
| Sample processing | Eight Repeat blocks. Each sample executes a Dictionary action and Add to Variable `Metrics`; sample dates/properties are rendered inside the dictionary. | For `N` returned samples, there are `2N` body-action executions, excluding loop management and property conversion. |
| Request construction | One base dictionary, insertion of `metrics`, two Get Type actions (`com.apple.plist` → `public.json`), one file-body POST. | There is already one request per run, rather than one per sample. Measure coercion and upload separately. |
| Setup | One import question targets `WFURL` at action index 45. There is no Ask for Input action in the execution path. | Clearing the example improves setup; ordinary runs already use the saved address. |
| Server response | `sync_apple_health()` awaits `_notify_ingest_summary_to_telegram()` after ingestion and before returning. That helper reads the language and awaits Telegram `send_message()`. | The HTTP action includes notification latency after the data has been saved. Measure this tail before changing it. |

### 2.2 Contract constraints for any optimization

- **Replacement, not delta accumulation.** `_extract_snapshot_meta()` and `_upsert_metric_family()` operate on complete covered date/family pairs. A newer snapshot replaces those rows. Querying only “since the last run” and declaring today complete would discard earlier totals. Omitted families keep their rows; a declared family with no samples writes zero or a `NULL` average. A family skipped by a fast mode must also be omitted from coverage. [L2]
- **Freshness and day boundaries.** Keep the observation timestamp with its export, preserve it on a transport retry, and use consistent local date bounds. The current workflow constructs its Current Date envelope after the loops; instrumentation or schedule changes must account for a run crossing midnight. Sleep remains attributed to the date its interval ends. [L1] [L2]
- **Raw-sample wire format.** The native contract requires a `metrics` list, at most 10,000 items; the router caps the request at 5 MiB and accepts JSON or plist dictionaries. There is no explicit delta or daily-aggregate wire contract. Representing a daily average as one synthetic sample changes sample counts/weighting; daily totals can also violate per-sample bounds. Introduce a defined aggregate contract before sending compact daily statistics. [L2] [L3]
- **Source semantics.** The current server sums raw cumulative quantities and averages discrete samples; it does not implement HealthKit's cross-device quantity deduplication. Sleep does union overlapping intervals. Source filtering or switching to HealthKit statistics can therefore change results and needs a separate correctness comparison against Health, not just payload parity. [L2] [S14]
- **Historical corrections.** Today's signed workflow does not reconcile previous days. Adding a reduced-frequency mode requires an explicit catch-up policy for late Watch sync, historical edits, and deletions. Each catch-up request must still contain complete covered pairs and fit the server limits. [L1] [L2] [S12]

### 2.3 Completed: empty URL setup

- Set both `WFWorkflowImportQuestions[0].DefaultValue` and the POST action's `WFURL` to empty strings in [the editable source][L1]. The question still asks for the personal URL from `/connect_apple_health` and targets the same POST action.
- Regenerated [the signed `.shortcut` file](../../shortcuts/apple-health-sync.shortcut) with `shortcuts sign --mode anyone`.
- Passed plist lint and all **9 artifact tests**, including signature verification and decoded source/action/import-question parity. No example host or replacement token remains in the source.
- Passed the **5 existing download/template tests** for the served artifact, iPhone/iPad delivery, macOS handoff, and payload structure.
- Import the updated signed file on an iPhone/iPad and paste the full personal URL into the empty field. Existing installed copies retain their configuration; the server's download changes when the new artifact is deployed. Device confirmation of the import UI remains pending.

### 2.4 Prioritized tasks

Effort labels describe relative implementation scope, not promised timing or speedup. All performance changes below remain proposed.

| ID / priority | Task | Effort | Acceptance evidence |
|---|---|---|---|
| M1 / P0 | Profile a diagnostic copy: launch, eight queries, eight loops, payload coercion, HTTP. On the server, separate parsing, ingestion/commit, and notification time. | Small + device access | At least five paired runs per experiment; model/iOS/launch route, counts, bytes, median and range recorded. Minimal no-Health launch control distinguishes app startup from Health work. |
| M2 / P1 | If processing dominates, make each loop's final output its metric dictionary; use Repeat Results and append/concatenate once per family. | Medium | Reduce explicit append executions from `N` to at most eight. The receiver sees the same **flat** list, values, units, dates, sleep stages, and counts, including zero/one/many-sample cases. Timings improve beyond normal run variation. [S3] |
| M3 / P1 | If the HTTP tail dominates, move the existing best-effort Telegram summary to a post-response background task after a successful commit. | Small | An intentionally delayed Telegram sender no longer delays the HTTP response body. Persistence failures still return errors; notification failures remain isolated. Use actual response-send timing, not just a test client's total task duration. |
| M4 / P1 | Check that the saved URL is configured before the first Health query; stop with a setup message if it is empty. | Small | A skipped/cancelled setup fails immediately rather than after all eight queries. Configured runs need no recurring input dialog. |
| M5 / P2 | Offer an opt-in fast mode for steps, active energy, distance, and exercise time; run the full eight-family mode at a separately configured cadence. | Medium | Fast mode declares exactly its four queried families, preserving recovery/weight rows. Full runs refresh them and reconcile yesterday; missed runs trigger bounded catch-up. Select modes through automation input, without a menu on every run. Benchmark: dense activity samples may still dominate. |
| M6 / P2 | If payload preparation is material, compare direct plist file upload with the current two-step type coercion; the endpoint already accepts plist. | Small–medium | Equal decoded request and ingestion results, correct content type, nonempty file body, measured bytes/encoding/parse cost. The existing zero-byte-body regression stays covered. Two one-time actions are lower priority than per-sample work. [L3] [L4] |
| M7 / P3 | If measured costs remain unacceptable, prototype a native HealthKit collector using anchored changes and affected-day statistics. | Large | Device benchmark plus an explicit aggregate/collector contract; source policy, deletions, averages/counts, sleep intervals, retries, and reconciliation verified. See section 5. |

**Execution order:** M1 first; select M2 and/or M3 from the measurements, then repeat the same paired benchmark. M4 improves incomplete setup. M5 is an explicit freshness tradeoff; M6 depends on measured encoding cost. M7 is a separate client project.

**Implementation touchpoints:** M2/M4/M5/M6 update both Shortcut artifacts and their import-question action index, then adapt `tests/test_apple_health_shortcut_artifact.py` and the template test in `tests/test_apple_health.py` to the new wiring. M3 changes `app/routers/apple_health.py` and response/notification tests. Coverage or aggregate changes need the contract tests in `tests/test_apple_health_v3_contract.py`, extended-family tests, and disposable-PostgreSQL tests when persistence changes. [L4] [L5]

### 2.5 Device acceptance and immediate baseline

1. Complete a fresh import with the blank URL field, paste the personal URL, and approve Health/network access. Measure setup separately.
2. On an unlocked iPhone, compare the tile in the open Shortcuts app with the user's usual launch route. Record tap-to-first-action and total time; also run a minimal no-Health control. Use a diagnostic copy for per-stage measurements as described in section 4.
3. Compare baseline/candidate payloads and server results on a fixed observation window. Check empty families, overnight sleep, kg/lb and km/mi, multiple sources, and a run near midnight. A change to statistics/source semantics requires a Health reference comparison as well.
4. Test the configured automation and a transport retry. Confirm successful coverage, stable freshness on retry, no recurring URL prompt, and no regression in failures or cancellations. Treat locked-device execution as a separate case.

No iPhone was available in this session, so the long-running symptom and any speedup remain unmeasured. The useful next observations are **iPhone model, iOS version, launch route, approximate delay before execution, and total duration**. The plan can then prioritize the stage actually causing the wait.

## 3. Primary-source findings

### 3.1 Find Health Samples: type, date, source, and limits

**Documented:** Apple lists **Find Health Samples** among Find actions. Find actions filter supplied input; without input, they retrieve their own app's content. Multiple filters support **All** or **Any**. **Limit → Get x Items** sets the maximum number of returned results; sorting controls their order. Apple recommends more specific filters, or continuing in the app, when broad queries/large content affect widget or Apple Watch performance. [S1] [S2]

**Proposed checks on the target iPhone:**

| Parameter | Optimization candidate and correctness condition |
|---|---|
| Type | Select the required health type in each query. Apple documents health-type search in this action's editor; confirm the actual metric labels on the installed iOS version. [S20] |
| Date | Apply the required date bounds in the Find action. Verify `Start Date`/`End Date` operators, local midnight, timezone/DST changes, inclusivity, and samples crossing midnight. Do not assume that a Shortcuts date filter maps to a particular HealthKit predicate. |
| Source | Inspect whether the installed action exposes the intended source filter and what it identifies. Restricting to a source is acceptable only for an explicitly chosen source policy; it can exclude valid data from another device. Apple's Health app prioritizes sources, and its HealthKit step-count example combines nonduplicated contributions from iPhone and Watch. [S14] [S16] |
| Limit | Keep complete-snapshot queries uncapped. A smaller cap can omit matching records; it is not a complete-day aggregation or a documented pagination mechanism. A sorted latest-item lookup is suitable only when the required result really is the latest item, not a daily total or average. [S2] [S14] |

**Evidence boundary:** The reviewed public Shortcuts pages do not provide a complete, versioned Health action parameter schema, date-boundary rules, or source-filter identity rules. Those details require inspection and validation on the target device. HealthKit's native date predicate explicitly compares sample start/end dates and has overlap options; that documents the API, not the built-in action's implementation. [S13]

### 3.2 Repeat overhead and aggregation

**Documented:** Shortcuts executes actions sequentially. **Repeat with Each** runs its body once for every input item, and collects each iteration's final output into **Repeat Results**. [S3] [S4]

**Inference:** With `N` samples and `k` unconditional actions per sample, the loop executes `N × k` body actions, plus loop management. This supports investigating repeated formatting, property extraction, and list-building. It supplies no milliseconds-per-action estimate and does not prove that loops dominate this Shortcut.

**Proposed experiment:** Hoist invariant work outside loops; compare direct use of Repeat Results with equivalent explicit accumulation. Verify output types, ordering, units, and empty-list behavior before adopting a change. Apple's documented output collection supports this experiment; its speed benefit remains unmeasured. [S3]

**Aggregation distinction:** HealthKit explicitly offers statistics queries over quantity samples, including cumulative sums and discrete averages, and collection queries for daily/hourly intervals. [S10] [S11] The reviewed Shortcuts documentation does **not establish** a built-in equivalent exposing those APIs. Inspect any health grouping or numeric-list statistics options available on the actual device. Neither their absence nor their equivalence to HealthKit can be concluded from the generic guide. Calculating over an already fetched list should be benchmarked separately from database-side aggregation.

Do not assume that summing raw records, or summing independently computed source totals, reproduces Health's total. Apple's WWDC example specifically shows that summing iPhone and Watch steps double-counts overlapping contributions, whereas `HKStatisticsQuery` handles that example's deduplication. A Watch-only filter can also miss phone-only contributions. [S14]

### 3.3 Full-day snapshots versus recent-time deltas

**Documented:** HealthKit sample-date predicates concern when samples start/end. Anchored queries instead return newly saved and deleted objects since an anchor. Health supports manually entered dates/times and deletion of historical records. [S12] [S13] [S16]

**Inference:** “Samples whose start date is after the last upload” is not a change feed: it can miss a later insertion with an older event date and cannot report a deletion. A recent-time slice also lacks the earlier samples needed to reconstruct a complete daily statistic.

**Proposed contract-preserving approach:** If ingestion replaces a covered date/family, send all required samples for that date through a recorded cutoff, including today's elapsed portion. A shorter recent-time slice must not declare full-day coverage. Review any reduced lookback, separate metric cadence, or today-only fast mode together with reconciliation of older dates and explicit omission semantics. Fewer queried days/families is a candidate to measure, not evidence of correctness or guaranteed acceleration.

Precomputed totals require an explicitly supported payload contract and defined source, unit, average, and interval semantics. The local contract review in section 2.2 confirms that the current server expects complete raw-sample snapshots.

### 3.4 Protected Health data, foreground execution, and automation

**Documented:** Apple encrypts the HealthKit store when the device locks; an app may be unable to read it in the background. Read permission is per data type, and a denied read can appear to the app as no data. [S9] Apple's Shortcuts guide also describes input and private-data permission dialogs during execution. [S4]

**Proposed baseline:** Complete setup and permissions in an unlocked foreground run. Measure permission/setup time separately from repeat runs. Test locked execution as a distinct reliability case; an empty result alone is not proof of a valid zero-activity day. Record observed prompts/errors rather than assuming one particular locked-device failure mode.

**Documented:** Supported personal automations can run without confirmation; Apple lists triggers including Time of Day, App, and Charger, and notes that individual actions may also need configuration. Its iOS 17 release notes explicitly introduced **Run Immediately** for Bluetooth, Wi-Fi, Arrive, and Leave automations. [S7] [S8]

**Proposed use:** Choose Run Immediately where the installed version/trigger supports it; older guide wording is “Ask Before Running” off. Verify the actual trigger on the device. This removes confirmation interaction, but is not evidence of faster Health queries or a bypass of protected-data access. Compare foreground and automation runs separately. Apple's documented large-query performance concern specifically names widgets and Apple Watch; it does not establish a universal foreground speedup. [S2] [S7] [S9]

### 3.5 Remove the editable example server URL through setup

**Documented:** Import questions attach to action parameters. Apple says a questioned personal field is cleared when shared, answers populate the recipient's shortcut, **Default Answer is optional**, and Customize Shortcut answers overwrite the previous configuration. By comparison, **Ask for Input** displays an editable dialog when execution reaches that action. [S5] [S6]

**Proposed UX:** Leave the setup answer and destination parameter empty in the distributed artifact, ask for the complete personal connection URL once during setup, and use that configured parameter during ordinary runs. Remove the prefilled example server URL rather than asking users to edit an example host. Validate the configured destination before querying Health. This reduces configuration friction; any runtime benefit depends on removing recurring interaction.

**Device verification:** Fresh import, blank/cancelled setup, successful setup, a second run after reopening Shortcuts, and a real automation trigger. Confirm that the configured value persists and the normal path does not prompt again. Check replacement/reimport behavior separately; the guide's saved configuration model is not a guarantee that updating an installed shortcut preserves previous answers. [S5]

## 4. On-device benchmarking proposal

Use a diagnostic copy and a non-production receiver for upload experiments. Keep diagnostic output to timings, counts, and byte sizes; show one summary at completion. Apple recommends Quick Look to inspect action boundaries; use it for correctness checks, then remove interactive previews from timed runs. [S17]

| Stage | Suggested measurement |
|---|---|
| Launch | Observe tap/trigger to first executed action with an external stopwatch or screen recording. Internal timestamps cannot measure the delay before the first action executes. |
| Health query | Capture a fresh time immediately before and after each Find action; record family, date bounds, and returned count. Keep inputs explicit so added timing actions do not change what Find searches. [S1] [S2] |
| Per-sample processing | Time each complete Repeat block separately from its query. Avoid a timer or notification per sample. |
| Payload preparation | Time dictionary/text construction and any explicit JSON conversion; record payload size. If conversion occurs inside the HTTP action, report it with that stage instead. |
| Request/response | Time the full Get Contents of URL action. Treat this as a combined client stage; it does not isolate encoding, connection, upload, server work, download, or response conversion. Server-side timings from a test receiver can narrow that uncertainty. |
| Completion | Measure response handling and final user feedback separately. |

For each candidate, run several paired baseline/candidate trials on the same device and fixed date interval. Separate opening the app from running a tile in an already-open app; compare a minimal no-Health control through the same launch route. Record iPhone model, iOS build, launch route, lock state, first versus subsequent run, permission prompts, per-family counts, network, and power/thermal conditions. Report median, range, failures, and cancellations; retain slow results. Use a completed day where possible, and note any changed sample counts between trials.

Compare query-only, query-plus-processing, and complete runs. Prioritize the stage that actually dominates. Check payload/value parity, declared coverage, day boundaries, and multi-source cases alongside timing. A faster run with missing records, prompts, or failed uploads is not an accepted optimization. All measurements in this section are proposed; no device benchmark has been performed here.

## 5. Longer-term native HealthKit collector

**Documented app capabilities:** `HKStatisticsQuery` computes quantity statistics; `HKStatisticsCollectionQuery` returns interval-based results. Statistics queries are quantity-only, so they are not a universal replacement for other sample types. [S10] [S11] Apple's WWDC sync pattern combines persisted anchored queries with statistics queries to recompute affected days and send updated statistics, reducing redundant querying and transmission. [S12] [S15]

**Proposed architecture:** A native iOS collector could use that pattern, then send explicitly supported complete daily results. Design durable progress/retry handling and reconciliation before advancing checkpoints. Account for deletions when identifying affected dates: `HKDeletedObject` exposes a deleted object's UUID and metadata, and Apple states that deletion records are temporary. Plan a way to map deletions to dates or perform broader reconciliation. [S18]

For background operation, observer queries can notify an app of changes; the app must issue another query to retrieve those changes. Background delivery requires registration and has frequency limits; Apple requires testing it on a device rather than the Simulator. Protected-data constraints still apply. [S19] [S9] These are native-app mechanisms, not verified capabilities of the built-in Find Health Samples action, and they carry no measured speedup or always-on execution promise for this project.

## 6. Uncertainties and decision gates

- **Unknown bottleneck:** launch delay, a particular family query, loop work, serialization, and the request stage remain unmeasured.
- **Public documentation gap:** complete Health action filter/grouping metadata and its exact mapping to HealthKit were not located. Confirm them on the target iPhone rather than inferring availability or absence.
- **Version-sensitive setup:** automation labels, supported triggers, health-type labels, and import/reimport behavior need device confirmation. The cited user-guide pages are pinned to iOS 26; release-note claims are scoped to their stated versions.
- **Local contract:** section 2 records the verified replacement/coverage behavior and the missing explicit aggregate/delta contract. Any such format change needs separate implementation and correctness evidence.

## 7. Verified primary sources

All sources below were fetched successfully on **2026-09-27**. Developer documentation was read through Apple's JSON documentation endpoints. Source descriptions identify the evidence used, not performance measurements.

| Source | Apple publication |
|---|---|
| [S1] | Shortcuts: Intro to Find and Filter actions — Find Health Samples and input behavior. |
| [S2] | Shortcuts: Add filter parameters — All/Any, sorting, maximum-result limits, widget/Watch guidance. |
| [S3] | Shortcuts: Use Repeat actions — per-item execution and Repeat Results. |
| [S4] | Shortcuts: Run a shortcut from the app — sequential execution and prompts. |
| [S5] | Shortcuts: Add import questions — optional defaults and saved parameter configuration. |
| [S6] | Shortcuts: Ask for Input — runtime dialogs and editable defaults. |
| [S7] | Shortcuts: Enable or disable a personal automation — confirmation settings and trigger support. |
| [S8] | Shortcuts iOS 17 release notes — Run Immediately for additional triggers. |
| [S9] | HealthKit: Protecting user privacy — locked store and read authorization. |
| [S10] | HealthKit: HKStatisticsQuery — quantity statistics. |
| [S11] | HealthKit: HKStatisticsCollectionQuery — interval statistics. |
| [S12] | HealthKit: HKAnchoredObjectQuery — saved/deleted changes and anchors. |
| [S13] | HealthKit: predicateForSamples — sample-date comparisons. |
| [S14] | WWDC20: Getting started with HealthKit — statistics and multi-device step deduplication. |
| [S15] | WWDC20: Synchronize health data with HealthKit — anchored changes plus affected-day statistics. |
| [S16] | Manage Health data — historical entry/deletion and source priorities. |
| [S17] | Shortcuts: Testing your actions — inspect intermediate output. |
| [S18] | HealthKit: HKDeletedObject — deletion identity and temporary retention. |
| [S19] | HealthKit: Executing Observer Queries — background delivery and device testing. |
| [S20] | Shortcuts iOS 16 release notes — health-type search and sleep phases. |

[S1]: https://support.apple.com/guide/shortcuts/intro-to-find-and-filter-actions-apd3c845e881/9.0/ios/26
[S2]: https://support.apple.com/guide/shortcuts/add-filter-parameters-apdbdab3433f/9.0/ios/26
[S3]: https://support.apple.com/guide/shortcuts/use-repeat-actions-apdc11deb2c1/9.0/ios/26
[S4]: https://support.apple.com/guide/shortcuts/run-a-shortcut-from-the-app-apd5ba077760/9.0/ios/26
[S5]: https://support.apple.com/guide/shortcuts/add-import-questions-to-shared-shortcuts-apdf330fd3a0/9.0/ios/26
[S6]: https://support.apple.com/guide/shortcuts/use-the-ask-for-input-action-apd68b5c9161/9.0/ios/26
[S7]: https://support.apple.com/guide/shortcuts/enable-or-disable-a-personal-automation-apd602971e63/9.0/ios/26
[S8]: https://support.apple.com/en-us/HT213909
[S9]: https://developer.apple.com/tutorials/data/documentation/healthkit/protecting-user-privacy.json
[S10]: https://developer.apple.com/tutorials/data/documentation/healthkit/hkstatisticsquery.json
[S11]: https://developer.apple.com/tutorials/data/documentation/healthkit/hkstatisticscollectionquery.json
[S12]: https://developer.apple.com/tutorials/data/documentation/healthkit/hkanchoredobjectquery.json
[S13]: https://developer.apple.com/tutorials/data/documentation/healthkit/hkquery/predicateforsamples(withstart:end:options:).json
[S14]: https://developer.apple.com/videos/play/wwdc2020/10664/
[S15]: https://developer.apple.com/videos/play/wwdc2020/10184/
[S16]: https://support.apple.com/en-us/108779
[S17]: https://support.apple.com/guide/shortcuts/test-your-actions-apda75604f37/9.0/ios/26
[S18]: https://developer.apple.com/tutorials/data/documentation/healthkit/hkdeletedobject.json
[S19]: https://developer.apple.com/tutorials/data/documentation/healthkit/executing-observer-queries.json
[S20]: https://support.apple.com/en-us/HT213544

[L1]: ../../shortcuts/apple-health-sync.shortcut.plist
[L2]: ../../../app/services/apple_health.py
[L3]: ../../../app/routers/apple_health.py
[L4]: ../../../tests/test_apple_health_shortcut_artifact.py
[L5]: ../../../tests/test_apple_health.py
