# Small Issues TODO
Use this file for follow-up items that are too small for separate plan docs.

## Open Items

### iFLYTEK Handshake Error Codes
- Symptom: `35022 usedQuantity exceeds the limit` currently surfaces as `did not receive a valid HTTP response`.
- Target: show iFLYTEK code and message directly in CLI and logs.
- TODO: parse code and message from handshake `InvalidMessage`; mark quota/auth/account errors as non-retriable; keep transient network failures retriable; preserve provider details in final errors; add unit tests for `35022`, auth failure, and pure transport failure.
