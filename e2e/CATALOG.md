# E2E用例目录

65项；由e2e/cases的元数据生成。完整筛选和运行方式见[手册](README.md)。

| case_id | module | labels | effects | requires | 检查内容 |
|---|---|---|---|---|---|
| AUTH-001 | auth | smoke, negative, auth | read | — | Missing Bearer is rejected |
| AUTH-002 | auth | smoke, negative, auth | read | — | Unregistered key is rejected |
| AUTH-003 | auth | smoke, positive, auth | read | — | Registered customer key and model authorization |
| AUTH-004 | auth | negative, auth, isolation | read | — | Identity headers cannot bypass authentication |
| AUTH-005 | auth | negative, auth, optional | read | restricted_key_file | Restricted credential cannot access OCR |
| CAP-001 | capabilities | smoke, contract | read | — | Published async input and budget schema |
| CAP-002 | capabilities | negative, contract | read | — | Unknown model is not implicitly aliased |
| EDGE-001 | edge | smoke, boundary | read | — | API health and OpenAPI are not customer routes |
| EDGE-002 | edge | boundary, negative | read | — | Unsupported generation and batch routes are closed |
| EDGE-003 | edge | boundary, negative | read | — | Query parameters rejected at business ingress |
| EDGE-004 | edge | smoke, contract | read | — | Generated request ID and safe JSON error envelope |
| EDGE-005 | edge | contract, negative | read | — | Client request ID is not trusted as server ID |
| EDGE-006 | edge | boundary, negative | write | — | Unsupported method denied by ingress |
| OCR-001 | ocr | positive, gpu, smoke | inference | — | Bare base64 through real OCR |
| OCR-002 | ocr | positive, gpu, contract | inference | — | Data URL via v1 alias with polygons omitted |
| OCR-003 | ocr | positive, gpu, fixture | inference | — | Blank PNG yields successful empty lines |
| OCR-004 | ocr | positive, gpu, optional | inference | jpeg_file | Real JPEG fixture accepted |
| OCR-005 | ocr | positive, gpu, external, optional | inference | public_image_url | Public image URL downloaded and inferred |
| OCR-006 | ocr | accuracy, gpu, optional | inference | image_file, expected_text_contains | Configured bilingual expected text is recognized |
| OCR-V001 | ocr_validation | negative, contract | write | — | Missing image source |
| OCR-V002 | ocr_validation | negative, contract | write | — | Two image sources rejected |
| OCR-V003 | ocr_validation | negative, contract | write | — | Malformed base64 rejected |
| OCR-V004 | ocr_validation | negative, contract | write | — | Unsupported language hint rejected |
| OCR-V005 | ocr_validation | negative, contract | write | — | Boolean must not be string |
| OCR-V006 | ocr_validation | negative, contract | write | — | Unknown request field rejected |
| OCR-V007 | ocr_validation | negative, contract | write | — | Request ID control character rejected |
| OCR-V008 | ocr_validation | negative, contract | write | — | Non-image bytes rejected |
| OCR-V009 | ocr_validation | negative, contract | write | — | Data URL media mismatch rejected |
| OCR-V010 | ocr_validation | negative, contract | write | — | PNG/JPEG-only data URL format |
| OCR-V011 | ocr_validation | negative, contract | write | — | Oversized pixel dimensions rejected without GPU |
| OCR-V012 | ocr_validation | negative, contract | write | — | Unknown logical model is not aliased |
| OCR-V013 | ocr_validation | negative, ssrf, boundary | write | — | Private/loopback URLs, ports and credentials rejected |
| OCR-V014 | ocr_validation | negative, contract | write | — | Malformed JSON safely rejected |
| CHAT-001 | chat | positive, gpu, legacy | inference | — | Legacy Chat envelope and nested OCR JSON |
| CHAT-002 | chat | negative, legacy | write | — | Streaming not supported on MaaS Chat |
| ART-001 | artifacts | positive, storage, smoke | write | — | Upload metadata and exact authenticated download |
| ART-002 | artifacts | negative, storage | write | — | Empty upload rejected |
| ART-003 | artifacts | negative, storage | write | — | Unsupported media type rejected |
| ART-004 | artifacts | negative, storage | read | — | Missing artifact returns safe 404 |
| ART-005 | artifacts | positive, storage | write | — | Duplicate upload creates separate IDs |
| ART-006 | artifacts | negative, boundary, large | write, load | — | Upload byte limit enforced |
| TASK-001 | tasks | positive, gpu, smoke, async | write, inference | — | Upload-submit-poll-download real async OCR |
| TASK-002 | tasks | positive, idempotency, async | write, inference | — | Same key/body reuses same task without new execution |
| TASK-003 | tasks | negative, idempotency, async | write, inference | — | Same key and changed request conflicts |
| TASK-004 | tasks | negative, idempotency | write | — | Missing idempotency header rejected |
| TASK-005 | tasks | negative, async | write | — | Missing artifact is not accepted |
| TASK-006 | tasks | negative, contract | write | — | Extra fields and invalid retry budgets rejected |
| TASK-007 | tasks | negative, contract | write | — | OCR input and parameter schema enforced |
| TASK-008 | tasks | negative, async | write | — | Missing task cannot be queried or cancelled |
| TASK-009 | tasks | positive, cancellation, async | write, inference | — | Cancellation is race-aware and repeatable |
| ISO-001 | isolation | negative, isolation, optional | write | secondary_key_file, distinct_projects | Different project cannot download known input or spoof ownership |
| ISO-002 | isolation | negative, isolation, optional, async | write, inference | secondary_key_file, distinct_projects | Different project cannot query/cancel task or download result |
| TTL-001 | lifecycle | retention, optional | read | expired_artifact_id | Previously expired own artifact is unavailable |
| MAINT-001 | maintenance | maintenance, window, optional | inference | expect_maintenance | Synchronous inference blocked in externally prepared maintenance |
| MAINT-002 | maintenance | maintenance, window, cancellation, optional | write, inference | expect_maintenance | Queued task cancellation while dispatch is externally paused |
| QUOTA-001 | quota | quota, window, optional | inference, load | expect_capacity_full | Externally saturated inference capacity returns 429 |
| QUOTA-002 | quota | quota, window, optional | write, inference, load | expect_queue_full | Externally full queue rejects a new task |
| JSON-001 | edge | negative, boundary | write | — | Task JSON envelope byte budget enforced |
| OPS-001 | operations | operations, readonly, optional | read | operator | Current control plane components running |
| OPS-002 | operations | operations, readonly, optional | read | operator | Business monitor fresh, healthy and no UNKNOWN |
| OPS-003 | operations | operations, readonly, optional | read | operator | Retired collector remains inactive and masked |
| E5-001 | bootstrap | e5, operations, readonly, optional | read | operator | Configuration and authenticated registry components running |
| METER-001 | metering | metering, gpu, async, optional | write, inference | operator | One successful async task creates exactly one task event |
| METER-002 | metering | metering, gpu, exclusive, optional | inference | operator, project, exclusive_project | Successful sync increments exclusive project event count once |
| METER-003 | metering | metering, negative, exclusive, optional | write | operator, project, exclusive_project | Invalid image creates no successful sync event |
