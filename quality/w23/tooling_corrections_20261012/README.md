# W23 corrective byte evidence — pending functional acceptance

The user authorized additive correction work in the current checkout. This package preserves historical evidence and binds corrected bytes separately. It does not select a release candidate, activate private authority, authorize installation/publication or close W23.

Verify the independently selected record:

```text
python scripts/w23_tooling_successor.py --record quality/w23/tooling_corrections_20261012.json --sha256 8a0937c7283d2d34844cfd4df5e4d01047f3c856fb060a26121634d0ece73951
```

The verifier returns three separate states: archived historical integrity, current byte identity and `PENDING_USER_ACCEPTANCE`. Computing a new expected digest from a changed record does not constitute independent selection or user approval.

The witness v001 and predecessor reports are unchanged. `historical_tooling/checkpoint_e3c1b615` contains 22 original toolings, the original presentation owner and Git attributes, 26 authenticated retired sources and three additional historical inputs. These are data blobs; archived Python is never executed. Subdirectory `.gitattributes` rules preserve their raw bytes. The repository's original Git attributes and six F02 rules are unchanged.

The corrected presentation owner uses canonical LF. Its historical projection accepts only the exact mojibake replacement plus CRLF-to-LF transport, then reproduces the pinned old production fingerprint. Current production identity is checked independently against all 883 production Python files. The historical cycle test authenticates archived inputs and retired source ASTs; it does not label old execution results as current functional acceptance.

The six bound logs cover 158 focused tests, five historical graph/mutation checks, clean-host builder imports, Ruff/C901, focused Linux/Win32 typing and size/visibility/CURRENT subgates. Output hashes authenticate logs; they cannot independently prove that a command executed. Execution attribution and source scopes remain explicit, and final closure requires independent review.

CAND-03's materializer remains unchanged pending ACL/concurrency resolution. BEHAVIOR-01, Windows A/B diagnostics, full CI, installed lifecycle and private-authority reconciliation remain open. No workflow gate or baseline was relaxed, and no commit/push/merge occurred.
